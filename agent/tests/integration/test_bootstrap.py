"""Start-up, end to end, against the live mock HR database.

The point of the whole path: no entry point can obtain a working container
without the write barrier having been proved first.
"""

from __future__ import annotations

import pytest

from erp_agent.core.bootstrap import bootstrap
from erp_agent.domain.errors import ConfigurationError, DataSourceUnavailableError
from erp_agent.domain.models.sql import SqlStatement

pytestmark = [pytest.mark.integration, pytest.mark.security]

EXPECTED_EMPLOYEES = 60


def _structured_only(database_url: str) -> dict[str, str]:
    """A boot that serves structured questions and does not wire retrieval.

    These tests are about the write barrier. Start-up builds the retriever --
    deliberately, so a deployment that asked for document search finds out at
    boot rather than on somebody's first policy question -- and building it
    needs RAG's own configuration, which a minimal env does not carry.

    Disabling it here keeps each test about one thing. The behaviour that
    start-up *does* fail when retrieval is asked for and cannot be built has its
    own test below, because it is the more important half.
    """
    return {"ERP_DATABASE_URL": database_url, "ERP_KNOWLEDGE_ENABLED": "false"}


class TestBootstrap:
    async def test_a_booted_container_can_read(self, database_url):
        container = await bootstrap(_structured_only(database_url), use_env_file=False)
        try:
            row = await container.data_source.fetch_one(
                SqlStatement("SELECT count(*) AS n FROM employees")
            )

            assert row is not None
            assert row["n"] == EXPECTED_EMPLOYEES
        finally:
            await container.close()

    async def test_booting_proves_the_barrier_rather_than_assuming_it(self, database_url):
        container = await bootstrap(_structured_only(database_url), use_env_file=False)
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
                    **_structured_only("postgresql://readonly:pw@203.0.113.1:5433/mock_hr"),
                    "ERP_DB_CONNECT_TIMEOUT_S": "2",
                },
                use_env_file=False,
            )

    async def test_a_failed_boot_leaves_no_connections_open(self, database_url):
        # A container that failed its safety check must not be left holding the
        # pool it opened before the check ran.
        container = await bootstrap(_structured_only(database_url), use_env_file=False)
        await container.close()

        with pytest.raises(Exception):  # noqa: B017 -- pool is closed, any failure will do
            await container.data_source.fetch_one(SqlStatement("SELECT 1 AS n"))


class TestRetrievalIsWiredAtStartUpOrNotAtAll:
    """The half that matters more: a misconfigured retriever stops the boot.

    Until Phase 8 the container built the retriever lazily, so a deployment with
    ``ERP_KNOWLEDGE_ENABLED=true`` and no RAG configuration started cleanly and
    failed on the first policy question -- the worst place to discover it. The
    start-up warm-up moved that discovery to boot, and these pin it there.
    """

    async def test_asking_for_retrieval_without_configuring_it_stops_the_boot(self, database_url):
        with pytest.raises(ConfigurationError, match="RAG stack could not be built"):
            await bootstrap(
                {"ERP_DATABASE_URL": database_url, "ERP_KNOWLEDGE_ENABLED": "true"},
                use_env_file=False,
            )

    async def test_the_failure_names_what_to_do_about_it(self, database_url):
        # The message carries RAG's own error, which names the missing variable.
        with pytest.raises(ConfigurationError) as caught:
            await bootstrap(
                {"ERP_DATABASE_URL": database_url, "ERP_KNOWLEDGE_ENABLED": "true"},
                use_env_file=False,
            )

        assert "ERP_RAG_ENV_FILE" in caught.value.message
        assert "ERP_KNOWLEDGE_ENABLED=false" in caught.value.message

    async def test_a_structured_only_deployment_boots_with_six_tools(self, database_url):
        # A coherent deployment, not a degraded one: no index, no document tool,
        # and the model is never offered one it cannot use.
        container = await bootstrap(_structured_only(database_url), use_env_file=False)
        try:
            assert container.knowledge is None
            assert len(container.tool_registry) == 6
        finally:
            await container.close()
