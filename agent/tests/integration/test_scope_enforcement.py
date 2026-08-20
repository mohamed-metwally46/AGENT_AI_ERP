"""The scope ladder, enforced against real rows.

The unit tests prove the predicate renders correctly. These prove it *filters*
correctly, which is a different claim: a predicate can be perfectly well-formed
and still compare the wrong column, cast a uuid to text and match nothing, or
match everything.

The whole chain runs here: SecurityContext -> visible_employees ->
scope_predicate -> SqlStatement -> PostgreSQL.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from erp_agent.domain.errors import OutOfScopeError
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.models.sql import SqlStatement
from erp_agent.domain.policies.scope import visible_employees
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.predicates import (
    merge_parameters,
    scope_predicate,
    where_clause,
)

pytestmark = [pytest.mark.integration, pytest.mark.security]

TOTAL_EMPLOYEES = 60


@pytest.fixture
async def source(database_url: str) -> AsyncIterator[PostgresReadOnlyDataSource]:
    adapter = PostgresReadOnlyDataSource(dsn=database_url, min_pool_size=1, max_pool_size=2)
    try:
        await adapter.open()
    except Exception as exc:
        pytest.skip(f"the mock HR database is not reachable: {exc}")
    try:
        yield adapter
    finally:
        await adapter.close()


@pytest.fixture
async def sales_employee(source: PostgresReadOnlyDataSource) -> dict[str, Any]:
    """A real employee in Sales, with their department and branch."""
    row = await source.fetch_one(
        SqlStatement(
            """
            SELECT e.employee_id, e.employee_number, e.dept_id, e.branch_id
              FROM employees e
              JOIN branch_departments d ON d.dept_id = e.dept_id
             WHERE d.dept_name = %(dept)s
             ORDER BY e.employee_number
             LIMIT 1
            """,
            {"dept": "Sales"},
        )
    )
    assert row is not None, "the mock database should contain Sales employees"
    return dict(row)


async def _count_visible(source: PostgresReadOnlyDataSource, context: SecurityContext) -> int:
    """Count employees this context may see, through the real predicate path."""
    clause = where_clause([scope_predicate(visible_employees(context))])
    row = await source.fetch_one(
        SqlStatement(f"SELECT count(*) AS n FROM employees e {clause.sql}", clause.parameters)
    )
    assert row is not None
    return int(row["n"])


class TestEachRungSeesWhatItShould:
    async def test_self_scope_sees_exactly_one_row(self, source, sales_employee):
        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.SELF,
        )

        assert await _count_visible(source, context) == 1

    async def test_team_scope_sees_the_manager_plus_their_reports(self, source, sales_employee):
        manager_id = str(sales_employee["employee_id"])
        reports = await source.fetch_all(
            SqlStatement(
                "SELECT employee_id FROM employees WHERE manager_id = %(id)s::uuid",
                {"id": manager_id},
            )
        )
        report_ids = frozenset(str(row["employee_id"]) for row in reports.rows)

        context = SecurityContext(
            user_id="1",
            employee_id=manager_id,
            scope=DataScope.TEAM,
            manager_of=report_ids,
        )

        assert await _count_visible(source, context) == len(report_ids) + 1

    async def test_department_scope_sees_the_department(self, source, sales_employee):
        # Plan.md section 11, Phase 2: Sales has 12 employees.
        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.DEPARTMENT,
            dept_id=str(sales_employee["dept_id"]),
        )

        assert await _count_visible(source, context) == 12

    async def test_branch_scope_sees_the_branch(self, source, sales_employee):
        branch_id = str(sales_employee["branch_id"])
        control = await source.fetch_one(
            SqlStatement(
                "SELECT count(*) AS n FROM employees WHERE branch_id = %(id)s::uuid",
                {"id": branch_id},
            )
        )
        assert control is not None

        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.BRANCH,
            allowed_branch_ids=frozenset({branch_id}),
        )

        assert await _count_visible(source, context) == int(control["n"])

    async def test_organisation_scope_sees_everyone(self, source, sales_employee):
        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.ORGANISATION,
        )

        assert await _count_visible(source, context) == TOTAL_EMPLOYEES


class TestTheLadderActuallyNarrows:
    async def test_each_rung_sees_no_more_than_the_next(self, source, sales_employee):
        # The property that matters more than any individual number: widening
        # the scope never *reduces* what is visible, and narrowing never
        # increases it.
        employee_id = str(sales_employee["employee_id"])
        counts = [
            await _count_visible(
                source,
                SecurityContext(
                    user_id="1",
                    employee_id=employee_id,
                    scope=scope,
                    dept_id=str(sales_employee["dept_id"]),
                    allowed_branch_ids=frozenset({str(sales_employee["branch_id"])}),
                ),
            )
            for scope in DataScope
        ]

        assert counts == sorted(counts), f"the ladder is not monotonic: {counts}"
        assert counts[0] == 1
        assert counts[-1] == TOTAL_EMPLOYEES


class TestCrossScopeAccessIsBlocked:
    async def test_a_self_scoped_user_cannot_read_another_employee(self, source, sales_employee):
        # The tool argument names someone else; the scope predicate is ANDed in
        # and wins. This is the "blocked by the filter layer, not the model"
        # requirement of Plan.md section 9.2.
        other = await source.fetch_one(
            SqlStatement(
                """
                SELECT employee_id, employee_number
                  FROM employees
                 WHERE employee_id <> %(id)s::uuid
                 ORDER BY employee_number
                 LIMIT 1
                """,
                {"id": str(sales_employee["employee_id"])},
            )
        )
        assert other is not None

        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.SELF,
        )
        clause = where_clause([scope_predicate(visible_employees(context))])

        row = await source.fetch_one(
            SqlStatement(
                f"""
                SELECT e.employee_number
                  FROM employees e
                  {clause.sql} AND e.employee_number = %(wanted)s
                """,
                {**clause.parameters, "wanted": other["employee_number"]},
            )
        )

        assert row is None, "a SELF-scoped principal read another employee's record"

    async def test_a_department_scoped_user_cannot_read_another_department(
        self, source, sales_employee
    ):
        engineering = await source.fetch_one(
            SqlStatement(
                """
                SELECT e.employee_number
                  FROM employees e
                  JOIN branch_departments d ON d.dept_id = e.dept_id
                 WHERE d.dept_name = %(dept)s
                 LIMIT 1
                """,
                {"dept": "Engineering"},
            )
        )
        assert engineering is not None

        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.DEPARTMENT,
            dept_id=str(sales_employee["dept_id"]),
        )
        clause = where_clause([scope_predicate(visible_employees(context))])

        row = await source.fetch_one(
            SqlStatement(
                f"""
                SELECT e.employee_number
                  FROM employees e
                  {clause.sql} AND e.employee_number = %(wanted)s
                """,
                {**clause.parameters, "wanted": engineering["employee_number"]},
            )
        )

        assert row is None, "a DEPARTMENT-scoped principal read across departments"


class TestScopeParametersCannotBeOverwritten:
    async def test_a_tool_argument_named_like_a_scope_parameter_is_refused(
        self, source, sales_employee
    ):
        # If a tool argument could occupy `scope_employee_ids`, it would choose
        # its own visibility. Relying on merge order to prevent that is a
        # convention, not a control -- so the collision raises instead.
        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.SELF,
        )
        clause = where_clause([scope_predicate(visible_employees(context))])
        everyone = await source.fetch_all(SqlStatement("SELECT employee_id FROM employees"))
        all_ids = [str(row["employee_id"]) for row in everyone.rows]

        with pytest.raises(OutOfScopeError, match="scope_employee_ids"):
            merge_parameters(clause, {"scope_employee_ids": all_ids})

    async def test_ordinary_tool_arguments_merge_cleanly(self, source, sales_employee):
        context = SecurityContext(
            user_id="1",
            employee_id=str(sales_employee["employee_id"]),
            scope=DataScope.SELF,
        )
        clause = where_clause([scope_predicate(visible_employees(context))])
        parameters = merge_parameters(clause, {"wanted": sales_employee["employee_number"]})

        row = await source.fetch_one(
            SqlStatement(
                f"""
                SELECT e.employee_number
                  FROM employees e
                  {clause.sql} AND e.employee_number = %(wanted)s
                """,
                parameters,
            )
        )

        assert row is not None
        assert row["employee_number"] == sales_employee["employee_number"]
