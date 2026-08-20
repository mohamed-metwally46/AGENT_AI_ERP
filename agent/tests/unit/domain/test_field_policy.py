"""Field-level restrictions (Plan.md section 8.3)."""

from __future__ import annotations

import pytest

from erp_agent.domain.errors import RestrictedFieldError
from erp_agent.domain.models.security_context import DataScope, SecurityContext
from erp_agent.domain.policies.field_policy import (
    assert_columns_permitted,
    is_never_returned,
    permitted_columns,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"


def _context(
    scope: DataScope = DataScope.SELF, roles: frozenset[str] = frozenset()
) -> SecurityContext:
    return SecurityContext(
        user_id="42",
        employee_id=ALICE,
        scope=scope,
        dept_id="dept",
        branch_id="branch",
        allowed_branch_ids=frozenset({"branch"}),
        role_names=roles,
    )


class TestNeverReturned:
    @pytest.mark.parametrize(
        "column",
        [
            "national_id",
            "passport_number",
            "passport_expiry",
            "bank_iban",
            "bank_account",
            "religion",
            "blood_type",
        ],
    )
    def test_identity_banking_and_belief_data_is_withheld_from_everyone(self, column):
        assert is_never_returned(column)

    @pytest.mark.parametrize("scope", list(DataScope))
    def test_not_even_an_organisation_scoped_admin_may_read_them(self, scope):
        context = _context(scope, roles=frozenset({"hr_admin", "payroll", "superuser"}))

        assert permitted_columns(["national_id", "bank_iban"], context) == ()

    def test_not_even_about_your_own_record(self):
        # There is deliberately no role or relationship that unlocks these. The
        # moment one exists it becomes the role everybody has.
        context = _context()

        assert permitted_columns(["national_id"], context, subject_employee_id=ALICE) == ()

    def test_the_check_is_case_insensitive(self):
        assert is_never_returned("NATIONAL_ID")
        assert is_never_returned("  Passport_Number  ")


class TestCompensation:
    def test_the_subject_may_read_their_own(self):
        allowed = permitted_columns(["basic_salary"], _context(), subject_employee_id=ALICE)

        assert allowed == ("basic_salary",)

    def test_a_peer_may_not_read_someone_elses(self):
        allowed = permitted_columns(["basic_salary"], _context(), subject_employee_id=BOB)

        assert allowed == ()

    def test_a_manager_may_not_read_it_by_default(self):
        # Managing someone is not the same as being payroll.
        context = _context(DataScope.TEAM)

        assert permitted_columns(["net_salary"], context, subject_employee_id=BOB) == ()

    def test_a_payroll_role_may(self):
        context = _context(DataScope.ORGANISATION, roles=frozenset({"payroll"}))

        assert permitted_columns(["net_salary"], context, subject_employee_id=BOB) == (
            "net_salary",
        )

    def test_an_hr_role_is_not_automatically_a_payroll_role(self):
        context = _context(DataScope.ORGANISATION, roles=frozenset({"hr"}))

        assert permitted_columns(["gross_salary"], context, subject_employee_id=BOB) == ()


class TestContactPii:
    def test_the_subject_may_read_their_own(self):
        allowed = permitted_columns(["date_of_birth"], _context(), subject_employee_id=ALICE)

        assert allowed == ("date_of_birth",)

    def test_a_peer_may_not(self):
        assert permitted_columns(["phone_home"], _context(), subject_employee_id=BOB) == ()

    def test_an_hr_role_may(self):
        context = _context(DataScope.ORGANISATION, roles=frozenset({"hr_manager"}))

        assert permitted_columns(["address_line1"], context, subject_employee_id=BOB) == (
            "address_line1",
        )

    def test_an_unknown_subject_is_treated_as_someone_else(self):
        # `None` means "not a single known subject" -- e.g. a count across a
        # department. Failing closed is the only safe reading.
        assert permitted_columns(["date_of_birth"], _context()) == ()


class TestNarrowing:
    def test_unrestricted_columns_pass_through_in_order(self):
        columns = ["employee_number", "first_name", "last_name", "employment_status"]

        assert permitted_columns(columns, _context()) == tuple(columns)

    def test_restricted_columns_are_dropped_not_the_whole_query(self):
        allowed = permitted_columns(
            ["first_name", "national_id", "last_name"], _context(), subject_employee_id=ALICE
        )

        assert allowed == ("first_name", "last_name")

    def test_duplicates_collapse(self):
        assert permitted_columns(["first_name", "first_name"], _context()) == ("first_name",)


class TestAssertion:
    def test_a_clean_column_list_passes(self):
        assert_columns_permitted(["first_name", "employee_number"], _context())

    def test_a_restricted_column_in_a_fixed_template_is_refused(self):
        # Narrowing is right for optional fields; a fixed template naming a
        # restricted column is a bug, and must be loud.
        with pytest.raises(RestrictedFieldError, match="national_id"):
            assert_columns_permitted(["first_name", "national_id"], _context())

    def test_the_error_names_columns_but_carries_no_values(self):
        with pytest.raises(RestrictedFieldError) as caught:
            assert_columns_permitted(["bank_iban", "national_id"], _context())

        assert caught.value.context["columns"] == ("bank_iban", "national_id")
        assert not caught.value.retryable
