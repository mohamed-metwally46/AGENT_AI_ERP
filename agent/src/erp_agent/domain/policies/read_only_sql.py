"""Write barrier 3: no write statement exists as a code path (Plan.md 8.2).

Barriers 1 and 2 live in PostgreSQL -- the role holds only ``SELECT`` and every
transaction opens ``READ ONLY``. This module is the application-side barrier
that runs *before* the driver is reached, and it exists because a barrier that
only fires at the far end of a network hop is a barrier that cannot be unit
tested.

The check is deliberately conservative and fails closed. It analyses statement
text after removing everything a keyword could hide behind -- comments, string
literals, dollar-quoted bodies and quoted identifiers -- then requires that:

1. exactly one statement is present,
2. it begins with ``SELECT`` or ``WITH``, and
3. no write, transaction-control, session-control or file-access keyword appears.

``SET`` is on the forbidden list for a specific, demonstrated reason.
``default_transaction_read_only`` is a ``USERSET`` GUC: a client that can issue
``SET default_transaction_read_only = off`` disables barrier 1's session half by
itself. Barrier 1's *privilege* half still holds, but the application must not be
the thing that tries.

False positives are acceptable here. Every statement in this system is authored
as a fixed template by a developer, never assembled from model output, so a
rejected legitimate query is a five-second fix while an accepted illegitimate one
is an incident.
"""

from __future__ import annotations

import re

from erp_agent.domain.errors import ReadOnlyViolationError

__all__ = [
    "FORBIDDEN_FUNCTIONS",
    "FORBIDDEN_KEYWORDS",
    "PERMITTED_LEADING_KEYWORDS",
    "assert_read_only",
    "is_read_only",
]

#: A statement must begin with one of these. ``EXPLAIN`` is absent on purpose:
#: ``EXPLAIN ANALYZE`` executes the statement it is explaining.
PERMITTED_LEADING_KEYWORDS: frozenset[str] = frozenset({"SELECT", "WITH"})

#: Keywords that may not appear anywhere in a statement.
#:
#: Grouped by why they are here, because a bare list invites deletion of the
#: entries whose danger is not obvious.
FORBIDDEN_KEYWORDS: frozenset[str] = frozenset(
    {
        # Data modification.
        "INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "COPY",
        # Schema modification.
        "CREATE", "ALTER", "DROP", "REINDEX", "CLUSTER", "COMMENT", "REFRESH",
        # Privilege modification.
        "GRANT", "REVOKE",
        # Row locks: `SELECT ... FOR UPDATE` / `FOR SHARE` write lock state.
        "SHARE",
        # Transaction control. The unit of work owns this, not the statement.
        "BEGIN", "START", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE",
        "PREPARE", "DEALLOCATE",
        # Session control -- notably SET, which can switch off the read-only
        # default the database applies at login.
        "SET", "RESET", "DISCARD", "LOAD",
        # Procedural execution.
        "DO", "CALL", "EXECUTE",
        # Server state and maintenance.
        "VACUUM", "ANALYZE", "CHECKPOINT", "LOCK", "LISTEN", "NOTIFY",
        "UNLISTEN", "IMPORT",
    }
)  # fmt: skip

