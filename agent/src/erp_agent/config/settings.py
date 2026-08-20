"""Typed application configuration.

Follows the RAG package's design exactly, because one configuration idiom across
the repository is worth more than a marginally better second one: section objects
for grouped access, flat uppercase environment variable names as the deployment
contract, no ``os.getenv`` anywhere outside this package, and validation that
fails at startup rather than at first use.

Environment variables are prefixed ``ERP_`` where they name something this system
owns, so an agent process and a RAG process can share a shell without one
reading the other's ``DATABASE_URL``. The process-level names (``APP_ENV``,
``LOG_LEVEL``, ``LOG_FORMAT``) are intentionally shared -- one deployment, one
environment, one log format.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from erp_agent.config.enums import AppEnv, LLMProvider, LogFormat
from erp_agent.config.validation import DsnParts, parse_postgres_dsn

__all__ = [
    "AgentSettings",
    "ApiSettings",
    "AppSettings",
    "DatabaseSettings",
    "KnowledgeSettings",
    "LLMSettings",
    "SecuritySettings",
    "Settings",
]

_ENV_FILE = ".env"


class _Section(BaseSettings):
    """Base for a configuration section.

    ``case_sensitive`` is on and every field declares an explicit uppercase
    alias, so the environment variable names are exactly the deployment contract
    rather than whatever a field name happens to produce.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
        populate_by_name=True,
        protected_namespaces=(),
    )


class AppSettings(_Section):
    """Process-level settings.

    Attributes:
        env: Deployment environment.
        log_level: Standard logging level name.
        log_format: ``console`` for local work, ``json`` anywhere real.
    """

    env: AppEnv = Field(default=AppEnv.LOCAL, validation_alias="APP_ENV")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    log_format: LogFormat = Field(default=LogFormat.CONSOLE, validation_alias="LOG_FORMAT")

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        """Reject a log level name the logging module would not understand."""
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = value.upper()
        if upper not in allowed:
            raise ValueError(f"LOG_LEVEL must be one of {sorted(allowed)}, got {value!r}")
        return upper

    @property
    def is_production(self) -> bool:
        """Whether relaxed, developer-only behaviour must be refused."""
        return self.env is AppEnv.PRODUCTION


class DatabaseSettings(_Section):
    """Read-only connection to the ERP database.

    The DSN is a :class:`~pydantic.SecretStr` because it carries a password, and
    a settings object that renders its own password in a traceback has undone
    the reason for holding it separately.

    Attributes:
        dsn: PostgreSQL URL. The role it names must be read-only; a role known
            to own the schema is refused here, and any role that can actually
            write is refused by the startup probe.
        min_pool_size: Connections opened eagerly.
        max_pool_size: Ceiling on concurrent connections.
        connect_timeout_s: How long to wait for a connection before failing.
        statement_timeout_ms: Server-side ceiling on a single statement,
            applied per unit of work. Complements the role-level default so a
            pooler that resets role settings cannot silently remove it.
        max_rows: Default row cap for a query that does not set its own.
        application_name: Reported in ``pg_stat_activity``, so a DBA looking at
            a running query can tell who is asking.
    """

    dsn: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="ERP_DATABASE_URL",
    )
    min_pool_size: int = Field(default=1, ge=0, le=50, validation_alias="ERP_DB_MIN_POOL_SIZE")
    max_pool_size: int = Field(default=5, ge=1, le=100, validation_alias="ERP_DB_MAX_POOL_SIZE")
    connect_timeout_s: float = Field(
        default=10.0, gt=0.0, le=120.0, validation_alias="ERP_DB_CONNECT_TIMEOUT_S"
    )
    # 15s matches TOOL_TIMEOUT_SECONDS in Plan.md section 5.3.
    statement_timeout_ms: int = Field(
        default=15_000, ge=100, le=120_000, validation_alias="ERP_DB_STATEMENT_TIMEOUT_MS"
    )
    # 100 matches MAX_ROWS_PER_TOOL_RESULT in Plan.md section 5.3.
    max_rows: int = Field(default=100, ge=1, le=10_000, validation_alias="ERP_DB_MAX_ROWS")
    application_name: str = Field(
        default="erp-agent", min_length=1, max_length=63, validation_alias="ERP_DB_APPLICATION_NAME"
    )

    @model_validator(mode="after")
    def _dsn_is_a_usable_read_only_url(self) -> Self:
        """Reject a missing, malformed or schema-owning DSN at startup."""
        # Parsing here, in a validator, is what makes "no DATABASE_URL" a
        # startup failure with an actionable message instead of a psycopg
        # error on the first question a user asks.
        parse_postgres_dsn(self.dsn.get_secret_value())
        return self

    @model_validator(mode="after")
    def _pool_bounds_are_coherent(self) -> Self:
        """Reject a minimum pool size larger than the maximum."""
        if self.min_pool_size > self.max_pool_size:
            raise ValueError(
                f"ERP_DB_MIN_POOL_SIZE ({self.min_pool_size}) must not exceed "
                f"ERP_DB_MAX_POOL_SIZE ({self.max_pool_size})"
            )
        return self

    @property
    def parts(self) -> DsnParts:
        """The DSN's addressable parts, without the password."""
        return parse_postgres_dsn(self.dsn.get_secret_value())

    @property
    def safe_url(self) -> str:
        """The DSN rendered for logs and diagnostics, password removed."""
        return str(self.parts)


