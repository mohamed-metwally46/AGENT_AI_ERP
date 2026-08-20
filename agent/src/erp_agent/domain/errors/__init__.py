"""Domain errors."""

from __future__ import annotations

from erp_agent.domain.errors.exceptions import (
    AgentLoopLimitError,
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
    ReadOnlyViolationError,
    RestrictedFieldError,
    ToolArgumentError,
    ToolError,
    WriteBarrierError,
)

__all__ = [
    "AgentLoopLimitError",
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
    "ReadOnlyViolationError",
    "RestrictedFieldError",
    "ToolArgumentError",
    "ToolError",
    "WriteBarrierError",
]
