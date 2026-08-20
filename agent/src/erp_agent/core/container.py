"""Composition root: the only place that knows which adapter implements a port.

Everything above depends on interfaces. This is where the interfaces meet their
implementations, and it is the only module permitted to import from both
:mod:`erp_agent.config` and :mod:`erp_agent.infrastructure`.

The container is constructible without a running event loop and without a
reachable database, so building the object graph is not itself a network
operation. :meth:`open` is what makes it live.
"""

from __future__ import annotations

from functools import cached_property
from types import TracebackType
from typing import Self

from rag.domain.models import GenerationParams
from rag.domain.ports.llm_client import LLMClient

from erp_agent.application.agent.loop import AgentLoop
from erp_agent.application.agent.tool_registry import ToolRegistry, build_hr_registry
from erp_agent.config import Settings
from erp_agent.domain.errors import WriteBarrierError
from erp_agent.domain.models.diagnostics import DataSourceProbe
from erp_agent.domain.ports.data_source import ReadOnlyDataSource
from erp_agent.domain.ports.hr_directory import HrDirectory
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.queries.hr_directory import PostgresHrDirectory
from erp_agent.infrastructure.llm.factory import build_llm_client
from erp_agent.infrastructure.security.fixture_principal_resolver import (
    FixturePrincipalResolver,
    default_fixture_path,
)
from erp_agent.infrastructure.security.principal_resolver_port import PrincipalResolverPort

__all__ = ["Container"]


class Container:
    """Builds and owns the object graph for one process.

    Attributes:
        settings: The validated configuration this container was built from.
    """

    def __init__(self, settings: Settings) -> None:
        """Hold the configuration; build nothing yet.

        Args:
            settings: Validated configuration.
        """
        self.settings = settings

    @property
    def data_source(self) -> ReadOnlyDataSource:
        """The read-only ERP data source.

        Typed as the port, not the adapter, so nothing downstream can quietly
        start depending on psycopg.
        """
        return self._postgres

    @cached_property
    def _postgres(self) -> PostgresReadOnlyDataSource:
        """The concrete adapter, which also owns the connection pool."""
        database = self.settings.database
        return PostgresReadOnlyDataSource(
            dsn=database.dsn.get_secret_value(),
            min_pool_size=database.min_pool_size,
            max_pool_size=database.max_pool_size,
            connect_timeout_s=database.connect_timeout_s,
            statement_timeout_ms=database.statement_timeout_ms,
            default_max_rows=database.max_rows,
            application_name=database.application_name,
        )

    @cached_property
    def hr_directory(self) -> HrDirectory:
        """Named, scope-aware HR reads.

        Typed as the port so the six tools cannot reach psycopg, or SQL, or the
        connection pool.
        """
        return PostgresHrDirectory(self.data_source)

    @cached_property
    def tool_registry(self) -> ToolRegistry:
        """The static tool allowlist, validated at construction."""
        return build_hr_registry(self.hr_directory)

    @cached_property
    def llm_client(self) -> LLMClient:
        """The configured language model, behind RAG's port.

        Built lazily: the diagnostics command and the unit suite resolve the
        rest of the graph without needing a credential or a provider SDK.
        """
        llm = self.settings.llm
        return build_llm_client(
            provider=llm.provider.value,
            model=llm.model,
            api_key=None if llm.api_key is None else llm.api_key.get_secret_value(),
            base_url=llm.openrouter_base_url,
        )

    @cached_property
    def generation_params(self) -> GenerationParams:
        """Sampling settings, translated from configuration into RAG's type."""
        llm = self.settings.llm
        return GenerationParams(
            temperature=llm.temperature,
            max_output_tokens=llm.max_output_tokens,
            timeout_s=llm.timeout_s,
        )

    def agent_loop(self, llm: LLMClient | None = None) -> AgentLoop:
        """Build the bounded agent loop.

        Args:
            llm: Overrides the configured client. Tests pass a scripted one;
                nothing else should.

        Returns:
            A loop wired to the static registry and the configured bounds.
        """
        agent = self.settings.agent
        return AgentLoop(
            llm=llm if llm is not None else self.llm_client,
            registry=self.tool_registry,
            generation_params=self.generation_params,
            max_rounds=agent.max_rounds,
            max_calls_per_round=agent.max_calls_per_round,
            max_total_calls=agent.max_total_calls,
            tool_timeout_s=agent.tool_timeout_s,
            max_result_chars=agent.max_result_chars,
        )

    @cached_property
    def principal_resolver(self) -> PrincipalResolverPort:
        """Resolves an authenticated identity to a principal.

        Typed as the port. Phase 9 swaps the fixture adapter for a join against
        the Platform ``users`` tables, and nothing above here changes.

        The fixture is loaded and validated on first access, which is during
        start-up -- a malformed principal must not be discovered as a failed
        authorization on a live question.

        Raises:
            ConfigurationError: If the fixture is missing, malformed, or
                declares an unsupported schema version.
        """
        # Resolving `None` to the packaged file happens here rather than in
        # config, which imports no other layer and cannot know where an
        # infrastructure file lives.
        path = self.settings.security.principal_fixture_path or default_fixture_path()
        return FixturePrincipalResolver.from_file(path)

    async def open(self) -> None:
        """Open every resource the container owns.

        Raises:
            DataSourceUnavailableError: If the database is unreachable.
        """
        await self._postgres.open()

    async def close(self) -> None:
        """Release every resource the container owns."""
        await self._postgres.close()

    async def __aenter__(self) -> Self:
        """Open resources on entry."""
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release resources on exit."""
        await self.close()

    async def verify_write_barrier(self) -> DataSourceProbe:
        """Prove the configured connection cannot write, or refuse to serve.

        Called during start-up. A process that cannot demonstrate the barrier
        does not get to answer questions "carefully" -- it stops.

        Returns:
            What the probe observed, for reporting.

        Raises:
            WriteBarrierError: If any part of the barrier is not intact.
            DataSourceUnavailableError: If the database is unreachable.
        """
        probe = await self.data_source.probe()
        if not probe.barrier_intact:
            raise WriteBarrierError(
                "the database connection is not provably read-only: " + "; ".join(probe.failures),
                context={"role": probe.role, "database": probe.database},
            )
        return probe
