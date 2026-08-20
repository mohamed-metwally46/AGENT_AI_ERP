"""Who is asking, and what they are allowed to see.

Resolved **once per request, before the model runs** (Plan.md section 5.1). The
LLM never contributes to this object and never sees it: it is an input to the
query layer, not an output of a conversation. That ordering is the whole
authorization design -- filters are applied before retrieval, so the model cannot
leak a row it was never given.

Frozen, and built only from trusted server-side state. A caller must not be able
to influence their own scope through any request field (Plan.md section 11,
Phase 7 acceptance criterion 2), and the cheapest way to keep that true is for
the type to be immutable and for its construction to live behind a resolver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = ["DataScope", "SecurityContext", "validate_scope_requirements"]


class DataScope(StrEnum):
    """How much of the organisation a principal may read.

    Ordered most-restrictive-first (Plan.md section 8.1). The ladder is
    deliberately coarse: five rungs that map onto columns the schema actually
    has, rather than a permission algebra nothing can populate.
    """

    SELF = "self"
    TEAM = "team"
    DEPARTMENT = "department"
    BRANCH = "branch"
    ORGANISATION = "organisation"

    @property
    def rank(self) -> int:
        """Position on the ladder; larger sees more."""
        return _SCOPE_ORDER.index(self)

    def includes(self, other: DataScope) -> bool:
        """Whether this scope is at least as permissive as ``other``."""
        return self.rank >= other.rank


_SCOPE_ORDER: tuple[DataScope, ...] = (
    DataScope.SELF,
    DataScope.TEAM,
    DataScope.DEPARTMENT,
    DataScope.BRANCH,
    DataScope.ORGANISATION,
)


def validate_scope_requirements(
    scope: DataScope,
    *,
    dept_id: str | None,
    allowed_branch_ids: frozenset[str],
) -> None:
    """Reject a grant that cannot express its own scope.

    Shared by :class:`SecurityContext` and
    :class:`~erp_agent.domain.models.principal.Principal` so the rule is stated
    once. It has to hold at *both* -- a principal is validated when the identity
    store is read, at start-up, and a context when a request is served. Checking
    only the second would mean a malformed fixture row is discovered as a failed
    authorization on a live question rather than as a boot failure.

    Args:
        scope: The granted scope.
        dept_id: The department, if any.
        allowed_branch_ids: The reachable branches.

    Raises:
        ValueError: If the scope needs a value that is absent. A
            ``DEPARTMENT``-scoped grant with no department would silently match
            nothing -- or, with a different predicate bug, everything.
    """
    if scope is DataScope.DEPARTMENT and not dept_id:
        raise ValueError("DEPARTMENT scope requires dept_id")
    if scope is DataScope.BRANCH and not allowed_branch_ids:
        raise ValueError("BRANCH scope requires at least one allowed branch")


@dataclass(frozen=True, slots=True)
class SecurityContext:
    """The resolved identity and reach of one principal.

    Attributes:
        user_id: The authenticated principal, from ``users.id``.
        employee_id: Their HR record, reached through
            ``users.employee_id -> employees.employee_id``. This single hop is
            the bridge from a login to a department, branch and manager chain,
            and therefore the backbone of every predicate below.
        scope: How far the principal may read.
        dept_id: Their department, used by ``DEPARTMENT`` scope. Derived from
            the employee record rather than granted by RBAC, because
            ``user_roles`` scopes by branch only (Plan.md section 8.5, gap S1).
        branch_id: Their own branch.
        allowed_branch_ids: Branches reachable under ``BRANCH`` scope, from
            ``user_roles.branch_id``. Empty means "not branch-scoped", never
            "all branches" -- failing closed matters more here than convenience.
        manager_of: Employees reporting to this principal, transitively. Used by
            ``TEAM`` scope. Computed server-side from the reporting tree.
        role_names: Role names held, for field-level decisions such as whether
            an HR role may see contact PII.
    """

    user_id: str
    employee_id: str
    scope: DataScope
    dept_id: str | None = None
    branch_id: str | None = None
    allowed_branch_ids: frozenset[str] = field(default_factory=frozenset)
    manager_of: frozenset[str] = field(default_factory=frozenset)
    role_names: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Validate the context can actually express its own scope.

        Raises:
            ValueError: If an identifier is blank, or the scope needs a value
                the context does not carry. A ``DEPARTMENT``-scoped context with
                no department would silently match nothing -- or, with a
                different predicate bug, everything.
        """
        if not self.user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not self.employee_id.strip():
            raise ValueError("employee_id must be a non-empty string")

        validate_scope_requirements(
            self.scope,
            dept_id=self.dept_id,
            allowed_branch_ids=frozenset(self.allowed_branch_ids),
        )

        # Normalise so equality and hashing are stable regardless of how the
        # resolver built the collections.
        object.__setattr__(self, "allowed_branch_ids", frozenset(self.allowed_branch_ids))
        object.__setattr__(self, "manager_of", frozenset(self.manager_of))
        object.__setattr__(self, "role_names", frozenset(self.role_names))

    @property
    def is_self_scoped(self) -> bool:
        """Whether the principal may read only their own record."""
        return self.scope is DataScope.SELF

    def has_any_role(self, *names: str) -> bool:
        """Whether the principal holds any of the named roles.

        Args:
            names: Role names to test, compared case-insensitively.

        Returns:
            ``True`` if any is held.
        """
        held = {name.lower() for name in self.role_names}
        return any(name.lower() in held for name in names)

    def as_log_payload(self) -> dict[str, str | int]:
        """A safe rendering for logs (Plan.md section 20).

        Identifiers and the scope decision, never the reachable-id sets: logging
        a manager's whole reporting tree on every request is how an access log
        becomes an org chart.
        """
        return {
            "user_id": self.user_id,
            "employee_id": self.employee_id,
            "scope": self.scope.value,
            "roles": ",".join(sorted(self.role_names)),
            "team_size": len(self.manager_of),
            "branches": len(self.allowed_branch_ids),
        }
