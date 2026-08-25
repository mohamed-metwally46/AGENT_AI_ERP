"""The development-only identity adapter.

Stands where the ERP's ``users`` / ``user_roles`` / ``roles`` /
``role_permissions`` tables will stand. It implements
:class:`~erp_agent.domain.ports.principal_resolver.PrincipalResolverPort` and
nothing above the port can tell which implementation it got -- which is the
property that makes the cutover an adapter swap rather than a rewrite.

What is tested here is the adapter's *own* obligations, since the resolution
rule itself is proved in ``tests/unit/domain/test_role_catalogue.py``:

*Everything is validated at load.* An unknown role, an unknown resource, a
repeated user, a bad schema version -- all fail before the process serves
anything. A malformed grant discovered mid-request is a malformed grant
discovered as somebody's failed question.

*Org placement comes from the database, not the file.* The fixture names
``EMP-0001``; the employee id, department and branch are looked up. That is what
survives a re-seed, and it is also the shape the ERP resolver will have.

*There is no fallback anywhere.* Unknown identity, unresolvable employee number,
unprepared resolver: each raises. None of them quietly becomes a narrower
principal, because an authentication failure that returns a working principal is
the worst outcome available.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from erp_agent.domain.errors import ConfigurationError, PrincipalNotFoundError
from erp_agent.domain.models.authorization import Action, Permission, Resource
from erp_agent.domain.models.security_context import DataScope
from erp_agent.infrastructure.security.mock_identity import (
    MockIdentityResolver,
    default_identity_fixture_path,
    packaged_role_catalogue,
)
from tests.fixtures.identity import (
    BRANCH_HQ,
    DEPARTMENTS,
    DEPT_EXE,
    DEPT_HR,
    InMemoryPlacements,
)

pytestmark = pytest.mark.unit

READ_CONTACT_PII = Permission(Resource.HR_CONTACT_PII, Action.READ)
READ_COMPENSATION = Permission(Resource.HR_COMPENSATION, Action.READ)
READ_EMPLOYEE = Permission(Resource.HR_EMPLOYEE, Action.READ)


async def _prepared(path: Path | None = None) -> MockIdentityResolver:
    resolver = MockIdentityResolver.from_file(
        path or default_identity_fixture_path(), placements=InMemoryPlacements()
    )
    await resolver.prepare()
    return resolver


def _fixture(body: str, tmp_path: Path) -> Path:
    path = tmp_path / "identity.yaml"
    path.write_text(body, encoding="utf-8")
    return path


_MINIMAL = """
schema_version: 2
roles:
  - name: employee
    scope: self
    grants:
      - {resource: hr.employee, action: read, level: read}
users:
  - user_id: "1016"
    employee_number: "EMP-0016"
    roles: [employee]
