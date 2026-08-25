"""Roles, and the rule that turns a set of them into one grant.

The ``Roles -> Permissions`` rung of the chain. A :class:`Role` is what the
ERP's ``roles`` table holds joined to ``role_permissions``; a
:class:`RoleCatalogue` is that table; :meth:`RoleCatalogue.resolve` is the join
that :class:`~erp_agent.domain.models.principal.Principal` is built from.

Pure domain. No file, no database, no adapter -- which is what lets the mock
identity fixture and a future ERP query share one statement of the rule instead
of two readings of it that drift.

**The multi-role rule, in full:**

*Scope is the maximum.* The widest rung any held role grants. Anything else
would mean adding a role could narrow the holder's view, so a promotion would
take access away.

*Permissions are the union*, at the highest level any role confers -- see
:meth:`~erp_agent.domain.models.authorization.PermissionSet.merge`.

**Nothing is resolved by omission.** An unrecognised role name raises rather
than being skipped, and an empty role list raises rather than yielding a default
scope. Both refusals point the same way: a principal the catalogue cannot place
has no reach, and the only safe reading of "no reach" is "no access". A dropped
role name would instead produce a working principal with less access than
intended -- which nobody reports as a bug, because it looks like the system
working.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from erp_agent.domain.errors import AuthorizationError
from erp_agent.domain.models.authorization import PermissionSet
from erp_agent.domain.models.security_context import DataScope

__all__ = ["ResolvedGrant", "Role", "RoleCatalogue", "UnknownRoleError"]


class UnknownRoleError(AuthorizationError):
    """A role name the catalogue does not define, or no roles at all.

    An :class:`~erp_agent.domain.errors.AuthorizationError` rather than a
    configuration error because it is raised while resolving a principal: the
    identity authenticated, and then could not be placed. At the HTTP boundary
    that is folded into the same uniform 401 as every other identity failure.
    """


@dataclass(frozen=True, slots=True)
class Role:
    """A named role and everything holding it confers.

    Attributes:
        name: The role name, as the identity store spells it. Matched
            case-insensitively when resolving.
        scope: How far along the ladder holding this role reaches.
        grants: The permissions it confers.
        description: What the role is for. Documentation only -- it appears in
            the fixture so the roster is readable, and is never consulted for a
            decision.
    """

    name: str
    scope: DataScope
    grants: PermissionSet = field(default_factory=PermissionSet)
    description: str = ""

    def __post_init__(self) -> None:
        """Validate the role is usable.

        Note that a ``DEPARTMENT``-scoped role deliberately does *not* have to
        name a department: ``user_roles`` scopes by branch only (Plan.md section
        8.5, gap S1), so the department is derived from the employee record and
        supplied by the principal, not by the grant.

        Raises:
            ValueError: If the name is blank.
        """
        if not self.name.strip():
            raise ValueError("a role name must be a non-empty string")

    @property
    def key(self) -> str:
        """The name in its canonical, case-folded form."""
        return self.name.strip().lower()


@dataclass(frozen=True, slots=True)
class ResolvedGrant:
    """What a set of roles adds up to.

    Everything :class:`~erp_agent.domain.models.principal.Principal` needs that
    comes from RBAC rather than from the employee record.

    Attributes:
        scope: The widest rung any held role grants.
        permissions: The union of every held role's grants.
        role_names: The roles held, canonicalised. Kept for logging and
            diagnostics -- never for an authorization decision, which is what
            :attr:`permissions` is for.
    """

    scope: DataScope
    permissions: PermissionSet
    role_names: frozenset[str]


# Deliberately not `slots=True`: the derived lookup index is populated in place
# during __post_init__, which a slotted frozen dataclass cannot express as
# cleanly.
@dataclass(frozen=True)
class RoleCatalogue:
    """Every role this deployment defines.

    The ERP's ``roles`` table joined to ``role_permissions``, as a value. Built
    once at start-up from the identity fixture, and replaced wholesale by a
    query at cutover.

    Attributes:
        roles: The defined roles. Held as a tuple rather than a mapping so the
            catalogue stays frozen and hashable; the lookup index below is
            derived from it.
    """

    roles: tuple[Role, ...]

    #: Case-folded name to role. Derived state, so it takes no part in equality,
    #: hashing or the constructor -- two catalogues built from the same roles
    #: must compare equal whatever their index happens to hold.
    _index: dict[str, Role] = field(init=False, repr=False, compare=False, default_factory=dict)

    def __post_init__(self) -> None:
        """Validate the catalogue and build the lookup index.

        Raises:
            ValueError: If the catalogue is empty, or defines one role name
                twice. A repeated name is refused rather than last-one-wins:
                which grant applied would depend on file order, and an
                authorization decision must not.
        """
        object.__setattr__(self, "roles", tuple(self.roles))

        if not self.roles:
            raise ValueError("a role catalogue must define at least one role")

        # Populated in place rather than reassigned: the dataclass is frozen, and
        # the index is a field the generated __init__ has already created.
        for role in self.roles:
            if role.key in self._index:
                raise ValueError(
                    f"role {role.name!r} is defined more than once; which grant "
                    f"applied would depend on declaration order"
                )
            self._index[role.key] = role

    def __contains__(self, name: str) -> bool:
        """Whether a role name is defined."""
        return name.strip().lower() in self._index

    def get(self, name: str) -> Role:
        """Look up one role.

        Args:
            name: The role name, matched case-insensitively.

        Returns:
            The role.

        Raises:
            UnknownRoleError: If it is not defined.
        """
        role: Role | None = self._index.get(name.strip().lower())
        if role is None:
            raise UnknownRoleError(
                f"role {name.strip()!r} is not defined by the role catalogue",
                context={"role": name.strip()},
            )
        return role

    def resolve(self, names: Iterable[str]) -> ResolvedGrant:
        """Combine every named role into one grant.

        Args:
            names: The role names held, as the identity store spells them.
                Matched case-insensitively; repeats are held once.

        Returns:
            The widest scope and the union of the permissions.

        Raises:
            UnknownRoleError: If any name is not defined, or if no names were
                given at all. Neither is skipped: see the module docstring --
                a principal the catalogue cannot place has no reach.
        """
        held = [self.get(name) for name in names]

        if not held:
            raise UnknownRoleError(
                "the identity holds no roles, so it grants no scope and no access"
            )

        # max() over the ladder rather than a hand-rolled comparison, so a new
        # rung added to DataScope is ordered correctly here without an edit.
        scope = max((role.scope for role in held), key=lambda value: value.rank)

        return ResolvedGrant(
            scope=scope,
            permissions=PermissionSet.merge(*(role.grants for role in held)),
            role_names=frozenset(role.key for role in held),
        )
