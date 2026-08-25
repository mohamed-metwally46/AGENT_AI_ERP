"""Where employees sit, by their stable business key.

The adapter behind :class:`~erp_agent.domain.ports.org_placement.OrgPlacementPort`.
It is the smallest query in this package and deliberately so: four identifier
columns, one parameter, no joins beyond the row itself.

Two things it does **not** do, both on purpose.

*It does not take a :class:`SecurityContext`.* It cannot -- it runs while one is
being built. Every other query in this package is scoped; this one is the base
case of the recursion. What keeps that safe is not a filter but the column list:
it returns identifiers and nothing else, so there is no data here to scope. See
the port's docstring for the full argument.

*It does not accept a caller's input.* The mock identity resolver calls it once,
at start-up, with the fixed set of employee numbers its fixture names. Nothing
reaches it from a request. The numbers are still bound as parameters rather than
formatted in, because a query that is safe only because of who calls it is a
query that becomes unsafe when somebody else calls it.
"""

from __future__ import annotations

from collections.abc import Mapping

from erp_agent.domain.models.sql import Row, SqlStatement
from erp_agent.domain.ports.data_source import ReadOnlyDataSource
from erp_agent.domain.ports.org_placement import OrgPlacement, OrgPlacementPort

__all__ = ["PostgresOrgPlacement"]

#: Identifiers only. No name, no contact detail, nothing conditional -- so this
#: statement needs no field policy and cannot acquire one by accident.
_COLUMNS = "employee_number, employee_id, dept_id, branch_id"

#: A generous ceiling on a roster. Present because every statement in this
#: package is bounded, not because a realistic fixture approaches it.
_MAX_ROSTER = 500


class PostgresOrgPlacement(OrgPlacementPort):
    """Reads org placement from the HR database."""

    def __init__(self, source: ReadOnlyDataSource) -> None:
        """Hold the read-only connection.

        Args:
            source: Typed as the port, so this class has no opinion about
                pooling, transactions or dialect beyond the SQL it writes.
        """
        self._source = source

    async def placements(self, employee_numbers: frozenset[str]) -> Mapping[str, OrgPlacement]:
        """Resolve employee numbers to their placement.

        Args:
            employee_numbers: The numbers to look up. An empty set is answered
                without touching the database.

        Returns:
            A mapping keyed by employee number. Numbers matching no employee are
            absent from it.

        Raises:
            DataSourceUnavailableError: If the database is unreachable.
            QueryTimeoutError: If the read exceeds the statement timeout.
        """
        wanted = tuple(sorted(number.strip() for number in employee_numbers if number.strip()))
        if not wanted:
            return {}

        statement = SqlStatement(
            text=f"""
            SELECT {_COLUMNS}
              FROM employees
             WHERE employee_number = ANY(%(employee_numbers)s)
             ORDER BY employee_number
            """,
            parameters={"employee_numbers": list(wanted)},
            max_rows=_MAX_ROSTER,
        )

        result = await self._source.fetch_all(statement)
        return {
            placement.employee_number: placement
            for placement in (_to_placement(row) for row in result.rows)
        }


def _to_placement(row: Row) -> OrgPlacement:
    """Convert one row, preserving nullable columns as ``None``."""
    return OrgPlacement(
        employee_number=str(row["employee_number"]),
        employee_id=str(row["employee_id"]),
        dept_id=None if row["dept_id"] is None else str(row["dept_id"]),
        branch_id=None if row["branch_id"] is None else str(row["branch_id"]),
    )
