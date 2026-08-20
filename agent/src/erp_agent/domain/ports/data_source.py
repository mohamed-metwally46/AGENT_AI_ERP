"""Port: read-only structured data access.

The interface has no write method. That is the design, not an omission awaiting a
later phase: barrier 3 of Plan.md section 8.2 is "no write code path exists", and
the cheapest way to guarantee that is to leave the verb out of the vocabulary.
Adding one would be a visible, reviewable change to this file rather than a quiet
call somewhere in an adapter.

Implementations translate driver exceptions into the
:mod:`erp_agent.domain.errors` family at their own boundary, so retry policy can
read ``retryable`` as data instead of matching on psycopg message strings.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from erp_agent.domain.models.diagnostics import DataSourceProbe
from erp_agent.domain.models.sql import QueryResult, Row, SqlStatement

__all__ = ["ReadOnlyDataSource"]


class ReadOnlyDataSource(ABC):
    """Executes bounded, parameterised reads against the ERP database.

    Every implementation must run each statement inside a read-only transaction
    with a statement timeout, and must apply the statement's row cap.
    """

    @abstractmethod
    async def fetch_all(self, statement: SqlStatement) -> QueryResult:
        """Execute a statement and return up to ``statement.max_rows`` rows.

        Args:
            statement: The validated, parameterised statement to run.

        Returns:
            The rows, flagged if the row cap truncated them.

        Raises:
            QueryTimeoutError: If the server aborted the statement on timeout.
            DataSourceUnavailableError: If the database could not be reached.
            DataSourceError: On any other read failure.
        """

    @abstractmethod
    async def fetch_one(self, statement: SqlStatement) -> Row | None:
        """Execute a statement expected to match at most one row.

        Args:
            statement: The validated, parameterised statement to run.

        Returns:
            The single row, or ``None`` when nothing matched. Returning ``None``
            rather than raising keeps "this employee has no balance on file" a
            answerable question instead of an error.

        Raises:
            QueryTimeoutError: If the server aborted the statement on timeout.
            DataSourceUnavailableError: If the database could not be reached.
            DataSourceError: If more than one row matched, or on any other read
                failure.
        """

    @abstractmethod
    async def probe(self) -> DataSourceProbe:
        """Verify the connection is what the configuration claims it is.

        Reports the connected role, the transaction mode and the outcome of a
        deliberate set of write attempts. Called at startup and by the
        diagnostics command.

        Returns:
            What the probe observed. The caller decides whether that is
            acceptable; the probe itself does not raise on a failed barrier, so
            diagnostics can print the whole picture rather than the first
            problem.

        Raises:
            DataSourceUnavailableError: If the database could not be reached.
        """
