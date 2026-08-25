"""Scope and field enforcement, through the tools, against real rows.

Phase 2 acceptance criteria 3 and 4 (Plan.md section 11):

* no tool response contains a "never returned" field, and
* scope predicates are injected by the security layer and cannot be overridden
  by tool arguments.

The second is the one worth being careful about. Every test here has a
*malicious* shape: the tool argument names a real employee, in a real
department, who genuinely exists -- and the caller is simply not allowed to see
them. A tool that answered would be leaking real data, not failing a synthetic
check.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from erp_agent.application.tools.hr import (
    CountEmployeesTool,
    GetAttendanceSummaryTool,
    GetEmployeeTool,
    GetLeaveBalanceTool,
    ListLeaveRequestsTool,
)
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.models.sql import SqlStatement
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.queries.hr_directory import PostgresHrDirectory
from erp_agent.infrastructure.security.mock_identity import packaged_role_catalogue

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
async def sales_person(source: PostgresReadOnlyDataSource) -> dict[str, Any]:
    """A real Sales employee, used as the *caller*."""
    row = await source.fetch_one(
        SqlStatement(
            """
            SELECT e.employee_id, e.employee_number, e.dept_id, e.branch_id
              FROM employees e
              JOIN branch_departments d ON d.dept_id = e.dept_id
             WHERE d.dept_name = 'Sales'
             ORDER BY e.employee_number
             LIMIT 1
            """
        )
    )
    assert row is not None
    return dict(row)


@pytest.fixture
async def engineer(source: PostgresReadOnlyDataSource) -> dict[str, Any]:
    """A real Engineering employee, used as the *target* the caller may not see."""
    row = await source.fetch_one(
        SqlStatement(
            """
            SELECT e.employee_id, e.employee_number
              FROM employees e
              JOIN branch_departments d ON d.dept_id = e.dept_id
             WHERE d.dept_name = 'Engineering'
             ORDER BY e.employee_number
             LIMIT 1
            """
        )
    )
    assert row is not None
    return dict(row)


def _self_scoped(employee: dict[str, Any]) -> SecurityContext:
    return SecurityContext(
        user_id="1", employee_id=str(employee["employee_id"]), scope=DataScope.SELF
    )


def _department_scoped(employee: dict[str, Any]) -> SecurityContext:
    return SecurityContext(
        user_id="1",
        employee_id=str(employee["employee_id"]),
        scope=DataScope.DEPARTMENT,
        dept_id=str(employee["dept_id"]),
    )


class TestCrossScopeReadsAreRefusedByTheFilter:
    async def test_a_self_scoped_caller_cannot_look_up_a_real_colleague(
        self, directory, sales_person, engineer
    ):
        # The target exists and the employee number is correct. Only the filter
        # stops this.
        result = await GetEmployeeTool(directory).execute(
            _self_scoped(sales_person), employee_number=engineer["employee_number"]
        )

        assert not result.is_found
        assert not result.is_ambiguous

    async def test_the_same_lookup_succeeds_for_a_caller_who_may_see_them(
        self, directory, engineer
    ):
        # Proves the previous test failed for the right reason: the row is
        # readable, just not by that principal.
        result = await GetEmployeeTool(directory).execute(
            _self_scoped(engineer), employee_number=engineer["employee_number"]
        )

        assert result.is_found

    async def test_a_self_scoped_caller_cannot_read_a_colleagues_leave_balance(
        self, directory, sales_person, engineer
    ):
        balances = await GetLeaveBalanceTool(directory).execute(
            _self_scoped(sales_person), employee_number=engineer["employee_number"]
        )

        assert balances == ()

    async def test_a_self_scoped_caller_cannot_read_a_colleagues_leave_requests(
        self, directory, sales_person, engineer
    ):
        requests = await ListLeaveRequestsTool(directory).execute(
            _self_scoped(sales_person), employee_number=engineer["employee_number"]
        )

        assert requests == ()

    async def test_a_self_scoped_caller_cannot_read_a_colleagues_attendance(
        self, directory, sales_person, engineer
    ):
        summaries = await GetAttendanceSummaryTool(directory).execute(
            _self_scoped(sales_person), employee_number=engineer["employee_number"], year=2026
        )

        assert summaries == ()

    async def test_a_department_scoped_caller_cannot_count_another_department(
        self, directory, sales_person
    ):
        # Engineering has 10 employees. A Sales-scoped caller must see none of
        # them, whatever the filter argument says.
        result = await CountEmployeesTool(directory).execute(
            _department_scoped(sales_person), department="Engineering"
        )

        assert result.total == 0

    async def test_a_department_scoped_caller_counts_their_own_department(
        self, directory, sales_person
    ):
        result = await CountEmployeesTool(directory).execute(
            _department_scoped(sales_person), department="Sales"
        )

        assert result.total == 12


class TestScopeSurvivesHostileArguments:
    @pytest.mark.parametrize(
        "payload",
        [
            "' OR '1'='1",
            "%",
            "_",
            "EMP-0001' OR 1=1 --",
            "\\",
        ],
    )
    async def test_an_injection_payload_never_widens_a_lookup(
        self, directory, sales_person, payload
    ):
        # Values are bound and LIKE wildcards escaped, so the worst outcome is
        # "no match" -- never "every employee".
        result = await GetEmployeeTool(directory).execute(
            _self_scoped(sales_person), full_name=payload
        )

        assert not result.is_ambiguous
        if result.is_found:
            # A SELF-scoped caller can only ever match themselves.
            assert result.employee is not None
            assert result.employee.employee_number == sales_person["employee_number"]

    async def test_a_wildcard_department_does_not_match_every_department(
        self, directory, sales_person
    ):
        result = await CountEmployeesTool(directory).execute(
            _department_scoped(sales_person), department="%"
        )

        assert result.total == 0


class TestFieldLevelRestrictions:
    async def test_contact_pii_is_withheld_from_a_caller_without_the_role(
        self, directory, sales_person, engineer
    ):
        # A department-scoped colleague can see that the person exists, but not
        # their date of birth or personal contact details.
        colleague = await GetEmployeeTool(directory).execute(
            _department_scoped(sales_person), employee_number=sales_person["employee_number"]
        )

        assert colleague.is_found
        assert colleague.employee is not None
        assert colleague.employee.date_of_birth is None
        assert colleague.employee.phone_mobile is None
        assert colleague.employee.email_personal is None

    async def test_a_self_scoped_caller_reads_their_own_contact_details(
        self, directory, sales_person, source
    ):
        stored = await source.fetch_one(
            SqlStatement(
                "SELECT date_of_birth FROM employees WHERE employee_number = %(n)s",
                {"n": sales_person["employee_number"]},
            )
        )
        assert stored is not None

        own = await GetEmployeeTool(directory).execute(
            _self_scoped(sales_person), employee_number=sales_person["employee_number"]
        )

        assert own.employee is not None
        assert own.employee.date_of_birth == stored["date_of_birth"]

    async def test_an_hr_role_reads_contact_details_about_others(
        self, directory, sales_person, engineer, source
    ):
        # The grant comes from resolving the role through the real catalogue,
        # not from naming it: the policy asks for `hr.contact_pii:read`, and a
        # test that granted that by hand would keep passing even if no role
        # anybody holds still conferred it.
        grant = packaged_role_catalogue().resolve(["hr_manager"])
        hr_context = SecurityContext(
            user_id="1",
            employee_id=str(sales_person["employee_id"]),
            scope=DataScope.ORGANISATION,
            role_names=grant.role_names,
            permissions=grant.permissions,
        )

        result = await GetEmployeeTool(directory).execute(
            hr_context, employee_number=engineer["employee_number"]
        )

        assert result.employee is not None
        assert result.employee.date_of_birth is not None

    async def test_no_employee_record_can_carry_an_identity_document(self, directory, sales_person):
        # There is no field to put one in. Checked structurally rather than by
        # value, so it holds for every row and not just this seed.
        from erp_agent.domain.models.hr import EmployeeSummary

        assert not {
            "national_id",
            "passport_number",
            "passport_expiry",
            "bank_iban",
            "religion",
            "blood_type",
        } & set(EmployeeSummary.__slots__)
