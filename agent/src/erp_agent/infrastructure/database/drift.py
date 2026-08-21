"""Comparing what the agent requires against what a target database actually has.

The check that makes "point it at the real ERP" a decision somebody can take on
evidence. Plan.md section 13's migration path is "a DSN and a schema-drift
check"; this is the second half.

**It reads the catalogue, not the data.** Every query here is against
``information_schema``, through the same read-only connection as everything
else, so running a drift check against production is exactly as safe as running
the agent against it -- which is the point of the barrier being a property of
the connection rather than of the caller's good intentions.

**A missing column is fatal; an extra one is not.** The agent selects explicit
column lists, so a table that grew a column it does not know about cannot leak
through it -- that is precisely why ``SELECT *`` is banned. A table that *lost*
a column, or changed its type under one, breaks a tool. The report separates
them because treating both as errors would make every ERP upgrade look like an
outage and train people to skip the check.

**Type comparison is deliberately coarse.** ``varchar`` becoming ``text``, or
``int4`` becoming ``int8``, breaks nothing the agent does. Reporting those would
fill the output with noise, and a report that is mostly noise is a report nobody
reads at 3am. Only a change of *family* -- a date that became text, a numeric
that became a string -- is reported, because that is the change that makes a
tool return something wrong rather than something absent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from erp_agent.domain.models.sql import SqlStatement
from erp_agent.domain.ports.data_source import ReadOnlyDataSource
from erp_agent.infrastructure.database.schema_contract import (
    REQUIRED_TABLES,
    SCHEMA,
    RequiredTable,
)

__all__ = ["ColumnDrift", "DriftReport", "TableDrift", "check_drift", "render_drift"]

#: Type families the agent actually depends on. Two types in the same family are
#: interchangeable as far as every tool is concerned; two in different families
#: are not.
_FAMILIES: dict[str, str] = {
    "character varying": "text",
    "character": "text",
    "text": "text",
    "uuid": "text",
    "smallint": "number",
    "integer": "number",
    "bigint": "number",
    "numeric": "number",
    "real": "number",
    "double precision": "number",
    "boolean": "boolean",
    "date": "time",
    "timestamp without time zone": "time",
    "timestamp with time zone": "time",
    "time without time zone": "time",
    "jsonb": "json",
    "json": "json",
}

#: What the contract expects, by column name, where the agent depends on the
#: family rather than merely on the column existing. Everything not named here
#: is checked for presence only.
_EXPECTED_FAMILY: dict[str, str] = {
    "employee_id": "text",
    "employee_number": "text",
    "hire_date": "time",
    "date_of_birth": "time",
    "start_date": "time",
    "end_date": "time",
    "reviewed_at": "time",
    "fiscal_year": "number",
    "year": "number",
    "month": "number",
    "working_days": "number",
    "opening_balance": "number",
    "accrued_ytd": "number",
    "used_ytd": "number",
    "pending_ytd": "number",
    "annual_entitlement_days": "number",
    "max_carry_forward_days": "number",
    "min_service_months": "number",
    "max_consecutive_days": "number",
    "notice_days": "number",
    "is_active": "boolean",
    "is_half_day": "boolean",
    "allow_negative_balance": "boolean",
}


@dataclass(frozen=True, slots=True)
class ColumnDrift:
    """One column that is missing or has changed shape.

    Attributes:
        table: Where.
        column: Which.
        problem: ``missing`` or ``type``.
        detail: What was found instead.
    """

    table: str
    column: str
    problem: str
    detail: str = ""

    def __str__(self) -> str:
        """Render as one line of a report."""
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.table}.{self.column}: {self.problem}{suffix}"


@dataclass(frozen=True, slots=True)
class TableDrift:
    """One table the agent requires and the target does not have."""

    table: str
    why: str

    def __str__(self) -> str:
        """Render as one line of a report."""
        return f"{self.table}: missing -- breaks {self.why}"


@dataclass(frozen=True, slots=True)
class DriftReport:
    """What a target database has, against what the agent needs.

    Attributes:
        database: Which database was inspected.
        missing_tables: Tables the agent requires that are absent.
        missing_columns: Columns the agent selects that are absent.
        changed_types: Columns whose type family changed under the agent.
        extra_columns: Columns the target has that the contract does not name.
            Informational: explicit column lists mean the agent cannot read one
            by accident.
        unreadable_tables: Required tables the connected role cannot see. A
            different problem from absence, and a much more likely one on a
            fresh replica -- the table is there and the grant is not.
    """

    database: str
    missing_tables: tuple[TableDrift, ...] = ()
    missing_columns: tuple[ColumnDrift, ...] = ()
    changed_types: tuple[ColumnDrift, ...] = ()
    extra_columns: tuple[str, ...] = ()
    unreadable_tables: tuple[str, ...] = field(default_factory=tuple)

    @property
    def compatible(self) -> bool:
        """Whether the agent can run against this target unmodified."""
        return not (
            self.missing_tables
            or self.missing_columns
            or self.changed_types
            or self.unreadable_tables
        )


async def check_drift(source: ReadOnlyDataSource, database: str = "") -> DriftReport:
    """Compare a target database against the agent's schema contract.

    Args:
        source: A read-only connection to the target.
        database: What to call it in the report.

    Returns:
        The differences. An empty report means the agent can be pointed here by
        changing a DSN and nothing else, which is Plan.md section 11's Phase 9
        acceptance criterion 1.

    Raises:
        DataSourceError: If the catalogue itself could not be read.
    """
    catalogue = await _catalogue(source)

    missing_tables: list[TableDrift] = []
    missing_columns: list[ColumnDrift] = []
    changed_types: list[ColumnDrift] = []
    extra: list[str] = []

    unreadable = await _unreadable(source, catalogue)

    for required in REQUIRED_TABLES:
        present = catalogue.get(required.name)
        if present is None:
            # Absent from the catalogue *and* absent from pg_catalog is missing;
            # absent from one and present in the other is a grant problem. Made
            # exclusive here so a table can never be reported as both, which is
            # the self-contradiction that exposed the row-cap bug above.
            if required.name not in unreadable:
                missing_tables.append(TableDrift(table=required.name, why=required.why))
            continue

        missing_columns.extend(_missing(required, present))
        changed_types.extend(_retyped(required, present))
        extra.extend(
            f"{required.name}.{column}" for column in sorted(set(present) - required.columns)
        )

    return DriftReport(
        database=database,
        missing_tables=tuple(missing_tables),
        missing_columns=tuple(missing_columns),
        changed_types=tuple(changed_types),
        extra_columns=tuple(extra),
        unreadable_tables=unreadable,
    )


def _missing(required: RequiredTable, present: dict[str, str]) -> list[ColumnDrift]:
    """Columns the agent selects that the target does not have."""
    return [
        ColumnDrift(table=required.name, column=column, problem="missing")
        for column in sorted(required.columns - set(present))
    ]


def _retyped(required: RequiredTable, present: dict[str, str]) -> list[ColumnDrift]:
    """Columns whose type family changed under the agent."""
    found: list[ColumnDrift] = []
    for column in sorted(required.columns & set(present)):
        expected = _EXPECTED_FAMILY.get(column)
        if expected is None:
            continue
        actual = _FAMILIES.get(present[column], present[column])
        if actual != expected:
            found.append(
                ColumnDrift(
                    table=required.name,
                    column=column,
                    problem="type",
                    detail=f"expected a {expected}, found {present[column]}",
                )
            )
    return found


async def _catalogue(source: ReadOnlyDataSource) -> dict[str, dict[str, str]]:
    """Every required table's columns and types, as the target reports them.

    Aggregated to one row per table rather than one per column, and that is not
    a style choice. The data source clamps every statement to the configured row
    cap -- a safety property, and the right one -- so a query returning a row per
    column silently returned the first hundred and the check reported six tables
    as missing that were plainly there. Asking a question whose answer is small
    is the fix; asking for the cap to be lifted would have been the bug.

    Reads ``information_schema.columns``, which is privilege-filtered: a table
    the connected role cannot select from does not appear. That is a feature
    here -- it makes "the grant is missing" and "the table is missing" different
    findings -- and it is why :func:`_unreadable` exists to tell them apart.
    """
    rows = await source.fetch_all(
        SqlStatement(
            """
            SELECT table_name,
                   string_agg(column_name || ' ' || data_type, ',' ORDER BY column_name)
                       AS columns
              FROM information_schema.columns
             WHERE table_schema = %(schema)s
               AND table_name = ANY(%(tables)s::text[])
             GROUP BY table_name
             ORDER BY table_name
            """,
            {
                "schema": SCHEMA,
                "tables": [table.name for table in REQUIRED_TABLES],
            },
            max_rows=len(REQUIRED_TABLES),
        )
    )

    catalogue: dict[str, dict[str, str]] = {}
    for row in rows.rows:
        columns: dict[str, str] = {}
        for entry in str(row["columns"]).split(","):
            name, _, data_type = entry.partition(" ")
            columns[name] = data_type
        catalogue[str(row["table_name"])] = columns
    return catalogue


async def _unreadable(
    source: ReadOnlyDataSource, catalogue: dict[str, dict[str, str]]
) -> tuple[str, ...]:
    """Required tables that exist but this role cannot read.

    The most likely failure on a fresh replica: the schema is right and the
    ``GRANT SELECT`` was never run. Reported separately because the fix is
    completely different from a schema change, and because a report that called
    it "missing" would send somebody to the wrong team.
    """
    absent = [table.name for table in REQUIRED_TABLES if table.name not in catalogue]
    if not absent:
        return ()

    rows = await source.fetch_all(
        SqlStatement(
            """
            SELECT c.relname AS table_name
              FROM pg_catalog.pg_class c
              JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %(schema)s
               AND c.relkind IN ('r', 'v', 'm', 'p')
               AND c.relname = ANY(%(tables)s::text[])
             ORDER BY c.relname
            """,
            {"schema": SCHEMA, "tables": absent},
            max_rows=1_000,
        )
    )
    return tuple(str(row["table_name"]) for row in rows.rows)


def render_drift(report: DriftReport) -> str:
    """Render a drift report for a person.

    Args:
        report: What the check found.

    Returns:
        The findings, worst first, and a verdict.
    """
    lines = [
        "ERP AI Agent -- schema drift",
        "",
        f"  target             {report.database or 'the configured database'}",
        f"  required tables    {len(REQUIRED_TABLES)}",
        "",
    ]

    def section(title: str, entries: tuple[Any, ...]) -> None:
        if not entries:
            return
        lines.append(f"  {title}")
        lines.extend(f"    {entry}" for entry in entries)
        lines.append("")

    section("MISSING TABLES", report.missing_tables)
    section("PRESENT BUT NOT READABLE BY THIS ROLE", report.unreadable_tables)
    section("MISSING COLUMNS", report.missing_columns)
    section("CHANGED TYPES", report.changed_types)

    if report.extra_columns:
        lines.append(
            f"  {len(report.extra_columns)} column(s) the contract does not name -- "
            f"not a problem: the agent selects explicit column lists"
        )
        lines.append("")

    lines.append(
        f"  verdict            {'COMPATIBLE' if report.compatible else '** INCOMPATIBLE **'}"
    )
    lines.append("")
    return "\n".join(lines)