"""


class TestThePackagedRoster:
    async def test_it_loads(self):
        resolver = await _prepared()
        assert len(resolver) >= 5

    async def test_it_asks_the_database_only_for_the_numbers_it_names(self):
        placements = InMemoryPlacements()
        resolver = MockIdentityResolver.from_file(
            default_identity_fixture_path(), placements=placements
        )
        await resolver.prepare()

        assert placements.asked_for == frozenset(DEPARTMENTS)

    async def test_the_admin_reads_the_whole_organisation(self):
        principal = await (await _prepared()).resolve("1001")
        assert principal.scope is DataScope.ORGANISATION

    async def test_the_hr_manager_is_branch_scoped(self):
        principal = await (await _prepared()).resolve("1002")
        assert principal.scope is DataScope.BRANCH

    async def test_the_hr_employee_is_department_scoped(self):
        principal = await (await _prepared()).resolve("1048")
        assert principal.scope is DataScope.DEPARTMENT

    async def test_the_sales_manager_is_team_scoped(self):
        principal = await (await _prepared()).resolve("1008")
        assert principal.scope is DataScope.TEAM

    async def test_the_plain_employee_is_self_scoped(self):
        principal = await (await _prepared()).resolve("1016")
        assert principal.scope is DataScope.SELF

    async def test_every_role_the_roster_names_is_defined(self):
        # Guards the failure the loader is meant to catch: a roster naming a
        # role the catalogue dropped would resolve to less access than intended.
        catalogue = packaged_role_catalogue()
        resolver = await _prepared()

        for identity in resolver.identities:
            principal = await resolver.resolve(identity)
            assert all(name in catalogue for name in principal.role_names)


class TestOrgPlacement:
    async def test_the_employee_id_comes_from_the_database(self):
        principal = await (await _prepared()).resolve("1016")
        assert principal.employee_id == "emp-id-EMP-0016"

    async def test_the_department_comes_from_the_database(self):
        principal = await (await _prepared()).resolve("1048")
        assert principal.dept_id == DEPT_HR

    async def test_a_branch_scoped_principal_reaches_its_own_branch(self):
        principal = await (await _prepared()).resolve("1002")
        assert principal.allowed_branch_ids == frozenset({BRANCH_HQ})

    async def test_an_employee_number_the_database_does_not_have_fails_at_prepare(self):
        resolver = MockIdentityResolver.from_file(
            default_identity_fixture_path(),
            placements=InMemoryPlacements({"EMP-0001": DEPT_EXE}),
        )

        with pytest.raises(ConfigurationError, match="EMP-"):
            await resolver.prepare()

    async def test_resolving_before_preparing_is_refused(self, tmp_path):
        # Not a silent empty result: an unprepared resolver that answered would
        # answer with a principal whose placement nobody looked up.
        resolver = MockIdentityResolver.from_file(
            _fixture(_MINIMAL, tmp_path), placements=InMemoryPlacements()
        )

        with pytest.raises(ConfigurationError, match="prepare"):
            await resolver.resolve("1016")


class TestMultipleRoles:
    async def test_the_scope_is_the_widest_of_the_held_roles(self):
        # 1003 holds employee (self) and payroll_officer (department).
        principal = await (await _prepared()).resolve("1003")
        assert principal.scope is DataScope.DEPARTMENT

    async def test_it_keeps_the_permissions_of_the_narrower_role_too(self):
        principal = await (await _prepared()).resolve("1006")
        assert principal.permissions.allows(READ_CONTACT_PII)

    async def test_both_role_names_are_carried(self):
        principal = await (await _prepared()).resolve("1006")
        assert principal.role_names == frozenset({"hr_employee", "payroll_officer"})

    async def test_a_permission_only_one_role_grants_is_still_held(self):
        # payroll_officer grants compensation; `employee` never does.
        principal = await (await _prepared()).resolve("1003")
        assert principal.permissions.allows(READ_COMPENSATION)

    async def test_that_principal_is_widened_by_the_second_role(self):
        principal = await (await _prepared()).resolve("1003")
        assert principal.scope is DataScope.DEPARTMENT


class TestDifferentUsersGetDifferentGrants:
    async def test_only_some_principals_may_read_contact_pii(self):
        resolver = await _prepared()
        permitted = {
            identity
            for identity in resolver.identities
            if (await resolver.resolve(identity)).permissions.allows(READ_CONTACT_PII)
        }

        assert permitted == {"1001", "1002", "1006", "1048"}

    async def test_only_some_principals_may_read_compensation(self):
        resolver = await _prepared()
        permitted = {
            identity
            for identity in resolver.identities
            if (await resolver.resolve(identity)).permissions.allows(READ_COMPENSATION)
        }

        assert permitted == {"1001", "1003", "1006"}

    async def test_every_principal_may_read_employee_records(self):
        resolver = await _prepared()
        for identity in resolver.identities:
            assert (await resolver.resolve(identity)).permissions.allows(READ_EMPLOYEE)


class TestRefusals:
    async def test_an_unknown_identity_resolves_to_nothing(self):
        with pytest.raises(PrincipalNotFoundError):
            await (await _prepared()).resolve("9999")

    async def test_the_error_discloses_no_other_identity(self):
        resolver = await _prepared()
        with pytest.raises(PrincipalNotFoundError) as raised:
            await resolver.resolve("9999")

        assert "1001" not in str(raised.value)


class TestFixtureValidation:
    async def test_an_unrecognised_schema_version_is_refused(self, tmp_path):
        body = _MINIMAL.replace("schema_version: 2", "schema_version: 1")
        with pytest.raises(ConfigurationError, match="schema_version"):
            MockIdentityResolver.from_file(
                _fixture(body, tmp_path), placements=InMemoryPlacements()
            )

    async def test_a_role_the_catalogue_does_not_define_is_refused(self, tmp_path):
        body = _MINIMAL.replace("roles: [employee]", "roles: [auditor]")
        with pytest.raises(ConfigurationError, match="auditor"):
            MockIdentityResolver.from_file(
                _fixture(body, tmp_path), placements=InMemoryPlacements()
            )

    async def test_a_resource_this_build_does_not_implement_is_refused(self, tmp_path):
        body = _MINIMAL.replace("resource: hr.employee", "resource: hr.telepathy")
        with pytest.raises(ConfigurationError, match=r"hr\.telepathy"):
            MockIdentityResolver.from_file(
                _fixture(body, tmp_path), placements=InMemoryPlacements()
            )

    async def test_a_repeated_user_id_is_refused(self, tmp_path):
        body = (
            _MINIMAL
            + """
  - user_id: "1016"
    employee_number: "EMP-0008"
    roles: [employee]
"""
        )
        with pytest.raises(ConfigurationError, match="more than once"):
            MockIdentityResolver.from_file(
                _fixture(body, tmp_path), placements=InMemoryPlacements()
            )

    async def test_a_user_holding_no_roles_is_refused(self, tmp_path):
        body = _MINIMAL.replace("roles: [employee]", "roles: []")
        with pytest.raises(ConfigurationError):
            MockIdentityResolver.from_file(
                _fixture(body, tmp_path), placements=InMemoryPlacements()
            )

    async def test_an_unknown_field_is_refused_rather_than_ignored(self, tmp_path):
        # A misspelled `roles:` would otherwise be dropped and the user would
        # resolve with no roles at all.
        body = _MINIMAL.replace("roles: [employee]", "roles: [employee]\n    rolez: [admin]")
        with pytest.raises(ConfigurationError):
            MockIdentityResolver.from_file(
                _fixture(body, tmp_path), placements=InMemoryPlacements()
            )

    async def test_a_fixture_with_no_users_is_refused(self, tmp_path):
        body = _MINIMAL.split("users:")[0] + "users: []\n"
        with pytest.raises(ConfigurationError):
            MockIdentityResolver.from_file(
                _fixture(body, tmp_path), placements=InMemoryPlacements()
            )

    async def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ConfigurationError):
            MockIdentityResolver.from_file(tmp_path / "nope.yaml", placements=InMemoryPlacements())
