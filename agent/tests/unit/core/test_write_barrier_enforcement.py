"""A process that cannot prove the barrier refuses to serve.

The success path is covered against a live database in the integration suite.
What is exercised here is the *failure* path -- the branch that only runs when a
security control is broken, and which therefore never runs in normal operation.
An unverified refusal is not a refusal.
"""

from __future__ import annotations

import pytest

from erp_agent.config import Settings
from erp_agent.core.container import Container
from erp_agent.domain.errors import WriteBarrierError
from erp_agent.domain.models.diagnostics import DataSourceProbe, WriteAttempt
from erp_agent.domain.models.sql import QueryResult, Row, SqlStatement
from erp_agent.domain.ports.data_source import ReadOnlyDataSource

pytestmark = [pytest.mark.unit, pytest.mark.security]

_DSN = "postgresql://mock_hr_readonly:pw@localhost:5433/mock_hr"


class FakeDataSource(ReadOnlyDataSource):
    """A data source that reports whatever posture a test needs."""

    def __init__(self, probe: DataSourceProbe) -> None:
        self._probe = probe

    async def fetch_all(self, statement: SqlStatement) -> QueryResult:
        return QueryResult(rows=())

    async def fetch_one(self, statement: SqlStatement) -> Row | None:
        return None

    async def probe(self) -> DataSourceProbe:
        return self._probe


def _probe(**overrides: object) -> DataSourceProbe:
    defaults: dict[str, object] = {
        "server_version": "16.14",
        "database": "mock_hr",
        "role": "mock_hr_readonly",
        "transaction_read_only": True,
        "statement_timeout": "15s",
        "write_attempts": (WriteAttempt("INSERT", True, "refused"),),
        "write_privileges": (),
        "is_superuser": False,
        "can_create_in_public": False,
    }
    return DataSourceProbe(**{**defaults, **overrides})  # type: ignore[arg-type]


def _container_reporting(probe: DataSourceProbe) -> Container:
    """A container whose data source reports the given probe."""
    settings = Settings.from_environment({"ERP_DATABASE_URL": _DSN}, use_env_file=False)
    container = Container(settings)
    # `_postgres` is a cached_property; seeding the cache substitutes the
    # adapter without opening a socket.
    container.__dict__["_postgres"] = FakeDataSource(probe)
    return container


class TestTheProcessRefusesAnUnsafeConnection:
    async def test_an_intact_barrier_is_accepted(self):
        result = await _container_reporting(_probe()).verify_write_barrier()

        assert result.barrier_intact

    async def test_an_accepted_write_stops_the_process(self):
        container = _container_reporting(
            _probe(write_attempts=(WriteAttempt("UPDATE", False, "the server accepted it"),))
        )

        with pytest.raises(WriteBarrierError, match="not provably read-only"):
            await container.verify_write_barrier()

    async def test_held_write_grants_stop_the_process(self):
        # The role could write in a session configured differently, even though
        # this session refused. That is enough to refuse to serve.
        container = _container_reporting(_probe(write_privileges=("employees:UPDATE",)))

        with pytest.raises(WriteBarrierError, match="write privileges"):
            await container.verify_write_barrier()

    async def test_a_writable_transaction_stops_the_process(self):
        container = _container_reporting(_probe(transaction_read_only=False))

        with pytest.raises(WriteBarrierError, match="READ ONLY"):
            await container.verify_write_barrier()

    async def test_a_superuser_connection_stops_the_process(self):
        container = _container_reporting(_probe(is_superuser=True))

        with pytest.raises(WriteBarrierError, match="superuser"):
            await container.verify_write_barrier()

    async def test_a_probe_that_attempted_nothing_stops_the_process(self):
        # Silence is not proof. A probe with no attempts must not pass.
        container = _container_reporting(_probe(write_attempts=()))

        with pytest.raises(WriteBarrierError, match="nothing was proved"):
            await container.verify_write_barrier()

    async def test_the_error_names_the_connection_but_carries_no_secret(self):
        container = _container_reporting(_probe(is_superuser=True))

        with pytest.raises(WriteBarrierError) as caught:
            await container.verify_write_barrier()

        assert caught.value.context["role"] == "mock_hr_readonly"
        assert "pw" not in str(caught.value.context.values())
        assert not caught.value.retryable
