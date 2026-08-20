"""``get_leave_balance``, ``list_leave_requests`` and ``get_leave_policy``.

Three of the six MVP tools (Plan.md section 6.3). ``get_leave_policy`` is the
one that makes hybrid questions checkable: entitlement rules exist as structured
rows, so "does Ahmed have enough leave under company policy?" can be answered
from the database *and* cross-checked against the policy document, with the
answer saying so when the two disagree.
"""

from __future__ import annotations

from erp_agent.application.tools.hr.arguments import (
    optional_date,
    optional_int,
    optional_text,
    required_employee_number,
    required_text,
)
from erp_agent.domain.errors import ToolArgumentError
from erp_agent.domain.models.hr import LeaveBalance, LeavePolicy, LeaveRequestSummary
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.ports.hr_directory import HrDirectory

__all__ = ["GetLeaveBalanceTool", "GetLeavePolicyTool", "ListLeaveRequestsTool"]

#: Widest fiscal year a question may name. Refusing anything outside it turns a
#: nonsense argument into a clear error rather than an empty result that reads
#: as "no entitlement".
_MIN_FISCAL_YEAR = 1990
_MAX_FISCAL_YEAR = 2100

#: Row cap for a request listing, below the agent-wide ceiling of 100.
MAX_REQUESTS = 50


class GetLeaveBalanceTool:
    """Read one employee's leave balances."""

    name = "get_leave_balance"

    def __init__(self, directory: HrDirectory) -> None:
        """Hold the directory port."""
        self._directory = directory

    async def execute(
        self,
        context: SecurityContext,
        *,
        employee_number: object,
        leave_code: object = None,
        fiscal_year: object = None,
    ) -> tuple[LeaveBalance, ...]:
        """Read leave balances within the caller's scope.

        Args:
            context: The caller's resolved scope, applied as a predicate.
            employee_number: Whose balances to read.
            leave_code: Restrict to one leave type, e.g. ``ANN``.
            fiscal_year: Restrict to one year. Omitted means the latest year on
                file for that employee, not the current calendar year -- a
                question asked in January would otherwise return nothing.

        Returns:
            Matching balances, each stating its fiscal year. Empty when the
            employee is outside the caller's scope, which is indistinguishable
            from having no balances on file.

        Raises:
            ToolArgumentError: If an argument is missing or malformed.
        """
        return await self._directory.leave_balances(
            context,
            employee_number=required_employee_number(employee_number),
            leave_code=optional_text(leave_code, field="leave_code", max_length=16),
            fiscal_year=optional_int(
                fiscal_year,
                field="fiscal_year",
                minimum=_MIN_FISCAL_YEAR,
                maximum=_MAX_FISCAL_YEAR,
            ),
        )


class ListLeaveRequestsTool:
    """List one employee's leave requests."""

    name = "list_leave_requests"

    def __init__(self, directory: HrDirectory) -> None:
        """Hold the directory port."""
        self._directory = directory

    async def execute(
        self,
        context: SecurityContext,
        *,
        employee_number: object,
        status: object = None,
        start_from: object = None,
        start_to: object = None,
    ) -> tuple[LeaveRequestSummary, ...]:
        """List leave requests within the caller's scope.

        Args:
            context: The caller's resolved scope, applied as a predicate.
            employee_number: Whose requests to list.
            status: Restrict to one status.
            start_from: Earliest start date, inclusive.
            start_to: Latest start date, inclusive.

        Returns:
            Matching requests, most recent first.

        Raises:
            ToolArgumentError: If an argument is malformed, or the date range is
                inverted. An inverted range is refused rather than swapped: it
                means the question was misread, and silently correcting it hides
                that.
        """
        earliest = optional_date(start_from, field="start_from")
        latest = optional_date(start_to, field="start_to")

        if earliest is not None and latest is not None and earliest > latest:
            raise ToolArgumentError(
                "start_from must not be later than start_to",
                context={"argument": "start_from|start_to"},
            )

        return await self._directory.leave_requests(
            context,
            employee_number=required_employee_number(employee_number),
            status=optional_text(status, field="status", max_length=32),
            start_from=earliest,
            start_to=latest,
            limit=MAX_REQUESTS,
        )


class GetLeavePolicyTool:
    """Read the entitlement rules for a leave type.

    The only tool that takes no security context. An entitlement rule is company
    policy -- the same for everyone, and exactly what an employee is entitled to
    be told about their own leave. Scoping it would mean an employee could not
    read the rule that governs them.
    """

    name = "get_leave_policy"

    def __init__(self, directory: HrDirectory) -> None:
        """Hold the directory port."""
        self._directory = directory

    async def execute(
        self,
        *,
        leave_code: object,
        job_grade_code: object = None,
    ) -> tuple[LeavePolicy, ...]:
        """Read entitlement rules.

        Args:
            leave_code: Leave type code, e.g. ``ANN``.
            job_grade_code: Narrow to rules applying to one grade, plus the
                organisation-wide ones. Both are returned, organisation-wide
                first, so an answer can state which rule it applied instead of
                silently preferring one.

        Returns:
            Matching policies.

        Raises:
            ToolArgumentError: If ``leave_code`` is missing or malformed.
        """
        return await self._directory.leave_policies(
            leave_code=required_text(leave_code, field="leave_code", max_length=16),
            job_grade_code=optional_text(job_grade_code, field="job_grade_code", max_length=16),
        )
