"""``POST /ask`` -- the endpoint the whole system exists to serve.

Short by design. Everything that decides anything lives below: authentication in
the application layer, scope in the security layer, tool choice in the agent,
permission predicates in the query layer and the vector store. What is left here
is transport -- take a question, hand it down with the caller's context, render
what comes back.

Two things this route does own, and both are refusals.

*The rate limit is keyed on the authenticated identity.* Taken after
authentication rather than before, so an unauthenticated flood is rejected by
the cheaper check and a caller cannot spend someone else's budget by guessing
their id.

*The question is length-bounded here, not only in the schema.* The schema's
ceiling is a static maximum; the configured one is what the deployment chose,
and a question long enough to fill a context window is a way to bury an
injection in the middle of one.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response, status

from erp_agent.application.use_cases import AnsweredQuestion
from erp_agent.core.logging import get_logger
from erp_agent.domain.errors import RateLimitError, ToolArgumentError
from erp_agent.interfaces.api.dependencies import CurrentCaller, Limiter, Questions
from erp_agent.interfaces.api.schemas import (
    AskRequest,
    AskResponse,
    CitationOut,
    ErrorResponse,
    SourceOut,
    ToolCallOut,
)

__all__ = ["router"]

router = APIRouter(tags=["ask"])
_logger = get_logger(__name__)


@router.post(
    "/ask",
    response_model=AskResponse,
    status_code=status.HTTP_200_OK,
    summary="Ask a question about HR data or company policy",
    responses={
        400: {"model": ErrorResponse, "description": "The question could not be used."},
        401: {"model": ErrorResponse, "description": "Authentication failed."},
        422: {
            "model": ErrorResponse,
            "description": "The agent reached a bound without answering.",
        },
        429: {"model": ErrorResponse, "description": "Rate limit exceeded."},
        502: {"model": ErrorResponse, "description": "The language model was unreachable."},
        503: {"model": ErrorResponse, "description": "A data source was unavailable."},
    },
)
async def ask(
    body: AskRequest,
    caller: CurrentCaller,
    questions: Questions,
    limiter: Limiter,
    request: Request,
    response: Response,
) -> AskResponse:
    """Answer one question within the caller's scope.

    Args:
        body: The question, and optionally the conversation it continues.
        caller: Resolved from the ``Authorization`` header. The security context
            comes from here and from nowhere else.
        questions: The use case.
        limiter: The per-principal rate limiter.
        request: For configured bounds held on the application.
        response: For the rate-limit headers.

    Returns:
        The answer, its citations and the tools that ran.

    Raises:
        ToolArgumentError: If the question is empty or longer than the
            deployment allows.
        RateLimitError: If the caller is over budget.
        AgentLoopLimitError: If the agent hit a bound before answering.
        LLMUnavailableError: If the model could not be reached.
    """
    question = body.question.strip()
    max_chars: int = request.app.state.max_question_chars
    if not question:
        raise ToolArgumentError("the question is empty", context={"argument": "question"})
    if len(question) > max_chars:
        raise ToolArgumentError(
            f"the question is longer than the {max_chars} characters this deployment accepts",
            context={"argument": "question", "limit": max_chars},
        )

    decision = limiter.check(caller.identity)
    response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
    if not decision.allowed:
        raise RateLimitError(
            "too many requests; slow down",
            context={"retry_after_s": decision.retry_after_s},
        )

    conversation_id = body.conversation_id or uuid.uuid4().hex

    answered = await questions(
        question,
        context=caller.context,
        identity=caller.identity,
        conversation_id=conversation_id,
    )

    _logger.info(
        "api.answered",
        # The identity, never the credential; the trace, already sanitised;
        # never the question and never the answer, both of which quote data the
        # scope ladder was applied to (Plan.md section 20).
        identity=caller.identity,
        scope=caller.context.scope.value,
        conversation_id=conversation_id,
        **answered.trace.as_log_payload(),
    )

    return _render(answered)


def _render(answered: AnsweredQuestion) -> AskResponse:
    """Project the use case's result onto the public response.

    A projection rather than a serialisation: the internal result carries the
    full trace, and what is published is narrower -- tool names and outcomes,
    never arguments, never rows.
    """
    return AskResponse(
        answer=answered.text,
        conversation_id=answered.conversation_id,
        citations=tuple(
            CitationOut(
                marker=citation.marker,
                document_id=citation.document_id,
                document_title=citation.document_title,
                chunk_id=citation.chunk_id,
                section=citation.section,
                page_number=citation.page_number,
            )
            for citation in answered.citations
        ),
        sources=tuple(
            SourceOut(
                marker=source.marker,
                kind=source.kind.value,
                tool_name=source.tool_name,
                label=source.label,
            )
            for source in answered.sources
        ),
        tools_used=answered.trace.tools_used,
        tool_calls=tuple(
            ToolCallOut(
                tool_name=invocation.tool_name,
                outcome=invocation.outcome.value,
                dispatched=invocation.dispatched,
                round_index=invocation.round_index,
            )
            for invocation in answered.trace.invocations
        ),
        model_id=answered.model_id,
        rounds=answered.trace.rounds,
    )
