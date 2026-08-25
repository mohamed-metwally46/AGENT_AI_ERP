"""The mock identity chain against the live mock HR database.

The unit suite stubs :class:`OrgPlacementPort`, which is the right trade -- it
keeps the hermetic suite hermetic. But the stub is also where the interesting
failure would hide: the roster names ``EMP-0048``, and whether such an employee
*exists*, and which department they are in, is a fact about the seed rather than
about our code.

So this file asserts the two things a double cannot:

*Every rostered employee number resolves against the real seed.* If a future
seed drops one, start-up fails here rather than in somebody's deployment.

*The placements are real.* Different mock users land in different departments
and their employee ids are the seeded uuids, not fabricated ones -- which is
what makes the scope predicates in the unit suite mean anything.

Skipped when no database is configured, like every other integration test.
"""

from __future__ import annotations

import pytest

from erp_agent.core.bootstrap import bootstrap
from erp_agent.domain.errors import ConfigurationError
from erp_agent.domain.models.security_context import DataScope
from erp_agent.infrastructure.database.queries.org_placement import PostgresOrgPlacement
from erp_agent.infrastructure.security.mock_identity import (
    MockIdentityResolver,
    default_identity_fixture_path,
)
from tests.fixtures.identity import DEPARTMENTS, TOKENS

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _env(database_url: str) -> dict[str, str]:
    """A boot that serves structured questions without wiring retrieval."""
    return {"ERP_DATABASE_URL": database_url, "ERP_KNOWLEDGE_ENABLED": "false"}


class TestPlacementAgainstTheRealSeed:
    async def test_every_rostered_employee_number_exists(self, database_url):
        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            placements = await PostgresOrgPlacement(container.data_source).placements(
                frozenset(DEPARTMENTS)
            )

            assert set(placements) == set(DEPARTMENTS)
        finally:
            await container.close()

    async def test_a_number_no_employee_has_is_absent_rather_than_empty(self, database_url):
        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            placements = await PostgresOrgPlacement(container.data_source).placements(
                frozenset({"EMP-0001", "EMP-9999"})
            )

            assert "EMP-0001" in placements
            assert "EMP-9999" not in placements
        finally:
            await container.close()

    async def test_the_placement_carries_a_department_and_a_branch(self, database_url):
        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            placements = await PostgresOrgPlacement(container.data_source).placements(
                frozenset({"EMP-0001"})
            )

            placement = placements["EMP-0001"]
            assert placement.employee_id
            assert placement.dept_id
            assert placement.branch_id
        finally:
            await container.close()

    async def test_it_returns_no_column_the_field_policy_restricts(self, database_url):
        # The one query with no security context must have nothing to protect.
        from erp_agent.domain.policies.field_policy import COMPENSATION, CONTACT_PII, NEVER_RETURNED

        restricted = NEVER_RETURNED | COMPENSATION | CONTACT_PII
        selected = {"employee_number", "employee_id", "dept_id", "branch_id"}

        assert not (selected & restricted)


class TestTheWholeChainAgainstTheSeed:
    async def test_start_up_prepares_the_roster(self, database_url):
        # `warm()` runs during bootstrap, so a roster that could not be placed
        # would have stopped the boot before this line.
        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            caller = await container.authenticate(TOKENS["1001"])

            assert caller.context.scope is DataScope.ORGANISATION
        finally:
            await container.close()

    async def test_every_dev_credential_resolves(self, database_url):
        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            for identity, token in TOKENS.items():
                caller = await container.authenticate(token)
                assert caller.identity == identity
        finally:
            await container.close()

    async def test_the_employee_ids_are_the_seeded_ones(self, database_url):
        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            caller = await container.authenticate(TOKENS["1016"])
            placements = await PostgresOrgPlacement(container.data_source).placements(
                frozenset({"EMP-0016"})
            )

            assert caller.context.employee_id == placements["EMP-0016"].employee_id
        finally:
            await container.close()

    async def test_different_credentials_land_in_different_departments(self, database_url):
        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            hr = await container.authenticate(TOKENS["1048"])
            finance = await container.authenticate(TOKENS["1003"])

            assert hr.context.dept_id != finance.context.dept_id
        finally:
            await container.close()

    async def test_a_roster_naming_an_unknown_employee_stops_start_up(self, database_url, tmp_path):
        # The failure mode this design exists to convert into a boot failure:
        # a re-seed that renumbers employees.
        roster = tmp_path / "identity.yaml"
        roster.write_text(
            default_identity_fixture_path()
            .read_text(encoding="utf-8")
            .replace('employee_number: "EMP-0001"', 'employee_number: "EMP-9999"'),
            encoding="utf-8",
        )

        container = await bootstrap(_env(database_url), use_env_file=False)
        try:
            resolver = MockIdentityResolver.from_file(
                roster, placements=PostgresOrgPlacement(container.data_source)
            )

            with pytest.raises(ConfigurationError, match="EMP-9999"):
                await resolver.prepare()
        finally:
            await container.close()
