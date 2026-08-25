"""What a role grants, in the vocabulary the ERP's RBAC tables use.

Plan.md section 8.1 describes the real chain as ``user_roles -> roles ->
role_permissions -> permissions(resource, action)``, with ``permission_level``
in ``none | read | write | approval_required``. This module is that shape as
domain types, so the mock adapter and the eventual ERP adapter produce the same
values from different sources.

**The resource vocabulary is enumerated here, deliberately.** Plan.md gap S2
records that ``permissions.resource`` is not enumerated anywhere in the ERP
documentation, and that the plan does not invent answers. It does not: this is
*this agent's* vocabulary, the set of things it is capable of reading, and the
cutover's job is to map ERP resource strings onto it. Making it an enum rather
than a free string means a fixture naming a resource this build does not
implement fails at load, instead of resolving to a permission that silently
grants nothing -- an under-privileged success, which is the hardest kind of
authorization bug to notice.

**Absence is refusal.** A permission nobody granted reports
:attr:`PermissionLevel.NONE`, and :meth:`PermissionSet.allows` is false for it.
An empty :class:`PermissionSet` therefore permits nothing at all, which is what
makes a default-constructed :class:`~erp_agent.domain.models.security_context.SecurityContext`
safe rather than dangerous.

**Merging is additive.** Holding two roles gives the union of their grants at
the highest level either confers. A level of ``none`` contributes nothing and
never withdraws another role's grant: deny-wins RBAC would mean adding a role
could take access away, and the ERP schema shows no evidence it is meant that
way. A ``role_permissions`` row at level ``none`` is simply not a grant.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "Action",
    "Grant",
    "Permission",
    "PermissionLevel",
    "PermissionSet",
    "Resource",
]


class Resource(StrEnum):
    """A thing this agent can be permitted to read.

    Coarse on purpose, and matched to controls that already exist: each member
    is either a scope-independent category the field policy knows about
    (:attr:`HR_CONTACT_PII`, :attr:`HR_COMPENSATION`) or a family of records a
    tool returns. A finer vocabulary would be a permission algebra nothing can
    populate, which is the failure mode Plan.md section 8.1 warns about for the
    scope ladder.
    """

    HR_EMPLOYEE = "hr.employee"
    HR_CONTACT_PII = "hr.contact_pii"
    HR_COMPENSATION = "hr.compensation"
    HR_LEAVE = "hr.leave"
    HR_ATTENDANCE = "hr.attendance"
    DOCUMENTS = "documents"


class Action(StrEnum):
    """What may be done to a resource.

    The ERP's ``permissions`` table is unique on ``(resource, action)``, so the
    action is part of a permission's identity rather than a property of it.
    This agent can only ever ask for :attr:`READ` -- it has no write code path
    at all (Plan.md section 8.2, barrier 3) -- but the other members exist so an
    ERP row naming them maps onto something rather than failing to load.
    """

    READ = "read"
    WRITE = "write"
    APPROVE = "approve"


class PermissionLevel(StrEnum):
    """How strongly a permission is held.

    The four values Plan.md section 8.1 names. The ordering below exists only so
    :meth:`PermissionSet.merge` is deterministic when two roles grant the same
    permission at different levels; it is not a claim that approval-required
    access is "less" than write access in any general sense.

    What matters for this system is :meth:`permits_read`, and only that: a
    read-only agent never asks for anything above :attr:`READ`.
    """

    NONE = "none"
    READ = "read"
    APPROVAL_REQUIRED = "approval_required"
    WRITE = "write"

    @property
    def rank(self) -> int:
        """Position in the ordering; larger wins a merge."""
        return _LEVEL_ORDER.index(self)

    @property
    def permits_read(self) -> bool:
        """Whether this level confers the ability to read.

        Everything except :attr:`NONE` does. A principal who may write a record
        may certainly read it, and one whose write needs approval may read it
        while waiting.
        """
        return self is not PermissionLevel.NONE


_LEVEL_ORDER: tuple[PermissionLevel, ...] = (
    PermissionLevel.NONE,
    PermissionLevel.READ,
    PermissionLevel.APPROVAL_REQUIRED,
    PermissionLevel.WRITE,
)


@dataclass(frozen=True, slots=True)
class Permission:
    """One ``(resource, action)`` pair -- a row of the ERP's ``permissions``.

    Attributes:
        resource: What is being reached for.
        action: What is being done to it.
    """

    resource: Resource
    action: Action

    def __str__(self) -> str:
        """Render as ``resource:action``, the form used in errors and logs."""
        return f"{self.resource.value}:{self.action.value}"


@dataclass(frozen=True, slots=True)
class Grant:
    """A permission held at a level -- a row of ``role_permissions``.

    Attributes:
        permission: What is granted.
        level: How strongly.
    """

    permission: Permission
    level: PermissionLevel


@dataclass(frozen=True, slots=True)
class PermissionSet:
    """Every grant one principal holds, at most one level per permission.

    Frozen and hashable, because a :class:`SecurityContext` carries one and is
    itself frozen. Held as a ``frozenset`` rather than a mapping for exactly
    that reason -- a ``dict`` member would quietly make the whole context
    unhashable.

    Attributes:
        grants: The held grants. At most one per ``(resource, action)``.
    """

    grants: frozenset[Grant] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        """Reject a set that holds one permission at two levels.

        Raises:
            ValueError: If any permission appears more than once. Which level
                applied would then depend on iteration order, and an
                authorization decision must not depend on that.
        """
        object.__setattr__(self, "grants", frozenset(self.grants))

        seen: set[Permission] = set()
        for grant in self.grants:
            if grant.permission in seen:
                raise ValueError(
                    f"permission {grant.permission} is held at more than one level; "
                    f"merge the grants instead of collecting them"
                )
            seen.add(grant.permission)

    def level_for(self, permission: Permission) -> PermissionLevel:
        """The level at which a permission is held.

        Args:
            permission: The permission to look up.

        Returns:
            The held level, or :attr:`PermissionLevel.NONE` if it was never
            granted. Absence and an explicit ``none`` are deliberately the same
            answer: both mean "not permitted", and a caller that had to tell
            them apart would be a caller treating ``none`` as a veto.
        """
        for grant in self.grants:
            if grant.permission == permission:
                return grant.level
        return PermissionLevel.NONE

    def allows(
        self,
        permission: Permission,
        *,
        at_least: PermissionLevel = PermissionLevel.READ,
    ) -> bool:
        """Whether this principal may exercise a permission.

        Args:
            permission: What is being reached for.
            at_least: The level required. Defaults to :attr:`PermissionLevel.READ`
                because that is the only level this system ever needs; the
                parameter exists so a future write path cannot accidentally be
                satisfied by a read grant.

        Returns:
            ``True`` if the held level is at least the one required.
        """
        held = self.level_for(permission)
        if not held.permits_read:
            return False
        return held.rank >= at_least.rank

    @classmethod
    def merge(cls, *sets: PermissionSet) -> PermissionSet:
        """Combine grants from several roles, keeping the highest level of each.

        The multi-role rule, stated once. Additive: a level of ``none`` in one
        set never withdraws a grant made by another, so holding an extra role
        can only ever widen access.

        Args:
            sets: The permission sets to combine. None is a valid number of
                them, and produces a set that permits nothing.

        Returns:
            The union.
        """
        highest: dict[Permission, PermissionLevel] = {}
        for permissions in sets:
            for grant in permissions.grants:
                current = highest.get(grant.permission)
                if current is None or grant.level.rank > current.rank:
                    highest[grant.permission] = grant.level

        return cls(
            frozenset(
                Grant(permission, level)
                for permission, level in highest.items()
                # A merged set holds only real grants. Carrying `none` forward
                # would make two equal sets compare unequal depending on which
                # roles happened to mention a permission they did not grant.
                if level.permits_read
            )
        )

    @classmethod
    def of(cls, grants: Iterable[tuple[Permission, PermissionLevel]]) -> PermissionSet:
        """Build a set from ``(permission, level)`` pairs.

        Args:
            grants: The pairs to hold.

        Returns:
            The permission set.

        Raises:
            ValueError: If a permission appears more than once.
        """
        return cls(frozenset(Grant(permission, level) for permission, level in grants))

    def __bool__(self) -> bool:
        """Whether anything at all is granted."""
        return bool(self.grants)

    def as_log_value(self) -> str:
        """A stable, sorted rendering for logs and diagnostics."""
        return ",".join(sorted(f"{grant.permission}={grant.level.value}" for grant in self.grants))
