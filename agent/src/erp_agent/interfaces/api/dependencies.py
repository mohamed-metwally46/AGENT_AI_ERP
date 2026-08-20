"""What a route is allowed to ask for, and where it comes from.

Everything a request needs is resolved from the composition root held on the
application, never constructed here. That is what keeps contract 6 true --
``interfaces`` imports no infrastructure -- and it is also what makes the API
substitutable in a test: a fake container produces a working app.

The dependency worth reading is :func:`current_caller`. It is the only way a
route obtains a :class:`SecurityContext`, and it takes the ``Authorization``
header as its sole input. A route cannot ask for a context and a body field that
influences it, because no such dependency exists.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, Header, Request

from erp_agent.application.conversation import ConversationStore
from erp_agent.application.security import AuthenticatedCaller
from erp_agent.application.use_cases import AnswerErpQuestion
from erp_agent.interfaces.api.middleware.auth import authenticate_header
from erp_agent.interfaces.api.middleware.rate_limit import TokenBucketLimiter

__all__ = [
    "CurrentCaller",
    "Limiter",
    "Questions",
    "current_caller",
    "get_conversations",
    "get_limiter",
    "get_questions",
]


def _service(request: Request, name: str) -> Any:
    """Fetch something the app factory put on the application state."""
    return getattr(request.app.state, name)


async def current_caller(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthenticatedCaller:
    """Resolve who is asking, from the ``Authorization`` header alone.

    Args:
        request: The incoming request, for the application's services.
        authorization: The bearer header. FastAPI populates this from the
            header of the same name and from nowhere else -- in particular not
            from the body, which is what makes "a caller cannot influence their
            own scope" a property of the wiring rather than of a check.

    Returns:
        The authenticated caller.

    Raises:
        AuthenticationError: If the credential is absent or not valid. Mapped to
            401 by the application's exception handler.
    """
    return await authenticate_header(authorization, authenticate=_service(request, "authenticate"))


def get_questions(request: Request) -> AnswerErpQuestion:
    """The use case behind ``POST /ask``."""
    result: AnswerErpQuestion = _service(request, "questions")
    return result


def get_limiter(request: Request) -> TokenBucketLimiter:
    """The per-principal rate limiter."""
    result: TokenBucketLimiter = _service(request, "limiter")
    return result


def get_conversations(request: Request) -> ConversationStore:
    """The bounded conversation store."""
    result: ConversationStore = _service(request, "conversations")
    return result


CurrentCaller = Annotated[AuthenticatedCaller, Depends(current_caller)]
Questions = Annotated[AnswerErpQuestion, Depends(get_questions)]
Limiter = Annotated[TokenBucketLimiter, Depends(get_limiter)]
Conversations = Annotated[ConversationStore, Depends(get_conversations)]
