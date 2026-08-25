"""Start-up: load configuration, validate it, and build the object graph.

Fail fast, and fail with an explanation. A misconfigured deployment stops here
rather than answering its first question through a connection nobody verified.

Checks grow with the system. Phase 1 validates the configuration, the log format
and -- the substance of Task 1.1 -- that the database connection is provably
incapable of writing. The mock identity work adds the one that refuses
development credentials in production, deliberately here rather than only in the
container: see :func:`_check_mock_credentials`.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from erp_agent.config import LogFormat, Settings
from erp_agent.core.container import Container
from erp_agent.core.logging import configure_logging, get_logger
from erp_agent.domain.errors import ConfigurationError
from erp_agent.domain.models.diagnostics import DataSourceProbe

__all__ = ["bootstrap", "load_settings", "run_startup_checks", "start", "verify_write_barrier"]

_logger = get_logger(__name__)


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    use_env_file: bool = True,
) -> Settings:
    """Load and validate configuration.

    :mod:`erp_agent.config` raises pydantic's
    :class:`~pydantic.ValidationError` because it imports no other layer.
    Translating it into a domain error is the composition root's job, so every
    entry point upstream can catch one thing.

    Args:
        env: Explicit variable values, overriding the process environment.
        use_env_file: Whether to also read ``.env``.

    Returns:
        Validated settings.

    Raises:
        ConfigurationError: If any value or combination is invalid. The original
            validation error is preserved as ``__cause__``.
    """
    try:
        return Settings.from_environment(env or {}, use_env_file=use_env_file)
    except ValidationError as exc:
        messages = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'settings'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigurationError(f"invalid configuration: {messages}") from exc


def run_startup_checks(settings: Settings) -> tuple[str, ...]:
    """Verify everything that can be verified without a network round trip.

    Args:
        settings: The loaded configuration.

    Returns:
        The names of the checks that passed, for logging.

    Raises:
        ConfigurationError: On the first failed check, naming the environment
            variable that needs attention.
    """
    _check_logging(settings)
    _check_database_role(settings)
    _check_mock_credentials(settings)
    return ("logging", "database-role", "mock-credentials")


def _check_logging(settings: Settings) -> None:
    """Verify production emits logs that can actually be queried."""
    if settings.app.is_production and settings.app.log_format is not LogFormat.JSON:
        raise ConfigurationError(
            "LOG_FORMAT must be 'json' when APP_ENV=production; console output "
            "cannot be queried by log aggregation",
            context={"variable": "LOG_FORMAT", "value": settings.app.log_format.value},
        )


def _check_mock_credentials(settings: Settings) -> None:
    """Refuse the packaged development identity files in production.

    :class:`~erp_agent.core.container.Container` refuses them too, when it
    builds the adapters. This check exists *as well* because those are
    ``cached_property`` and therefore only refuse when something touches them:
    a process that answered a health check before its first authenticated
    question would have started successfully with mock credentials loaded.
    Here the refusal is unconditional and happens at boot.

    Both files matter, and for different reasons. The roster grants
    organisation scope to a fixed identity; the token file is what lets someone
    present that identity. Overriding one and not the other is not enough.

    Args:
        settings: The loaded configuration.

    Raises:
        ConfigurationError: If ``APP_ENV=production`` and either fixture path is
            unset, so the packaged development file would apply by default.
    """
    if not settings.app.is_production:
        return

    for variable, path, what in (
        (
            "ERP_PRINCIPAL_FIXTURE_PATH",
            settings.security.principal_fixture_path,
            "the mock identity roster (it grants organisation scope to a fixed identity)",
        ),
        (
            "ERP_ACCESS_TOKEN_FIXTURE_PATH",
            settings.security.access_token_fixture_path,
            "the development bearer credentials (they are static and non-expiring)",
        ),
    ):
        if path is None:
            raise ConfigurationError(
                f"{variable} is unset, so {what} would be loaded from the copy "
                f"packaged with this application; development credentials must not "
                f"authorise a production deployment",
                context={"variable": variable},
            )


def _check_database_role(settings: Settings) -> None:
    """Verify the DSN names a role and a database, without connecting.

    The DSN was already parsed during settings validation, so this cannot
    normally fail. It is kept because it is the check whose *absence* would be
    invisible: a future refactor that drops the validator would otherwise
    silently remove the static half of the write barrier.
    """
    parts = settings.database.parts
    if not parts.has_password:
        _logger.warning(
            "startup.dsn_has_no_password",
            role=parts.role,
            hint="acceptable for a local mock database; not for a real deployment",
        )


async def verify_write_barrier(container: Container) -> DataSourceProbe:
    """Prove the database connection cannot write.

    Args:
        container: An **opened** container.

    Returns:
        What the probe observed.

    Raises:
        WriteBarrierError: If any part of the barrier is not intact.
        DataSourceUnavailableError: If the database is unreachable.
    """
    probe = await container.verify_write_barrier()
    _logger.info(
        "startup.write_barrier_verified",
        role=probe.role,
        database=probe.database,
        server_version=probe.server_version,
        writes_refused=[attempt.label for attempt in probe.write_attempts],
        write_privileges=len(probe.write_privileges),
    )
    return probe


async def start(container: Container) -> Container:
    """Run the static checks, open the container, and prove the write barrier.

    Split out of :func:`bootstrap` so a caller that already has a container --
    the API's lifespan, which must open the pool on the loop that will serve
    requests -- goes through exactly the same start-up as everything else. Two
    start-up paths would eventually differ, and the difference that matters is
    whether the barrier was checked.

    Args:
        container: A constructed, not-yet-opened container.

    Returns:
        The same container, opened and verified.

    Raises:
        ConfigurationError: If a static check fails.
        WriteBarrierError: If the connection is not provably read-only.
        DataSourceUnavailableError: If the database is unreachable.
    """
    checks = run_startup_checks(container.settings)
    await container.open()

    try:
        await verify_write_barrier(container)
    except BaseException:
        # A container that failed its safety check must not be left holding
        # connections, whatever the failure was.
        await container.close()
        raise

    await container.warm()

    _logger.info(
        "startup.checks_passed",
        checks=[*checks, "write-barrier"],
        app_env=container.settings.app.env.value,
        database=container.settings.database.safe_url,
    )
    return container


async def bootstrap(
    env: Mapping[str, str] | None = None,
    *,
    use_env_file: bool = True,
) -> Container:
    """Perform full application start-up.

    Loads configuration, configures logging, runs the static checks, opens the
    container and verifies the write barrier. Every entry point calls this and
    nothing else, so they cannot drift apart in how they start -- and in
    particular, no entry point can skip the barrier check.

    Args:
        env: Explicit variable values, overriding the process environment.
        use_env_file: Whether to also read ``.env``.

    Returns:
        An opened container whose write barrier has been verified.

    Raises:
        ConfigurationError: If configuration is invalid or a static check fails.
        WriteBarrierError: If the connection is not provably read-only.
        DataSourceUnavailableError: If the database is unreachable.
    """
    settings = load_settings(env, use_env_file=use_env_file)
    configure_logging(settings)
    return await start(Container(settings))
