"""Three questions left open at the end of Task 2.3, answered by assertion.

Each was a "probably fine, but nobody checked" note in a docstring. A note is not
a fact, and two of these turned out to have real answers that change what a later
phase must do. Kept as tests rather than as prose so the answers stay true.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from erp_agent.application.tools.hr import GetEmployeeTool, GetLeavePolicyTool
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.models.sql import SqlStatement
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.queries.hr_directory import PostgresHrDirectory

pytestmark = pytest.mark.integration


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
async def manager(source: PostgresReadOnlyDataSource) -> dict[str, Any]:
    """An employee who actually manages people, so TEAM scope is meaningful."""
    row = await source.fetch_one(
        SqlStatement(
            """
            SELECT e.employee_id, e.employee_number, e.date_of_birth,
                   (SELECT count(*) FROM employees r WHERE r.manager_id = e.employee_id) AS reports
              FROM employees e
             WHERE (SELECT count(*) FROM employees r WHERE r.manager_id = e.employee_id) > 0
               AND e.date_of_birth IS NOT NULL
             ORDER BY e.employee_number
             LIMIT 1
            """
        )
    )
    assert row is not None, "the mock database should contain at least one manager"
    return dict(row)


class TestOpenQuestion1TeamScopedSelfLookup:
    """Can a TEAM-scoped manager read their *own* contact details?

    Finding: **no**, and it is a real gap. ``SecurityContext.scope`` is set once
    from the principal's grant and does not narrow to ``SELF`` for a
    self-targeted call, so ``_may_read_contact_pii`` -- which admits only
    ``SELF`` scope or an HR role -- withholds the manager's own date of birth
    from them.

    Filed rather than fixed here: the fix belongs with whoever owns the
    permission model, because "narrow to SELF when the target is the caller"
    is a policy decision, not a bug in a query.
    """

    async def test_a_team_scoped_manager_cannot_read_their_own_date_of_birth(
        self, directory, manager
    ):
        context = SecurityContext(
            user_id="1",
            employee_id=str(manager["employee_id"]),
            scope=DataScope.TEAM,
            manager_of=frozenset(),
        )

        result = await GetEmployeeTool(directory).execute(
            context, employee_number=manager["employee_number"]
        )

        assert result.is_found, "the manager can see their own record"
        assert result.employee is not None
        # The gap, asserted rather than assumed. Change this test when the
        # policy changes; do not change it to make a fix look unnecessary.
        assert result.employee.date_of_birth is None
        assert manager["date_of_birth"] is not None, "the row really does have one on file"

    async def test_the_same_person_reads_it_under_self_scope(self, directory, manager):
        # Proves the withholding is about scope, not about the data being
        # absent -- and shows the workaround until the policy question is
        # settled.
        context = SecurityContext(
            user_id="1", employee_id=str(manager["employee_id"]), scope=DataScope.SELF
        )

        result = await GetEmployeeTool(directory).execute(
            context, employee_number=manager["employee_number"]
        )

        assert result.employee is not None
        assert result.employee.date_of_birth == manager["date_of_birth"]

    async def test_an_hr_role_lifts_it_at_any_scope(self, directory, manager):
        context = SecurityContext(
            user_id="1",
            employee_id=str(manager["employee_id"]),
            scope=DataScope.TEAM,
            role_names=frozenset({"hr"}),
        )

        result = await GetEmployeeTool(directory).execute(
            context, employee_number=manager["employee_number"]
        )

        assert result.employee is not None
        assert result.employee.date_of_birth is not None


class TestOpenQuestion2LeavePolicySelector:
    """Is leave policy uniform organisation-wide, or does it vary?

    Finding: it **varies by job grade**. ``leave_policies.scope`` holds both
    ``all`` and ``grade`` in this database, and annual leave has four rows --
    one organisation-wide and three grade-specific. Checked against the data and
    against the schema, which also permits ``dept``.

    ``get_leave_policy`` already takes ``job_grade_code`` as a plain domain
    argument, so the selector exists and no redesign is needed. What the tests
    below pin is that the selector actually selects.
    """

    async def test_annual_leave_has_more_than_one_policy(self, directory):
        policies = await GetLeavePolicyTool(directory).execute(leave_code="ANN")

        assert len(policies) > 1
        assert {p.scope for p in policies} == {"all", "grade"}

    async def test_entitlement_actually_differs_between_grades(self, directory, source):
        grades = await source.fetch_all(
            SqlStatement(
                """
                SELECT g.grade_code
                  FROM leave_policies lp
                  JOIN leave_types lt ON lt.leave_type_id = lp.leave_type_id
                  JOIN job_grades  g  ON g.job_grade_id   = lp.scope_id
                 WHERE lt.leave_code = 'ANN' AND lp.scope = 'grade'
                """
            )
        )
        assert len(grades.rows) >= 2, "need two grade policies to compare"

        tool = GetLeavePolicyTool(directory)
        entitlements = set()
        for row in grades.rows:
            policies = await tool.execute(leave_code="ANN", job_grade_code=row["grade_code"])
            grade_specific = [p for p in policies if p.scope == "grade"]
            assert grade_specific, f"no grade policy returned for {row['grade_code']}"
            entitlements.add(grade_specific[0].annual_entitlement_days)

        # If every grade had the same number, the selector would be pointless.
        assert len(entitlements) > 1, "grade policies do not actually differ"

    async def test_the_selector_narrows_to_the_requested_grade(self, directory, source):
        grade = await source.fetch_one(
            SqlStatement(
                """
                SELECT g.grade_code
                  FROM leave_policies lp
                  JOIN leave_types lt ON lt.leave_type_id = lp.leave_type_id
                  JOIN job_grades  g  ON g.job_grade_id   = lp.scope_id
                 WHERE lt.leave_code = 'ANN' AND lp.scope = 'grade'
                 LIMIT 1
                """
            )
        )
        assert grade is not None

        narrowed = await GetLeavePolicyTool(directory).execute(
            leave_code="ANN", job_grade_code=grade["grade_code"]
        )
        everything = await GetLeavePolicyTool(directory).execute(leave_code="ANN")

        assert len(narrowed) < len(everything)
        assert narrowed[0].scope == "all", "the organisation-wide rule still comes first"

    async def test_no_department_scoped_policy_exists_in_this_database(self, source):
        # Recorded because the *selector* only admits 'all' and 'grade' when a
        # grade is supplied. A 'dept'-scoped row would be silently dropped by
        # that filter. None exist today; this test is what makes that a
        # deliberate limitation rather than an undiscovered bug.
        row = await source.fetch_one(
            SqlStatement("SELECT count(*) AS n FROM leave_policies WHERE scope = 'dept'")
        )

        assert row is not None
        assert row["n"] == 0, (
            "a dept-scoped policy now exists; get_leave_policy's grade filter "
            "would drop it and needs extending"
        )


class TestOpenQuestion3RowCapsSurviveTheJoins:
    """Does the row cap bound logical rows, or joined rows?

    Finding: logical rows, and it cannot be otherwise. Every ``LEFT JOIN`` in
    these two queries matches on the parent's *primary key*
    (``leave_types.leave_type_id``, ``employees.employee_id``), so at most one
    row can match and fan-out is structurally impossible.

    Asserted empirically as well, because "structurally impossible" is exactly
    the kind of claim that stops being true when someone adds a join.
    """

    async def test_the_cap_bounds_leave_requests_not_joined_rows(self, directory, source):
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
        total = int(subject["n"])
        assert total >= 2, "need an employee with several requests"

        context = SecurityContext(user_id="1", employee_id="x", scope=DataScope.ORGANISATION)

        # Uncapped: exactly the control count, so no join inflated it.
        uncapped = await directory.leave_requests(
            context, employee_number=subject["employee_number"], limit=100
        )
        assert len(uncapped) == total

        # Capped below the real count: the cap is what binds, and it binds to
        # requests rather than to some larger joined row set.
        cap = total - 1
        capped = await directory.leave_requests(
            context, employee_number=subject["employee_number"], limit=cap
        )
        assert len(capped) == cap
        assert len({(r.start_date, r.leave_code) for r in capped}) == cap, "rows are distinct"

    async def test_attendance_summaries_are_not_inflated_by_the_employee_join(
        self, directory, source
    ):
        subject = await source.fetch_one(
            SqlStatement(
                """
                SELECT e.employee_number, a.year, count(*) AS n
                  FROM attendance_summaries a
                  JOIN employees e ON e.employee_id = a.employee_id
                 WHERE a.is_active
                 GROUP BY e.employee_number, a.year
                 ORDER BY n DESC
                 LIMIT 1
                """
            )
        )
        assert subject is not None

        context = SecurityContext(user_id="1", employee_id="x", scope=DataScope.ORGANISATION)
        summaries = await directory.attendance_summaries(
            context, employee_number=subject["employee_number"], year=subject["year"]
        )

        assert len(summaries) == int(subject["n"])
        # One row per period is the whole contract of a summary table.
        assert len({s.month for s in summaries}) == len(summaries)

    async def test_every_join_in_these_queries_is_on_a_primary_key(self, source):
        # The structural claim, checked against the catalogue rather than by
        # reading the SQL. If someone adds a join to a non-unique column, the
        # cap starts bounding the wrong thing and this fails.
        #
        # pg_catalog, not information_schema: the information_schema views are
        # privilege-filtered, and `mock_hr_readonly` -- which owns nothing --
        # sees zero rows in `table_constraints` even for constraints that
        # plainly exist. A guard built on that view would pass by seeing
        # nothing, which is the worst way for a guard to pass.
        row = await source.fetch_one(
            SqlStatement(
                """
                SELECT count(*) AS n
                  FROM pg_constraint c
                  JOIN pg_class     t ON t.oid = c.conrelid
                  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
                 WHERE c.contype = 'p'
                   AND (   (t.relname = 'leave_types' AND a.attname = 'leave_type_id')
                        OR (t.relname = 'employees'   AND a.attname = 'employee_id'))
                """
            )
        )

        assert row is not None
        assert row["n"] == 2, "the joined parents are no longer keyed as assumed"
