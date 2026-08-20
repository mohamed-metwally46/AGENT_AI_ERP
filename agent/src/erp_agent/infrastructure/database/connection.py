"""Barrier 2: every unit of work is a read-only transaction.

A pooled PostgreSQL adapter for :class:`ReadOnlyDataSource`. Three properties are
deliberate and load-bearing:

*Read-only is set on the connection, not requested per query.* The pool's
``configure`` hook sets ``read_only`` once, so psycopg emits ``BEGIN READ ONLY``
for every transaction on every connection. There is no code path that opens a
writable transaction, so none can be reached by forgetting a flag.

*The statement timeout is re-applied per unit of work.* The role already carries
one (``10_readonly_role.sql``), but a connection pooler in front of PostgreSQL
can reset role-level settings. ``SET LOCAL`` inside the transaction is the half
that survives that.

*One row past the cap is fetched.* That is how truncation is detected without a
second ``count(*)``: if the extra row exists, rows were discarded, and the result
says so rather than letting a partial set be reported as complete.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Self

import psycopg
from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout

from erp_agent.domain.errors import (
    DataSourceError,
    DataSourceUnavailableError,
    QueryTimeoutError,
)
from erp_agent.domain.models.diagnostics import DataSourceProbe
from erp_agent.domain.models.sql import QueryResult, Row, SqlStatement
from erp_agent.domain.ports.data_source import ReadOnlyDataSource
from erp_agent.infrastructure.database.read_only_guard import (
    attempt_write_probes,
    read_privilege_audit,
    read_session_state,
)

__all__ = ["PostgresReadOnlyDataSource"]

_POOL_NAME = "erp-agent-readonly"


class PostgresReadOnlyDataSource(ReadOnlyDataSource):
    """A pooled, read-only PostgreSQL adapter.

    Construct it, ``await open()`` before use and ``await close()`` on shutdown,
    or use it as an async context manager. The pool is created eagerly but not
    opened, so building the object graph does not require a running event loop
    or a reachable database.
    """

    def __init__(
        self,
        *,
        dsn: str,
        min_pool_size: int = 1,
        max_pool_size: int = 5,
        connect_timeout_s: float = 10.0,
        statement_timeout_ms: int = 15_000,
        default_max_rows: int = 100,
        application_name: str = "erp-agent",
    ) -> None:
        """Build the adapter.

        Args:
            dsn: PostgreSQL connection URL naming a read-only role.
            min_pool_size: Connections opened eagerly.
            max_pool_size: Ceiling on concurrent connections.
            connect_timeout_s: How long to wait for a connection.
            statement_timeout_ms: Server-side ceiling per statement.
            default_max_rows: Row cap for statements that do not set their own.
            application_name: Reported in ``pg_stat_activity``.
        """
        self._statement_timeout_ms = statement_timeout_ms
        self._default_max_rows = default_max_rows
        self._connect_timeout_s = connect_timeout_s
        self._pool: AsyncConnectionPool[AsyncConnection[DictRow]] = AsyncConnectionPool(
            conninfo=dsn,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=connect_timeout_s,
            name=_POOL_NAME,
            # Not opened here: psycopg_pool warns when a pool is opened without a
            # running event loop, and the composition root must be constructible
            # in a synchronous context.
            open=False,
            configure=self._configure,
            kwargs={
                "row_factory": dict_row,
                "application_name": application_name,
                "connect_timeout": max(1, int(connect_timeout_s)),
            },
        )

    # ------------------------------------------------------------- lifecycle #
    @staticmethod
    async def _configure(connection: AsyncConnection[DictRow]) -> None:
        """Make every transaction on this connection read-only.

        Runs once per pooled connection. ``autocommit`` stays off so psycopg
        opens an explicit transaction, which is what gives ``BEGIN READ ONLY``
        something to attach to.
        """
        await connection.set_autocommit(False)
        await connection.set_read_only(True)

    async def open(self) -> None:
        """Open the pool and wait for the first connections.

        Raises:
            DataSourceUnavailableError: If the database is unreachable or
                refuses the credentials.
        """
        try:
            await self._pool.open(wait=True, timeout=self._connect_timeout_s)
        except (PoolTimeout, psycopg.OperationalError) as exc:
            raise DataSourceUnavailableError(
                "the ERP database could not be reached",
                context={"reason": _reason(exc)},
            ) from exc

    async def close(self) -> None:
        """Close the pool and release every connection."""
        await self._pool.close()

    async def __aenter__(self) -> Self:
        """Open the pool on entry."""
        await self.open()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the pool on exit."""
        await self.close()

    # --------------------------------------------------------- unit of work #
    @asynccontextmanager
    async def _unit_of_work(self) -> AsyncIterator[AsyncConnection[DictRow]]:
        """Check out a connection inside a read-only, time-bounded transaction."""
        try:
            async with self._pool.connection() as connection:
                # The first statement opens the transaction, so SET LOCAL lands
                # inside it and expires with it. Composed rather than formatted:
                # the value is a validated int, and Literal keeps it that way.
                await connection.execute(
                    sql.SQL("SET LOCAL statement_timeout = {}").format(
                        sql.Literal(self._statement_timeout_ms)
                    )
                )
                yield connection
        except PoolTimeout as exc:
            raise DataSourceUnavailableError(
                "no database connection became available within the timeout",
                context={"reason": _reason(exc)},
            ) from exc
        except psycopg.OperationalError as exc:
            raise DataSourceUnavailableError(
                "the ERP database connection failed",
                context={"reason": _reason(exc)},
            ) from exc

    # ------------------------------------------------------------------ read #
    async def fetch_all(self, statement: SqlStatement) -> QueryResult:
        """Execute a statement and return up to ``statement.max_rows`` rows."""
        cap = min(statement.max_rows, self._default_max_rows)
        started = time.perf_counter()

        async with self._unit_of_work() as connection, connection.cursor() as cursor:
            try:
                await cursor.execute(statement.text, dict(statement.parameters))
                # One past the cap: its presence is the truncation signal.
                fetched = await cursor.fetchmany(cap + 1)
            except psycopg.Error as exc:
                raise _translate(exc, statement) from exc

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        truncated = len(fetched) > cap
        rows: tuple[Row, ...] = tuple(dict(row) for row in fetched[:cap])
        return QueryResult(rows=rows, truncated=truncated, duration_ms=elapsed_ms)

    async def fetch_one(self, statement: SqlStatement) -> Row | None:
        """Execute a statement expected to match at most one row."""
        result = await self.fetch_all(statement)
        if result.row_count > 1:
            raise DataSourceError(
                "a single-row query matched more than one row",
                context={
                    "row_count": result.row_count,
                    "parameters": statement.parameter_names,
                },
            )
        return result.rows[0] if result.rows else None

    # ----------------------------------------------------------------- probe #
    async def probe(self) -> DataSourceProbe:
        """Verify the connection is provably incapable of writing."""
        async with self._unit_of_work() as connection:
            version, database, role, read_only, timeout = await read_session_state(connection)
            privileges, is_superuser, can_create = await read_privilege_audit(connection)
            attempts = await attempt_write_probes(connection)

        return DataSourceProbe(
            server_version=version,
            database=database,
            role=role,
            transaction_read_only=read_only,
            statement_timeout=timeout,
            write_attempts=attempts,
            write_privileges=privileges,
            is_superuser=is_superuser,
            can_create_in_public=can_create,
        )


def _reason(exc: BaseException) -> str:
    """Render an exception as one short line, without a stack trace."""
    text = str(exc).strip()
    return text.splitlines()[0] if text else type(exc).__name__


def _translate(exc: psycopg.Error, statement: SqlStatement) -> DataSourceError:
    """Map a psycopg error onto the domain error family.

    The statement text is never included. Its parameter *names* are, because
    knowing which query failed is what makes a log actionable, while the values
    are exactly what must not be logged (Plan.md section 20).
    """
    context = {"parameters": statement.parameter_names, "sqlstate": exc.diag.sqlstate or ""}
    detail = exc.diag.message_primary or _reason(exc)

    if isinstance(exc, psycopg.errors.QueryCanceled):
        return QueryTimeoutError(
            "the query exceeded its statement timeout and was cancelled",
            context=context,
        )
    if isinstance(exc, psycopg.OperationalError):
        return DataSourceUnavailableError(
            "the ERP database connection failed while reading",
            context=context,
        )
    return DataSourceError(f"the query failed: {detail}", context=context)
