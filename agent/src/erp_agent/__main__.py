"""Diagnostics entry point: ``python -m erp_agent``.

Reports what the process resolved. Until the API exists this is what "the
project runs" means, and it stays useful afterwards as the fastest way to answer
"is this deployment configured correctly, and is the write barrier intact?"
without starting a server.

Deliberately prints no secret. Holding the DSN as a ``SecretStr`` is defeated by
one diagnostics command that renders its password.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import TextIO

from erp_agent.config import Settings
from erp_agent.core.bootstrap import load_settings, run_startup_checks
from erp_agent.core.event_loop import run as run_async
from erp_agent.domain.errors import ConfigurationError, DataSourceError, ErpAgentError
from erp_agent.domain.models.diagnostics import DataSourceProbe
from erp_agent.domain.models.sql import SqlStatement

__all__ = ["main"]

_USAGE = """usage: python -m erp_agent [--check] [--list-settings]

Boots the ERP agent and reports the resolved configuration.

options:
  --check           Also connect to PostgreSQL, prove the write barrier by
                    attempting a write of every shape, and run a read. Exits
                    non-zero if the connection is not provably read-only.
  --list-settings   List every environment variable the system reads and exit.
                    Works even when the current configuration is invalid.
"""

# The acceptance criterion for Task 1.1 names this query specifically: it must
# return 60 through the read-only connection.
_CONNECTIVITY_READ = SqlStatement("SELECT count(*) AS employee_count FROM employees")


def _report(settings: Settings, checks: Sequence[str], stream: TextIO) -> None:
    """Print a human-readable summary of a successful boot."""
    database = settings.database
    lines = [
        "ERP AI Agent -- configuration resolved",
        "",
        f"  environment        {settings.app.env.value}",
        f"  log format         {settings.app.log_format.value}",
        f"  database           {database.safe_url}",
        f"  role               {database.parts.role}",
        f"  pool               {database.min_pool_size}..{database.max_pool_size} connections",
        f"  statement timeout  {database.statement_timeout_ms} ms",
        f"  row cap            {database.max_rows} rows",
        f"  principals         {_principal_source(settings)}",
        "",
        f"  startup checks     {', '.join(checks)} -- passed",
        "",
    ]
    stream.write("\n".join(lines))


def _principal_source(settings: Settings) -> str:
    """Describe where principals resolve from, without importing infrastructure early."""
    from erp_agent.infrastructure.security import default_fixture_path

    configured = settings.security.principal_fixture_path
    if configured is not None:
        return f"{configured} (configured)"
    return f"{default_fixture_path()} (packaged dev fixture)"


def _report_probe(probe: DataSourceProbe, employee_count: str, stream: TextIO) -> None:
    """Print what the live probe observed."""
    verdict = "INTACT" if probe.barrier_intact else "** BROKEN **"
    lines = [
        "  connectivity       ok",
        f"  server             PostgreSQL {probe.server_version}",
        f"  connected as       {probe.role} @ {probe.database}",
        f"  transaction mode   {'READ ONLY' if probe.transaction_read_only else 'READ WRITE'}",
        f"  statement timeout  {probe.statement_timeout}",
        f"  write privileges   {len(probe.write_privileges)} granted",
        f"  superuser          {'yes' if probe.is_superuser else 'no'}",
        f"  create in public   {'yes' if probe.can_create_in_public else 'no'}",
        # Display text, not a query: the statement itself is the module-level
        # constant above, built once and validated by SqlStatement.
        f"  read probe         SELECT count(*) FROM employees -> {employee_count}",  # noqa: S608
        "",
        f"  write barrier      {verdict}",
    ]
    lines.extend(
        f"    {'refused' if attempt.rejected else 'ACCEPTED'}  {attempt.label:<13} {attempt.detail}"
        for attempt in probe.write_attempts
    )
    if not probe.barrier_intact:
        lines.extend(f"    problem: {failure}" for failure in probe.failures)
    lines.append("")
    stream.write("\n".join(lines))


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    stream: TextIO | None = None,
) -> int:
    """Run the diagnostics command.

    Args:
        argv: Command-line arguments, excluding the program name.
        env: Explicit configuration values, overriding the process environment.
            Tests pass this instead of mutating the environment.
        stream: Where to write the report. Defaults to standard output.

    Returns:
        ``0`` when the agent boots and, with ``--check``, the write barrier is
        intact. ``1`` otherwise.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    out = stream if stream is not None else sys.stdout

    if "--help" in arguments or "-h" in arguments:
        out.write(_USAGE)
        return 0

    if "--list-settings" in arguments:
        # Deliberately before any validation: this is most useful precisely when
        # the configuration is broken.
        out.write("Environment variables read by this system:\n\n")
        for name in Settings.environment_variable_names():
            out.write(f"  {name}\n")
        return 0

    check = "--check" in arguments
    if check:
        arguments.remove("--check")

    if arguments:
        out.write(f"unrecognised arguments: {' '.join(arguments)}\n\n{_USAGE}")
        return 1

    try:
        settings = load_settings(env, use_env_file=env is None)
        checks = run_startup_checks(settings)
    except ConfigurationError as exc:
        out.write(f"configuration error\n\n  {exc.message}\n\n")
        return 1

    _report(settings, checks, out)

    if check:
        return run_async(_check_database(settings, out))
    return 0


async def _check_database(settings: Settings, out: TextIO) -> int:
    """Connect, prove the write barrier, and run one read.

    Kept out of the default path because it is the only thing here that touches
    the network, and a configuration check should stay usable when the database
    is down.
    """
    from erp_agent.core.container import Container

    container = Container(settings)
    try:
        await container.open()
    except ErpAgentError as exc:
        out.write(f"  connectivity       UNAVAILABLE\n\n  {exc.message}\n\n")
        return 1

    try:
        probe = await container.data_source.probe()

        try:
            row = await container.data_source.fetch_one(_CONNECTIVITY_READ)
            employee_count = "no rows" if row is None else str(row["employee_count"])
        except DataSourceError as exc:
            employee_count = f"unavailable ({exc.message})"

        _report_probe(probe, employee_count, out)
        return 0 if probe.barrier_intact else 1
    finally:
        await container.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
