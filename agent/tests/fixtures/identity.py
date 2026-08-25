"""Driving the real identity chain in a test, without a database.

The only thing stubbed is :class:`OrgPlacementPort` -- the one step that is
genuinely a database read. Everything else is production code: the real token
adapter, the real roster, the real role catalogue, the real
:class:`~erp_agent.application.security.Authenticate`.

That matters more than it looks. A test that built a
:class:`SecurityContext` by hand would be asserting against a scope nobody can
obtain; these contexts are the ones a real credential really produces, so a
change to the roster or the catalogue shows up as a failing test rather than as
a passing test about a fiction.
"""

from __future__ import annotations

from collections.abc import Mapping

from erp_agent.application.security import Authenticate, AuthenticatedCaller
from erp_agent.domain.ports.org_placement import OrgPlacement, OrgPlacementPort
from erp_agent.infrastructure.security.fixture_access_tokens import (
    FixtureAccessTokens,
    default_token_fixture_path,
)
from erp_agent.infrastructure.security.mock_identity import (
    MockIdentityResolver,
    default_identity_fixture_path,
)

__all__ = [
    "BRANCH_HQ",
    "BRANCH_OTHER",
    "DEPARTMENTS",
    "DEPT_EXE",
    "DEPT_FIN",
    "DEPT_HR",
    "DEPT_SLS",
    "TOKENS",
    "InMemoryPlacements",
    "mock_callers",
    "mock_resolver",
]

# Synthetic placement, stable across re-seeds because nothing here comes from a
# database. Shaped like the real values (uuid-ish strings) so nothing depends on
# them being short.
BRANCH_HQ = "b1111111-1111-1111-1111-111111111111"
BRANCH_OTHER = "b2222222-2222-2222-2222-222222222222"
DEPT_EXE = "d1111111-1111-1111-1111-111111111111"
DEPT_HR = "d2222222-2222-2222-2222-222222222222"
DEPT_SLS = "d3333333-3333-3333-3333-333333333333"
DEPT_FIN = "d4444444-4444-4444-4444-444444444444"

#: Every employee number the packaged roster names, and where they sit.
#: Mirrors database/seed/hr_seed.sql: 1 exec, 2-5 directors, 6-15 managers,
#: 16+ staff, with EMP-0016 in Sales and EMP-0048 in HR.
DEPARTMENTS: Mapping[str, str] = {
    "EMP-0001": DEPT_EXE,
    "EMP-0002": DEPT_HR,
    "EMP-0003": DEPT_FIN,
    "EMP-0006": DEPT_HR,
    "EMP-0008": DEPT_SLS,
    "EMP-0016": DEPT_SLS,
    "EMP-0048": DEPT_HR,
}

#: The dev credential for each rostered identity. Kept here so a test names a
#: token rather than repeating the fixture, and so a renamed token breaks one
#: line instead of several files.
TOKENS: Mapping[str, str] = {
    "1001": "dev-token-admin-1001",
    "1002": "dev-token-hr-manager-1002",
    "1048": "dev-token-hr-employee-1048",
    "1008": "dev-token-sales-manager-1008",
    "1016": "dev-token-employee-1016",
    "1006": "dev-token-hr-payroll-1006",
    "1003": "dev-token-payroll-employee-1003",
}


class InMemoryPlacements(OrgPlacementPort):
    """Org placement without a database.

    Stands in for :class:`PostgresOrgPlacement`. Every employee number the
    packaged roster names is present by default, so a failure here is about the
    code under test rather than about the double.
    """

    def __init__(self, known: Mapping[str, str] | None = None) -> None:
        """Hold the employee-number-to-department map to answer from."""
        self._known = dict(known if known is not None else DEPARTMENTS)
        self.asked_for: frozenset[str] = frozenset()

    async def placements(self, employee_numbers: frozenset[str]) -> Mapping[str, OrgPlacement]:
        """Answer for the numbers this double knows, omitting the rest."""
        self.asked_for = frozenset(employee_numbers)
        return {
            number: OrgPlacement(
                employee_number=number,
                employee_id=f"emp-id-{number}",
                dept_id=self._known[number],
                branch_id=BRANCH_HQ,
            )
            for number in employee_numbers
            if number in self._known
        }


async def mock_resolver(
    placements: OrgPlacementPort | None = None,
) -> MockIdentityResolver:
    """A prepared resolver over the packaged dev roster.

    Args:
        placements: Where placement comes from. Defaults to a double that knows
            every rostered employee.

    Returns:
        The prepared resolver.
    """
    resolver = MockIdentityResolver.from_file(
        default_identity_fixture_path(),
        placements=placements if placements is not None else InMemoryPlacements(),
    )
    await resolver.prepare()
    return resolver


async def mock_callers() -> dict[str, AuthenticatedCaller]:
    """Authenticate every dev credential, keyed by identity.

    Goes through :class:`Authenticate` rather than calling the resolver, so the
    whole chain is exercised: credential -> identity -> roles -> permissions ->
    principal -> security context.

    Returns:
        One authenticated caller per rostered identity.
    """
    authenticate = Authenticate(
        tokens=FixtureAccessTokens.from_file(default_token_fixture_path()),
        principals=await mock_resolver(),
    )
    return {identity: await authenticate(token) for identity, token in TOKENS.items()}
