"""The bounded single-agent loop, its static tool registry, and its trace."""

from __future__ import annotations

from erp_agent.application.agent.loop import DEFAULT_SYSTEM_PROMPT, AgentAnswer, AgentLoop
from erp_agent.application.agent.tool_registry import (
    RESERVED_ARGUMENT_NAMES,
    RegisteredTool,
    ToolRegistry,
    build_hr_registry,
)
from erp_agent.application.agent.trace import (
    SAFE_ARGUMENT_VALUES,
    AgentTrace,
    ToolInvocation,
    ToolOutcome,
)

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "RESERVED_ARGUMENT_NAMES",
    "SAFE_ARGUMENT_VALUES",
    "AgentAnswer",
    "AgentLoop",
    "AgentTrace",
    "RegisteredTool",
    "ToolInvocation",
    "ToolOutcome",
    "ToolRegistry",
    "build_hr_registry",
]
