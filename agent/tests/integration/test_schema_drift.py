"""The drift check, against a real database.

Phase 9's acceptance criterion 2. Two halves, and the second is the one that
makes the first mean anything.

*It reports no drift against a compatible target.* `mock_hr` is the documented
schema, so a clean report is the expected answer -- and a clean report is what
"switching to a replica is configuration only" rests on.

*It reports drift when there is drift.* A checker that has only ever returned
COMPATIBLE has not been shown to detect anything. Rather than mutate the
database -- which the read-only role cannot do, and should not be able to -- the
contract is bent instead: a required table and column nobody has, checked
against the same real catalogue.

The row-cap incident is pinned here too. The first version of the catalogue
query returned one row per column, the data source clamped it to the configured
cap, and the check confidently reported six present tables as missing. The
regression test is that every required table resolves.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.drift import check_drift, render_drift
from erp_agent.infrastructure.database.schema_contract import REQUIRED_TABLES, RequiredTable

pytestmark = [pytest.mark.integration]


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


class TestTheDocumentedSchemaIsCompatible:
    async def test_the_mock_database_reports_no_drift(self, source):
        report = await check_drift(source, database="mock_hr")

        assert report.compatible, render_drift(report)

    async def test_every_required_table_resolves(self, source):
        # The row-cap regression: the catalogue query used to return a row per
        # column, the data source clamped it to the configured cap, and six
        # tables that were plainly present were reported missing.
        report = await check_drift(source)

        assert report.missing_tables == ()
        assert report.unreadable_tables == ()

    async def test_a_table_is_never_both_missing_and_unreadable(self, source):
        # The self-contradiction that exposed the row-cap bug. Now impossible by
        # construction, and asserted so it stays that way.
        report = await check_drift(source)
        missing = {drift.table for drift in report.missing_tables}

        assert missing & set(report.unreadable_tables) == set()

    async def test_extra_columns_are_reported_but_do_not_fail(self, source):
        # The documented schema has far more columns than the agent reads, and
        # that is fine: explicit column lists mean it cannot read one by
        # accident.
        report = await check_drift(source)

        assert report.extra_columns
        assert report.compatible

    async def test_the_check_reads_only_the_catalogue(self, source):
        # Safe to run against production. Proved by the connection itself: the
        # role has no write privileges and the transaction is read-only.
        probe = await source.probe()

        await check_drift(source)

        assert probe.barrier_intact


class TestTheCheckCanDetectDrift:
    async def test_a_required_table_nobody_has_is_reported_missing(self, source, monkeypatch):
        monkeypatch.setattr(
            "erp_agent.infrastructure.database.drift.REQUIRED_TABLES",
            (
                *REQUIRED_TABLES,
                RequiredTable(
                    name="employee_secrets",
                    columns=frozenset({"employee_id"}),
                    why="a table invented by this test",
                ),
            ),
        )

        report = await check_drift(source)

        assert not report.compatible
        assert [drift.table for drift in report.missing_tables] == ["employee_secrets"]

    async def test_a_required_column_nobody_has_is_reported_missing(self, source, monkeypatch):
        monkeypatch.setattr(
            "erp_agent.infrastructure.database.drift.REQUIRED_TABLES",
            (
                RequiredTable(
                    name="employees",
                    columns=frozenset({"employee_id", "a_column_that_does_not_exist"}),
                    why="a column invented by this test",
                ),
            ),
        )

        report = await check_drift(source)

        assert not report.compatible
        assert [drift.column for drift in report.missing_columns] == [
            "a_column_that_does_not_exist"
        ]

    async def test_a_type_that_changed_family_is_reported(self, source, monkeypatch):
        # `hire_date` is a date. Telling the checker to expect a number is the
        # same shape of finding as the ERP changing it to text under us.
        from erp_agent.infrastructure.database import drift as drift_module

        monkeypatch.setitem(drift_module._EXPECTED_FAMILY, "hire_date", "number")

        report = await check_drift(source)

        assert not report.compatible
        assert [drift.column for drift in report.changed_types] == ["hire_date"]

    async def test_the_report_names_what_breaks(self, source, monkeypatch):
        monkeypatch.setattr(
            "erp_agent.infrastructure.database.drift.REQUIRED_TABLES",
            (
                RequiredTable(
                    name="employee_secrets",
                    columns=frozenset({"employee_id"}),
                    why="get_employee",
                ),
            ),
        )

        rendered = render_drift(await check_drift(source, database="mock_hr"))

        assert "INCOMPATIBLE" in rendered
        assert "employee_secrets" in rendered
        assert "get_employee" in rendered
