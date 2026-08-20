"""Proving the write barrier, rather than asserting it.

Plan.md section 8.2 claims four independent write barriers. A claim in a document
is not a control. This module is what turns it into one: it interrogates a live
connection and reports what the database will actually permit.

Two independent questions are asked, because barrier 1 has two halves that fail
separately:

*Can the role write?* Answered from the catalogue --
:func:`read_privilege_audit` reads which table privileges the connected role
actually holds. This is true regardless of transaction mode, and it is the half
that survives a client issuing ``SET default_transaction_read_only = off``
(``default_transaction_read_only`` is a ``USERSET`` GUC, so a client can turn it
off; the grants are not negotiable).

*Does this session refuse writes?* Answered by trying --
:func:`attempt_write_probes` runs a deliberate write of each shape and records
the server's refusal.

Every probe runs inside a forced-rollback savepoint. If the barrier were broken
and a write succeeded, it would still be undone -- a security probe must not be
the thing that corrupts the data it was checking.
"""

from __future__ import annotations

import psycopg
from psycopg import AsyncConnection, sql
from psycopg.rows import DictRow

from erp_agent.domain.models.diagnostics import WriteAttempt

__all__ = [
    "WRITE_PROBES",
    "attempt_write_probes",
    "read_privilege_audit",
    "read_session_state",
]

#: The write attempts made at startup, one per shape of modification the system
#: must be incapable of (Plan.md section 1).
#:
#: Every DML probe carries ``WHERE false`` or an empty source, so it touches no
#: row. PostgreSQL checks table privileges and transaction read-only status
#: before it looks at whether any row matches, so an empty probe proves exactly
#: as much as a destructive one and risks nothing. It also means a NOT NULL or
#: foreign-key violation can never masquerade as a successful refusal.
WRITE_PROBES: tuple[tuple[str, str], ...] = (
    ("INSERT", "INSERT INTO employees (employee_id) SELECT NULL WHERE false"),
    ("UPDATE", "UPDATE employees SET employee_number = employee_number WHERE false"),
    ("DELETE", "DELETE FROM employees WHERE false"),
    ("CREATE TABLE", "CREATE TABLE erp_agent_write_probe (probe integer)"),
    ("ALTER TABLE", "ALTER TABLE employees ADD COLUMN erp_agent_write_probe integer"),
    ("TRUNCATE", "TRUNCATE employees"),
)

_SESSION_STATE = """
SELECT current_setting('server_version')          AS server_version,
       current_database()                         AS database,
       current_user                               AS role,
       current_setting('transaction_read_only')   AS transaction_read_only,
       current_setting('statement_timeout')       AS statement_timeout
"""

# Privileges held on real tables. `information_schema.table_privileges` reports
# what the role can exercise, including privileges reached through role
# membership, which is the question that matters -- not what a GRANT statement
# once said.
_WRITE_PRIVILEGES = """
SELECT table_name, privilege_type
  FROM information_schema.table_privileges
 WHERE grantee = current_user
   AND table_schema = 'public'
   AND privilege_type <> 'SELECT'
 ORDER BY table_name, privilege_type
 LIMIT 200
"""

_ROLE_ATTRIBUTES = """
SELECT rolsuper                                          AS is_superuser,
       has_schema_privilege(current_user, 'public', 'CREATE') AS can_create_in_public
  FROM pg_roles
 WHERE rolname = current_user
"""


def _server_reason(exc: psycopg.Error) -> str:
    """Extract the server's own one-line reason for refusing a statement.

    Prefers ``diag.message_primary`` over ``str(exc)`` because psycopg appends
    context and a statement position, and the reason itself is what
    distinguishes "permission denied for table employees" (the grants held) from
    "cannot execute INSERT in a read-only transaction" (the session held).
    """
    primary = exc.diag.message_primary
    if primary:
        sqlstate = exc.diag.sqlstate or "?????"
        return f"{sqlstate}: {primary}"
    return str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__


async def read_session_state(
    connection: AsyncConnection[DictRow],
) -> tuple[str, str, str, bool, str]:
    """Read the identity and transaction mode of a live connection.

    Args:
        connection: An open connection, inside its unit of work.

    Returns:
        ``(server_version, database, role, transaction_read_only, statement_timeout)``.
    """
    async with connection.cursor() as cursor:
        await cursor.execute(_SESSION_STATE)
        row = await cursor.fetchone()

    if row is None:  # pragma: no cover -- a one-row query with no FROM clause
        raise RuntimeError("session state query returned no row")

    return (
        str(row["server_version"]),
        str(row["database"]),
        str(row["role"]),
        str(row["transaction_read_only"]) == "on",
        str(row["statement_timeout"]),
    )


async def read_privilege_audit(
    connection: AsyncConnection[DictRow],
) -> tuple[tuple[str, ...], bool, bool]:
    """Read which write privileges the connected role actually holds.

    This is the check that cannot be talked out of. It asks the catalogue what
    the role may do, independently of how the current transaction happens to be
    configured.

    Args:
        connection: An open connection, inside its unit of work.

    Returns:
        ``(write_privileges, is_superuser, can_create_in_public)`` where each
        privilege is rendered ``table:PRIVILEGE``.
    """
    async with connection.cursor() as cursor:
        await cursor.execute(_WRITE_PRIVILEGES)
        privileges = tuple(
            f"{row['table_name']}:{row['privilege_type']}" for row in await cursor.fetchall()
        )

        await cursor.execute(_ROLE_ATTRIBUTES)
        attributes = await cursor.fetchone()

    # A role absent from pg_roles cannot happen for a connected session, but
    # defaulting to the *unsafe* answer would be the wrong way to be wrong.
    is_superuser = bool(attributes["is_superuser"]) if attributes else True
    can_create = bool(attributes["can_create_in_public"]) if attributes else True

    return privileges, is_superuser, can_create


async def attempt_write_probes(
    connection: AsyncConnection[DictRow],
) -> tuple[WriteAttempt, ...]:
    """Deliberately attempt each shape of write and record the refusal.

    Args:
        connection: An open connection, inside its unit of work.

    Returns:
        One :class:`WriteAttempt` per entry in :data:`WRITE_PROBES`, in order.
    """
    attempts: list[WriteAttempt] = []

    for label, statement in WRITE_PROBES:
        try:
            # force_rollback: the savepoint is discarded whatever happens. If a
            # barrier were broken and the write succeeded, this is what keeps
            # the probe harmless.
            async with connection.transaction(force_rollback=True):
                # Deliberately bypasses the SqlStatement read-only policy: these
                # statements are *supposed* to be writes. It is the only place
                # in the system that constructs one, and they are constants
                # defined above -- never assembled from input.
                await connection.execute(sql.SQL(statement))
        except psycopg.Error as exc:
            attempts.append(WriteAttempt(label=label, rejected=True, detail=_server_reason(exc)))
        else:
            attempts.append(
                WriteAttempt(
                    label=label,
                    rejected=False,
                    detail="the server accepted it; the write was rolled back by the probe",
                )
            )

    return tuple(attempts)
