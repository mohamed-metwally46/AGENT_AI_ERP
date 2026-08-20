"""The domain exception hierarchy.

Mirrors the RAG package's design: every failure the agent can express is one of
these types, adapters translate vendor exceptions at their own boundary, and two
properties carry real weight.

``code``
    A stable, machine-readable identifier derived from the class name. It cannot
    drift from the class it names because it is computed from it.

``retryable``
    Whether retrying the same operation could plausibly succeed, read as *data*
    by retry policy rather than matched out of message strings.

One rule is specific to this package: a security failure is **never** retryable
and never carries the offending value in :attr:`context`. Re-running a refused
query does not make it allowed, and a rejected statement logged verbatim is a
rejected statement leaked into the log.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, ClassVar

__all__ = [
    "ConfigurationError",
    "DataSourceError",
    "DataSourceUnavailableError",
    "ErpAgentError",
    "QueryTimeoutError",
    "ReadOnlyViolationError",
    "WriteBarrierError",
]

# Splits CamelCase into words while keeping acronyms intact.
_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _derive_code(class_name: str) -> str:
    """Convert a CamelCase class name to a SCREAMING_SNAKE_CASE error code."""
    return _WORD_BOUNDARY.sub("_", class_name).upper()


class ErpAgentError(Exception):
    """Base class for every error the agent raises deliberately.

    Attributes:
        code: Stable machine-readable identifier, derived from the class name.
        default_retryable: Class-level default for :attr:`retryable`.
        message: Human-readable description of the failure.
        context: Structured detail for logging. Never contains SQL text,
            credentials, prompts or row data.
        retryable: Whether retrying the operation could plausibly succeed.
    """

    code: ClassVar[str] = "ERP_AGENT_ERROR"
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        context: Mapping[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            message: Human-readable description.
            context: Structured, non-sensitive detail for logging.
            retryable: Overrides the class default when a specific occurrence
                differs from the general case.
        """
        super().__init__(message)
        self.message = message
        self.context: Mapping[str, Any] = dict(context or {})
        self.retryable = self.default_retryable if retryable is None else retryable

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Derive the error code from the subclass name."""
        super().__init_subclass__(**kwargs)
        cls.code = _derive_code(cls.__name__)

    def __repr__(self) -> str:
        """Render for a traceback without expanding the context payload."""
        return f"{type(self).__name__}({self.message!r}, code={self.code!r})"


class ConfigurationError(ErpAgentError):
    """The deployment is misconfigured and must not start."""


class DataSourceError(ErpAgentError):
    """Base for failures reaching or reading the structured data source."""


class DataSourceUnavailableError(DataSourceError):
    """The database could not be reached."""

    default_retryable = True


class QueryTimeoutError(DataSourceError):
    """A query exceeded its statement timeout and was aborted by the server."""

    default_retryable = True


class ReadOnlyViolationError(DataSourceError):
    """A statement was refused because it is not a read.

    Raised by the application-side guard (write barrier 3 of Plan.md section
    8.2) before anything reaches the driver. Deliberately not retryable, and
    deliberately carries no SQL text: the point of refusing a statement is not
    to then write it somewhere.
    """

    default_retryable = False


class AuthenticationError(ErpAgentError):
    """The caller did not prove who they are.

    Distinct from :class:`AuthorizationError`, which is about what a known
    principal may read. Kept separate because they map to different HTTP
    statuses and, more importantly, to different fixes: one means "present a
    credential", the other means "you cannot see that".

    Carries no detail about *why* the credential failed. Unknown, malformed and
    expired are one answer to the caller, because distinguishing them tells
    someone probing the endpoint which tokens exist.
    """

    default_retryable = False


class AuthorizationError(ErpAgentError):
    """The principal may not read what was asked for.

    Produced by the security layer, never by the model's discretion. Not
    retryable: asking again does not change what someone is allowed to see.
    """

    default_retryable = False


class RestrictedFieldError(AuthorizationError):
    """A query named a column this principal may not read."""


class OutOfScopeError(AuthorizationError):
    """A request reached beyond the principal's data scope."""


class PrincipalNotFoundError(AuthorizationError):
    """The authenticated identity does not resolve to a known principal.

    Deliberately an authorization failure rather than a "not found": an identity
    the system cannot place has no scope, and the only safe reading of "no
    scope" is "no access". It must surface at the API boundary as 401/403 --
    never as an empty result set, and never by quietly falling back to ``SELF``,
    which would turn an unknown caller into a valid one.
    """


class RateLimitError(ErpAgentError):
    """The caller has spent their request budget.

    A transport concern expressed as a domain error so the whole system keeps
    one exception family -- but the reason it exists is not transport. Plan.md
    R6 is exfiltration by aggregation: many individually *authorised* questions
    reconstructing a restricted whole. No scope predicate refuses those,
    because none of them is out of scope. A budget is what refuses them.

    Retryable, and the only error in this module that is retryable by waiting
    rather than by fixing something.
    """

    default_retryable = True


class ToolError(ErpAgentError):
    """Base for failures a tool reports back to the agent loop.

    These become tool *results*, not crashes: the model must be told that its
    call failed and given the chance to correct it, which is only possible if the
    failure is a value rather than an exception escaping the loop.
    """


class ToolArgumentError(ToolError):
    """A tool was called with arguments it cannot use.

    Not retryable with the same arguments -- the model must supply different
    ones. Carries the argument *name* but never its value: tool arguments come
    from model output and may contain whatever a user typed.
    """


class AgentLoopLimitError(ToolError):
    """The bounded agent loop hit a ceiling without producing an answer.

    Raised rather than returning whatever had accumulated. A partial answer
    presented as a complete one is worse than an error: the user cannot tell
    that the agent gave up, and neither can an evaluation harness.
    """


class LLMUnavailableError(ErpAgentError):
    """The configured model could not be reached or refused the exchange."""

    default_retryable = True


class KnowledgeUnavailableError(ErpAgentError):
    """The document index could not be searched.

    Distinct from "found nothing": an unavailable index must not be reported to
    the model as an empty result, or the answer becomes "there is no such
    policy" when the truth is "we could not look".
    """

    default_retryable = True


class WriteBarrierError(ErpAgentError):
    """A startup probe proved the write barrier is not intact.

    The connected role was able to do something a read-only principal must not
    be able to do. The process refuses to serve rather than run with an
    unverified safety claim.
    """

    default_retryable = False
