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
from erp_agent.application.agent.tool_registry import (
    ToolRegistry,
    build_agent_registry,
    build_hr_registry,
)
from erp_agent.application.conversation import ConversationStore
from erp_agent.application.security import Authenticate
from erp_agent.application.use_cases import AnswerErpQuestion
from erp_agent.config import AppEnv, Settings
from erp_agent.domain.errors import ConfigurationError, WriteBarrierError
from erp_agent.domain.models.diagnostics import DataSourceProbe
from erp_agent.domain.ports.access_tokens import AccessTokenPort
from erp_agent.domain.ports.data_source import ReadOnlyDataSource
from erp_agent.domain.ports.hr_directory import HrDirectory
from erp_agent.domain.ports.knowledge import KnowledgeBase
from erp_agent.domain.ports.principal_resolver import PrincipalResolverPort
from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.queries.hr_directory import PostgresHrDirectory
from erp_agent.infrastructure.llm.factory import build_llm_client
from erp_agent.infrastructure.rag.document_catalogue import (
    DocumentCatalogue,
    default_catalogue_path,
)
from erp_agent.infrastructure.security.fixture_access_tokens import (
    FixtureAccessTokens,
    default_token_fixture_path,
)
from erp_agent.infrastructure.security.fixture_principal_resolver import (
    FixturePrincipalResolver,
    default_fixture_path,
)

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
    def document_catalogue(self) -> DocumentCatalogue:
        """Which documents exist and what audience each was ingested under.

        Since Phase 6 the tags on the chunks are the access control; this
        manifest is what decided them at ingestion, and what resolves a
        document's human-readable title for a citation.

        Raises:
            ConfigurationError: If the manifest is missing or malformed. At
                start-up, so a bad manifest is a boot failure rather than a
                search that quietly finds nothing.
        """
        path = self.settings.knowledge.manifest_path or default_catalogue_path()
        return DocumentCatalogue.from_file(path)

    @cached_property
    def knowledge(self) -> KnowledgeBase | None:
        """Scope-aware document retrieval, or ``None`` when it is switched off.

        Built lazily and by importing RAG's own composition root, so RAG stays
        configured by RAG: re-declaring its Qdrant URL, collection and embedding
        model here would create a second vocabulary for one deployment's
        settings, and the failure mode is two processes pointed at different
        collections while both look correct.

        Returns:
            The retrieval adapter, or ``None`` if ``ERP_KNOWLEDGE_ENABLED`` is
            false. ``None`` means the registry holds six tools instead of seven
            -- a coherent deployment where no index exists, and better than a
            document tool that fails on every call.

        Raises:
            ConfigurationError: If retrieval is enabled but RAG cannot be
                reached or built. Not swallowed into a degraded six-tool mode:
                a deployment that asked for document search and silently did not
                get it answers policy questions from the model's own memory.
        """
        if not self.settings.knowledge.enabled:
            return None

        # Imported here rather than at module scope so a deployment with
        # retrieval switched off does not need the vector store installed.
        from rag.core.bootstrap import bootstrap as rag_bootstrap

        from erp_agent.infrastructure.rag.retrieval_adapter import RagRetrievalAdapter

        try:
            rag = rag_bootstrap(env=self._rag_environment())
        except Exception as exc:
            raise ConfigurationError(
                # RAG's own message names the environment variable it wants,
                # which is the one thing an operator needs. Swallowing it into a
                # type name -- as this did until the evaluation harness was the
                # first thing to actually boot RAG in-process -- turns a
                # one-line fix into an afternoon.
                f"document retrieval is enabled but the RAG stack could not be built: {exc}. "
                f"Point ERP_RAG_ENV_FILE at RAG's .env, or set "
                f"ERP_KNOWLEDGE_ENABLED=false to serve structured questions only",
                context={"reason": type(exc).__name__},
            ) from exc

        return RagRetrievalAdapter(retriever=rag.retriever, catalogue=self.document_catalogue)

    def _rag_environment(self) -> dict[str, str] | None:
        """RAG's configuration, when it is not already in this process.

        Returns the parsed contents of the configured file, or ``None`` to let
        RAG read the process environment as it normally would. Parsed rather
        than re-declared: the agent does not know or care what is in there, it
        only knows where it is.
        """
        path = self.settings.knowledge.rag_env_file
        if path is None:
            return None

        from dotenv import dotenv_values

        return {key: value for key, value in dotenv_values(path).items() if value is not None}

    @cached_property
    def tool_registry(self) -> ToolRegistry:
        """The static tool allowlist, validated at construction.

        Seven tools when document retrieval is wired, six when it is not. The
        difference is visible in what the model is offered, which is the only
        honest way to express it: advertising a tool that cannot run invites
        the model to call it and then be told off for calling it.
        """
        knowledge = self.knowledge
        if knowledge is None:
            return build_hr_registry(self.hr_directory)
        return build_agent_registry(directory=self.hr_directory, knowledge=knowledge)

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
    def access_tokens(self) -> AccessTokenPort:
        """Authenticates a bearer credential.

        The packaged dev fixture is refused in production. It holds static,
        non-expiring credentials committed to the repository, and the one place
        that can tell it is being used unintentionally is here -- the fixture
        itself does not know what environment it is in.

        Raises:
            ConfigurationError: If the fixture is missing or malformed, or if
                the packaged dev fixture would be used in production.
        """
        configured = self.settings.security.access_token_fixture_path
        if configured is None and self.settings.app.env is AppEnv.PRODUCTION:
            raise ConfigurationError(
                "ERP_ACCESS_TOKEN_FIXTURE_PATH is unset, so the packaged dev "
                "credentials would be used; those are static, non-expiring and "
                "committed to the repository, and must not authenticate a "
                "production deployment",
                context={"variable": "ERP_ACCESS_TOKEN_FIXTURE_PATH"},
            )
        return FixtureAccessTokens.from_file(configured or default_token_fixture_path())

    @cached_property
    def conversations(self) -> ConversationStore:
        """Bounded conversation history, in this process."""
        api = self.settings.api
        return ConversationStore(
            max_turns=api.max_conversation_turns,
            max_conversations=api.max_conversations,
        )

    @cached_property
    def authenticate(self) -> Authenticate:
        """Turns a bearer credential into a resolved caller.

        ``manager_of`` is not wired: ``TEAM`` scope therefore narrows to the
        principal themselves rather than reaching their reports. That is the
        safe direction to be wrong in, and closing it means a recursive query
        over the reporting tree, which belongs with the Phase 9 work that
        replaces both fixtures with real joins.
        """
        return Authenticate(tokens=self.access_tokens, principals=self.principal_resolver)

    def answer_question(self, llm: LLMClient | None = None) -> AnswerErpQuestion:
        """Build the use case behind ``POST /ask``.

        Args:
            llm: Overrides the configured client. Tests pass a scripted one;
                nothing else should.

        Returns:
            The use case, wired to the loop and the conversation store.
        """
        return AnswerErpQuestion(loop=self.agent_loop(llm), conversations=self.conversations)

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

    async def warm(self) -> None:
        """Do the expensive lazy work now, not inside the first request.

        Only the retriever has any today: its embedding model loads on first
        use and takes long enough to exceed the loop's per-tool ceiling.

        Duck-typed rather than widened into the ``KnowledgeBase`` port, for the
        same reason the loop duck-types ``render_for_model``: warming is a
        property of an implementation that has something slow to load, not of
        what it means to be a knowledge base.
        """
        warm = getattr(self.knowledge, "warm", None)
        if callable(warm):
            await warm()

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