class SecuritySettings(_Section):
    """How an authenticated identity is resolved to a principal.

    Attributes:
        principal_fixture_path: YAML fixture standing in for the Platform
            ``users`` / ``user_roles`` tables, which ``mock_hr`` does not have
            (Plan.md section 8.5, gap S6). ``None`` means "use the fixture
            packaged with the application"; the composition root resolves that,
            because this package imports no other layer and cannot know where an
            infrastructure file lives.
    """

    principal_fixture_path: Path | None = Field(
        default=None, validation_alias="ERP_PRINCIPAL_FIXTURE_PATH"
    )
    access_token_fixture_path: Path | None = Field(
        default=None, validation_alias="ERP_ACCESS_TOKEN_FIXTURE_PATH"
    )

    @model_validator(mode="after")
    def _configured_fixtures_must_exist(self) -> Self:
        """Reject a configured fixture path that is not a readable file.

        Checked here so a typo in the path fails at start-up rather than as a
        failed authorization on the first question somebody asks.
        """
        for variable, path in (
            ("ERP_PRINCIPAL_FIXTURE_PATH", self.principal_fixture_path),
            ("ERP_ACCESS_TOKEN_FIXTURE_PATH", self.access_token_fixture_path),
        ):
            if path is not None and not path.is_file():
                raise ValueError(f"{variable} points at {path}, which is not a readable file")
        return self


class LLMSettings(_Section):
    """Answer generation.

    Attributes:
        provider: Which adapter to wire. Only ``openrouter`` implements tool
            calling today (Plan.md gap G6): ``LLMClient.respond`` has a base
            implementation that raises, and the Gemini adapter does not override
            it. Selecting Gemini is refused at start-up rather than failing on
            the first question that needs a tool.
        model: Provider-specific model identifier. A free string on purpose --
            a provider's catalogue changes weekly, so the deployment names the
            model and the code never does.
        openrouter_base_url: OpenRouter's OpenAI-compatible endpoint.
        api_key: Credential for the selected provider.
        temperature: Sampling temperature. 0.0 keeps tool selection reproducible,
            which the Phase 8 golden set depends on.
        max_output_tokens: Ceiling on generated tokens.
        timeout_s: How long to wait for the provider.
    """

    provider: LLMProvider = Field(
        default=LLMProvider.OPENROUTER, validation_alias="ERP_LLM_PROVIDER"
    )
    model: str = Field(default="qwen/qwen3-8b", min_length=1, validation_alias="ERP_LLM_MODEL")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1", validation_alias="OPENROUTER_BASE_URL"
    )
    api_key: SecretStr | None = Field(default=None, validation_alias="ERP_LLM_API_KEY")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, validation_alias="ERP_LLM_TEMPERATURE")
    max_output_tokens: int = Field(
        default=2048, ge=1, le=32_000, validation_alias="ERP_LLM_MAX_OUTPUT_TOKENS"
    )
    timeout_s: float = Field(default=60.0, gt=0.0, le=600.0, validation_alias="ERP_LLM_TIMEOUT_S")

    @model_validator(mode="after")
    def _provider_must_support_tool_calling(self) -> Self:
        """Refuse a provider that cannot call tools.

        The agent is nothing but a tool loop. A provider whose adapter raises on
        `respond` produces a process that starts cleanly and fails on the first
        real question -- the worst possible place to discover it.
        """
        if self.provider is not LLMProvider.OPENROUTER:
            raise ValueError(
                f"ERP_LLM_PROVIDER={self.provider.value} does not implement tool "
                f"calling in the RAG adapter it reuses; only 'openrouter' does. "
                f"See Plan.md section 2.5, gap G6"
            )
        return self


