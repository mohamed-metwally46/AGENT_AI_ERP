"""What a startup probe learned about the data source.

Reported by ``python -m erp_agent --check`` and asserted by the integration
suite. The write-attempt results are the substance: a claim that the connection
is read-only is worth nothing next to a list of writes that were actually tried
and actually refused.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["DataSourceProbe", "WriteAttempt"]


@dataclass(frozen=True, slots=True)
class WriteAttempt:
    """One deliberate write, and how the server answered it.

    Attributes:
        label: Short name of the attempted operation, e.g. ``INSERT``.
        rejected: Whether the server refused it. Anything but ``True`` is a
            failed security control.
        detail: The server's own reason, kept because *why* it was refused
            distinguishes the privilege barrier from the read-only-transaction
            barrier, and the two fail independently.
    """

    label: str
    rejected: bool
    detail: str

    def __post_init__(self) -> None:
        """Validate the attempt is described."""
        if not self.label.strip():
            raise ValueError("a write attempt must be labelled")


@dataclass(frozen=True, slots=True)
class DataSourceProbe:
    """The resolved identity and safety posture of a database connection.

    Attributes:
        server_version: PostgreSQL version string.
        database: Database actually connected to.
        role: Role actually connected as. Compared against configuration,
            because a DSN can say one thing and a connection pooler another.
        transaction_read_only: Whether the probing transaction was read-only.
        statement_timeout: The effective ``statement_timeout`` setting.
        write_attempts: Every write tried during the probe.
        write_privileges: Table privileges the role holds that are not
            ``SELECT``. Read from the catalogue rather than inferred, so
            barrier 1's *grants* are checked independently of barrier 2's
            read-only transaction. Both can fail on their own.
        is_superuser: Whether the role bypasses privilege checks entirely.
        can_create_in_public: Whether the role may create objects in ``public``.
    """

    server_version: str
    database: str
    role: str
    transaction_read_only: bool
    statement_timeout: str
    write_attempts: tuple[WriteAttempt, ...] = ()
    write_privileges: tuple[str, ...] = ()
    is_superuser: bool = False
    can_create_in_public: bool = False

    @property
    def barrier_intact(self) -> bool:
        """Whether the connection is provably incapable of writing.

        All four conditions are required, because they fail independently: a
        role can hold write grants while the session happens to be read-only,
        and a session can be writable while the grants happen to be absent.

        Requires at least one write attempt: a probe that tried nothing has
        proved nothing, and must not report success.
        """
        return (
            self.transaction_read_only
            and bool(self.write_attempts)
            and all(attempt.rejected for attempt in self.write_attempts)
            and not self.write_privileges
            and not self.is_superuser
            and not self.can_create_in_public
        )

    @property
    def failures(self) -> tuple[str, ...]:
        """Human-readable reasons the barrier is not intact. Empty when it is."""
        problems: list[str] = []
        if not self.transaction_read_only:
            problems.append("the session's transactions are not READ ONLY")
        if not self.write_attempts:
            problems.append("no write was attempted, so nothing was proved")
        problems.extend(f"the server accepted {label}" for label in self.accepted_writes)
        if self.write_privileges:
            shown = ", ".join(self.write_privileges[:5])
            more = (
                ""
                if len(self.write_privileges) <= 5
                else f" (+{len(self.write_privileges) - 5} more)"
            )
            problems.append(f"the role holds write privileges: {shown}{more}")
        if self.is_superuser:
            problems.append("the role is a superuser and bypasses every privilege check")
        if self.can_create_in_public:
            problems.append("the role may create objects in schema 'public'")
        return tuple(problems)

    @property
    def accepted_writes(self) -> tuple[str, ...]:
        """Labels of any write the server did *not* refuse."""
        return tuple(attempt.label for attempt in self.write_attempts if not attempt.rejected)
