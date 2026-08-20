"""The scope ladder, and the visibility it produces."""

from __future__ import annotations

import pytest

from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.policies.scope import EmployeeVisibility, visible_employees

pytestmark = [pytest.mark.unit, pytest.mark.security]

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
CAROL = "33333333-3333-3333-3333-333333333333"
SALES = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
CAIRO = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _context(scope: DataScope, **overrides: object) -> SecurityContext:
    defaults: dict[str, object] = {
        "user_id": "42",
        "employee_id": ALICE,
        "scope": scope,
        "dept_id": SALES,
        "branch_id": CAIRO,
    }
    return SecurityContext(**{**defaults, **overrides})  # type: ignore[arg-type]


class TestTheLadder:
    def test_the_rungs_are_ordered_least_to_most_permissive(self):
        ranks = [scope.rank for scope in DataScope]

        assert ranks == sorted(ranks)
        assert DataScope.SELF.rank < DataScope.ORGANISATION.rank

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_a_scope_includes_itself(self, scope):
        assert scope.includes(scope)

    def test_a_wider_scope_includes_a_narrower_one(self):
        assert DataScope.BRANCH.includes(DataScope.SELF)
        assert not DataScope.SELF.includes(DataScope.BRANCH)


class TestContextValidation:
    @pytest.mark.parametrize("blank", ["", "   "])
    def test_a_blank_identifier_is_refused(self, blank):
        with pytest.raises(ValueError, match="non-empty"):
            SecurityContext(user_id=blank, employee_id=ALICE, scope=DataScope.SELF)

    def test_department_scope_without_a_department_is_refused(self):
        # Would silently match nothing -- or, with a different predicate bug,
        # everything.
        with pytest.raises(ValueError, match="DEPARTMENT scope requires dept_id"):
            SecurityContext(
                user_id="1", employee_id=ALICE, scope=DataScope.DEPARTMENT, dept_id=None
            )

    def test_branch_scope_without_a_branch_is_refused(self):
        with pytest.raises(ValueError, match="BRANCH scope requires"):
            SecurityContext(user_id="1", employee_id=ALICE, scope=DataScope.BRANCH)

    def test_self_scope_needs_nothing_else(self):
        assert SecurityContext(user_id="1", employee_id=ALICE, scope=DataScope.SELF).is_self_scoped


class TestVisibility:
    def test_self_scope_sees_only_its_own_record(self):
        visibility = visible_employees(_context(DataScope.SELF))

        assert visibility.employee_ids == frozenset({ALICE})
        assert not visibility.unrestricted

    def test_team_scope_sees_reports_and_the_manager_themselves(self):
        # A manager must be able to answer about themselves without a second
        # scope.
        visibility = visible_employees(_context(DataScope.TEAM, manager_of=frozenset({BOB, CAROL})))

        assert visibility.employee_ids == frozenset({ALICE, BOB, CAROL})

    def test_a_manager_with_no_reports_sees_only_themselves(self):
        visibility = visible_employees(_context(DataScope.TEAM))

        assert visibility.employee_ids == frozenset({ALICE})

    def test_department_scope_restricts_by_department(self):
        visibility = visible_employees(_context(DataScope.DEPARTMENT))

        assert visibility.dept_id == SALES
        assert visibility.employee_ids == frozenset()

    def test_branch_scope_restricts_by_branch(self):
        visibility = visible_employees(
            _context(DataScope.BRANCH, allowed_branch_ids=frozenset({CAIRO}))
        )

        assert visibility.branch_ids == frozenset({CAIRO})

    def test_organisation_scope_is_the_only_unrestricted_outcome(self):
        visibility = visible_employees(_context(DataScope.ORGANISATION))

        assert visibility.unrestricted

    @pytest.mark.parametrize(
        "scope",
        [DataScope.SELF, DataScope.TEAM, DataScope.DEPARTMENT, DataScope.BRANCH],
    )
    def test_no_restricted_scope_ever_becomes_unrestricted(self, scope):
        # The failure that matters: a scope falling through to "no predicate" is
        # a total authorization bypass.
        context = _context(scope, allowed_branch_ids=frozenset({CAIRO}))

        assert not visible_employees(context).unrestricted

    def test_every_scope_is_handled(self):
        # A new rung must fail loudly rather than default to permissive.
        for scope in DataScope:
            assert (
                visible_employees(_context(scope, allowed_branch_ids=frozenset({CAIRO})))
                is not None
            )


class TestVisibilityInvariants:
    def test_an_unrestricted_visibility_carries_no_restriction(self):
        with pytest.raises(ValueError, match="no restriction"):
            EmployeeVisibility(unrestricted=True, dept_id=SALES)

    def test_a_restricted_visibility_restricts_on_exactly_one_dimension(self):
        with pytest.raises(ValueError, match="exactly one dimension"):
            EmployeeVisibility(employee_ids=frozenset({ALICE}), dept_id=SALES)

    def test_a_visibility_that_restricts_on_nothing_is_refused(self):
        # This is the dangerous shape: it would render as an absent WHERE clause
        # and expose the whole table.
        with pytest.raises(ValueError, match="exactly one dimension"):
            EmployeeVisibility()