class AgentSettings(_Section):
    """Hard bounds on the agent loop (Plan.md section 5.3).

    Ceilings, not suggestions. Exceeding one raises rather than truncating.
    """

    max_rounds: int = Field(default=3, ge=1, le=10, validation_alias="MAX_TOOL_ROUNDS")
    max_calls_per_round: int = Field(
        default=3, ge=1, le=10, validation_alias="MAX_TOOL_CALLS_PER_ROUND"
    )
    max_total_calls: int = Field(default=6, ge=1, le=30, validation_alias="MAX_TOTAL_TOOL_CALLS")
    tool_timeout_s: float = Field(
        default=15.0, gt=0.0, le=120.0, validation_alias="TOOL_TIMEOUT_SECONDS"
    )
    max_result_tokens: int = Field(
        default=4000, ge=100, le=32_000, validation_alias="MAX_TOOL_RESULT_TOKENS"
    )

    @model_validator(mode="after")
    def _bounds_are_coherent(self) -> Self:
        """Reject a per-round ceiling above the total ceiling."""
        if self.max_calls_per_round > self.max_total_calls:
            raise ValueError(
                f"MAX_TOOL_CALLS_PER_ROUND ({self.max_calls_per_round}) must not "
                f"exceed MAX_TOTAL_TOOL_CALLS ({self.max_total_calls})"
            )
        return self

    @property
    def max_result_chars(self) -> int:
        """The token ceiling, in characters."""
        return self.max_result_tokens * 4


class KnowledgeSettings(_Section):
    """Document retrieval.

    Deliberately thin. RAG owns its own configuration -- Qdrant URL, collection,
    embedding provider and model -- and re-declaring those here would create a
    second vocabulary for one deployment's settings, which is how two processes
    end up pointed at different collections while both look correctly
    configured. RAG reads them from the process environment; this section says
    only whether to wire retrieval at all and which manifest names the corpus.

    Attributes:
        enabled: Whether to build the document tool. ``False`` serves structured
            questions only, which is a coherent deployment when no index exists
            -- and better than a seventh tool that fails on every call.
        manifest_path: The document manifest. ``None`` means the packaged dev
            manifest; the composition root resolves that, because config imports
            no other layer and cannot know where an infrastructure file lives.
        module: Functional area stamped at ingestion, recorded here so the
            deployment that ingests and the deployment that reads agree.
    """

    enabled: bool = Field(default=True, validation_alias="ERP_KNOWLEDGE_ENABLED")
    manifest_path: Path | None = Field(default=None, validation_alias="ERP_DOCUMENT_MANIFEST_PATH")
    module: str | None = Field(default=None, validation_alias="ERP_DOCUMENT_MODULE")

    @model_validator(mode="after")
    def _configured_manifest_must_exist(self) -> Self:
        """Reject a configured manifest path that is not a readable file."""
        path = self.manifest_path
        if path is not None and not path.is_file():
            raise ValueError(
                f"ERP_DOCUMENT_MANIFEST_PATH points at {path}, which is not a readable file"
            )
        return self


