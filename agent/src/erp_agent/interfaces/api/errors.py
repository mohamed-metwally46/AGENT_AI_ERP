"""Turning a domain failure into an HTTP response that says nothing extra.

Plan.md section 19 lists what a response must never contain: raw SQL, stack
traces, credentials, internal paths, implementation details, sensitive query
information. The way to guarantee that is not to sanitise messages on the way
out -- that is a filter someone eventually forgets to update -- but to *never
build a response from an arbitrary exception*.

So the mapping here is an allowlist. A known domain error contributes its stable
code and its own message, which was written to be read by a caller. Anything
else contributes nothing at all: the code is ``INTERNAL_ERROR``, the message is
fixed, and the real exception goes to the log where it belongs.

``ErpAgentError.context`` is deliberately never serialised. It is structured
detail for logs -- the argument at fault, the tool that failed, the reason class
-- and while none of it is meant to be sensitive, "meant to be" is not a
guarantee that survives someone adding a field.
"""

from __future__ import annotations

from http import HTTPStatus

from erp_agent.domain.errors import (
    AgentLoopLimitError,
    AuthenticationError,
    AuthorizationError,
    DataSourceUnavailableError,
    ErpAgentError,
    KnowledgeUnavailableError,
    LLMUnavailableError,
    QueryTimeoutError,
    RateLimitError,
    ToolArgumentError,
)

__all__ = ["INTERNAL_ERROR_MESSAGE", "status_for", "to_error_body"]

#: What a caller is told when something failed that has no public description.
INTERNAL_ERROR_MESSAGE = "the request could not be completed"

#: Domain error type to HTTP status. An allowlist: a type absent from here is
#: reported as 500 with no detail, which is the correct treatment for a failure
#: nobody decided how to describe.
_STATUS: tuple[tuple[type[ErpAgentError], HTTPStatus], ...] = (
    (AuthenticationError, HTTPStatus.UNAUTHORIZED),
    (AuthorizationError, HTTPStatus.FORBIDDEN),
    (ToolArgumentError, HTTPStatus.BAD_REQUEST),
    (AgentLoopLimitError, HTTPStatus.UNPROCESSABLE_ENTITY),
    (RateLimitError, HTTPStatus.TOO_MANY_REQUESTS),
    (QueryTimeoutError, HTTPStatus.GATEWAY_TIMEOUT),
    (DataSourceUnavailableError, HTTPStatus.SERVICE_UNAVAILABLE),
    (KnowledgeUnavailableError, HTTPStatus.SERVICE_UNAVAILABLE),
    (LLMUnavailableError, HTTPStatus.BAD_GATEWAY),
)


def status_for(error: BaseException) -> HTTPStatus:
    """The status an error should be reported as.

    Args:
        error: What was raised.

    Returns:
        The mapped status, or ``500`` for anything not in the allowlist.
    """
    for error_type, status in _STATUS:
        if isinstance(error, error_type):
            return status
    return HTTPStatus.INTERNAL_SERVER_ERROR


def to_error_body(error: BaseException) -> dict[str, object]:
    """Render an error as a response body.

    Args:
        error: What was raised.

    Returns:
        ``code``, ``message`` and ``retryable``. For an unmapped error the
        message is fixed and the code is generic -- deliberately identical for
        a driver crash, a bug and a failure nobody anticipated, because the
        differences between them are exactly the implementation details that
        must not be published.
    """
    if status_for(error) is HTTPStatus.INTERNAL_SERVER_ERROR or not isinstance(
        error, ErpAgentError
    ):
        # Both conditions, not either: an unmapped domain error is as
        # undescribed as a driver crash, and `isinstance` is checked rather
        # than asserted because an assertion is removed by `python -O` and this
        # is the branch that keeps a stack trace out of a response body.
        return {"code": "INTERNAL_ERROR", "message": INTERNAL_ERROR_MESSAGE, "retryable": False}

    return {"code": error.code, "message": error.message, "retryable": error.retryable}
