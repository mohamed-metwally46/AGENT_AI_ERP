"""Turning a scope into a restriction, as data rather than as SQL.

The scope ladder (Plan.md section 8.1) says what a principal may read.
:func:`visible_employees` turns that into an :class:`EmployeeVisibility` -- a
plain description of which rows are reachable, with no SQL in it. The query layer
renders it into a predicate.

The indirection earns its keep twice. The domain stays free of a dialect, so this
policy is unit-testable without a database; and because the visibility object is
computed from the context alone, no tool argument can widen it. A tool asking
about employee X does not *ask permission* -- it receives a restriction it cannot
see past.

Fail closed, always. Every branch here either narrows or refuses. There is no
path that returns "unrestricted" by omission: ``ORGANISATION`` has to be stated.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from erp_agent.domain.models.security_context import DataScope, SecurityContext

__all__ = ["EmployeeVisibility", "visible_employees"]


@dataclass(frozen=True, slots=True)
class EmployeeVisibility:
    """Which employee rows a principal may read.

    Exactly one restriction applies, matching the scope that produced it.

    Attributes:
        unrestricted: ``ORGANISATION`` scope -- no predicate at all.
        employee_ids: Reachable employee identifiers, for ``SELF`` and ``TEAM``.
        dept_id: Reachable department, for ``DEPARTMENT``.
        branch_ids: Reachable branches, for ``BRANCH``.
    """

    unrestricted: bool = False
    employee_ids: frozenset[str] = field(default_factory=frozenset)
    dept_id: str | None = None
    branch_ids: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate the restriction is exactly one thing, and is not empty.

        Raises:
            ValueError: If the visibility restricts on more than one dimension,
                or restricts on nothing while not being unrestricted. The second
                case is the dangerous one: an empty restriction that is not
                explicitly unrestricted would render as an absent ``WHERE``
                clause and expose the whole table.
        """
        object.__setattr__(self, "employee_ids", frozenset(self.employee_ids))
        object.__setattr__(self, "branch_ids", frozenset(self.branch_ids))

        dimensions = sum([bool(self.employee_ids), self.dept_id is not None, bool(self.branch_ids)])

        if self.unrestricted and dimensions:
            raise ValueError("an unrestricted visibility must carry no restriction")
        if not self.unrestricted and dimensions != 1:
            raise ValueError(
                f"a restricted visibility must restrict on exactly one dimension; got {dimensions}"
            )


def visible_employees(context: SecurityContext) -> EmployeeVisibility:
    """Compute which employee rows a principal may read.

    Args:
        context: The resolved security context. Its scope is authoritative;
            nothing else in the request contributes.

    Returns:
        The restriction to apply. Never ``None`` and never implicitly
        unrestricted.

    Raises:
        ValueError: If the scope is one this function does not implement. A new
            ladder rung must fail loudly here rather than fall through to a
            default -- an unhandled scope that returned "unrestricted" would be
            a total authorization bypass.
    """
    match context.scope:
        case DataScope.SELF:
            return EmployeeVisibility(employee_ids=frozenset({context.employee_id}))

        case DataScope.TEAM:
            # The principal is always inside their own team, so a manager can
            # answer questions about themselves without a second scope.
            return EmployeeVisibility(
                employee_ids=frozenset({context.employee_id, *context.manager_of})
            )

        case DataScope.DEPARTMENT:
            # dept_id is guaranteed present by SecurityContext validation.
            return EmployeeVisibility(dept_id=context.dept_id)

        case DataScope.BRANCH:
            return EmployeeVisibility(branch_ids=frozenset(context.allowed_branch_ids))

        case DataScope.ORGANISATION:
            return EmployeeVisibility(unrestricted=True)

        case _:  # pragma: no cover -- unreachable while DataScope is exhaustive
            raise ValueError(f"no visibility rule for scope {context.scope!r}")
