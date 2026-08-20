"""Start-up: load configuration, validate it, and build the object graph.

Fail fast, and fail with an explanation. A misconfigured deployment stops here
rather than answering its first question through a connection nobody verified.

Checks grow with the system. Phase 1 validates the configuration, the log format
and -- the substance of Task 1.1 -- that the database connection is provably
incapable of writing. Later phases add credential and vector-store checks as
their adapters arrive.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import ValidationError

from erp_agent.config import LogFormat, Settings
from erp_agent.core.container import Container
from erp_agent.core.logging import configure_logging, get_logger
from erp_agent.domain.errors import ConfigurationError
from erp_agent.domain.models.diagnostics import DataSourceProbe

__all__ = ["bootstrap", "load_settings", "run_startup_checks", "verify_write_barrier"]

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
    return ("logging", "database-role")


def _check_logging(settings: Settings) -> None:
    """Verify production emits logs that can actually be queried."""
    if settings.app.is_production and settings.app.log_format is not LogFormat.JSON:
        raise ConfigurationError(
            "LOG_FORMAT must be 'json' when APP_ENV=production; console output "
            "cannot be queried by log aggregation",
            context={"variable": "LOG_FORMAT", "value": settings.app.log_format.value},
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

    checks = run_startup_checks(settings)
    container = Container(settings)
    await container.open()

    try:
        await verify_write_barrier(container)
    except BaseException:
        # A container that failed its safety check must not be left holding
        # connections, whatever the failure was.
        await container.close()
        raise

    _logger.info(
        "startup.checks_passed",
        checks=[*checks, "write-barrier"],
        app_env=settings.app.env.value,
        database=settings.database.safe_url,
    )
    return container