#: Functions that read, but read something other than ERP rows. A read-only
#: transaction happily executes every one of these.
FORBIDDEN_FUNCTIONS: frozenset[str] = frozenset(
    {
        "PG_READ_FILE",
        "PG_READ_BINARY_FILE",
        "PG_LS_DIR",
        "PG_STAT_FILE",
        "LO_IMPORT",
        "LO_EXPORT",
        "DBLINK",
        "DBLINK_EXEC",
        "PG_TERMINATE_BACKEND",
        "PG_CANCEL_BACKEND",
        "PG_RELOAD_CONF",
        "PG_ROTATE_LOGFILE",
    }
)

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Matches `$$` and `$tag$`, but never a `$1` positional placeholder.
_DOLLAR_TAG = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def _strip_noise(sql: str) -> tuple[str, str | None]:
    """Blank out comments, string literals and quoted identifiers.

    A single character-wise pass rather than layered regexes, because the
    constructs nest and overlap: ``'--'`` is a literal, not a comment, and
    ``/* '"' */`` is a comment, not a quote. Getting that wrong in the permissive
    direction is how a keyword scanner is defeated.

    An *unterminated* construct is reported rather than tolerated. Swallowing
    everything after a stray quote would let ``SELECT 'x ; DROP TABLE employees``
    reduce to a bare ``SELECT`` and pass. PostgreSQL would reject that statement
    anyway, so this is not a live hole -- but a scanner whose safety depends on
    the next component also being strict is a scanner that fails the moment the
    next component changes.

    Args:
        sql: Raw statement text.

    Returns:
        ``(stripped, unterminated)`` where every quoted or commented span in
        ``stripped`` is a space, and ``unterminated`` names the construct that
        was never closed, or is ``None``.
    """
    out: list[str] = []
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]

        if char in "'\"":
            quote = char
            kind = "string literal" if quote == "'" else "quoted identifier"
            index += 1
            closed = False
            while index < length:
                if sql[index] == quote:
                    # A doubled quote is an escaped quote, not the end.
                    if index + 1 < length and sql[index + 1] == quote:
                        index += 2
                        continue
                    index += 1
                    closed = True
                    break
                index += 1
            if not closed:
                return "".join(out), kind
            out.append(" ")

        elif char == "-" and sql.startswith("--", index):
            while index < length and sql[index] != "\n":
                index += 1
            out.append(" ")

        elif char == "/" and sql.startswith("/*", index):
            depth = 1
            index += 2
            while index < length and depth > 0:
                if sql.startswith("/*", index):
                    depth += 1
                    index += 2
                elif sql.startswith("*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth > 0:
                return "".join(out), "block comment"
            out.append(" ")

        elif char == "$" and (match := _DOLLAR_TAG.match(sql, index)) is not None:
            tag = match.group(0)
            closing = sql.find(tag, match.end())
            if closing == -1:
                return "".join(out), "dollar-quoted block"
            index = closing + len(tag)
            out.append(" ")

        else:
            out.append(char)
            index += 1

    return "".join(out), None


def _reason_not_read_only(sql: str) -> str | None:
    """Return why a statement is refused, or ``None`` when it is acceptable."""
    stripped, unterminated = _strip_noise(sql)
    if unterminated is not None:
        return f"it contains an unterminated {unterminated}"

    body = stripped.strip()

    # One optional trailing semicolon is idiomatic; anything after it is a
    # second statement, which is how a read turns into a read plus a write.
    while body.endswith(";"):
        body = body[:-1].rstrip()
    if ";" in body:
        return "it contains more than one statement"

    tokens = [token.upper() for token in _WORD.findall(body)]
    if not tokens:
        return "it is empty once comments and literals are removed"

    if tokens[0] not in PERMITTED_LEADING_KEYWORDS:
        permitted = ", ".join(sorted(PERMITTED_LEADING_KEYWORDS))
        return f"it starts with {tokens[0]!r}; only {permitted} may begin a statement"

    seen = set(tokens)
    if forbidden := sorted(seen & FORBIDDEN_KEYWORDS):
        return f"it contains the forbidden keyword(s) {', '.join(forbidden)}"
    if functions := sorted(seen & FORBIDDEN_FUNCTIONS):
        return f"it calls the forbidden function(s) {', '.join(functions)}"

    return None


def is_read_only(sql: str) -> bool:
    """Whether a statement passes the read-only policy.

    Args:
        sql: Statement text.

    Returns:
        ``True`` if the statement is a single, purely-reading statement.
    """
    return _reason_not_read_only(sql) is None


def assert_read_only(sql: str) -> None:
    """Refuse a statement that is not a single, purely-reading statement.

    Args:
        sql: Statement text.

    Raises:
        ReadOnlyViolationError: If the statement could modify anything, control a
            transaction or session, or read outside the ERP tables. The rejected
            SQL is deliberately absent from the message and from the error
            context -- refusing a statement and then logging it verbatim defeats
            the refusal.
    """
    reason = _reason_not_read_only(sql)
    if reason is not None:
        raise ReadOnlyViolationError(f"statement refused by the read-only policy: {reason}")
