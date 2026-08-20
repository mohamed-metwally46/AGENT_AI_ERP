"""Value objects for parameterised, read-only queries.

The important property of :class:`SqlStatement` is that an unsafe one cannot be
constructed. The read-only policy runs in ``__post_init__``, so there is no
window in which a write statement exists as a valid object waiting for someone
downstream to remember to check it. "Validate at the boundary" fails whenever a
second boundary is added later; "invalid states are unrepresentable" does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from erp_agent.domain.policies.read_only_sql import assert_read_only

__all__ = ["DEFAULT_MAX_ROWS", "QueryResult", "Row", "SqlStatement"]

#: Row cap applied when a statement does not name its own (Plan.md section 5.3,
#: MAX_ROWS_PER_TOOL_RESULT). A query that would return more is truncated and
#: says so, rather than quietly filling a context window.
DEFAULT_MAX_ROWS = 100

type Row = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class SqlStatement:
    """A single read-only statement and its named parameters.

    Attributes:
        text: The statement. Placeholders only -- values are never formatted
            into this string. Validated against the read-only policy on
            construction.
        parameters: Values for the named placeholders, defensively frozen so a
            caller cannot mutate them between validation and execution.
        max_rows: Ceiling on returned rows for this statement.
    """

    text: str
    parameters: Mapping[str, Any] = field(default_factory=dict)
    max_rows: int = DEFAULT_MAX_ROWS

    def __post_init__(self) -> None:
        """Validate the statement is a bounded, parameterised read.

        Raises:
            ValueError: If the text is blank or the row cap is not positive.
            ReadOnlyViolationError: If the statement is not a single, purely
                reading statement.
        """
        if not self.text.strip():
            raise ValueError("statement text must be a non-empty string")
        if self.max_rows < 1:
            raise ValueError(f"max_rows must be >= 1, got {self.max_rows}")

        assert_read_only(self.text)

        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def parameter_names(self) -> tuple[str, ...]:
        """The supplied parameter names, sorted. Safe to log; the values are not."""
        return tuple(sorted(self.parameters))


@dataclass(frozen=True, slots=True)
class QueryResult:
    """Rows returned by one statement.

    Attributes:
        rows: The returned rows, each a column-name to value mapping.
        truncated: Whether the row cap discarded rows the query would otherwise
            have returned. Surfaced rather than hidden: an answer computed from
            a silently truncated set is a wrong answer delivered confidently.
        duration_ms: Server round-trip time, for the tool trace.
    """

    rows: tuple[Row, ...]
    truncated: bool = False
    duration_ms: float = 0.0

    def __post_init__(self) -> None:
        """Validate the result is internally consistent."""
        if self.duration_ms < 0:
            raise ValueError("duration_ms must be >= 0")

    @property
    def row_count(self) -> int:
        """How many rows are present."""
        return len(self.rows)

    @property
    def is_empty(self) -> bool:
        """Whether the query matched nothing.

        Distinct from an error: "no rows" is a legitimate, citable answer.
        """
        return not self.rows
