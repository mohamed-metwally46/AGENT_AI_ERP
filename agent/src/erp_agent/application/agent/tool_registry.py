"""The static tool allowlist (Plan.md section 8.2, barrier 4).

The registry is built once, from a literal list, and validated at construction.
There is no dynamic registration, no discovery from arbitrary modules, and no
tool whose arguments name a table, a column or a SQL fragment. Adding a seventh
tool is a visible change to this file.

Validation is fail-fast for a specific reason: a malformed tool spec does not
fail at construction, it fails by being *advertised to the model*. A property
with no description, or a schema whose ``required`` list names something that is
not a property, produces a tool the model will call wrongly and then be told off
for calling wrongly. Catching that at start-up costs nothing; catching it in
production costs a confused conversation.

The vocabulary is RAG's :class:`~rag.domain.models.tools.ToolDefinition`, not a
second one of our own (Plan.md section 7.1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from rag.domain.models.tools import ToolDefinition

from erp_agent.application.tools.hr import (
    MVP_TOOL_NAMES,
    CountEmployeesTool,
    GetAttendanceSummaryTool,
    GetEmployeeTool,
    GetLeaveBalanceTool,
    GetLeavePolicyTool,
    ListLeaveRequestsTool,
)
from erp_agent.application.tools.rag_search import MAX_TOP_K, RagSearchTool
from erp_agent.domain.errors import ConfigurationError, ToolArgumentError
from erp_agent.domain.ports.hr_directory import HrDirectory
from erp_agent.domain.ports.knowledge import KnowledgeBase

__all__ = [
    "RESERVED_ARGUMENT_NAMES",
    "RegisteredTool",
    "ToolRegistry",
    "build_agent_registry",
    "build_hr_registry",
]

#: Argument names a tool may never accept, because the security layer supplies
#: the same concepts from the :class:`SecurityContext`.
#:
#: This is the tool-call-layer twin of the collision rule ``merge_parameters``
#: enforces at the SQL layer. Both exist because "the scope wins because we merge
#: it last" is a convention, and a convention is not a control. A model that
#: could pass ``employee_id`` would be choosing its own visibility, and a
#: prompt-injected argument would reach the query builder before anything
#: noticed.
RESERVED_ARGUMENT_NAMES: frozenset[str] = frozenset(
    {
        "context",
        "security_context",
        "scope",
        "user_id",
        "employee_id",
        "dept_id",
        "department_id",
        "branch_id",
        "manager_of",
        "allowed_branch_ids",
        "role_names",
        "roles",
        # Resolved from roles before the model runs. A model that could pass a
        # permission would be granting itself one.
        "permissions",
        "permission_levels",
        # Row caps are ours, never the model's.
        "limit",
        "max_rows",
        # Raw SQL is never a tool argument. `query` was on this list until the
        # document tool needed it: it was reserved to block SQL, but SQL is
        # blocked by there being no tool that executes any, and by
        # `SqlStatement` refusing to be built from a write. Reserving the
        # natural name for a *search string* bought nothing and cost the whole
        # document leg. (Phase 3 over-reach, found by a Phase 4 test.)
        "sql",
        "table",
        "columns",
    }
)

type ToolHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """One capability the agent may use.

    Attributes:
        definition: What the model is told, in RAG's provider-neutral shape.
        handler: The bound coroutine that performs it.
        requires_context: Whether the handler takes a ``SecurityContext`` as its
            first positional argument. ``get_leave_policy`` is the one that does
            not -- an entitlement rule is company policy, not personal data --
            and making that explicit here stops the loop guessing.
    """

    definition: ToolDefinition
    handler: ToolHandler
    requires_context: bool = True

    @property
    def name(self) -> str:
        """The advertised tool name."""
        return self.definition.name

    @property
    def argument_names(self) -> frozenset[str]:
        """Every argument the schema declares."""
        properties = self.definition.parameters_schema.get("properties", {})
        return frozenset(properties) if isinstance(properties, Mapping) else frozenset()

    def reject_reserved(self, arguments: Mapping[str, Any]) -> None:
        """Refuse model-supplied arguments that collide with security context.

        Args:
            arguments: What the model asked to pass.

        Raises:
            ToolArgumentError: If any argument name is reserved. Refused rather
                than merged or overridden: silently dropping it would teach the
                model that the call succeeded, and silently honouring it would
                be the whole attack.
        """
        if collisions := sorted(set(arguments) & RESERVED_ARGUMENT_NAMES):
            raise ToolArgumentError(
                f"{self.name} does not accept the argument(s) "
                f"{', '.join(collisions)}; they are supplied by the security layer",
                context={"tool": self.name, "arguments": tuple(collisions)},
            )

    def reject_unknown(self, arguments: Mapping[str, Any]) -> None:
        """Refuse arguments the schema does not declare.

        Args:
            arguments: What the model asked to pass.

        Raises:
            ToolArgumentError: If any argument is undeclared. ``additionalProperties``
                is ``false`` in every schema, but a provider is not obliged to
                enforce it, so it is enforced here too.
        """
        if unknown := sorted(set(arguments) - self.argument_names):
            raise ToolArgumentError(
                f"{self.name} does not accept the argument(s) {', '.join(unknown)}",
                context={"tool": self.name, "arguments": tuple(unknown)},
            )


class ToolRegistry:
    """A validated, immutable allowlist of tools.

    Construct it with the full set. There is deliberately no ``register``
    method: a registry that can grow at request time is not an allowlist.
    """

    def __init__(self, tools: Sequence[RegisteredTool]) -> None:
        """Validate and hold the tool set.

        Args:
            tools: The complete set of tools this agent may use.

        Raises:
            ConfigurationError: If any tool spec is malformed, a name repeats,
                or the set does not match the MVP allowlist. Every one of these
                is a deployment fault and must stop start-up.
        """
        if not tools:
            raise ConfigurationError("a tool registry must contain at least one tool")

        by_name: dict[str, RegisteredTool] = {}
        for tool in tools:
            _validate_specification(tool)
            if tool.name in by_name:
                raise ConfigurationError(
                    f"tool {tool.name!r} is registered more than once; which "
                    f"implementation the model reaches would depend on list order",
                    context={"tool": tool.name},
                )
            by_name[tool.name] = tool

        self._tools = by_name

    def __contains__(self, name: object) -> bool:
        """Whether a name is registered."""
        return isinstance(name, str) and name in self._tools

    def __len__(self) -> int:
        """How many tools are registered."""
        return len(self._tools)

    def __iter__(self) -> Iterator[RegisteredTool]:
        """Iterate the registered tools, in declaration order."""
        return iter(self._tools.values())

    @property
    def names(self) -> frozenset[str]:
        """Every registered tool name."""
        return frozenset(self._tools)

    @property
    def definitions(self) -> tuple[ToolDefinition, ...]:
        """What the model is told it may call."""
        return tuple(tool.definition for tool in self._tools.values())

    def get(self, name: str) -> RegisteredTool:
        """Resolve a tool by name.

        Args:
            name: The name the model asked for.

        Returns:
            The registered tool.

        Raises:
            ToolArgumentError: If the name is not registered. A model asking for
                an unregistered tool gets a controlled error and nothing is
                dispatched -- the failure mode this allowlist exists for.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise ToolArgumentError(
                f"no tool named {name!r} is available",
                context={"tool": name, "available": tuple(sorted(self._tools))},
            )
        return tool


