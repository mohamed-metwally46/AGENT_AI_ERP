"""Validating what a tool was asked for, before anything is read.

Tool arguments ultimately come from model output, so they are untrusted input in
the ordinary sense: they may be missing, malformed, hostile, or simply wrong.
Every one is validated here and converted to a typed value before it reaches the
directory port.

Validation refuses rather than repairs. A blank employee number is not silently
turned into "all employees"; an out-of-range month is not clamped to December. A
tool that quietly widens what it was asked for is a tool whose answers cannot be
reproduced, and in this system it would also be a tool that quietly widens what
it *returns*.

None of this is a security control -- the scope predicate is, and it is applied
whatever these arguments say. This layer exists so that a malformed call
produces a clear, deterministic error instead of a confusing empty result.
"""

from __future__ import annotations

import re
from datetime import date

from erp_agent.domain.errors import ToolArgumentError

__all__ = [
    "MAX_NAME_LENGTH",
    "optional_date",
    "optional_int",
    "optional_text",
    "required_employee_number",
    "required_text",
]

#: Long enough for any real name, short enough that a megabyte of model output
#: cannot become a LIKE pattern.
MAX_NAME_LENGTH = 120

#: Employee numbers are a controlled business key, not free text.
_EMPLOYEE_NUMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,31}$")


def required_text(value: object, *, field: str, max_length: int = MAX_NAME_LENGTH) -> str:
    """Validate a required string argument.

    Args:
        value: The supplied value.
        field: Argument name, for the error message.
        max_length: Longest accepted value.

    Returns:
        The trimmed string.

    Raises:
        ToolArgumentError: If it is absent, not a string, blank, or too long.
    """
    if value is None:
        raise ToolArgumentError(f"{field} is required", context={"argument": field})
    if not isinstance(value, str):
        raise ToolArgumentError(
            f"{field} must be text, got {type(value).__name__}", context={"argument": field}
        )

    trimmed = value.strip()
    if not trimmed:
        raise ToolArgumentError(f"{field} must not be blank", context={"argument": field})
    if len(trimmed) > max_length:
        raise ToolArgumentError(
            f"{field} must be at most {max_length} characters", context={"argument": field}
        )
    return trimmed


def optional_text(value: object, *, field: str, max_length: int = MAX_NAME_LENGTH) -> str | None:
    """Validate an optional string argument.

    Args:
        value: The supplied value, or ``None``.
        field: Argument name, for the error message.
        max_length: Longest accepted value.

    Returns:
        The trimmed string, or ``None`` when absent. A blank string is treated
        as absent, because a model that emits ``""`` means "I did not supply
        this" -- but an all-whitespace *filter* would match everything, so the
        two must not be confused.

    Raises:
        ToolArgumentError: If it is present but not usable text.
    """
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return required_text(value, field=field, max_length=max_length)


def required_employee_number(value: object, *, field: str = "employee_number") -> str:
    """Validate an employee number.

    Args:
        value: The supplied value.
        field: Argument name, for the error message.

    Returns:
        The trimmed employee number.

    Raises:
        ToolArgumentError: If it is absent or not a plausible business key.
            Shape is checked so an obviously wrong argument fails as a bad
            argument rather than as an empty result that reads like "this person
            does not exist".
    """
    text = required_text(value, field=field, max_length=32)
    if not _EMPLOYEE_NUMBER.match(text):
        raise ToolArgumentError(
            f"{field} {text!r} is not a valid employee number",
            context={"argument": field},
        )
    return text


def optional_int(
    value: object,
    *,
    field: str,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    """Validate an optional integer argument.

    Args:
        value: The supplied value, or ``None``.
        field: Argument name, for the error message.
        minimum: Smallest accepted value, inclusive.
        maximum: Largest accepted value, inclusive.

    Returns:
        The integer, or ``None`` when absent.

    Raises:
        ToolArgumentError: If it is not an integer or falls outside the range.
            Out-of-range values are refused, never clamped: a question about
            month 13 is a question that was misunderstood, and answering about
            December hides that.
    """
    if value is None:
        return None

    # bool is an int in Python, and `month=True` is not a month.
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ToolArgumentError(
            f"{field} must be a whole number, got {type(value).__name__}",
            context={"argument": field},
        )

    try:
        number = int(value)
    except ValueError as exc:
        raise ToolArgumentError(
            f"{field} must be a whole number", context={"argument": field}
        ) from exc

    if minimum is not None and number < minimum:
        raise ToolArgumentError(
            f"{field} must be at least {minimum}, got {number}", context={"argument": field}
        )
    if maximum is not None and number > maximum:
        raise ToolArgumentError(
            f"{field} must be at most {maximum}, got {number}", context={"argument": field}
        )
    return number


def optional_date(value: object, *, field: str) -> date | None:
    """Validate an optional ISO date argument.

    Args:
        value: A ``date``, an ISO-8601 string, or ``None``.
        field: Argument name, for the error message.

    Returns:
        The date, or ``None`` when absent.

    Raises:
        ToolArgumentError: If it is present but not a usable date.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ToolArgumentError(
            f"{field} must be an ISO-8601 date, got {type(value).__name__}",
            context={"argument": field},
        )

    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolArgumentError(
            f"{field} must be an ISO-8601 date such as 2026-03-01",
            context={"argument": field},
        ) from exc
