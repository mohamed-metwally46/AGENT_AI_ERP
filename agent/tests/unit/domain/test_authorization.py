"""The permission vocabulary, and how grants combine.

This is the layer Plan.md section 8.1 calls ``permission_levels`` and section
8.5 gap S2 says nobody has enumerated. Enumerating it here is the point: a
``Resource`` that is an enum rather than a free string means a fixture typo is a
load-time failure instead of a permission that silently grants nothing.

The rule under test is additive RBAC, stated once:

* a level of ``none`` is the *absence* of a grant, never a veto;
* merging keeps the **highest** level per ``(resource, action)``;
* an absent grant is a refusal, so an empty set permits nothing.

The last one is the one worth a test of its own. Every other property here is a
convenience; that one is the security boundary.
"""

from __future__ import annotations

import pytest

from erp_agent.domain.models.authorization import (
    Action,
    Grant,
    Permission,
    PermissionLevel,
    PermissionSet,
    Resource,
)

pytestmark = pytest.mark.unit

READ_EMPLOYEE = Permission(Resource.HR_EMPLOYEE, Action.READ)
READ_COMPENSATION = Permission(Resource.HR_COMPENSATION, Action.READ)
READ_CONTACT_PII = Permission(Resource.HR_CONTACT_PII, Action.READ)


def _granting(*grants: tuple[Permission, PermissionLevel]) -> PermissionSet:
    return PermissionSet(frozenset(Grant(permission, level) for permission, level in grants))


class TestAnEmptyPermissionSet:
    def test_it_permits_nothing(self):
        assert not PermissionSet().allows(READ_EMPLOYEE)

    def test_an_ungranted_permission_reports_level_none(self):
        assert PermissionSet().level_for(READ_EMPLOYEE) is PermissionLevel.NONE


class TestLevels:
    def test_read_permits_reading(self):
        granted = _granting((READ_EMPLOYEE, PermissionLevel.READ))
        assert granted.allows(READ_EMPLOYEE)

    def test_a_level_of_none_is_not_a_grant(self):
        # The distinction that makes `none` rows in `role_permissions` harmless
        # rather than load-bearing.
        granted = _granting((READ_EMPLOYEE, PermissionLevel.NONE))
        assert not granted.allows(READ_EMPLOYEE)

    @pytest.mark.parametrize("level", [PermissionLevel.WRITE, PermissionLevel.APPROVAL_REQUIRED])
    def test_a_level_above_read_still_permits_reading(self, level):
        granted = _granting((READ_EMPLOYEE, level))
        assert granted.allows(READ_EMPLOYEE)

    def test_a_grant_does_not_leak_to_a_different_resource(self):
        granted = _granting((READ_EMPLOYEE, PermissionLevel.READ))
        assert not granted.allows(READ_COMPENSATION)

    def test_a_grant_does_not_leak_to_a_different_action(self):
        granted = _granting((READ_EMPLOYEE, PermissionLevel.READ))
        assert not granted.allows(Permission(Resource.HR_EMPLOYEE, Action.WRITE))


class TestMerging:
    def test_merging_unions_distinct_permissions(self):
        merged = PermissionSet.merge(
            _granting((READ_EMPLOYEE, PermissionLevel.READ)),
            _granting((READ_COMPENSATION, PermissionLevel.READ)),
        )

        assert merged.allows(READ_EMPLOYEE)
        assert merged.allows(READ_COMPENSATION)

    def test_merging_keeps_the_highest_level(self):
        merged = PermissionSet.merge(
            _granting((READ_EMPLOYEE, PermissionLevel.READ)),
            _granting((READ_EMPLOYEE, PermissionLevel.WRITE)),
        )

        assert merged.level_for(READ_EMPLOYEE) is PermissionLevel.WRITE

    def test_a_none_level_never_removes_a_grant_another_role_made(self):
        # Additive RBAC: adding a role must not take access away, or holding
        # more roles could mean seeing less.
        merged = PermissionSet.merge(
            _granting((READ_CONTACT_PII, PermissionLevel.READ)),
            _granting((READ_CONTACT_PII, PermissionLevel.NONE)),
        )

        assert merged.allows(READ_CONTACT_PII)

    def test_merging_nothing_permits_nothing(self):
        assert not PermissionSet.merge().allows(READ_EMPLOYEE)


class TestValueSemantics:
    def test_two_sets_with_the_same_grants_are_equal(self):
        assert _granting((READ_EMPLOYEE, PermissionLevel.READ)) == _granting(
            (READ_EMPLOYEE, PermissionLevel.READ)
        )

    def test_a_permission_set_is_hashable(self):
        # SecurityContext is frozen and carries one; a member that cannot be
        # hashed would quietly make the context unhashable too.
        assert {_granting((READ_EMPLOYEE, PermissionLevel.READ))}

    def test_one_permission_cannot_be_held_at_two_levels(self):
        with pytest.raises(ValueError, match="more than one level"):
            PermissionSet(
                frozenset(
                    {
                        Grant(READ_EMPLOYEE, PermissionLevel.READ),
                        Grant(READ_EMPLOYEE, PermissionLevel.WRITE),
                    }
                )
            )
