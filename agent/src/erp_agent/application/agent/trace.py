"""A typed record of what the agent did, for grounding, audit and evaluation.

Phase 5's hybrid answers and Phase 8's evaluation harness both read this, so it
is a record type rather than a free-form dict: a metric computed from a dict
whose keys drifted is a metric nobody notices is wrong.

Sanitisation is by allowlist (Plan.md section 20). Argument *names* are always
recorded -- knowing which tool ran with which parameters is the point of an audit
trail -- but values are kept only for arguments that cannot identify a person.
``full_name`` is redacted; ``employee_number`` is not, because "user X looked up
EMP-0042" is exactly what an access log should say. Row *counts* are recorded;
rows never are.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = ["REDACTED", "SAFE_ARGUMENT_VALUES", "AgentTrace", "ToolInvocation", "ToolOutcome"]

#: What a withheld argument value renders as.
REDACTED = "[redacted]"

#: Arguments whose values may be recorded. Everything else keeps its name and
#: loses its value. An allowlist rather than a denylist, so a new argument is
#: withheld by default and someone has to decide it is safe.
SAFE_ARGUMENT_VALUES: frozenset[str] = frozenset(
    {
        "employee_number",
        "department",
        "branch",
        "employment_status",
        "leave_code",
        "job_grade_code",
        "fiscal_year",
        "year",
        "month",
        "status",
        "start_from",
        "start_to",
    }
)


class ToolOutcome(StrEnum):
    """How one tool call ended."""

    OK = "ok"
    #: Refused before dispatch: reserved argument, unknown tool, bad arguments.
    #: The handler was never reached.
    REFUSED = "refused"
    #: Dispatched and failed -- a database error, a timeout.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    """One tool call, as it will be audited.

    Attributes:
        tool_name: Which tool the model asked for.
        arguments: Argument names, with values kept only where safe.
        outcome: How it ended.
        dispatched: Whether the handler actually ran. ``False`` for anything
            refused by the registry or the argument guard -- the distinction
            that answers "did this injection attempt reach the database?".
        duration_ms: Wall-clock time.
        row_count: How many rows came back, where the result is a collection.
        error_code: The domain error code when it failed, never the message,
            which can quote user input.
        round_index: Which loop round it happened in.
    """

    tool_name: str
    outcome: ToolOutcome
    dispatched: bool = False
    arguments: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    row_count: int | None = None
    error_code: str | None = None
    round_index: int = 0

    def __post_init__(self) -> None:
        """Validate the record and sanitise the arguments.

        Raises:
            ValueError: If the invocation is internally inconsistent -- a
                refused call that nonetheless dispatched, for instance, which
                would make the audit trail lie about what reached the database.
        """
        if not self.tool_name.strip():
            raise ValueError("a tool invocation must name its tool")
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")
        if self.outcome is ToolOutcome.REFUSED and self.dispatched:
            raise ValueError("a refused call must not report having been dispatched")
        if self.outcome is ToolOutcome.OK and not self.dispatched:
            raise ValueError("a successful call must have been dispatched")

        object.__setattr__(self, "arguments", sanitise_arguments(self.arguments))


def sanitise_arguments(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
    """Keep every argument name; keep only safe values.

    Args:
        arguments: The raw arguments a tool was called with.

    Returns:
        A mapping with unsafe values replaced by :data:`REDACTED`.
    """
    return {
        name: (value if name in SAFE_ARGUMENT_VALUES else REDACTED)
        for name, value in sorted(arguments.items())
    }


@dataclass(frozen=True, slots=True)
class AgentTrace:
    """Everything the agent did while answering one question.

    Attributes:
        invocations: Tool calls, in the order they happened.
        rounds: How many model round-trips were used.
        completed: Whether the loop reached a final answer. ``False`` means a
            bound was hit -- recorded rather than hidden, because a partial
            answer presented as a complete one is worse than an error.
    """

    invocations: tuple[ToolInvocation, ...] = ()
    rounds: int = 0
    completed: bool = False

    @property
    def tools_used(self) -> tuple[str, ...]:
        """Names of the tools that actually ran, in order, without repeats."""
        seen: list[str] = []
        for invocation in self.invocations:
            if invocation.dispatched and invocation.tool_name not in seen:
                seen.append(invocation.tool_name)
        return tuple(seen)

    @property
    def refusals(self) -> tuple[ToolInvocation, ...]:
        """Calls that were blocked before reaching a handler."""
        return tuple(i for i in self.invocations if i.outcome is ToolOutcome.REFUSED)

    @property
    def reached_the_database(self) -> bool:
        """Whether any tool handler ran at all.

        The question a security test asks after an injection attempt.
        """
        return any(invocation.dispatched for invocation in self.invocations)

    def with_invocation(self, invocation: ToolInvocation) -> AgentTrace:
        """Return a new trace with one more invocation appended."""
        return AgentTrace(
            invocations=(*self.invocations, invocation),
            rounds=self.rounds,
            completed=self.completed,
        )

    def finished(self, *, rounds: int, completed: bool) -> AgentTrace:
        """Return a new trace marked with how the loop ended."""
        return AgentTrace(invocations=self.invocations, rounds=rounds, completed=completed)

    def as_log_payload(self) -> dict[str, Any]:
        """A structured, already-sanitised rendering for logs."""
        return {
            "rounds": self.rounds,
            "completed": self.completed,
            "tool_calls": len(self.invocations),
            "tools_used": list(self.tools_used),
            "refused": [i.tool_name for i in self.refusals],
            "invocations": [
                {
                    "tool": i.tool_name,
                    "outcome": i.outcome.value,
                    "dispatched": i.dispatched,
                    "arguments": dict(i.arguments),
                    "duration_ms": round(i.duration_ms, 1),
                    "row_count": i.row_count,
                    "error_code": i.error_code,
                    "round": i.round_index,
                }
                for i in self.invocations
            ],
        }
