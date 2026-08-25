"""The permissions a resolved context carries, and how they get there.

Plan.md section 8.1 lists ``permission_levels`` as part of the
``SecurityContext``. This is that field, and the two properties that make it
worth having:

*It is the only authority.* ``role_names`` survives, because it is real and
because :meth:`SecurityContext.as_log_payload` reports it, but nothing
authorizes on it any more -- ``has_any_role`` is gone, so a future call site
cannot reach for it.

*It defaults to permitting nothing.* A context built without permissions
refuses everything rather than allowing everything, so the dangerous direction
needs an explicit grant.
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
from erp_agent.domain.models.principal import Principal
from erp_agent.domain.models.security_context import DataScope, SecurityContext

pytestmark = pytest.mark.unit

READ_CONTACT_PII = Permission(Resource.HR_CONTACT_PII, Action.READ)
READ_COMPENSATION = Permission(Resource.HR_COMPENSATION, Action.READ)


def _context(permissions: PermissionSet | None = None) -> SecurityContext:
    return SecurityContext(
        user_id="1001",
        employee_id="e-1",
        scope=DataScope.SELF,
        permissions=permissions if permissions is not None else PermissionSet(),
    )


class TestFailingClosed:
    def test_a_context_with_no_permissions_permits_nothing(self):
        assert not _context().may(READ_CONTACT_PII)

    def test_an_organisation_scoped_context_still_needs_the_permission(self):
        # Scope and permission are orthogonal: reaching every row does not mean
        # reading every column of it.
        everywhere = SecurityContext(user_id="1", employee_id="e-1", scope=DataScope.ORGANISATION)
        assert not everywhere.may(READ_COMPENSATION)


class TestGrantedPermissions:
    def test_a_granted_permission_is_permitted(self):
        granted = PermissionSet.of([(READ_CONTACT_PII, PermissionLevel.READ)])
        assert _context(granted).may(READ_CONTACT_PII)

    def test_an_unrelated_permission_is_still_refused(self):
        granted = PermissionSet.of([(READ_CONTACT_PII, PermissionLevel.READ)])
        assert not _context(granted).may(READ_COMPENSATION)


class TestRolesAreNoLongerAnAuthority:
    def test_has_any_role_no_longer_exists(self):
        # The method is deleted rather than deprecated: leaving it would leave a
        # second source of authority for a future call site to reach for, and
        # the whole point of the permission set is that there is only one.
        assert not hasattr(_context(), "has_any_role")

    def test_role_names_are_still_carried_for_logging(self):
        context = SecurityContext(
            user_id="1",
            employee_id="e-1",
            scope=DataScope.SELF,
            role_names=frozenset({"hr_employee"}),
        )
        assert context.as_log_payload()["roles"] == "hr_employee"


class TestPrincipalCarriesThePermissionsThrough:
    def test_the_context_holds_the_principal_s_permissions(self):
        granted = PermissionSet.of([(READ_CONTACT_PII, PermissionLevel.READ)])
        principal = Principal(
            user_id="1001",
            employee_id="e-1",
            scope=DataScope.SELF,
            permissions=granted,
        )

        assert principal.to_security_context().may(READ_CONTACT_PII)

    def test_a_principal_with_no_permissions_yields_a_context_with_none(self):
        principal = Principal(user_id="1001", employee_id="e-1", scope=DataScope.SELF)
        assert not principal.to_security_context().may(READ_CONTACT_PII)

    def test_computing_the_reporting_tree_does_not_disturb_the_permissions(self):
        granted = PermissionSet.of([(READ_CONTACT_PII, PermissionLevel.READ)])
        principal = Principal(
            user_id="1001",
            employee_id="e-1",
            scope=DataScope.TEAM,
            permissions=granted,
        )

        context = principal.to_security_context(manager_of=frozenset({"e-2", "e-3"}))

        assert context.may(READ_CONTACT_PII)
        assert context.manager_of == frozenset({"e-2", "e-3"})


class TestTheLogPayload:
    def test_it_reports_which_permissions_were_held(self):
        granted = PermissionSet.of([(READ_CONTACT_PII, PermissionLevel.READ)])
        payload = _context(granted).as_log_payload()

        assert payload["permissions"] == "hr.contact_pii:read=read"

    def test_it_still_reports_no_reachable_identifiers(self):
        # Logging a manager's whole reporting tree on every request is how an
        # access log becomes an org chart.
        payload = SecurityContext(
            user_id="1",
            employee_id="e-1",
            scope=DataScope.TEAM,
            manager_of=frozenset({"e-2", "e-3"}),
        ).as_log_payload()

        assert "e-2" not in str(payload)
        assert payload["team_size"] == 2
