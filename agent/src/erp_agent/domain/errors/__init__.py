"""Domain errors."""

from __future__ import annotations

from erp_agent.domain.errors.exceptions import (
    AgentLoopLimitError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    DataSourceError,
    DataSourceUnavailableError,
    ErpAgentError,
    KnowledgeUnavailableError,
    LLMUnavailableError,
    OutOfScopeError,
    PrincipalNotFoundError,
    QueryTimeoutError,
    RateLimitError,
    ReadOnlyViolationError,
    RestrictedFieldError,
    ToolArgumentError,
    ToolError,
    WriteBarrierError,
)

__all__ = [
    "AgentLoopLimitError",
    "AuthenticationError",
    "AuthorizationError",
    "ConfigurationError",
    "DataSourceError",
    "DataSourceUnavailableError",
    "ErpAgentError",
    "KnowledgeUnavailableError",
    "LLMUnavailableError",
    "OutOfScopeError",
    "PrincipalNotFoundError",
    "QueryTimeoutError",
    "RateLimitError",
    "ReadOnlyViolationError",
    "RestrictedFieldError",
    "ToolArgumentError",
    "ToolError",
    "WriteBarrierError",
]
