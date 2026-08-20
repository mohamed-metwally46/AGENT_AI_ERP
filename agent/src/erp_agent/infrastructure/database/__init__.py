"""Read-only PostgreSQL access and the guard that proves it is read-only."""

from __future__ import annotations

from erp_agent.infrastructure.database.connection import PostgresReadOnlyDataSource
from erp_agent.infrastructure.database.read_only_guard import (
    WRITE_PROBES,
    attempt_write_probes,
    read_privilege_audit,
    read_session_state,
)

__all__ = [
    "WRITE_PROBES",
    "PostgresReadOnlyDataSource",
    "attempt_write_probes",
    "read_privilege_audit",
    "read_session_state",
]