def _validate_specification(tool: RegisteredTool) -> None:
    """Reject a tool spec that would mislead the model.

    Raises:
        ConfigurationError: Naming what is wrong with the schema.
    """
    schema = tool.definition.parameters_schema
    name = tool.definition.name

    if not callable(tool.handler):
        raise ConfigurationError(f"tool {name!r} has no callable handler", context={"tool": name})

    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        raise ConfigurationError(
            f"tool {name!r} declares no 'properties' object", context={"tool": name}
        )

    if schema.get("additionalProperties") is not False:
        raise ConfigurationError(
            f"tool {name!r} must set additionalProperties=false, or a provider "
            f"may pass through arguments the schema never declared",
            context={"tool": name},
        )

    # The description contract: every argument the model can supply must be
    # described, or the model is guessing at what it means.
    undescribed = sorted(
        argument
        for argument, specification in properties.items()
        if not isinstance(specification, Mapping) or not str(specification.get("description", ""))
    )
    if undescribed:
        raise ConfigurationError(
            f"tool {name!r} has undescribed argument(s): {', '.join(undescribed)}",
            context={"tool": name, "arguments": tuple(undescribed)},
        )

    required = schema.get("required", [])
    if not isinstance(required, list | tuple):
        raise ConfigurationError(
            f"tool {name!r} has a malformed 'required'", context={"tool": name}
        )
    if missing := sorted(set(required) - set(properties)):
        raise ConfigurationError(
            f"tool {name!r} requires argument(s) it does not declare: {', '.join(missing)}",
            context={"tool": name, "arguments": tuple(missing)},
        )

    if reserved := sorted(set(properties) & RESERVED_ARGUMENT_NAMES):
        raise ConfigurationError(
            f"tool {name!r} declares reserved argument(s): {', '.join(reserved)}. "
            f"These come from the security context and must never be model-supplied",
            context={"tool": name, "arguments": tuple(reserved)},
        )


