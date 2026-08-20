"""The HTTP application.

A factory rather than a module-level ``app``, for one reason that matters:
every dependency arrives through the container, so a test builds the whole
surface over fakes and a deployment builds it over the real thing, and neither
path is special-cased. A module-level singleton would have to construct a
database pool at import time, which makes the API untestable and start-up
failures invisible until the first request.

**Nothing is served before the safety properties hold.** The lifespan runs
:func:`~erp_agent.core.bootstrap.start`, which opens the pool and proves the
write barrier, and it does so before the first request is accepted. A barrier
failure aborts start-up rather than degrading into a server that answers
questions through a connection nobody verified.

**Every failure leaves through one door.** The exception handlers below are the
only place a response body is built from an error, and they build it from an
allowlist (:mod:`erp_agent.interfaces.api.errors`). An unhandled exception
becomes a fixed 500 with no detail, and the real one goes to the log. That is
Plan.md section 19 enforced structurally rather than by remembering to sanitise.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from rag.domain.ports.llm_client import LLMClient

from erp_agent.config import Settings
from erp_agent.core.bootstrap import start
from erp_agent.core.container import Container
from erp_agent.core.logging import get_logger
from erp_agent.domain.errors import ErpAgentError, RateLimitError
from erp_agent.interfaces.api.errors import status_for, to_error_body
from erp_agent.interfaces.api.middleware.rate_limit import TokenBucketLimiter
from erp_agent.interfaces.api.routes import ask, health

__all__ = ["create_app"]

#: What FastAPI expects for ``lifespan``. Named so ``_shell`` can take one
#: without describing the type inline.
Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

_logger = get_logger(__name__)

_TITLE = "ERP AI Agent"
_DESCRIPTION = (
    "Read-only question answering over the HR database and the document index. "
    "Every request is scoped to the principal its bearer token authenticates; "
    "no request field influences that scope."
)
_VERSION = "0.1.0"


def create_app(container: Container, *, llm: LLMClient | None = None) -> FastAPI:
    """Build the HTTP application over a constructed container.

    Start-up happens in the lifespan, not here, and that is not a stylistic
    choice. A connection pool belongs to the event loop it was created on, so
    opening one at construction -- on whichever loop happened to be running --
    and then serving requests on the server's loop produces a pool that is
    quietly unusable. Running :func:`~erp_agent.core.bootstrap.start` inside the
    lifespan puts start-up on the loop that will serve.

    The write barrier is therefore proved during lifespan startup, through the
    same code path ``--check`` uses. A failure aborts the server before it
    accepts a connection.

    Args:
        container: A constructed container. Opening it is this function's job,
            via the lifespan; passing an already-open one would open it twice.
        llm: Overrides the configured model client. Tests pass a scripted one;
            nothing else should.

    Returns:
        The application. Start-up happens when it is served.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Open and verify on the serving loop; release on the way out."""
        await start(container)
        _wire(app, container, llm=llm)
        try:
            yield
        finally:
            await container.close()

    return _shell(container.settings, lifespan)


def _shell(settings: Settings, lifespan: Lifespan) -> FastAPI:
    """The application object itself, before anything is wired into it."""
    app = FastAPI(
        title=_TITLE,
        description=_DESCRIPTION,
        version=_VERSION,
        lifespan=lifespan,
        # Phase 7 acceptance criterion 3 wants the schema published; a
        # deployment that would rather not advertise its surface can switch it
        # off without switching off the API.
        openapi_url="/openapi.json" if settings.api.docs_enabled else None,
        docs_url="/docs" if settings.api.docs_enabled else None,
        redoc_url=None,
    )
    _install_error_handlers(app)
    app.include_router(health.router)
    app.include_router(ask.router)
    return app


def _wire(app: FastAPI, container: Container, *, llm: LLMClient | None) -> None:
    """Resolve the object graph onto the application.

    Once, not per request, so a request never triggers wiring -- and so a
    misconfiguration surfaces at start-up rather than as a 500 on somebody's
    first question.
    """
    settings = container.settings
    app.state.authenticate = container.authenticate
    app.state.questions = container.answer_question(llm)
    app.state.conversations = container.conversations
    app.state.limiter = TokenBucketLimiter(
        per_minute=settings.api.rate_limit_per_minute,
        burst=settings.api.rate_limit_burst,
    )
    app.state.max_question_chars = settings.api.max_question_chars
    app.state.knowledge_enabled = container.knowledge is not None
    app.state.tool_count = len(container.tool_registry)


def _install_error_handlers(app: FastAPI) -> None:
    """Route every failure through the allowlist renderer."""

    @app.exception_handler(ErpAgentError)
    async def _domain_error(request: Request, exc: ErpAgentError) -> JSONResponse:
        status = status_for(exc)
        # The context, which may name an argument or a tool, goes to the log.
        # The body gets the code and the message and nothing else.
        _logger.warning(
            "api.request_failed",
            code=exc.code,
            status=int(status),
            path=request.url.path,
            **{key: value for key, value in exc.context.items() if key != "trace"},
        )
        headers = _retry_after(exc)
        return JSONResponse(status_code=int(status), content=to_error_body(exc), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Which field was wrong, never what was in it. A rejected body may carry
        # anything a caller typed, including a credential they put in the wrong
        # place.
        fields = sorted(
            {".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()}
        )
        _logger.info("api.request_rejected", path=request.url.path, fields=fields)
        return JSONResponse(
            status_code=422,
            content={
                "code": "REQUEST_INVALID",
                "message": (
                    "the request body was not accepted; unexpected or malformed "
                    f"fields: {', '.join(fields) or 'body'}"
                ),
                "retryable": False,
            },
        )

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        # `exc_info` so the real failure is diagnosable, and a fixed body so it
        # is not published. This handler is the reason a stack trace cannot
        # reach a caller by being forgotten about.
        _logger.error(
            "api.unhandled_error",
            path=request.url.path,
            error_type=type(exc).__name__,
            exc_info=exc,
        )
        return JSONResponse(status_code=500, content=to_error_body(exc))


def _retry_after(exc: ErpAgentError) -> dict[str, str] | None:
    """A ``Retry-After`` header for the one error where waiting is the fix."""
    if not isinstance(exc, RateLimitError):
        return None
    seconds = exc.context.get("retry_after_s")
    return {"Retry-After": str(seconds)} if isinstance(seconds, int) else None
