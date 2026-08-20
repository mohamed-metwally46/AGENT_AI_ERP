"""Port: named, scope-aware reads of HR data.

One method per MVP tool (Plan.md section 6.3). The shape is deliberate:

*Every method that reads about people takes a ``SecurityContext``, and it is not
optional.* A signature that allows the scope to be omitted is a signature that
will one day be called without it. ``leave_policies`` is the single exception,
and it is the exception because an entitlement rule is company policy, not
anyone's personal data.

*There is no generic method.* No ``query``, no ``find``, no ``execute``. Six
named capabilities, which is what makes the tool registry a static allowlist
rather than a suggestion (Plan.md section 8.2, barrier 4). Adding a seventh is a
visible change to this file.

*Arguments are values, never SQL.* The implementation owns the statement; the
caller owns only the parameters. The model supplies values to a tool, the tool
supplies values to this port, and at no point does anything above
:mod:`erp_agent.infrastructure` compose a query.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from erp_agent.domain.models.hr import (
    AttendanceSummary,
    EmployeeSummary,
    Headcount,
    LeaveBalance,
    LeavePolicy,
    LeaveRequestSummary,
)
from erp_agent.domain.models.security_context import SecurityContext

__all__ = ["HrDirectory"]


class HrDirectory(ABC):
    """Reads HR records within a principal's scope.

    Implementations must apply the scope predicate to every method that takes a
    context, must select an explicit column list, and must cap returned rows.
    """

    @abstractmethod
    async def find_employees(
        self,
        context: SecurityContext,
        *,
        employee_number: str | None = None,
        full_name: str | None = None,
        limit: int = 10,
    ) -> tuple[EmployeeSummary, ...]:
        """Find employees by business key or by name.

        Args:
            context: The principal's resolved scope. Applied as a predicate;
                cannot be widened by the other arguments.
            employee_number: Exact business key.
            full_name: Name fragment, matched case-insensitively. Ambiguous by
                design in real data, so this returns every match and lets the
                caller decide -- it never picks one.
            limit: Row cap.

        Returns:
            Matching employees the principal may see, possibly empty. A lookup
            that matched a real employee outside the principal's scope is
            indistinguishable from one that matched nobody, which is the
            intended behaviour: confirming existence is itself a disclosure.

        Raises:
            DataSourceError: On a read failure.
        """

    @abstractmethod
    async def count_employees(
        self,
        context: SecurityContext,
        *,
        department: str | None = None,
        branch: str | None = None,
        employment_status: str | None = None,
    ) -> Headcount:
        """Count employees matching a filter, within scope.

        Args:
            context: The principal's resolved scope.
            department: Department name, matched case-insensitively.
            branch: Branch name, matched case-insensitively.
            employment_status: Status to filter on.

        Returns:
            The total and the active subtotal.

        Raises:
            DataSourceError: On a read failure.
        """

    @abstractmethod
    async def leave_balances(
        self,
        context: SecurityContext,
        *,
        employee_number: str,
        leave_code: str | None = None,
        fiscal_year: int | None = None,
    ) -> tuple[LeaveBalance, ...]:
        """Read leave balances for one employee.

        Args:
            context: The principal's resolved scope.
            employee_number: Whose balances to read.
            leave_code: Restrict to one leave type.
            fiscal_year: Restrict to one year. Defaults to the most recent year
                on file for that employee, rather than to the current calendar
                year -- a mock database, or a late-January question, would
                otherwise return nothing and read as "no entitlement".

        Returns:
            Matching balances, possibly empty.

        Raises:
            DataSourceError: On a read failure.
        """

    @abstractmethod
    async def leave_requests(
        self,
        context: SecurityContext,
        *,
        employee_number: str,
        status: str | None = None,
        start_from: date | None = None,
        start_to: date | None = None,
        limit: int = 50,
    ) -> tuple[LeaveRequestSummary, ...]:
        """List leave requests for one employee.

        Args:
            context: The principal's resolved scope.
            employee_number: Whose requests to list.
            status: Restrict to one status.
            start_from: Earliest start date, inclusive.
            start_to: Latest start date, inclusive.
            limit: Row cap.

        Returns:
            Matching requests, most recent first.

        Raises:
            DataSourceError: On a read failure.
        """

    @abstractmethod
    async def leave_policies(
        self,
        *,
        leave_code: str,
        job_grade_code: str | None = None,
    ) -> tuple[LeavePolicy, ...]:
        """Read the entitlement rules for a leave type.

        The one method without a ``SecurityContext``: an entitlement rule is
        company policy, the same for everyone, and is exactly what an employee
        is entitled to be told. Scoping it would mean an employee could not read
        the rule that governs their own leave.

        Args:
            leave_code: Leave type code.
            job_grade_code: Narrow to policies applying to one grade, plus the
                organisation-wide ones.

        Returns:
            Matching policies, organisation-wide first.

        Raises:
            DataSourceError: On a read failure.
        """

    @abstractmethod
    async def attendance_summaries(
        self,
        context: SecurityContext,
        *,
        employee_number: str,
        year: int,
        month: int | None = None,
    ) -> tuple[AttendanceSummary, ...]:
        """Read pre-aggregated monthly attendance.

        Args:
            context: The principal's resolved scope.
            employee_number: Whose attendance to read.
            year: Calendar year.
            month: Calendar month, or ``None`` for the whole year.

        Returns:
            Matching summaries, in calendar order.

        Raises:
            DataSourceError: On a read failure.
        """
