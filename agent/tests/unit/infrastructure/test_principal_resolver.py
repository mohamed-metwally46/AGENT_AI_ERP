"""Resolving an identity, and refusing to guess when it does not resolve."""

from __future__ import annotations

from pathlib import Path

import pytest

from erp_agent.domain.errors import ConfigurationError, PrincipalNotFoundError
from erp_agent.domain.models.principal import Principal
from erp_agent.domain.models.security_context import DataScope
from erp_agent.infrastructure.security.fixture_principal_resolver import (
    SUPPORTED_SCHEMA_VERSION,
    FixturePrincipalResolver,
    default_fixture_path,
)
from erp_agent.infrastructure.security.principal_resolver_port import PrincipalResolverPort

pytestmark = [pytest.mark.unit, pytest.mark.security]

TEST_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "principals.yaml"

ORGANISATION_ID = "00000000-0000-0000-0000-000000000001"
BRANCH_ID = "00000000-0000-0000-0000-000000000002"
SELF_ID = "00000000-0000-0000-0000-000000000005"
PAYROLL_ID = "00000000-0000-0000-0000-000000000006"

_VALID_ROW = """
schema_version: 1
principals:
  - user_id: "1"
    employee_id: "emp-1"
    scope: self
"""


@pytest.fixture
def resolver() -> FixturePrincipalResolver:
    return FixturePrincipalResolver.from_file(TEST_FIXTURE)


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "principals.yaml"
    path.write_text(content, encoding="utf-8")
    return path


class TestResolution:
    async def test_a_known_identity_resolves(self, resolver):
        principal = await resolver.resolve(ORGANISATION_ID)

        assert principal.employee_id == ORGANISATION_ID
        assert principal.scope is DataScope.ORGANISATION
        assert principal.role_names == frozenset({"executive"})

    async def test_scope_and_placement_come_through(self, resolver):
        principal = await resolver.resolve(BRANCH_ID)

        assert principal.scope is DataScope.BRANCH
        assert len(principal.allowed_branch_ids) == 2
        assert principal.dept_id is not None

    async def test_roles_come_through(self, resolver):
        principal = await resolver.resolve(PAYROLL_ID)

        assert "payroll" in principal.role_names
        assert "hr" not in principal.role_names

    async def test_surrounding_whitespace_is_tolerated(self, resolver):
        principal = await resolver.resolve(f"  {SELF_ID}  ")

        assert principal.employee_id == SELF_ID

    async def test_only_domain_types_cross_the_port(self, resolver):
        # The pydantic row model is strictly internal: the fixture's shape must
        # not leak upward and become something the API depends on.
        principal = await resolver.resolve(SELF_ID)

        assert type(principal) is Principal


class TestUnknownIdentity:
    async def test_an_unknown_identity_raises(self, resolver):
        with pytest.raises(PrincipalNotFoundError):
            await resolver.resolve("00000000-0000-0000-0000-00000000ffff")

    async def test_it_does_not_fall_back_to_self_scope(self, resolver):
        # The failure this guards: a default of SELF turns an unknown caller
        # into a valid one with a real employee's reach.
        with pytest.raises(PrincipalNotFoundError) as caught:
            await resolver.resolve("nobody")

        assert not caught.value.retryable

    @pytest.mark.parametrize("identity", ["", "   "])
    async def test_a_blank_identity_raises_rather_than_matching_anything(self, resolver, identity):
        with pytest.raises(PrincipalNotFoundError):
            await resolver.resolve(identity)

    async def test_the_error_discloses_nothing_about_other_principals(self, resolver):
        with pytest.raises(PrincipalNotFoundError) as caught:
            await resolver.resolve("nobody")

        # Echoing back what the caller already supplied is fine; anything else
        # would let the error be used to enumerate the fixture.
        assert caught.value.context == {"identity": "nobody"}
        assert ORGANISATION_ID not in str(caught.value.context)


