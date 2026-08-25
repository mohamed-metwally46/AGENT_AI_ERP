"""Resolving a set of role names into one grant.

This is the ``Roles -> Permissions`` rung of the chain, and the only place the
multi-role rule is stated:

* **scope is the maximum** rung any held role grants;
* **permissions are the union**, at the highest level any role confers.

Both directions are tested, because the interesting failure is asymmetric: a
scope bug under-privileges (annoying), a permission bug over-privileges (a
disclosure). The unknown-role case is tested hardest of all -- silently dropping
a role name the catalogue does not define would resolve a principal to *less*
than intended, which nobody notices until an audit.

Pure domain: no fixture, no file, no database.
"""

from __future__ import annotations

import pytest

from erp_agent.domain.models.authorization import (
    Action,
    Permission,
    PermissionLevel,
    PermissionSet,
    Resource,
)
from erp_agent.domain.models.role import Role, RoleCatalogue, UnknownRoleError
from erp_agent.domain.models.security_context import DataScope

pytestmark = pytest.mark.unit

READ_EMPLOYEE = Permission(Resource.HR_EMPLOYEE, Action.READ)
READ_CONTACT_PII = Permission(Resource.HR_CONTACT_PII, Action.READ)
READ_COMPENSATION = Permission(Resource.HR_COMPENSATION, Action.READ)


def _catalogue() -> RoleCatalogue:
    return RoleCatalogue(
        (
            Role(
                name="employee",
                scope=DataScope.SELF,
                grants=PermissionSet.of([(READ_EMPLOYEE, PermissionLevel.READ)]),
            ),
            Role(
                name="team_lead",
                scope=DataScope.TEAM,
                grants=PermissionSet.of([(READ_EMPLOYEE, PermissionLevel.READ)]),
            ),
            Role(
                name="hr_employee",
                scope=DataScope.DEPARTMENT,
                grants=PermissionSet.of(
                    [
                        (READ_EMPLOYEE, PermissionLevel.READ),
                        (READ_CONTACT_PII, PermissionLevel.READ),
                    ]
                ),
            ),
            Role(
                name="payroll_officer",
                scope=DataScope.DEPARTMENT,
                grants=PermissionSet.of([(READ_COMPENSATION, PermissionLevel.READ)]),
            ),
        )
    )


class TestASingleRole:
    def test_the_scope_is_the_role_s_scope(self):
        resolved = _catalogue().resolve(["hr_employee"])
        assert resolved.scope is DataScope.DEPARTMENT

    def test_the_permissions_are_the_role_s_grants(self):
        resolved = _catalogue().resolve(["hr_employee"])
        assert resolved.permissions.allows(READ_CONTACT_PII)

    def test_it_grants_nothing_the_role_does_not(self):
        resolved = _catalogue().resolve(["hr_employee"])
        assert not resolved.permissions.allows(READ_COMPENSATION)

    def test_the_role_name_is_carried_through(self):
        resolved = _catalogue().resolve(["hr_employee"])
        assert resolved.role_names == frozenset({"hr_employee"})


class TestSeveralRoles:
    def test_the_scope_is_the_widest_any_role_grants(self):
        # SELF and DEPARTMENT held together must reach the department, or a
        # promotion that adds a role would narrow the holder's view.
        resolved = _catalogue().resolve(["employee", "hr_employee"])
        assert resolved.scope is DataScope.DEPARTMENT

    def test_the_order_the_roles_are_given_in_does_not_matter(self):
        catalogue = _catalogue()
        assert catalogue.resolve(["employee", "hr_employee"]) == catalogue.resolve(
            ["hr_employee", "employee"]
        )

    def test_permissions_from_every_role_are_held(self):
        resolved = _catalogue().resolve(["hr_employee", "payroll_officer"])

        assert resolved.permissions.allows(READ_CONTACT_PII)
        assert resolved.permissions.allows(READ_COMPENSATION)

    def test_a_permission_neither_role_holds_alone_is_still_refused(self):
        resolved = _catalogue().resolve(["employee", "team_lead"])
        assert not resolved.permissions.allows(READ_CONTACT_PII)

    def test_every_role_name_is_carried_through(self):
        resolved = _catalogue().resolve(["employee", "payroll_officer"])
        assert resolved.role_names == frozenset({"employee", "payroll_officer"})

    def test_a_role_repeated_is_held_once(self):
        resolved = _catalogue().resolve(["employee", "employee"])
        assert resolved.role_names == frozenset({"employee"})


class TestRefusals:
    def test_an_unknown_role_is_refused_rather_than_ignored(self):
        # Dropping it would resolve the principal to less than intended, and a
        # quietly under-privileged principal is a bug nobody reports.
        with pytest.raises(UnknownRoleError, match="auditor"):
            _catalogue().resolve(["employee", "auditor"])

    def test_holding_no_roles_at_all_is_refused(self):
        # There is no "default" scope. An identity the catalogue cannot place
        # has no reach, and the safe reading of no reach is no access.
        with pytest.raises(UnknownRoleError, match="no roles"):
            _catalogue().resolve([])

    def test_role_names_are_matched_case_insensitively(self):
        resolved = _catalogue().resolve(["HR_Employee"])
        assert resolved.scope is DataScope.DEPARTMENT

    def test_a_catalogue_cannot_define_one_role_twice(self):
        duplicate = Role(name="employee", scope=DataScope.ORGANISATION, grants=PermissionSet())
        with pytest.raises(ValueError, match="more than once"):
            RoleCatalogue((*_catalogue().roles, duplicate))

    def test_a_catalogue_with_no_roles_is_refused(self):
        with pytest.raises(ValueError, match="at least one role"):
            RoleCatalogue(())


class TestRoleInvariants:
    def test_a_role_must_be_named(self):
        with pytest.raises(ValueError, match="non-empty"):
            Role(name="  ", scope=DataScope.SELF, grants=PermissionSet())

    def test_a_department_scoped_role_needs_no_department_of_its_own(self):
        # The department comes from the employee record, not the grant
        # (Plan.md section 8.5, gap S1). A role may be DEPARTMENT-scoped
        # without naming one; the principal supplies it.
        role = Role(name="x", scope=DataScope.DEPARTMENT, grants=PermissionSet())
        assert role.scope is DataScope.DEPARTMENT
