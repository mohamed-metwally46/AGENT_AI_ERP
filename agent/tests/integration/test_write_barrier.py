"""The write barrier, proved against a live PostgreSQL.

Task 1.1's central claim is that the agent's connection is *incapable* of
writing. These tests are what makes that a fact rather than an assertion, and
they check the two database-side halves separately, because they fail
separately:

* **Barrier 2** -- every unit of work opens ``BEGIN READ ONLY``, so a write is
  refused with SQLSTATE 25006 regardless of what the role was granted.
* **Barrier 1** -- the role holds only ``SELECT``, so a write is refused with
  SQLSTATE 42501 even when a client turns the read-only default off.

The second is not hypothetical: ``default_transaction_read_only`` is a ``USERSET``
GUC, so any client can issue ``SET default_transaction_read_only = off``. If the
grants were wrong, that one statement would be the whole exploit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from erp_agent.domain.errors import DataSourceError, QueryTimeoutError
from erp_agent.domain.models.sql import SqlStatement
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.read_only_guard import WRITE_PROBES

pytestmark = [pytest.mark.integration, pytest.mark.security]

#: Phase 0 seeded exactly this many employees (Plan.md section 11, Phase 0).
EXPECTED_EMPLOYEES = 60


@pytest.fixture
async def source(database_url: str) -> AsyncIterator[PostgresReadOnlyDataSource]:
    """An opened read-only data source, or skip if the database is down."""
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
async def raw_connection(database_url: str) -> AsyncIterator[psycopg.AsyncConnection[tuple[str]]]:
    """A bare connection with autocommit, for testing the barrier's own edges."""
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


class TestReadsWork:
    async def test_select_count_employees_returns_sixty(self, source):
        # Task 1.1 acceptance criterion 2, to the row.
        row = await source.fetch_one(SqlStatement("SELECT count(*) AS n FROM employees"))

        assert row is not None
        assert row["n"] == EXPECTED_EMPLOYEES

    async def test_the_unit_of_work_is_read_only(self, source):
        row = await source.fetch_one(
            SqlStatement("SELECT current_setting('transaction_read_only') AS mode")
        )

        assert row is not None
        assert row["mode"] == "on"

    async def test_no_rows_is_an_answer_not_an_error(self, source):
        row = await source.fetch_one(
            SqlStatement(
                "SELECT employee_id FROM employees WHERE employee_number = %(number)s",
                {"number": "does-not-exist"},
            )
        )

        assert row is None

    async def test_fetch_one_refuses_an_ambiguous_result(self, source):
        # Silently taking the first of several rows is how a lookup crosses a
        # permission boundary (Plan.md section 8.5, gap S5).
        with pytest.raises(DataSourceError, match="more than one row"):
            await source.fetch_one(SqlStatement("SELECT employee_id FROM employees"))


class TestBoundsAreEnforced:
    async def test_the_row_cap_truncates_and_says_so(self, source):
        result = await source.fetch_all(
            SqlStatement("SELECT employee_id FROM employees", max_rows=5)
        )

        assert result.row_count == 5
        assert result.truncated, "a truncated result reported as complete is a wrong answer"

    async def test_an_uncapped_query_is_still_capped(self, source):
        # 60 employees, default cap of 100: nothing is discarded, so nothing is
        # flagged.
        result = await source.fetch_all(SqlStatement("SELECT employee_id FROM employees"))

        assert result.row_count == EXPECTED_EMPLOYEES
        assert not result.truncated

    async def test_the_statement_timeout_aborts_a_slow_query(self, database_url):
        adapter = PostgresReadOnlyDataSource(dsn=database_url, statement_timeout_ms=250)
        await adapter.open()
        try:
            with pytest.raises(QueryTimeoutError):
                await adapter.fetch_all(SqlStatement("SELECT pg_sleep(5)"))
        finally:
            await adapter.close()

    async def test_a_timeout_leaves_the_pool_usable(self, database_url):
        # A cancelled statement must not poison the connection for the next
        # question.
        adapter = PostgresReadOnlyDataSource(dsn=database_url, statement_timeout_ms=250)
        await adapter.open()
        try:
            with pytest.raises(QueryTimeoutError):
                await adapter.fetch_all(SqlStatement("SELECT pg_sleep(5)"))

            row = await adapter.fetch_one(SqlStatement("SELECT count(*) AS n FROM employees"))
            assert row is not None
            assert row["n"] == EXPECTED_EMPLOYEES
        finally:
            await adapter.close()


class TestBarrierTwoTheReadOnlyTransaction:
    @pytest.mark.parametrize(("label", "statement"), WRITE_PROBES, ids=[p[0] for p in WRITE_PROBES])
    async def test_every_write_is_refused_by_the_server(self, raw_connection, label, statement):
        # Task 1.1 acceptance criterion 1: the refusal comes from PostgreSQL,
        # not from application code. Executed on a bare connection so no
        # application-side guard can be credited for the result.
        with pytest.raises(psycopg.Error) as caught:
            await raw_connection.execute(statement)

        assert caught.value.diag.sqlstate == "25006", (
            f"{label} was expected to fail as a read-only transaction violation"
        )

    async def test_the_probe_reports_the_barrier_intact(self, source):
        probe = await source.probe()

        assert probe.barrier_intact, f"barrier not intact: {probe.failures}"
        assert probe.role == "mock_hr_readonly"
        assert probe.transaction_read_only
        assert all(attempt.rejected for attempt in probe.write_attempts)

    async def test_the_probe_leaves_the_data_untouched(self, source):
        # Every probe runs in a forced-rollback savepoint, so even a broken
        # barrier cannot let the check corrupt what it was checking.
        await source.probe()
        row = await source.fetch_one(SqlStatement("SELECT count(*) AS n FROM employees"))

        assert row is not None
        assert row["n"] == EXPECTED_EMPLOYEES


class TestBarrierOneThePrivilegeGrants:
    async def test_the_role_holds_no_write_privilege_at_all(self, source):
        probe = await source.probe()

        assert probe.write_privileges == ()
        assert not probe.is_superuser
        assert not probe.can_create_in_public

    @pytest.mark.parametrize(("label", "statement"), WRITE_PROBES, ids=[p[0] for p in WRITE_PROBES])
    async def test_writes_still_fail_when_the_read_only_default_is_disabled(
        self, raw_connection, label, statement
    ):
        # The exploit this defends against: `default_transaction_read_only` is a
        # USERSET GUC, so a client can switch off barrier 2 by itself. What
        # stops the write then is the grants, and this proves they do.
        await raw_connection.execute("SET default_transaction_read_only = off")

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as caught:
            await raw_connection.execute(statement)

        assert caught.value.diag.sqlstate == "42501", (
            f"{label} was refused, but not by the privilege system"
        )

    async def test_the_role_cannot_read_outside_its_grants(self, raw_connection):
        # A read-only role that can read server files is not read-only in any
        # useful sense.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await raw_connection.execute("SELECT pg_read_file('postgresql.conf')")


class TestParametersAreBoundNotInterpolated:
    async def test_a_sql_injection_payload_stays_a_value(self, source):
        payload = "'; DROP TABLE employees; --"

        result = await source.fetch_all(
            SqlStatement(
                "SELECT employee_id FROM employees WHERE employee_number = %(number)s",
                {"number": payload},
            )
        )

        assert result.is_empty

        # The table is still there, which is the point.
        row = await source.fetch_one(SqlStatement("SELECT count(*) AS n FROM employees"))
        assert row is not None
        assert row["n"] == EXPECTED_EMPLOYEES