_EMPLOYEE_NUMBER = {
    "type": "string",
    "description": "The employee's business key, for example EMP-0016.",
    "maxLength": 32,
}


def build_hr_registry(directory: HrDirectory) -> ToolRegistry:
    """Build the MVP registry over a directory port.

    Args:
        directory: The scope-aware HR reader the tools delegate to.

    Returns:
        The validated registry of exactly the six tools in Plan.md section 6.3.

    Raises:
        ConfigurationError: If a spec is malformed, or the set drifts from the
            MVP allowlist.
    """
    tools = [
        RegisteredTool(
            definition=ToolDefinition(
                name=GetEmployeeTool.name,
                description=(
                    "Look up one employee by their employee number, or search by name. "
                    "Supply exactly one of employee_number or full_name. If a name "
                    "matches several people the tool returns a list of candidates "
                    "instead of a record; ask the user which one they meant rather "
                    "than choosing."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "employee_number": _EMPLOYEE_NUMBER,
                        "full_name": {
                            "type": "string",
                            "description": (
                                "Part or all of the employee's name. Ambiguous by "
                                "nature; prefer employee_number when it is known."
                            ),
                            "maxLength": 120,
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            handler=GetEmployeeTool(directory).execute,
        ),
        RegisteredTool(
            definition=ToolDefinition(
                name=CountEmployeesTool.name,
                description=(
                    "Count employees, optionally filtered by department, branch or "
                    "employment status. Returns both the total and how many of those "
                    "are currently active; state which one you are quoting."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "department": {
                            "type": "string",
                            "description": "Department name, for example Sales.",
                            "maxLength": 120,
                        },
                        "branch": {
                            "type": "string",
                            "description": "Branch name.",
                            "maxLength": 120,
                        },
                        "employment_status": {
                            "type": "string",
                            "description": ("Employment status to filter on, for example active."),
                            "maxLength": 32,
                        },
                    },
                    "required": [],
                    "additionalProperties": False,
                },
            ),
            handler=CountEmployeesTool(directory).execute,
        ),
        RegisteredTool(
            definition=ToolDefinition(
                name=GetLeaveBalanceTool.name,
                description=(
                    "Read an employee's leave balances. Always state the fiscal year "
                    "the balance belongs to. Omit fiscal_year for the most recent year "
                    "on file."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "employee_number": _EMPLOYEE_NUMBER,
                        "leave_code": {
                            "type": "string",
                            "description": (
                                "Leave type code such as ANN (annual) or SCK (sick). "
                                "Omit for every type."
                            ),
                            "maxLength": 16,
                        },
                        "fiscal_year": {
                            "type": "integer",
                            "description": "Fiscal year, for example 2026.",
                            "minimum": 1990,
                            "maximum": 2100,
                        },
                    },
                    "required": ["employee_number"],
                    "additionalProperties": False,
                },
            ),
            handler=GetLeaveBalanceTool(directory).execute,
        ),
        RegisteredTool(
            definition=ToolDefinition(
                name=ListLeaveRequestsTool.name,
                description=(
                    "List an employee's leave requests, most recent first, optionally "
                    "filtered by status or start date."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "employee_number": _EMPLOYEE_NUMBER,
                        "status": {
                            "type": "string",
                            "description": (
                                "Request status such as approved, pending or rejected."
                            ),
                            "maxLength": 32,
                        },
                        "start_from": {
                            "type": "string",
                            "description": "Earliest start date, as YYYY-MM-DD.",
                            "maxLength": 10,
                        },
                        "start_to": {
                            "type": "string",
                            "description": "Latest start date, as YYYY-MM-DD.",
                            "maxLength": 10,
                        },
                    },
                    "required": ["employee_number"],
                    "additionalProperties": False,
                },
            ),
            handler=ListLeaveRequestsTool(directory).execute,
        ),
        RegisteredTool(
            definition=ToolDefinition(
                name=GetLeavePolicyTool.name,
                description=(
                    "Read the company's leave entitlement rules for a leave type. "
                    "Entitlement varies by job grade, so pass job_grade_code when you "
                    "know it; the organisation-wide rule is returned first either way. "
                    "This is company policy and is the same for everyone."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "leave_code": {
                            "type": "string",
                            "description": "Leave type code such as ANN.",
                            "maxLength": 16,
                        },
                        "job_grade_code": {
                            "type": "string",
                            "description": (
                                "Job grade code, from an employee record, to narrow to "
                                "the rule that applies to them."
                            ),
                            "maxLength": 16,
                        },
                    },
                    "required": ["leave_code"],
                    "additionalProperties": False,
                },
            ),
            handler=GetLeavePolicyTool(directory).execute,
            requires_context=False,
        ),
        RegisteredTool(
            definition=ToolDefinition(
                name=GetAttendanceSummaryTool.name,
                description=(
                    "Read an employee's monthly attendance totals for a year. Omit "
                    "month for every month of that year."
                ),
                parameters_schema={
                    "type": "object",
                    "properties": {
                        "employee_number": _EMPLOYEE_NUMBER,
                        "year": {
                            "type": "integer",
                            "description": "Calendar year, for example 2026.",
                            "minimum": 1990,
                            "maximum": 2100,
                        },
                        "month": {
                            "type": "integer",
                            "description": "Calendar month, 1 to 12.",
                            "minimum": 1,
                            "maximum": 12,
                        },
                    },
                    "required": ["employee_number", "year"],
                    "additionalProperties": False,
                },
            ),
            handler=GetAttendanceSummaryTool(directory).execute,
        ),
    ]

    registry = ToolRegistry(tools)

    if registry.names != MVP_TOOL_NAMES:
        # Catches both directions: a tool added here without being added to the
        # MVP set, and one removed from the tools package but left advertised.
        raise ConfigurationError(
            "the registry does not match the MVP tool set defined in Plan.md 6.3",
            context={
                "registered": tuple(sorted(registry.names)),
                "expected": tuple(sorted(MVP_TOOL_NAMES)),
            },
        )
    return registry


