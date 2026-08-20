"""Where the authorization model becomes a WHERE clause.

The failure mode these tests exist for is a predicate that renders to nothing.
Everything else is a syntax error someone notices; an absent restriction is a
silent, total bypass.
"""

from __future__ import annotations

import pytest

from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.policies.scope import EmployeeVisibility, visible_employees
from erp_agent.infrastructure.database.predicates import (
    ALWAYS_TRUE,
    Predicate,
    combine_and,
    scope_predicate,
    where_clause,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
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


class TestAPredicateIsNeverEmpty:
    def test_an_empty_fragment_cannot_be_constructed(self):
        # The whole point of the module. An empty fragment concatenated into a
        # template silently removes the restriction.
        with pytest.raises(ValueError, match="never be empty"):
            Predicate("")

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
    def test_blank_fragments_are_refused(self, blank):
        with pytest.raises(ValueError, match="never be empty"):
            Predicate(blank)

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_every_scope_renders_a_non_empty_predicate(self, scope):
        context = _context(scope, allowed_branch_ids=frozenset({CAIRO}))

        predicate = scope_predicate(visible_employees(context))

        assert predicate.sql.strip()

    def test_unrestricted_renders_true_explicitly(self):
        # "TRUE", not "". A reader must be able to see the decision in the SQL.
        predicate = scope_predicate(EmployeeVisibility(unrestricted=True))

        assert predicate.sql == "TRUE"
        assert predicate.parameters == {}


class TestRestrictedScopesRestrict:
    def test_self_scope_binds_the_principals_own_id(self):
        predicate = scope_predicate(visible_employees(_context(DataScope.SELF)))

        assert "employee_id = ANY(%(scope_employee_ids)s::uuid[])" in predicate.sql
        assert predicate.parameters["scope_employee_ids"] == [ALICE]

    def test_team_scope_binds_the_whole_team(self):
        context = _context(DataScope.TEAM, manager_of=frozenset({BOB}))

        predicate = scope_predicate(visible_employees(context))

        assert sorted(predicate.parameters["scope_employee_ids"]) == sorted([ALICE, BOB])

    def test_department_scope_binds_the_department(self):
        predicate = scope_predicate(visible_employees(_context(DataScope.DEPARTMENT)))

        assert "dept_id = %(scope_dept_id)s::uuid" in predicate.sql
        assert predicate.parameters["scope_dept_id"] == SALES

    def test_branch_scope_binds_the_branches(self):
        context = _context(DataScope.BRANCH, allowed_branch_ids=frozenset({CAIRO}))

        predicate = scope_predicate(visible_employees(context))

        assert "branch_id = ANY(%(scope_branch_ids)s::uuid[])" in predicate.sql
        assert predicate.parameters["scope_branch_ids"] == [CAIRO]

    @pytest.mark.parametrize(
        "scope", [DataScope.SELF, DataScope.TEAM, DataScope.DEPARTMENT, DataScope.BRANCH]
    )
    def test_no_restricted_scope_ever_renders_true(self, scope):
        context = _context(scope, allowed_branch_ids=frozenset({CAIRO}))

        predicate = scope_predicate(visible_employees(context))

        assert predicate.sql != "TRUE"
        assert predicate.parameters, "a restricted predicate must bind something"


class TestAnEmptyRestrictionCannotReachTheRenderer:
    def test_the_type_refuses_to_represent_one(self):
        # There is deliberately no "matches nothing" branch in the renderer: the
        # guarantee lives one layer earlier, at the type. An empty IN list is
        # therefore not a case the SQL layer has to get right.
        with pytest.raises(ValueError, match="exactly one dimension"):
            EmployeeVisibility()

    def test_a_populated_restriction_still_renders(self):
        predicate = scope_predicate(EmployeeVisibility(employee_ids=frozenset({ALICE})))

        assert predicate.parameters["scope_employee_ids"] == [ALICE]


class TestValuesAreBoundNotInterpolated:
    def test_identifiers_never_appear_in_the_sql_text(self):
        context = _context(DataScope.SELF)

        predicate = scope_predicate(visible_employees(context))

        assert ALICE not in predicate.sql

    def test_a_hostile_alias_is_refused(self):
        # The only string interpolated into a predicate, so the only place an
        # injection could enter.
        with pytest.raises(ValueError, match="plain lowercase identifier"):
            scope_predicate(visible_employees(_context(DataScope.SELF)), alias="e; DROP TABLE x--")

    @pytest.mark.parametrize("alias", ["E", "1e", "e-1", "e.f", "", "a" * 40])
    def test_only_plain_identifiers_are_accepted_as_aliases(self, alias):
        with pytest.raises(ValueError, match="plain lowercase identifier"):
            scope_predicate(visible_employees(_context(DataScope.SELF)), alias=alias)

    def test_a_custom_alias_is_applied(self):
        predicate = scope_predicate(visible_employees(_context(DataScope.SELF)), alias="emp")

        assert predicate.sql.startswith("emp.employee_id")


class TestParameterNamespacing:
    def test_scope_parameters_are_prefixed(self):
        # So a tool argument named `employee_ids` cannot overwrite the scope
        # value, which would be the whole attack.
        predicate = scope_predicate(visible_employees(_context(DataScope.SELF)))

        assert all(name.startswith("scope_") for name in predicate.parameter_names)

    def test_the_prefix_is_configurable_and_validated(self):
        predicate = scope_predicate(
            visible_employees(_context(DataScope.SELF)), parameter_prefix="ctx"
        )

        assert "ctx_employee_ids" in predicate.parameters

        with pytest.raises(ValueError, match="plain lowercase identifier"):
            scope_predicate(visible_employees(_context(DataScope.SELF)), parameter_prefix="a b")


class TestCombining:
    def test_combining_nothing_is_refused(self):
        # A function whose empty case means TRUE will one day be called with an
        # empty list.
        with pytest.raises(ValueError, match="at least one predicate"):
            combine_and()

    def test_a_single_predicate_passes_through(self):
        assert combine_and(ALWAYS_TRUE) is ALWAYS_TRUE

    def test_fragments_are_parenthesised(self):
        combined = combine_and(Predicate("a OR b"), Predicate("c"))

        # Without parentheses this would mean `a OR (b AND c)`.
        assert combined.sql == "(a OR b) AND (c)"

    def test_parameters_merge(self):
        combined = combine_and(Predicate("x = %(a)s", {"a": 1}), Predicate("y = %(b)s", {"b": 2}))

        assert combined.parameters == {"a": 1, "b": 2}

    def test_a_conflicting_parameter_is_refused(self):
        # This is the collision that would let a tool argument silently replace
        # a scope value.
        with pytest.raises(ValueError, match="never be overwritten"):
            combine_and(
                Predicate("x = %(scope_dept_id)s", {"scope_dept_id": SALES}),
                Predicate("y = %(scope_dept_id)s", {"scope_dept_id": "somewhere-else"}),
            )

    def test_an_identical_repeated_parameter_is_fine(self):
        combined = combine_and(Predicate("x = %(a)s", {"a": 1}), Predicate("y = %(a)s", {"a": 1}))

        assert combined.parameters == {"a": 1}


class TestWhereClause:
    def test_it_renders_a_complete_clause(self):
        clause = where_clause([ALWAYS_TRUE, Predicate("x = %(a)s", {"a": 1})])

        assert clause.sql == "WHERE (TRUE) AND (x = %(a)s)"
        assert clause.parameters == {"a": 1}

    def test_an_empty_clause_is_refused(self):
        with pytest.raises(ValueError, match="at least one predicate"):
            where_clause([])

    def test_a_bound_scope_survives_into_the_clause(self):
        scope = scope_predicate(visible_employees(_context(DataScope.SELF)))

        clause = where_clause([scope, Predicate("x = %(a)s", {"a": 1})])

        assert clause.sql.startswith("WHERE (e.employee_id")
        assert clause.parameters["scope_employee_ids"] == [ALICE]
