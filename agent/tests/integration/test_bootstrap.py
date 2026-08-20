"""Start-up, end to end, against the live mock HR database.

The point of the whole path: no entry point can obtain a working container
without the write barrier having been proved first.
"""

from __future__ import annotations

import pytest

from erp_agent.core.bootstrap import bootstrap
from erp_agent.domain.errors import DataSourceUnavailableError
from erp_agent.domain.models.sql import SqlStatement

pytestmark = [pytest.mark.integration, pytest.mark.security]

EXPECTED_EMPLOYEES = 60


class TestBootstrap:
    async def test_a_booted_container_can_read(self, database_url):
        container = await bootstrap({"ERP_DATABASE_URL": database_url}, use_env_file=False)
        try:
            row = await container.data_source.fetch_one(
                SqlStatement("SELECT count(*) AS n FROM employees")
            )

            assert row is not None
            assert row["n"] == EXPECTED_EMPLOYEES
        finally:
            await container.close()

    async def test_booting_proves_the_barrier_rather_than_assuming_it(self, database_url):
        container = await bootstrap({"ERP_DATABASE_URL": database_url}, use_env_file=False)
        try:
            probe = await container.data_source.probe()

            assert probe.barrier_intact
            assert probe.write_attempts, "a boot that attempted no write proved nothing"
        finally:
            await container.close()

    async def test_an_unreachable_database_stops_the_boot(self):
        # 203.0.113.0/24 is TEST-NET-3: reserved for documentation, never routed.
        with pytest.raises(DataSourceUnavailableError):
            await bootstrap(
                {
                    "ERP_DATABASE_URL": "postgresql://readonly:pw@203.0.113.1:5433/mock_hr",
                    "ERP_DB_CONNECT_TIMEOUT_S": "2",
                },
                use_env_file=False,
            )

    async def test_a_failed_boot_leaves_no_connections_open(self, database_url):
        # A container that failed its safety check must not be left holding the
        # pool it opened before the check ran.
        container = await bootstrap({"ERP_DATABASE_URL": database_url}, use_env_file=False)
        await container.close()

        with pytest.raises(Exception):  # noqa: B017 -- pool is closed, any failure will do
            await container.data_source.fetch_one(SqlStatement("SELECT 1 AS n"))
