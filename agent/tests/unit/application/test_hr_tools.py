"""Tool orchestration, without a database.

The tools own three things: argument validation, disambiguation, and passing a
context through untouched. All three are testable against a fake directory, and
none of them should need Postgres running to check.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from erp_agent.application.tools.hr import (
    MVP_TOOL_NAMES,
    CountEmployeesTool,
    GetAttendanceSummaryTool,
    GetEmployeeTool,
    GetLeaveBalanceTool,
    GetLeavePolicyTool,
    ListLeaveRequestsTool,
)
from erp_agent.domain.errors import ToolArgumentError
from erp_agent.domain.models.hr import (
    AttendanceSummary,
    EmployeeSummary,
    Headcount,
    LeaveBalance,
    LeavePolicy,
    LeaveRequestSummary,
)
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.ports.hr_directory import HrDirectory

pytestmark = pytest.mark.unit

CONTEXT = SecurityContext(
    user_id="1", employee_id="00000000-0000-0000-0000-000000000001", scope=DataScope.SELF
)


def _employee(
    number: str, name: str = "Ahmed Hassan", department: str = "Sales"
) -> EmployeeSummary:
    return EmployeeSummary(
        employee_number=number,
        full_name=name,
        employment_status="active",
        employment_type="full_time",
        hire_date=date(2020, 1, 1),
        department=department,
    )


class FakeDirectory(HrDirectory):
    """Records what it was asked, returns what a test told it to."""

    def __init__(
        self,
        employees: tuple[EmployeeSummary, ...] = (),
        *,
        notice_days: int | None = None,
    ) -> None:
        self.employees = employees
        # Phase 5's conflict check compares a policy figure against document
        # prose, so a test needs to be able to set one that disagrees.
        self.notice_days = notice_days
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.contexts: list[SecurityContext] = []

    async def find_employees(self, context, *, employee_number=None, full_name=None, limit=10):
        self.contexts.append(context)
        self.calls.append(
            ("find_employees", {"employee_number": employee_number, "full_name": full_name})
        )
        return self.employees[:limit]

    async def count_employees(
        self, context, *, department=None, branch=None, employment_status=None
    ):
        self.contexts.append(context)
        self.calls.append(
            (
                "count_employees",
                {
                    "department": department,
                    "branch": branch,
                    "employment_status": employment_status,
                },
            )
        )
        return Headcount(total=12, active=11, department=department)

    async def leave_balances(self, context, *, employee_number, leave_code=None, fiscal_year=None):
        self.contexts.append(context)
        self.calls.append(
            (
                "leave_balances",
                {
                    "employee_number": employee_number,
                    "leave_code": leave_code,
                    "fiscal_year": fiscal_year,
                },
            )
        )
        return (
            LeaveBalance(
                employee_number=employee_number,
                leave_code="ANN",
                leave_name="Annual",
                fiscal_year=2026,
                opening_balance=Decimal(5),
                accrued_ytd=Decimal(10),
                used_ytd=Decimal(3),
                pending_ytd=Decimal(2),
            ),
        )

    async def leave_requests(
        self, context, *, employee_number, status=None, start_from=None, start_to=None, limit=50
    ):
        self.contexts.append(context)
        self.calls.append(
            (
                "leave_requests",
                {
                    "employee_number": employee_number,
                    "status": status,
                    "start_from": start_from,
                    "start_to": start_to,
                },
            )
        )
        return (
            LeaveRequestSummary(
                employee_number=employee_number,
                leave_code="ANN",
                leave_name="Annual",
                start_date=date(2026, 3, 1),
                end_date=date(2026, 3, 5),
                working_days=Decimal(5),
                status="approved",
            ),
        )

    async def leave_policies(self, *, leave_code, job_grade_code=None):
        self.calls.append(
            ("leave_policies", {"leave_code": leave_code, "job_grade_code": job_grade_code})
        )
        return (
            LeavePolicy(
                leave_code=leave_code,
                leave_name="Annual",
                scope="all",
                annual_entitlement_days=Decimal(21),
                notice_days=self.notice_days,
            ),
        )

    async def attendance_summaries(self, context, *, employee_number, year, month=None):
        self.contexts.append(context)
        self.calls.append(
            (
                "attendance_summaries",
                {"employee_number": employee_number, "year": year, "month": month},
            )
        )
        return (
            AttendanceSummary(
                employee_number=employee_number,
                year=year,
                month=month or 1,
                working_days_in_month=22,
                days_present=20,
                days_absent=1,
                days_on_leave=1,
            ),
        )


class TestTheAllowlistIsStatic:
    def test_there_are_exactly_six_tools(self):
        # Plan.md section 6.3. A seventh must not appear without this failing.
        assert len(MVP_TOOL_NAMES) == 6

    def test_the_names_are_the_ones_the_plan_defines(self):
        assert {
            "get_employee",
            "count_employees",
            "get_leave_balance",
            "list_leave_requests",
            "get_leave_policy",
            "get_attendance_summary",
        } == MVP_TOOL_NAMES

    def test_no_tool_is_named_like_a_generic_sql_escape_hatch(self):
        assert not any(
            token in name for name in MVP_TOOL_NAMES for token in ("sql", "query", "execute", "raw")
        )


class TestGetEmployee:
    async def test_a_single_match_resolves(self):
        directory = FakeDirectory((_employee("EMP-0001"),))

        result = await GetEmployeeTool(directory).execute(CONTEXT, employee_number="EMP-0001")

        assert result.is_found
        assert not result.is_ambiguous

    async def test_several_matches_disambiguate_rather_than_guess(self):
        directory = FakeDirectory(
            (_employee("EMP-0001"), _employee("EMP-0002", department="Finance"))
        )

        result = await GetEmployeeTool(directory).execute(CONTEXT, full_name="Ahmed Hassan")

        assert result.is_ambiguous
        assert result.employee is None
        assert {c.employee_number for c in result.candidates} == {"EMP-0001", "EMP-0002"}

    async def test_candidates_carry_only_what_distinguishes_them(self):
        directory = FakeDirectory((_employee("EMP-0001"), _employee("EMP-0002")))

        result = await GetEmployeeTool(directory).execute(CONTEXT, full_name="Ahmed")
        candidate = result.candidates[0]

        assert set(vars(type(candidate))["__slots__"]) == {
            "employee_number",
            "full_name",
            "department",
        }

    async def test_no_match_is_neither_found_nor_ambiguous(self):
        result = await GetEmployeeTool(FakeDirectory()).execute(CONTEXT, employee_number="EMP-0001")

        assert not result.is_found
        assert not result.is_ambiguous

    async def test_an_exact_key_suppresses_the_name_search(self):
        # An exact business key is never improved by a fuzzy match.
        directory = FakeDirectory((_employee("EMP-0001"),))

        await GetEmployeeTool(directory).execute(
            CONTEXT, employee_number="EMP-0001", full_name="Someone Else"
        )

        assert directory.calls[0][1]["full_name"] is None

    async def test_neither_identifier_is_refused(self):
        with pytest.raises(ToolArgumentError, match="employee_number or full_name"):
            await GetEmployeeTool(FakeDirectory()).execute(CONTEXT)

    @pytest.mark.parametrize("bad", ["", "   ", "x" * 40, "EMP 0001; DROP TABLE employees"])
    async def test_a_malformed_employee_number_is_refused(self, bad):
        with pytest.raises(ToolArgumentError):
            await GetEmployeeTool(FakeDirectory()).execute(CONTEXT, employee_number=bad)

    @pytest.mark.parametrize("bad", [123, 4.5, True, ["EMP-0001"], {"a": 1}])
    async def test_a_non_string_identifier_is_refused(self, bad):
        with pytest.raises(ToolArgumentError):
            await GetEmployeeTool(FakeDirectory()).execute(CONTEXT, employee_number=bad)


class TestCountEmployees:
    async def test_it_reports_total_and_active(self):
        result = await CountEmployeesTool(FakeDirectory()).execute(CONTEXT, department="Sales")

        assert result.total == 12
        assert result.active == 11

    async def test_a_blank_filter_is_treated_as_absent(self):
        # A model emitting "" means "I did not supply this". An all-whitespace
        # filter must not become a filter that matches nothing.
        directory = FakeDirectory()

        await CountEmployeesTool(directory).execute(CONTEXT, department="   ")

        assert directory.calls[0][1]["department"] is None

    async def test_filters_are_trimmed(self):
        directory = FakeDirectory()

        await CountEmployeesTool(directory).execute(CONTEXT, department="  Sales  ")

        assert directory.calls[0][1]["department"] == "Sales"


class TestLeaveTools:
    async def test_a_balance_computes_what_remains(self):
        balances = await GetLeaveBalanceTool(FakeDirectory()).execute(
            CONTEXT, employee_number="EMP-0001"
        )

        # 5 opening + 10 accrued - 3 used - 2 pending. Pending counts against
        # the balance: a day already requested is not a day still available.
        assert balances[0].remaining == Decimal(10)

    @pytest.mark.parametrize("year", [1800, 3000, "not-a-year", 2.5])
    async def test_an_impossible_fiscal_year_is_refused(self, year):
        with pytest.raises(ToolArgumentError, match="fiscal_year"):
            await GetLeaveBalanceTool(FakeDirectory()).execute(
                CONTEXT, employee_number="EMP-0001", fiscal_year=year
            )

    async def test_an_inverted_date_range_is_refused_not_swapped(self):
        # Silently swapping hides that the question was misread.
        with pytest.raises(ToolArgumentError, match="start_from"):
            await ListLeaveRequestsTool(FakeDirectory()).execute(
                CONTEXT,
                employee_number="EMP-0001",
                start_from="2026-06-01",
                start_to="2026-01-01",
            )

    async def test_iso_dates_are_parsed(self):
        directory = FakeDirectory()

        await ListLeaveRequestsTool(directory).execute(
            CONTEXT, employee_number="EMP-0001", start_from="2026-01-01"
        )

        assert directory.calls[0][1]["start_from"] == date(2026, 1, 1)

    @pytest.mark.parametrize("bad", ["01/02/2026", "not a date", "2026-13-01", 20260101])
    async def test_a_malformed_date_is_refused(self, bad):
        with pytest.raises(ToolArgumentError):
            await ListLeaveRequestsTool(FakeDirectory()).execute(
                CONTEXT, employee_number="EMP-0001", start_from=bad
            )

    async def test_the_policy_tool_takes_no_security_context(self):
        # An entitlement rule is company policy. Scoping it would stop an
        # employee reading the rule that governs their own leave.
        policies = await GetLeavePolicyTool(FakeDirectory()).execute(leave_code="ANN")

        assert policies[0].annual_entitlement_days == Decimal(21)

    async def test_the_policy_tool_still_validates_its_argument(self):
        with pytest.raises(ToolArgumentError, match="leave_code"):
            await GetLeavePolicyTool(FakeDirectory()).execute(leave_code="")


class TestAttendanceTool:
    async def test_a_year_is_required(self):
        with pytest.raises(ToolArgumentError, match="year is required"):
            await GetAttendanceSummaryTool(FakeDirectory()).execute(
                CONTEXT, employee_number="EMP-0001", year=None
            )

    @pytest.mark.parametrize("month", [0, 13, -1, 99])
    async def test_an_impossible_month_is_refused_not_clamped(self, month):
        # Answering about December when asked about month 13 hides the
        # misunderstanding.
        with pytest.raises(ToolArgumentError, match="month"):
            await GetAttendanceSummaryTool(FakeDirectory()).execute(
                CONTEXT, employee_number="EMP-0001", year=2026, month=month
            )

    async def test_a_valid_month_passes_through(self):
        directory = FakeDirectory()

        await GetAttendanceSummaryTool(directory).execute(
            CONTEXT, employee_number="EMP-0001", year=2026, month=3
        )

        assert directory.calls[0][1]["month"] == 3


class TestTheContextIsPassedThroughUntouched:
    @pytest.mark.parametrize(
        ("tool_type", "arguments"),
        [
            (GetEmployeeTool, {"employee_number": "EMP-0001"}),
            (CountEmployeesTool, {"department": "Sales"}),
            (GetLeaveBalanceTool, {"employee_number": "EMP-0001"}),
            (ListLeaveRequestsTool, {"employee_number": "EMP-0001"}),
            (GetAttendanceSummaryTool, {"employee_number": "EMP-0001", "year": 2026}),
        ],
    )
    async def test_every_scoped_tool_forwards_the_caller_context(self, tool_type, arguments):
        # A tool that built its own context, or dropped one, would be choosing
        # its own visibility.
        directory = FakeDirectory((_employee("EMP-0001"),))

        await tool_type(directory).execute(CONTEXT, **arguments)

        assert directory.contexts == [CONTEXT]

    async def test_no_tool_argument_is_named_like_a_scope_field(self):
        # `scope`, `employee_id`, `dept_id` are context concerns. A tool
        # argument by those names would invite the model to supply one.
        import inspect

        for tool_type in (
            GetEmployeeTool,
            CountEmployeesTool,
            GetLeaveBalanceTool,
            ListLeaveRequestsTool,
            GetLeavePolicyTool,
            GetAttendanceSummaryTool,
        ):
            parameters = set(inspect.signature(tool_type.execute).parameters)
            assert not parameters & {"scope", "employee_id", "dept_id", "branch_id", "user_id"}