def build_agent_registry(
    *,
    directory: HrDirectory,
    knowledge: KnowledgeBase,
) -> ToolRegistry:
    """Build the full MVP registry: the six structured tools plus document search.

    Kept separate from :func:`build_hr_registry` rather than folded into it. The
    structured six are a closed set with its own guard (``MVP_TOOL_NAMES``), and
    that guard keeps its meaning only if adding the document tool does not
    quietly relax it. Composition here, no change to the validation rules there.

    Args:
        directory: The scope-aware HR reader.
        knowledge: The scope-aware document retriever.

    Returns:
        The validated seven-tool registry.

    Raises:
        ConfigurationError: If any spec is malformed.
    """
    structured = list(build_hr_registry(directory))
    document_tool = RegisteredTool(
        definition=ToolDefinition(
            name=RagSearchTool.name,
            description=(
                "Search company policy documents -- handbooks, procedures and "
                "written rules -- and return quoted passages with citations. Use "
                "this for questions about what the policy says, how a process "
                "works, or what the rules are. Do not use it for figures about a "
                "specific person or headcount: those come from the other tools. "
                "Cite every claim you take from a passage using its [n] marker."
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "What to search for, in natural language. Use the "
                            "user's own wording where you can."
                        ),
                        "maxLength": 1000,
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "How many passages to return.",
                        "minimum": 1,
                        "maximum": MAX_TOP_K,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        handler=RagSearchTool(knowledge).execute,
    )
    return ToolRegistry([*structured, document_tool])