class ApiSettings(_Section):
    """The HTTP surface (Plan.md section 11, Phase 7).

    Attributes:
        host: Interface to bind. Loopback by default: a service that binds
            every interface the moment someone runs it is a service that gets
            exposed by accident.
        port: Port to bind.
        request_timeout_s: Ceiling on one request, above the agent's own bounds
            so a hung provider cannot hold a connection open indefinitely.
        rate_limit_per_minute: Requests one principal may make per minute.
            Per principal rather than per IP: the thing being protected is the
            data, and Plan.md R6 is about many small allowed queries
            reconstructing a restricted whole, which one authenticated caller
            behind one address can do perfectly well.
        rate_limit_burst: Requests allowed to arrive at once before the per
            minute rate applies.
        max_question_chars: Ceiling on a question. A prompt-sized question is a
            way to spend a context window and to bury an injection in the
            middle of it.
        max_conversation_turns: Turns retained per conversation.
        max_conversations: Conversations retained in the process.
        docs_enabled: Whether to publish the interactive docs and the OpenAPI
            schema. On by default -- Phase 7 acceptance criterion 3 requires the
            schema -- and separately switchable because a public deployment may
            not want to advertise its surface.
    """

    host: str = Field(default="127.0.0.1", min_length=1, validation_alias="ERP_API_HOST")
    port: int = Field(default=8000, ge=1, le=65_535, validation_alias="ERP_API_PORT")
    request_timeout_s: float = Field(
        default=90.0, gt=0.0, le=600.0, validation_alias="ERP_API_REQUEST_TIMEOUT_S"
    )
    rate_limit_per_minute: int = Field(
        default=30, ge=1, le=10_000, validation_alias="ERP_API_RATE_LIMIT_PER_MINUTE"
    )
    rate_limit_burst: int = Field(
        default=5, ge=1, le=1_000, validation_alias="ERP_API_RATE_LIMIT_BURST"
    )
    max_question_chars: int = Field(
        default=2_000, ge=1, le=32_000, validation_alias="ERP_API_MAX_QUESTION_CHARS"
    )
    max_conversation_turns: int = Field(
        default=6, ge=1, le=100, validation_alias="ERP_API_MAX_CONVERSATION_TURNS"
    )
    max_conversations: int = Field(
        default=500, ge=1, le=100_000, validation_alias="ERP_API_MAX_CONVERSATIONS"
    )
    docs_enabled: bool = Field(default=True, validation_alias="ERP_API_DOCS_ENABLED")


_SECTION_TYPES: dict[str, type[_Section]] = {
    "app": AppSettings,
    "database": DatabaseSettings,
    "security": SecuritySettings,
    "llm": LLMSettings,
    "agent": AgentSettings,
    "knowledge": KnowledgeSettings,
    "api": ApiSettings,
}


def _aliases_of(section: type[_Section]) -> Iterator[tuple[str, str]]:
    """Yield ``(environment variable, field name)`` pairs for a section."""
    for field_name, field_info in section.model_fields.items():
        alias = field_info.validation_alias
        if isinstance(alias, str):
            yield alias, field_name


class Settings(BaseModel):
    """The complete, validated configuration of a running process.

    Constructing it *is* the validation: an invalid combination raises
    immediately, which is the point of failing at startup rather than at first
    use.

    Attributes:
        app: Process-level settings.
        database: Read-only ERP database connection settings.
        security: Principal resolution settings.
        llm: Answer generation settings.
        agent: Bounds on the agent loop.
        knowledge: Document retrieval settings.
        api: The HTTP surface.
    """

    model_config = {"frozen": True}

    app: AppSettings
    database: DatabaseSettings
    security: SecuritySettings
    llm: LLMSettings
    agent: AgentSettings
    knowledge: KnowledgeSettings
    api: ApiSettings

    @classmethod
    def load(cls) -> Self:
        """Build settings from the process environment and ``.env``.

        Returns:
            Validated settings.

        Raises:
            pydantic.ValidationError: If any value or combination is invalid.
                The composition root translates this into a
                ``ConfigurationError``; config itself raises no domain type
                because it imports no other layer.
        """
        return cls.from_environment({}, use_env_file=True)

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str],
        *,
        use_env_file: bool = True,
    ) -> Self:
        """Build settings, overriding the process environment with ``env``.

        Args:
            env: Explicit variable values, keyed by environment variable name.
            use_env_file: Whether to also read the ``.env`` file. Tests pass
                ``False`` so a developer's local file cannot change a result.

        Returns:
            Validated settings.

        Raises:
            pydantic.ValidationError: If any value or combination is invalid.
        """
        env_file = _ENV_FILE if use_env_file else None
        sections: dict[str, Any] = {}
        for attribute, section_type in _SECTION_TYPES.items():
            overrides = {
                field_name: env[alias]
                for alias, field_name in _aliases_of(section_type)
                if alias in env
            }
            # `_env_file` is a pydantic-settings init hook, not a declared
            # field, so it is invisible to the type checker.
            sections[attribute] = section_type(_env_file=env_file, **overrides)  # type: ignore[call-arg]
        return cls(**sections)

    @classmethod
    def environment_variable_names(cls) -> tuple[str, ...]:
        """Every environment variable this configuration reads.

        Useful for documentation, for startup diagnostics, and for tests that
        need to isolate themselves from a developer's shell.

        Returns:
            Variable names, sorted.
        """
        names = {alias for section in _SECTION_TYPES.values() for alias, _ in _aliases_of(section)}
        return tuple(sorted(names))
