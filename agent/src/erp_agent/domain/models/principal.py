"""The authenticated identity, as the identity store reports it.

Deliberately distinct from :class:`~erp_agent.domain.models.security_context.SecurityContext`,
and the distinction is not bookkeeping:

*A principal is what the identity store **says**.* Who you are, which roles you
hold, where you sit in the organisation, and the scope those roles grant. It is
resolved by a lookup and nothing more.

*A security context is what that **means** for data access.* It is a principal
plus everything that had to be computed -- most importantly ``manager_of``, the
transitive reporting tree, which is a query against the ERP rather than a fact
the identity store holds.

Keeping them apart is what stops the reporting tree being something an identity
provider could assert. A caller who could hand us a ``manager_of`` set would be
choosing their own visibility; here they cannot, because the type that reaches
the query layer is only ever built on our side of the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from erp_agent.domain.models.security_context import (
    DataScope,
    SecurityContext,
    validate_scope_requirements,
)

__all__ = ["Principal"]


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated identity and the access it was granted.

    Attributes:
        user_id: The authenticated principal, from ``users.id`` once the
            Platform tables exist.
        employee_id: Their HR record, via ``users.employee_id``.
        scope: The data scope their roles grant.
        dept_id: Their department. Derived from the employee record rather than
            granted by RBAC, because ``user_roles`` scopes by branch only
            (Plan.md section 8.5, gap S1).
        branch_id: Their own branch.
        allowed_branch_ids: Branches reachable under ``BRANCH`` scope, from
            ``user_roles.branch_id``.
        role_names: Role names held, used for field-level decisions.
    """

    user_id: str
    employee_id: str
    scope: DataScope
    dept_id: str | None = None
    branch_id: str | None = None
    allowed_branch_ids: frozenset[str] = field(default_factory=frozenset)
    role_names: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate the identity is usable.

        Raises:
            ValueError: If an identifier is blank, or the granted scope needs a
                value this principal lacks. The scope check is deliberately here
                and not only on :class:`SecurityContext`: the identity store is
                read at start-up, so an incoherent grant must fail then rather
                than when somebody asks a question.
        """
        if not self.user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not self.employee_id.strip():
            raise ValueError("employee_id must be a non-empty string")

        object.__setattr__(self, "allowed_branch_ids", frozenset(self.allowed_branch_ids))
        object.__setattr__(self, "role_names", frozenset(self.role_names))

        validate_scope_requirements(
            self.scope,
            dept_id=self.dept_id,
            allowed_branch_ids=self.allowed_branch_ids,
        )

    def to_security_context(self, *, manager_of: frozenset[str] = frozenset()) -> SecurityContext:
        """Combine this identity with computed reachability.

        Args:
            manager_of: Employees reporting to this principal, transitively.
                Computed server-side from the ERP reporting tree, never
                supplied by the caller or the model. Empty for every scope but
                ``TEAM``, where it is the whole point.

        Returns:
            The context the query layer will build predicates from.

        Raises:
            ValueError: If the scope needs a value this principal lacks. Already
                guaranteed by the constructor, and re-checked by
                :class:`SecurityContext` because the two types are validated at
                different moments.
        """
        return SecurityContext(
            user_id=self.user_id,
            employee_id=self.employee_id,
            scope=self.scope,
            dept_id=self.dept_id,
            branch_id=self.branch_id,
            allowed_branch_ids=self.allowed_branch_ids,
            manager_of=manager_of,
            role_names=self.role_names,
        )
