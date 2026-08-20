"""Rendering a visibility into a parameterised SQL predicate.

This is where the authorization model becomes a ``WHERE`` clause, and therefore
where it can fail open. Three rules make that hard:

*A predicate is never empty.* An unrestricted visibility renders ``TRUE``, not
``""``. An empty fragment concatenated into a query produces either a syntax
error or -- far worse, if the template happens to read ``WHERE 1=1 AND {scope}``
-- a silently absent restriction. There is no code path here that returns the
empty string.

*An empty restriction cannot arrive here at all.*
:class:`~erp_agent.domain.policies.scope.EmployeeVisibility` refuses to be
constructed restricting on nothing, so the renderer never has to decide what an
empty ``IN ()`` list means. The guarantee lives at the type, one layer earlier,
which is why there is no "matches nothing" branch below.

*Values are bound; only identifiers are interpolated.* Every reachable id,
department and branch travels as a query parameter. The one thing formatted into
the string is the table alias, which is validated against a strict pattern and
never comes from a request.

The UUID casts are load-bearing rather than cosmetic. ``employee_id`` and friends
are ``uuid`` columns, and psycopg adapts a Python ``list[str]`` to ``text[]``.
Without ``::uuid[]`` the comparison raises an operator-mismatch error at
execution time -- which is at least loud, but the cast makes it work rather than
inviting someone to "fix" it by casting the column to text and losing the index.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from erp_agent.domain.errors import OutOfScopeError
from erp_agent.domain.policies.scope import EmployeeVisibility

__all__ = [
    "ALWAYS_TRUE",
    "Predicate",
    "combine_and",
    "merge_parameters",
    "scope_predicate",
    "where_clause",
]

#: A table alias must look like a table alias. Interpolated into SQL, so it is
#: validated rather than trusted -- and it is always a developer-supplied
#: constant, never a value from a request.
_SAFE_ALIAS = re.compile(r"^[a-z_][a-z0-9_]{0,29}$")

#: Column names, per dimension. Fixed here rather than passed in, so a caller
#: cannot redirect a scope check at a different column.
_EMPLOYEE_ID_COLUMN = "employee_id"
_DEPARTMENT_COLUMN = "dept_id"
_BRANCH_COLUMN = "branch_id"


@dataclass(frozen=True, slots=True)
class Predicate:
    """A boolean SQL fragment and the parameters it references.

    Attributes:
        sql: The fragment. Always a complete boolean expression, safe to place
            directly after ``WHERE`` or ``AND``, and never empty.
        parameters: Values for the named placeholders in :attr:`sql`.
    """

    sql: str
    parameters: Mapping[str, Any] = MappingProxyType({})

    def __post_init__(self) -> None:
        """Validate the fragment is a usable boolean expression.

        Raises:
            ValueError: If the fragment is blank. An empty predicate is the
                failure mode this whole module exists to prevent, so it is
                refused at construction rather than caught downstream.
        """
        if not self.sql.strip():
            raise ValueError(
                "a predicate must never be empty; use ALWAYS_TRUE to say "
                "'no restriction' explicitly"
            )
        object.__setattr__(self, "parameters", MappingProxyType(dict(self.parameters)))

    @property
    def parameter_names(self) -> tuple[str, ...]:
        """The parameter names, sorted. Safe to log; the values are not."""
        return tuple(sorted(self.parameters))


#: Explicit, so "no restriction" is something a reader sees in the rendered SQL
#: rather than infers from an absence.
ALWAYS_TRUE = Predicate("TRUE")


def _validate_alias(alias: str) -> str:
    """Reject a table alias that is not a plain identifier.

    Args:
        alias: The alias to validate.

    Returns:
        The alias, unchanged.

    Raises:
        ValueError: If it is not a lowercase identifier. This is the only string
            interpolated into a predicate, so it is the only place an injection
            could enter, and it fails closed.
    """
    if not _SAFE_ALIAS.match(alias):
        raise ValueError(
            f"table alias {alias!r} is not a plain lowercase identifier; "
            f"aliases are interpolated into SQL and must be developer constants"
        )
    return alias


def scope_predicate(
    visibility: EmployeeVisibility,
    *,
    alias: str = "e",
    parameter_prefix: str = "scope",
) -> Predicate:
    """Render a visibility into the predicate that enforces it.

    Args:
        visibility: What the principal may read, computed by
            :func:`~erp_agent.domain.policies.scope.visible_employees`.
        alias: Table alias the employee columns hang off. A developer constant.
        parameter_prefix: Namespace for the generated parameter names, so a
            scope parameter can never collide with a tool's own argument. A
            collision would let a tool argument overwrite the scope value, which
            is precisely the attack this design exists to prevent.

    Returns:
        A predicate. Never empty, and never implicitly permissive.

    Raises:
        ValueError: If the alias is not a plain identifier.
    """
    prefix = _validate_alias(parameter_prefix)
    table = _validate_alias(alias)

    if visibility.unrestricted:
        return ALWAYS_TRUE

    if visibility.employee_ids:
        name = f"{prefix}_employee_ids"
        return Predicate(
            f"{table}.{_EMPLOYEE_ID_COLUMN} = ANY(%({name})s::uuid[])",
            {name: sorted(visibility.employee_ids)},
        )

    if visibility.dept_id is not None:
        name = f"{prefix}_dept_id"
        return Predicate(
            f"{table}.{_DEPARTMENT_COLUMN} = %({name})s::uuid",
            {name: visibility.dept_id},
        )

    if visibility.branch_ids:
        name = f"{prefix}_branch_ids"
        return Predicate(
            f"{table}.{_BRANCH_COLUMN} = ANY(%({name})s::uuid[])",
            {name: sorted(visibility.branch_ids)},
        )

    # Unreachable while EmployeeVisibility enforces exactly one dimension. Kept
    # because "unreachable" and "unreachable and permissive" are very different
    # bugs: if that invariant is ever relaxed, this fails closed rather than
    # falling through to an absent restriction.
    raise ValueError(  # pragma: no cover
        "a visibility restricted on no dimension reached the renderer"
    )


def combine_and(*predicates: Predicate) -> Predicate:
    """Conjoin predicates into one.

    Args:
        predicates: Fragments to AND together. At least one is required --
            combining nothing would have to mean ``TRUE``, and a function whose
            empty case silently grants everything is a function that will one
            day be called with an empty list.

    Returns:
        The conjunction, parenthesised so operator precedence inside a fragment
        cannot leak out and change the meaning of the whole clause.

    Raises:
        ValueError: If no predicate is supplied, or two predicates use the same
            parameter name for different values.
    """
    if not predicates:
        raise ValueError("combine_and requires at least one predicate")
    if len(predicates) == 1:
        return predicates[0]

    merged: dict[str, Any] = {}
    for predicate in predicates:
        for name, value in predicate.parameters.items():
            if name in merged and merged[name] != value:
                raise ValueError(
                    f"parameter {name!r} was supplied twice with different values; "
                    f"a scope parameter must never be overwritten by a tool argument"
                )
            merged[name] = value

    joined = " AND ".join(f"({predicate.sql})" for predicate in predicates)
    return Predicate(joined, merged)


def where_clause(predicates: Sequence[Predicate]) -> Predicate:
    """Build a complete ``WHERE`` clause from predicates.

    Args:
        predicates: The fragments to apply. Must not be empty, for the same
            reason as :func:`combine_and`.

    Returns:
        A predicate whose ``sql`` begins with ``WHERE``.

    Raises:
        ValueError: If no predicate is supplied.
    """
    combined = combine_and(*predicates)
    return Predicate(f"WHERE {combined.sql}", combined.parameters)


def merge_parameters(
    scope_clause: Predicate,
    tool_parameters: Mapping[str, Any],
) -> dict[str, Any]:
    """Combine a tool's own arguments with the scope parameters, refusing collisions.

    Relying on dictionary ordering to make the scope "win" is not a control: it
    is a convention, and the first person to reverse the merge silently hands a
    tool argument the power to choose its own visibility. So a collision is an
    error rather than a precedence question.

    Args:
        scope_clause: The rendered scope clause, whose parameters are
            authoritative.
        tool_parameters: Values supplied by the tool, ultimately derived from
            model output and therefore untrusted.

    Returns:
        The merged parameter mapping.

    Raises:
        OutOfScopeError: If a tool parameter uses a scope parameter's name. The
            offending names are reported; no value is, because the values are
            the untrusted part.
    """
    if collisions := sorted(set(tool_parameters) & set(scope_clause.parameters)):
        raise OutOfScopeError(
            f"tool arguments may not use the reserved scope parameter name(s): "
            f"{', '.join(collisions)}",
            context={"parameters": tuple(collisions)},
        )
    return {**tool_parameters, **scope_clause.parameters}
