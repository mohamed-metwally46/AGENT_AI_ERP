"""Regression guard for the ``::uuid[]`` casts, read off the query planner.

The row-count tests in ``test_scope_enforcement.py`` stay green if someone
"fixes" a cast mismatch by casting the *column* instead of the parameter --
``(e.employee_id)::text = ANY(...)`` returns exactly the same rows. What it also
does is make the predicate unindexable, turning every scoped query into a
sequential scan. On 60 mock rows nobody notices; on a real ERP it is the
difference between a scoped query and a table scan per question.

So these tests assert on the *plan*, not the result. Verified against the live
server: with ``::uuid[]`` the plan is

    Index Scan using employees_pkey on employees e
      Index Cond: (employee_id = ANY (...))

and with ``(employee_id)::text`` it becomes

    Seq Scan on employees e
      Filter: ((employee_id)::text = ANY (...))

Run on a raw connection because forcing the planner needs ``SET``, which the
read-only statement policy correctly refuses.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.models.sql import SqlStatement
from erp_agent.domain.policies.scope import visible_employees
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.predicates import scope_predicate, where_clause

pytestmark = [pytest.mark.integration, pytest.mark.security]

#: Casting an id column to text is the specific regression this file exists for.
_CAST_COLUMN_TO_TEXT = ("(employee_id)::text", "(dept_id)::text", "(branch_id)::text")


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
async def planner(database_url: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """A raw connection, so the planner can be forced with SET."""
    try:
        connection = await psycopg.AsyncConnection.connect(
            database_url, autocommit=True, connect_timeout=5
        )
    except psycopg.OperationalError as exc:
        pytest.skip(f"the mock HR database is not reachable: {exc}")
    try:
        yield connection
    finally:
        await connection.close()


@pytest.fixture
async def sales_employee(source: PostgresReadOnlyDataSource) -> dict[str, Any]:
    row = await source.fetch_one(
        SqlStatement(
            """
            SELECT e.employee_id, e.dept_id, e.branch_id
              FROM employees e
              JOIN branch_departments d ON d.dept_id = e.dept_id
             WHERE d.dept_name = %(dept)s
             ORDER BY e.employee_number
             LIMIT 1
            """,
            {"dept": "Sales"},
        )
    )
    assert row is not None
    return dict(row)


async def _plan(
    connection: psycopg.AsyncConnection[Any],
    context: SecurityContext,
    *,
    force_index: bool = False,
) -> str:
    """Return the query plan for a scoped employee query."""
    clause = where_clause([scope_predicate(visible_employees(context))])
    if force_index:
        # 60 rows makes a sequential scan genuinely cheapest, so the planner
        # picks it however good the predicate is. Disabling it asks the only
        # question that matters here: *could* this predicate use an index?
        await connection.execute("SET enable_seqscan = off")

    async with connection.cursor() as cursor:
        await cursor.execute(
            f"EXPLAIN SELECT e.employee_number FROM employees e {clause.sql}",
            dict(clause.parameters),
        )
        rows = await cursor.fetchall()

    return "\n".join(str(row[0]) for row in rows)


def _context(scope: DataScope, employee: dict[str, Any]) -> SecurityContext:
    return SecurityContext(
        user_id="1",
        employee_id=str(employee["employee_id"]),
        scope=scope,
        dept_id=str(employee["dept_id"]),
        allowed_branch_ids=frozenset({str(employee["branch_id"])}),
    )


class TestIdColumnsStayIndexable:
    async def test_self_scope_uses_the_primary_key_index(self, planner, sales_employee):
        # The natural plan -- no forcing needed, because an equality match on
        # the primary key is cheaper than a scan even on 60 rows.
        plan = await _plan(planner, _context(DataScope.SELF, sales_employee))

        assert "Index Scan using employees_pkey" in plan, plan
        assert "Index Cond: (employee_id = ANY" in plan, plan

    async def test_department_scope_can_use_the_department_index(self, planner, sales_employee):
        plan = await _plan(
            planner, _context(DataScope.DEPARTMENT, sales_employee), force_index=True
        )

        assert "idx_employees_dept_id" in plan, plan
        assert "Index Cond: (dept_id = " in plan, plan

    async def test_branch_scope_can_use_the_branch_index(self, planner, sales_employee):
        plan = await _plan(planner, _context(DataScope.BRANCH, sales_employee), force_index=True)

        assert "idx_employees_branch_id" in plan, plan
        assert "Index Cond: (branch_id = ANY" in plan, plan

    @pytest.mark.parametrize(
        "scope", [DataScope.SELF, DataScope.TEAM, DataScope.DEPARTMENT, DataScope.BRANCH]
    )
    async def test_no_scope_predicate_ever_casts_an_id_column(self, planner, sales_employee, scope):
        # The direct assertion. `(employee_id)::text = ANY(...)` returns the
        # right rows and destroys the index; only the plan reveals it.
        plan = await _plan(planner, _context(scope, sales_employee), force_index=True)

        for cast in _CAST_COLUMN_TO_TEXT:
            assert cast not in plan, f"{scope} casts a column to text:\n{plan}"

    @pytest.mark.parametrize(
        "scope", [DataScope.SELF, DataScope.TEAM, DataScope.DEPARTMENT, DataScope.BRANCH]
    )
    async def test_every_restricted_scope_reaches_the_index(self, planner, sales_employee, scope):
        plan = await _plan(planner, _context(scope, sales_employee), force_index=True)

        assert "Index Cond:" in plan, f"{scope} produced no index condition:\n{plan}"


class TestOrganisationScopeHasNothingToIndex:
    async def test_it_plans_a_full_scan_with_no_restriction(self, planner, sales_employee):
        # ORGANISATION renders `TRUE`, so there is no predicate and no index
        # scan is possible -- asserting one here would be asserting a fiction.
        # What *is* worth pinning is that it restricts nothing: if this ever
        # grew a Filter or an Index Cond, the unrestricted scope would have
        # quietly started excluding rows.
        plan = await _plan(planner, _context(DataScope.ORGANISATION, sales_employee))

        assert "Seq Scan on employees" in plan, plan
        assert "Index Cond:" not in plan, plan
        assert "Filter:" not in plan, plan
