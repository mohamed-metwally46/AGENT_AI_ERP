"""The six MVP tools against the live database, checked against control SQL.

Plan.md section 11, Phase 2 acceptance criterion 1: "All six tools return typed
results matching hand-written control queries exactly." Every numeric assertion
below is either a control query run in the same test, or the specific figure the
plan names.

A tool that agrees with itself proves nothing, so the controls are written
independently -- different joins, different filters -- rather than by calling the
same code twice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import fields, is_dataclass
from typing import Any

import pytest

from erp_agent.application.tools.hr import (
    CountEmployeesTool,
    GetAttendanceSummaryTool,
    GetEmployeeTool,
    GetLeaveBalanceTool,
    GetLeavePolicyTool,
    ListLeaveRequestsTool,
)
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.models.sql import SqlStatement
from erp_agent.domain.policies.field_policy import NEVER_RETURNED
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.queries.hr_directory import PostgresHrDirectory

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.fixture
async def source(database_url: str) -> AsyncIterator[PostgresReadOnlyDataSource]:
    adapter = PostgresReadOnlyDataSource(dsn=database_url, min_pool_size=1, max_pool_size=3)
    try:
        await adapter.open()
    except Exception as exc:
        pytest.skip(f"the mock HR database is not reachable: {exc}")
    try:
        yield adapter
    finally:
        await adapter.close()


@pytest.fixture
def directory(source: PostgresReadOnlyDataSource) -> PostgresHrDirectory:
    return PostgresHrDirectory(source)


@pytest.fixture
async def ceo(source: PostgresReadOnlyDataSource) -> SecurityContext:
    """An ORGANISATION-scoped context, so tool output is not scope-limited."""
    row = await source.fetch_one(
        SqlStatement("SELECT employee_id FROM employees WHERE employee_number = 'EMP-0001'")
    )
    assert row is not None
    return SecurityContext(
        user_id="1", employee_id=str(row["employee_id"]), scope=DataScope.ORGANISATION
    )


async def _scalar(source: PostgresReadOnlyDataSource, sql: str, **parameters: Any) -> Any:
    """Run a hand-written control query and return its single value."""
    row = await source.fetch_one(SqlStatement(sql, parameters))
    assert row is not None
    return next(iter(row.values()))


class TestCountEmployees:
    async def test_sales_matches_the_figure_in_the_plan(self, directory, ceo):
        # Plan.md section 11, Phase 2 acceptance criterion 2.
        result = await CountEmployeesTool(directory).execute(ceo, department="Sales")

        assert result.total == 12
        assert result.active == 11

    async def test_every_department_matches_a_control_query(self, directory, source, ceo):
        tool = CountEmployeesTool(directory)
        for department in ("Sales", "Engineering", "Human Resources", "Finance"):
            control = await _scalar(
                source,
                """
                SELECT count(*) FROM employees e
                  JOIN branch_departments d ON d.dept_id = e.dept_id
                 WHERE d.dept_name = %(dept)s
                """,
                dept=department,
            )

            assert (await tool.execute(ceo, department=department)).total == control, department

    async def test_the_department_filter_is_case_insensitive(self, directory, ceo):
        tool = CountEmployeesTool(directory)

        assert (await tool.execute(ceo, department="sales")).total == 12

    async def test_an_unknown_department_counts_zero(self, directory, ceo):
        result = await CountEmployeesTool(directory).execute(
            ceo, department="Ministry of Silly Walks"
        )

        assert result.total == 0
        assert result.active == 0

    async def test_the_status_filter_matches_a_control_query(self, directory, source, ceo):
        control = await _scalar(
            source, "SELECT count(*) FROM employees WHERE employment_status = 'active'"
        )

        result = await CountEmployeesTool(directory).execute(ceo, employment_status="active")

        assert result.total == control
        assert result.active == control

    async def test_the_filters_are_echoed_back(self, directory, ceo):
        result = await CountEmployeesTool(directory).execute(ceo, department="Sales")

        assert result.department == "Sales"


class TestGetEmployee:
    async def test_lookup_by_employee_number(self, directory, ceo):
        result = await GetEmployeeTool(directory).execute(ceo, employee_number="EMP-0016")

        assert result.is_found
        assert result.employee is not None
        assert result.employee.employee_number == "EMP-0016"
        assert result.employee.department == "Sales"

    async def test_an_unknown_number_is_simply_not_found(self, directory, ceo):
        result = await GetEmployeeTool(directory).execute(ceo, employee_number="EMP-9999")

        assert not result.is_found
        assert not result.is_ambiguous

    async def test_the_joined_columns_are_populated(self, directory, ceo):
        result = await GetEmployeeTool(directory).execute(ceo, employee_number="EMP-0001")

        assert result.employee is not None
        assert result.employee.branch is not None
        assert result.employee.job_grade is not None

    async def test_an_ambiguous_name_returns_candidates_not_a_guess(self, directory, source, ceo):
        # Plan.md section 8.5, gap S5: names are not unique, and guessing is how
        # a name lookup crosses a permission boundary.
        repeated = await source.fetch_one(
            SqlStatement(
                """
                SELECT first_name || ' ' || last_name AS full_name
                  FROM employees
                 GROUP BY first_name, last_name
                HAVING count(*) > 1
                 LIMIT 1
                """
            )
        )
        if repeated is None:
            pytest.skip("this seed happens to contain no repeated full name")

        result = await GetEmployeeTool(directory).execute(ceo, full_name=repeated["full_name"])

        assert result.is_ambiguous
        assert not result.is_found
        assert len(result.candidates) >= 2

    async def test_a_wildcard_in_a_name_is_not_a_wildcard(self, directory, ceo):
        # Without LIKE escaping, "%" would enumerate every employee in scope
        # from something that looked like a name lookup.
        result = await GetEmployeeTool(directory).execute(ceo, full_name="%")

        assert not result.is_found
        assert not result.is_ambiguous


class TestLeaveBalance:
    async def test_balances_match_a_control_query(self, directory, source, ceo):
        balances = await GetLeaveBalanceTool(directory).execute(
            ceo, employee_number="EMP-0016", leave_code="ANN"
        )
        assert balances, "EMP-0016 should have an annual leave balance"
        balance = balances[0]

        control = await _scalar(
            source,
            """
            SELECT lb.opening_balance + lb.accrued_ytd - lb.used_ytd - lb.pending_ytd
              FROM leave_balances lb
              JOIN employees   e  ON e.employee_id    = lb.employee_id
              JOIN leave_types lt ON lt.leave_type_id = lb.leave_type_id
             WHERE e.employee_number = 'EMP-0016'
               AND lt.leave_code = 'ANN'
               AND lb.fiscal_year = %(year)s
            """,
            year=balance.fiscal_year,
        )

        assert balance.remaining == control

    async def test_the_fiscal_year_is_always_stated(self, directory, ceo):
        balances = await GetLeaveBalanceTool(directory).execute(ceo, employee_number="EMP-0016")

        assert balances
        assert all(b.fiscal_year > 2000 for b in balances)

    async def test_omitting_the_year_uses_the_latest_on_file(self, directory, source, ceo):
        latest = await _scalar(
            source,
            """
            SELECT max(lb.fiscal_year) FROM leave_balances lb
              JOIN employees e ON e.employee_id = lb.employee_id
             WHERE e.employee_number = 'EMP-0016'
            """,
        )

        balances = await GetLeaveBalanceTool(directory).execute(ceo, employee_number="EMP-0016")

        assert {b.fiscal_year for b in balances} == {latest}

    async def test_an_explicit_year_is_honoured(self, directory, ceo):
        balances = await GetLeaveBalanceTool(directory).execute(
            ceo, employee_number="EMP-0016", fiscal_year=2025
        )

        assert all(b.fiscal_year == 2025 for b in balances)


class TestLeaveRequests:
    async def test_requests_match_a_control_count(self, directory, source, ceo):
        subject = await source.fetch_one(
            SqlStatement(
                """
                SELECT e.employee_number, count(*) AS n
                  FROM leave_requests lr
                  JOIN employees e ON e.employee_id = lr.employee_id
                 WHERE lr.is_active
                 GROUP BY e.employee_number
                 ORDER BY n DESC
                 LIMIT 1
                """
            )
        )
        assert subject is not None

        requests = await ListLeaveRequestsTool(directory).execute(
            ceo, employee_number=subject["employee_number"]
        )

        assert len(requests) == subject["n"]

    async def test_results_are_most_recent_first(self, directory, source, ceo):
        subject = await _scalar(
            source,
            """
            SELECT e.employee_number FROM leave_requests lr
              JOIN employees e ON e.employee_id = lr.employee_id
             GROUP BY e.employee_number HAVING count(*) > 1 LIMIT 1
            """,
        )

        requests = await ListLeaveRequestsTool(directory).execute(ceo, employee_number=subject)
        dates = [r.start_date for r in requests]

        assert dates == sorted(dates, reverse=True)

    async def test_the_status_filter_narrows(self, directory, ceo):
        tool = ListLeaveRequestsTool(directory)
        subject = "EMP-0016"

        every = await tool.execute(ceo, employee_number=subject)
        approved = await tool.execute(ceo, employee_number=subject, status="approved")

        assert len(approved) <= len(every)
        assert all(r.status == "approved" for r in approved)


class TestLeavePolicy:
    async def test_it_returns_structured_entitlement_rules(self, directory):
        policies = await GetLeavePolicyTool(directory).execute(leave_code="ANN")

        assert policies
        assert all(p.leave_code == "ANN" for p in policies)
        assert all(p.annual_entitlement_days > 0 for p in policies)

    async def test_organisation_wide_rules_come_first(self, directory):
        policies = await GetLeavePolicyTool(directory).execute(leave_code="ANN")
        scopes = [p.scope for p in policies]

        if "all" in scopes:
            assert scopes[0] == "all"

    async def test_an_unknown_leave_code_returns_nothing(self, directory):
        assert await GetLeavePolicyTool(directory).execute(leave_code="NOPE") == ()

    async def test_it_needs_no_security_context(self, directory):
        # An entitlement rule is company policy: scoping it would mean an
        # employee could not read the rule that governs their own leave.
        policies = await GetLeavePolicyTool(directory).execute(leave_code="ANN")

        assert policies


class TestAttendanceSummary:
    async def test_summaries_match_a_control_query(self, directory, source, ceo):
        subject = await source.fetch_one(
            SqlStatement(
                """
                SELECT e.employee_number, a.year, a.month, a.days_present
                  FROM attendance_summaries a
                  JOIN employees e ON e.employee_id = a.employee_id
                 ORDER BY e.employee_number, a.year, a.month
                 LIMIT 1
                """
            )
        )
        assert subject is not None

        summaries = await GetAttendanceSummaryTool(directory).execute(
            ceo,
            employee_number=subject["employee_number"],
            year=subject["year"],
            month=subject["month"],
        )

        assert len(summaries) == 1
        assert summaries[0].days_present == subject["days_present"]

    async def test_omitting_the_month_returns_the_year_in_order(self, directory, source, ceo):
        subject = await source.fetch_one(
            SqlStatement(
                """
                SELECT e.employee_number, a.year, count(*) AS n
                  FROM attendance_summaries a
                  JOIN employees e ON e.employee_id = a.employee_id
                 GROUP BY e.employee_number, a.year
                 ORDER BY n DESC LIMIT 1
                """
            )
        )
        assert subject is not None

        summaries = await GetAttendanceSummaryTool(directory).execute(
            ceo, employee_number=subject["employee_number"], year=subject["year"]
        )

        assert len(summaries) == subject["n"]
        assert [s.month for s in summaries] == sorted(s.month for s in summaries)


class TestNoRestrictedFieldEverReachesAnAnswer:
    """Phase 2 acceptance criterion 3, asserted by scanning real tool output."""

    async def test_no_tool_result_carries_a_never_returned_value(self, directory, source, ceo):
        results: list[Any] = [
            await GetEmployeeTool(directory).execute(ceo, employee_number="EMP-0016"),
            await CountEmployeesTool(directory).execute(ceo, department="Sales"),
            *await GetLeaveBalanceTool(directory).execute(ceo, employee_number="EMP-0016"),
            *await ListLeaveRequestsTool(directory).execute(ceo, employee_number="EMP-0016"),
            *await GetLeavePolicyTool(directory).execute(leave_code="ANN"),
            *await GetAttendanceSummaryTool(directory).execute(
                ceo, employee_number="EMP-0016", year=2026
            ),
        ]

        for result in results:
            for name in _field_names(result):
                assert name not in NEVER_RETURNED, f"{type(result).__name__}.{name} is restricted"

    async def test_the_forbidden_values_are_absent_from_the_rendered_output(
        self, directory, source, ceo
    ):
        # Field names could be renamed; this checks the actual values never
        # appear, by comparing against what the database holds.
        secrets = await source.fetch_one(
            SqlStatement(
                """
                SELECT national_id, passport_number
                  FROM employees
                 WHERE employee_number = 'EMP-0016'
                """
            )
        )
        assert secrets is not None

        lookup = await GetEmployeeTool(directory).execute(ceo, employee_number="EMP-0016")
        rendered = repr(lookup)

        for value in secrets.values():
            if value:
                assert str(value) not in rendered


def _field_names(value: Any) -> tuple[str, ...]:
    """Field names of a dataclass result, recursing one level into a lookup."""
    if not is_dataclass(value):
        return ()
    names = tuple(f.name for f in fields(value))
    nested: tuple[str, ...] = ()
    for name in names:
        attribute = getattr(value, name)
        if is_dataclass(attribute):
            nested += _field_names(attribute)
    return names + nested
