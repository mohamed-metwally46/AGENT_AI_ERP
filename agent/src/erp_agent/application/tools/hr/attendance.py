"""``get_attendance_summary``, the sixth MVP tool (Plan.md section 6.3)."""

from __future__ import annotations

from erp_agent.application.tools.hr.arguments import optional_int, required_employee_number
from erp_agent.domain.errors import ToolArgumentError
from erp_agent.domain.models.hr import AttendanceSummary
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.ports.hr_directory import HrDirectory

__all__ = ["GetAttendanceSummaryTool"]

_MIN_YEAR = 1990
_MAX_YEAR = 2100


class GetAttendanceSummaryTool:
    """Read pre-aggregated monthly attendance for one employee.

    Reads ``attendance_summaries``, never ``attendance_records`` (Plan.md
    section 6.4 rule 6). The summary table exists so a monthly question does not
    scan thousands of daily rows, and using the daily table here would also mean
    re-deriving totals that the ERP already computes -- two sources for one
    number is how two answers to one question appear.
    """

    name = "get_attendance_summary"

    def __init__(self, directory: HrDirectory) -> None:
        """Hold the directory port."""
        self._directory = directory

    async def execute(
        self,
        context: SecurityContext,
        *,
        employee_number: object,
        year: object,
        month: object = None,
    ) -> tuple[AttendanceSummary, ...]:
        """Read attendance summaries within the caller's scope.

        Args:
            context: The caller's resolved scope, applied as a predicate.
            employee_number: Whose attendance to read.
            year: Calendar year. Required, because "attendance" without a period
                is not a question with one answer.
            month: Calendar month 1-12, or omitted for the whole year.

        Returns:
            Matching summaries in calendar order.

        Raises:
            ToolArgumentError: If an argument is missing or out of range. Month
                13 is refused, never clamped to December.
        """
        resolved_year = optional_int(year, field="year", minimum=_MIN_YEAR, maximum=_MAX_YEAR)
        if resolved_year is None:
            raise ToolArgumentError("year is required", context={"argument": "year"})

        return await self._directory.attendance_summaries(
            context,
            employee_number=required_employee_number(employee_number),
            year=resolved_year,
            month=optional_int(month, field="month", minimum=1, maximum=12),
        )
