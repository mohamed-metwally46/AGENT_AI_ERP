"""Configuration-time checks that do not need a network round trip.

Two jobs. Parse the database URL into its parts so the rest of the system can
talk about "the role" and "the database" without re-parsing a string, and refuse
the connection strings that are obviously wrong before anything opens a socket.

The role denylist here is a *static* check and is honestly limited: it catches
the mistake of pointing the agent at the schema owner, which is the realistic
accident, and it cannot catch a differently-named role that happens to hold
write privileges. The real check is the startup probe, which attempts writes and
requires them to fail (Plan.md section 8.2, barrier 1). Both run.

This package imports no other layer, so it raises :class:`ValueError` and lets
the composition root translate.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

__all__ = [
    "DEFAULT_POSTGRES_PORT",
    "WRITABLE_ROLE_DENYLIST",
    "DsnParts",
    "parse_postgres_dsn",
]

DEFAULT_POSTGRES_PORT = 5432

#: Roles that own or administer the ERP schema. Connecting the agent as one of
#: these would hand it write privileges no application-side check can revoke.
WRITABLE_ROLE_DENYLIST: frozenset[str] = frozenset(
    {
        "postgres",
        "hr_user",
        "rdsadmin",
        "root",
        "admin",
        "superuser",
    }
)

_ACCEPTED_SCHEMES = frozenset({"postgresql", "postgres"})


@dataclass(frozen=True, slots=True)
class DsnParts:
    """The addressable parts of a PostgreSQL URL.

    Attributes:
        scheme: URL scheme, normalised to lowercase.
        role: The login role. Named ``role`` rather than ``user`` because that
            is what PostgreSQL calls it and what the grants in
            ``10_readonly_role.sql`` name.
        host: Server host.
        port: Server port.
        database: Database name.
        has_password: Whether a password was supplied. The password itself is
            deliberately not retained -- nothing outside the driver needs it.
    """

    scheme: str
    role: str
    host: str
    port: int
    database: str
    has_password: bool

    def __str__(self) -> str:
        """Render for logs and diagnostics, without the password."""
        return f"{self.scheme}://{self.role}@{self.host}:{self.port}/{self.database}"


def parse_postgres_dsn(dsn: str) -> DsnParts:
    """Parse and sanity-check a PostgreSQL connection URL.

    Args:
        dsn: A URL such as ``postgresql://role:password@host:5432/database``.
            The keyword/value form psycopg also accepts (``host=... user=...``)
            is deliberately not supported: one format keeps the parsing, the
            diagnostics and the role check unambiguous.

    Returns:
        The parsed parts.

    Raises:
        ValueError: If the URL is malformed, omits the role or the database, or
            names a role known to hold write privileges.
    """
    if not dsn.strip():
        raise ValueError("a database URL is required; set ERP_DATABASE_URL")

    try:
        split = urlsplit(dsn)
    except ValueError as exc:  # pragma: no cover -- urlsplit is very permissive
        raise ValueError(f"database URL could not be parsed: {exc}") from exc

    scheme = split.scheme.lower()
    if scheme not in _ACCEPTED_SCHEMES:
        accepted = ", ".join(sorted(_ACCEPTED_SCHEMES))
        raise ValueError(
            f"database URL must use one of the schemes {accepted}; "
            f"got {split.scheme!r}. Expected postgresql://role@host:port/database"
        )

    role = unquote(split.username or "")
    if not role:
        raise ValueError(
            "database URL must name the login role, so the read-only role is "
            "explicit rather than inherited from the operating-system user"
        )

    if role.lower() in WRITABLE_ROLE_DENYLIST:
        raise ValueError(
            f"database URL names role {role!r}, which owns or administers the "
            f"ERP schema. The agent must connect as a read-only role such as "
            f"'mock_hr_readonly' (see database/schema/hr/10_readonly_role.sql)"
        )

    database = unquote(split.path).lstrip("/")
    if not database:
        raise ValueError("database URL must name the database, e.g. .../mock_hr")

    host = split.hostname or ""
    if not host:
        raise ValueError("database URL must name the host")

    try:
        port = split.port or DEFAULT_POSTGRES_PORT
    except ValueError as exc:
        raise ValueError(f"database URL has an invalid port: {exc}") from exc

    return DsnParts(
        scheme=scheme,
        role=role,
        host=host,
        port=port,
        database=database,
        has_password=bool(split.password),
    )