class TestFixtureValidation:
    def test_the_shipped_test_fixture_is_valid(self, resolver):
        assert len(resolver) == 6
        assert resolver.source == TEST_FIXTURE

    def test_the_packaged_dev_fixture_is_valid(self):
        # Guards the file the application actually ships with. A broken dev
        # fixture would otherwise only surface when someone ran the app.
        packaged = FixturePrincipalResolver.from_file(default_fixture_path())

        assert len(packaged) >= 1

    def test_an_unrecognised_schema_version_is_refused(self, tmp_path):
        path = _write(tmp_path, _VALID_ROW.replace("schema_version: 1", "schema_version: 2"))

        with pytest.raises(ConfigurationError, match="schema_version"):
            FixturePrincipalResolver.from_file(path)

    def test_a_missing_schema_version_is_refused(self, tmp_path):
        path = _write(tmp_path, _VALID_ROW.replace("schema_version: 1\n", ""))

        with pytest.raises(ConfigurationError, match="schema_version"):
            FixturePrincipalResolver.from_file(path)

    def test_an_unknown_scope_is_refused(self, tmp_path):
        path = _write(tmp_path, _VALID_ROW.replace("scope: self", "scope: everything"))

        with pytest.raises(ConfigurationError, match="entry 0 is invalid"):
            FixturePrincipalResolver.from_file(path)

    def test_a_missing_required_field_is_refused(self, tmp_path):
        path = _write(tmp_path, _VALID_ROW.replace('    employee_id: "emp-1"\n', ""))

        with pytest.raises(ConfigurationError, match="employee_id"):
            FixturePrincipalResolver.from_file(path)

    def test_an_unknown_field_is_refused(self, tmp_path):
        # A misspelled `role_names` would otherwise be dropped silently and the
        # principal would resolve with no roles -- an under-privileged success,
        # which is much harder to notice than a failure.
        path = _write(tmp_path, _VALID_ROW + '    rolenames: ["hr"]\n')

        with pytest.raises(ConfigurationError, match="rolenames"):
            FixturePrincipalResolver.from_file(path)

    def test_a_scope_missing_its_required_value_is_refused(self, tmp_path):
        # DEPARTMENT scope with no dept_id would produce a predicate matching
        # nothing at question time. It fails at load instead.
        path = _write(tmp_path, _VALID_ROW.replace("scope: self", "scope: department"))

        with pytest.raises(ConfigurationError, match="DEPARTMENT scope requires dept_id"):
            FixturePrincipalResolver.from_file(path)

    def test_a_duplicate_employee_id_is_refused(self, tmp_path):
        # Which grant wins would depend on file order.
        path = _write(
            tmp_path,
            _VALID_ROW + '  - user_id: "2"\n    employee_id: "emp-1"\n    scope: organisation\n',
        )

        with pytest.raises(ConfigurationError, match="more than once"):
            FixturePrincipalResolver.from_file(path)

    def test_an_empty_principal_list_is_refused(self, tmp_path):
        path = _write(tmp_path, "schema_version: 1\nprincipals: []\n")

        with pytest.raises(ConfigurationError, match="non-empty"):
            FixturePrincipalResolver.from_file(path)

    def test_invalid_yaml_is_refused(self, tmp_path):
        path = _write(tmp_path, "schema_version: 1\nprincipals: [oh dear\n")

        with pytest.raises(ConfigurationError, match="not valid YAML"):
            FixturePrincipalResolver.from_file(path)

    def test_a_non_mapping_document_is_refused(self, tmp_path):
        path = _write(tmp_path, "- just\n- a\n- list\n")

        with pytest.raises(ConfigurationError, match="mapping at the top level"):
            FixturePrincipalResolver.from_file(path)

    def test_a_non_mapping_entry_is_refused(self, tmp_path):
        path = _write(tmp_path, "schema_version: 1\nprincipals:\n  - just-a-string\n")

        with pytest.raises(ConfigurationError, match="not a mapping"):
            FixturePrincipalResolver.from_file(path)

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ConfigurationError, match="could not be read"):
            FixturePrincipalResolver.from_file(tmp_path / "absent.yaml")

    def test_yaml_cannot_construct_arbitrary_python_objects(self, tmp_path):
        # safe_load, not load: a fixture must not be able to execute anything,
        # whatever else is wrong with it.
        path = _write(
            tmp_path,
            "schema_version: 1\nprincipals: !!python/object/apply:os.system ['echo pwned']\n",
        )

        with pytest.raises(ConfigurationError):
            FixturePrincipalResolver.from_file(path)


class TestPrincipalToSecurityContext:
    async def test_it_carries_identity_and_grant_across(self, resolver):
        principal = await resolver.resolve(BRANCH_ID)

        context = principal.to_security_context()

        assert context.employee_id == principal.employee_id
        assert context.scope is principal.scope
        assert context.role_names == principal.role_names

    async def test_the_reporting_tree_is_supplied_by_us_not_by_the_fixture(self, resolver):
        # `manager_of` is absent from the fixture schema on purpose: an identity
        # store that could assert a reporting tree would be choosing its own
        # visibility.
        principal = await resolver.resolve("00000000-0000-0000-0000-000000000004")
        reports = frozenset({"a", "b"})

        context = principal.to_security_context(manager_of=reports)

        assert context.manager_of == reports
        assert principal.to_security_context().manager_of == frozenset()

    async def test_an_incoherent_grant_cannot_be_constructed_at_all(self):
        # The rule holds at the Principal too, not only at SecurityContext:
        # otherwise a bad fixture row survives start-up and fails on a live
        # question instead.
        with pytest.raises(ValueError, match="BRANCH scope requires"):
            Principal(user_id="1", employee_id="e", scope=DataScope.BRANCH)


class TestThePortIsTheSeam:
    def test_the_fixture_resolver_implements_the_port(self):
        assert issubclass(FixturePrincipalResolver, PrincipalResolverPort)

    def test_the_port_exposes_exactly_one_method(self):
        import inspect

        methods = {
            name
            for name, _ in inspect.getmembers(PrincipalResolverPort, inspect.isfunction)
            if not name.startswith("_")
        }

        assert methods == {"resolve"}

    def test_resolve_is_asynchronous(self):
        # The Phase 9 implementation queries the database. A port whose
        # signature has to change when the adapter changes is not a seam.
        import inspect

        assert inspect.iscoroutinefunction(PrincipalResolverPort.resolve)

    def test_the_supported_schema_version_is_declared(self):
        assert SUPPORTED_SCHEMA_VERSION == 1
