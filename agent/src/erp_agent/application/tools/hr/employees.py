"""``get_employee`` and ``count_employees``.

Two of the six MVP tools (Plan.md section 6.3). Each is a thin orchestration:
validate arguments, call the directory port, shape a typed result. There is no
SQL here and no security decision here -- the scope predicate is applied inside
the directory, from the context, and cannot be reached by a tool argument.
"""

from __future__ import annotations

from erp_agent.application.tools.hr.arguments import optional_text, required_employee_number
from erp_agent.domain.errors import ToolArgumentError
from erp_agent.domain.models.hr import EmployeeCandidate, EmployeeLookup, Headcount
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.ports.hr_directory import HrDirectory

__all__ = ["CountEmployeesTool", "GetEmployeeTool"]

#: How many candidates a disambiguation list shows before it stops being useful.
MAX_CANDIDATES = 10


class GetEmployeeTool:
    """Look up one employee by business key or name.

    Name lookup is ambiguous by design in real data -- the mock set alone
    contains repeated names -- so a name matching several people returns a
    disambiguation list and never a guess. Guessing is how a name-based lookup
    crosses a permission boundary (Plan.md section 8.5, gap S5): the caller asked
    about *an* Ahmed, and picking one silently answers about a person they may
    not have meant and may not be entitled to ask about.
    """

    name = "get_employee"

    def __init__(self, directory: HrDirectory) -> None:
        """Hold the directory port."""
        self._directory = directory

    async def execute(
        self,
        context: SecurityContext,
        *,
        employee_number: object = None,
        full_name: object = None,
    ) -> EmployeeLookup:
        """Resolve an employee within the caller's scope.

        Args:
            context: The caller's resolved scope, applied as a predicate.
            employee_number: Exact business key.
            full_name: Name fragment. Ignored when ``employee_number`` is given,
                because an exact key is never improved by a fuzzy match.

        Returns:
            A resolved employee, an ambiguity, or neither. "Not found" and
            "outside your scope" are deliberately the same answer: confirming
            that an employee exists is itself a disclosure.

        Raises:
            ToolArgumentError: If neither identifier is supplied, or one is
                malformed.
        """
        number = None if employee_number is None else required_employee_number(employee_number)
        name = optional_text(full_name, field="full_name")

        if number is None and name is None:
            raise ToolArgumentError(
                "get_employee needs either employee_number or full_name",
                context={"argument": "employee_number|full_name"},
            )

        matches = await self._directory.find_employees(
            context,
            employee_number=number,
            full_name=None if number is not None else name,
            limit=MAX_CANDIDATES + 1,
        )

        if not matches:
            return EmployeeLookup()
        if len(matches) == 1:
            return EmployeeLookup(employee=matches[0])

        return EmployeeLookup(
            candidates=tuple(
                EmployeeCandidate(
                    employee_number=match.employee_number,
                    full_name=match.full_name,
                    department=match.department,
                )
                for match in matches[:MAX_CANDIDATES]
            )
        )


class CountEmployeesTool:
    """Count employees matching a filter, within the caller's scope.

    Reports the total *and* the active subtotal rather than choosing between
    them. "How many employees are in Sales?" has two defensible answers -- 12 on
    the books, 11 currently active -- and a tool that silently picks one produces
    a number nobody can reproduce from the database.
    """

    name = "count_employees"

    def __init__(self, directory: HrDirectory) -> None:
        """Hold the directory port."""
        self._directory = directory

    async def execute(
        self,
        context: SecurityContext,
        *,
        department: object = None,
        branch: object = None,
        employment_status: object = None,
    ) -> Headcount:
        """Count employees within the caller's scope.

        Args:
            context: The caller's resolved scope, applied as a predicate. A
                ``SELF``-scoped caller counting a department they are not in
                gets zero -- refused by the filter, not by the model's
                discretion.
            department: Department name to filter on.
            branch: Branch name to filter on.
            employment_status: Status to filter on.

        Returns:
            The total and active counts, with the filters echoed back so an
            answer can state what it counted.

        Raises:
            ToolArgumentError: If a supplied filter is not usable text.
        """
        return await self._directory.count_employees(
            context,
            department=optional_text(department, field="department"),
            branch=optional_text(branch, field="branch"),
            employment_status=optional_text(employment_status, field="employment_status"),
        )
