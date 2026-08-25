"""Seven mock users, and the different answers each of them gets.

The acceptance test for the mock identity work: *different users receive
different authorization results*, through the same code path, from nothing but
their credential.

Every context here is produced by the real chain -- a dev bearer token, the real
token adapter, the real roster, the real role catalogue,
:class:`~erp_agent.application.security.Authenticate`. Nothing is constructed by
hand, because a hand-built context is a context nobody can actually obtain.

Three legs, and each must differ per user or the exercise proves nothing:

**SQL rows** -- :func:`visible_employees` turns the resolved scope into a
restriction the query layer renders as a predicate.

**SQL columns** -- :func:`permitted_columns` narrows the select list against the
resolved *permissions*, which is a different axis from scope: two users can
reach the same rows and be shown different columns of them.

**RAG chunks** -- :func:`retrieval_filter` becomes a store-side predicate, so an
unreadable passage is never retrieved rather than retrieved and dropped.

The Phase 4 guarantees are re-asserted here rather than assumed, because this is
the change that moved the field policy from role names to permissions and the
whole point is that behaviour did not move with it.
"""

from __future__ import annotations

import pytest

from erp_agent.domain.models.security_context import DataScope
from erp_agent.domain.policies.field_policy import permitted_columns
from erp_agent.domain.policies.scope import visible_employees
from erp_agent.infrastructure.rag.document_catalogue import (
    AccessLevel,
    DocumentCatalogue,
    DocumentRecord,
)
from erp_agent.infrastructure.rag.security_filter import allowed_document_ids
from tests.fixtures.identity import (
    BRANCH_HQ,
    DEPT_FIN,
    DEPT_HR,
    DEPT_SLS,
    mock_callers,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

ADMIN = "1001"
HR_MANAGER = "1002"
HR_EMPLOYEE = "1048"
SALES_MANAGER = "1008"
EMPLOYEE = "1016"
HR_PLUS_PAYROLL = "1006"
EMPLOYEE_PLUS_PAYROLL = "1003"

#: A subject nobody in the roster is. Deliberately not a rostered employee id:
#: the field policy lets anyone read their own restricted columns, so using a
#: real one would turn "reading about someone else" into "reading about
#: yourself" for exactly one user and quietly weaken the matrix.
SOMEBODY_ELSE = "emp-id-EMP-9999"


@pytest.fixture
async def callers():
    return await mock_callers()


def _context(callers, identity):
    return callers[identity].context


# --------------------------------------------------------------------------- #
# The roster resolves to distinct grants.
# --------------------------------------------------------------------------- #


class TestTheRosterCoversTheLadder:
    @pytest.mark.parametrize(
        ("identity", "expected"),
        [
            (ADMIN, DataScope.ORGANISATION),
            (HR_MANAGER, DataScope.BRANCH),
            (HR_EMPLOYEE, DataScope.DEPARTMENT),
            (SALES_MANAGER, DataScope.TEAM),
            (EMPLOYEE, DataScope.SELF),
        ],
    )
    async def test_each_user_type_resolves_to_its_rung(self, callers, identity, expected):
        assert _context(callers, identity).scope is expected

    async def test_every_rung_of_the_ladder_is_represented(self, callers):
        # If a rung had no user, a predicate bug on that rung would be invisible.
        held = {caller.context.scope for caller in callers.values()}
        assert held == set(DataScope)

    async def test_no_two_user_types_resolve_to_the_same_grant(self, callers):
        # The whole exercise is "different users, different results". Two
        # identical grants would mean one of them tests nothing.
        grants = {
            (
                caller.context.scope,
                caller.context.permissions,
                caller.context.dept_id,
            )
            for caller in callers.values()
        }
        assert len(grants) == len(callers)


class TestMultipleRoles:
    async def test_the_wider_of_two_scopes_wins(self, callers):
        # employee (self) + payroll_officer (department).
        assert _context(callers, EMPLOYEE_PLUS_PAYROLL).scope is DataScope.DEPARTMENT

    async def test_the_union_holds_what_neither_role_grants_alone(self, callers):
        # hr_employee grants contact PII and no compensation; payroll_officer
        # grants compensation and no contact PII. Held together, both.
        context = _context(callers, HR_PLUS_PAYROLL)

        assert permitted_columns(
            ["date_of_birth", "basic_salary"], context, subject_employee_id=SOMEBODY_ELSE
        ) == ("date_of_birth", "basic_salary")

    async def test_the_first_role_s_permissions_survive(self, callers):
        context = _context(callers, HR_PLUS_PAYROLL)
        assert permitted_columns(["phone_mobile"], context, subject_employee_id=SOMEBODY_ELSE) == (
            "phone_mobile",
        )

    async def test_a_permission_only_one_role_grants_is_held(self, callers):
        # The `employee` role alone never confers compensation about others.
        context = _context(callers, EMPLOYEE_PLUS_PAYROLL)
        assert permitted_columns(["net_salary"], context, subject_employee_id=SOMEBODY_ELSE) == (
            "net_salary",
        )

    async def test_and_the_second_role_widened_the_scope(self, callers):
        assert _context(callers, EMPLOYEE_PLUS_PAYROLL).scope is DataScope.DEPARTMENT

    async def test_holding_both_roles_is_visible_in_the_context(self, callers):
        assert _context(callers, HR_PLUS_PAYROLL).role_names == frozenset(
            {"hr_employee", "payroll_officer"}
        )


# --------------------------------------------------------------------------- #
# SQL authorization: which rows.
# --------------------------------------------------------------------------- #


class TestSqlRowVisibility:
    async def test_the_admin_is_unrestricted(self, callers):
        assert visible_employees(_context(callers, ADMIN)).unrestricted

    async def test_the_employee_sees_exactly_one_row(self, callers):
        visibility = visible_employees(_context(callers, EMPLOYEE))

        assert visibility.employee_ids == frozenset({"emp-id-EMP-0016"})
        assert not visibility.unrestricted

    async def test_the_hr_manager_is_restricted_to_their_branch(self, callers):
        visibility = visible_employees(_context(callers, HR_MANAGER))

        assert visibility.branch_ids == frozenset({BRANCH_HQ})
        assert not visibility.unrestricted

    @pytest.mark.parametrize(
        ("identity", "department"),
        [(HR_EMPLOYEE, DEPT_HR), (EMPLOYEE_PLUS_PAYROLL, DEPT_FIN)],
    )
    async def test_a_department_scoped_user_is_restricted_to_their_own(
        self, callers, identity, department
    ):
        assert visible_employees(_context(callers, identity)).dept_id == department

    async def test_two_department_scoped_users_do_not_see_the_same_department(self, callers):
        # Scope alone is not the answer -- the department comes from the
        # employee record, so the same rung reaches different rows.
        assert (
            visible_employees(_context(callers, HR_EMPLOYEE)).dept_id
            != visible_employees(_context(callers, EMPLOYEE_PLUS_PAYROLL)).dept_id
        )

    async def test_the_sales_manager_reaches_only_themselves_without_a_reporting_tree(
        self, callers
    ):
        # `manager_of` is computed from the database and is not wired, so TEAM
        # narrows to the principal. Under-permission is the safe direction.
        visibility = visible_employees(_context(callers, SALES_MANAGER))
        assert visibility.employee_ids == frozenset({"emp-id-EMP-0008"})

    async def test_no_user_gets_an_empty_unrestricted_visibility(self, callers):
        # The dangerous shape: a restriction on nothing that is not explicitly
        # unrestricted renders as an absent WHERE clause.
        for caller in callers.values():
            visibility = visible_employees(caller.context)
            assert visibility.unrestricted or (
                visibility.employee_ids or visibility.dept_id or visibility.branch_ids
            )

    async def test_the_department_of_a_sales_user_is_not_the_hr_department(self, callers):
        assert _context(callers, EMPLOYEE).dept_id == DEPT_SLS


# --------------------------------------------------------------------------- #
# SQL authorization: which columns. This is the permission axis.
# --------------------------------------------------------------------------- #


class TestSqlColumnVisibility:
    @pytest.mark.parametrize(
        ("identity", "expected"),
        [
            (ADMIN, True),
            (HR_MANAGER, True),
            (HR_EMPLOYEE, True),
            (HR_PLUS_PAYROLL, True),
            (SALES_MANAGER, False),
            (EMPLOYEE, False),
            (EMPLOYEE_PLUS_PAYROLL, False),
        ],
    )
    async def test_contact_pii_about_others_follows_the_permission(
        self, callers, identity, expected
    ):
        allowed = permitted_columns(
            ["date_of_birth"], _context(callers, identity), subject_employee_id=SOMEBODY_ELSE
        )
        assert bool(allowed) is expected

    @pytest.mark.parametrize(
        ("identity", "expected"),
        [
            (ADMIN, True),
            (EMPLOYEE_PLUS_PAYROLL, True),
            (HR_PLUS_PAYROLL, True),
            (HR_MANAGER, False),
            (HR_EMPLOYEE, False),
            (SALES_MANAGER, False),
            (EMPLOYEE, False),
        ],
    )
    async def test_compensation_about_others_follows_a_different_permission(
        self, callers, identity, expected
    ):
        # HR and payroll are independent grants: holding one must not confer
        # the other (Plan.md section 8.3).
        allowed = permitted_columns(
            ["basic_salary"], _context(callers, identity), subject_employee_id=SOMEBODY_ELSE
        )
        assert bool(allowed) is expected

    async def test_scope_does_not_substitute_for_a_permission(self, callers):
        # The HR manager reaches an entire branch of rows and still may not see
        # a salary in any of them.
        context = _context(callers, HR_MANAGER)

        assert visible_employees(context).branch_ids
        assert permitted_columns(["net_salary"], context, subject_employee_id=SOMEBODY_ELSE) == ()

    async def test_a_permission_does_not_substitute_for_scope(self, callers):
        # And the converse: payroll may read compensation, over a department.
        context = _context(callers, EMPLOYEE_PLUS_PAYROLL)

        assert not visible_employees(context).unrestricted
        assert permitted_columns(["net_salary"], context, subject_employee_id=SOMEBODY_ELSE)

    async def test_everybody_may_read_their_own_restricted_columns(self, callers):
        # Reading your own record is a relationship, not a permission.
        for caller in callers.values():
            allowed = permitted_columns(
                ["basic_salary", "date_of_birth"],
                caller.context,
                subject_employee_id=caller.context.employee_id,
            )
            assert allowed == ("basic_salary", "date_of_birth")


class TestPhaseFourGuaranteesSurvive:
    @pytest.mark.parametrize(
        "column", ["national_id", "passport_number", "bank_iban", "religion", "blood_type"]
    )
    async def test_no_user_and_no_role_unlocks_the_never_returned_set(self, callers, column):
        # The moment a role unlocks these it becomes the role everybody has.
        for caller in callers.values():
            assert permitted_columns([column], caller.context) == ()
            assert (
                permitted_columns(
                    [column], caller.context, subject_employee_id=caller.context.employee_id
                )
                == ()
            )

    async def test_an_unknown_subject_is_still_treated_as_someone_else(self, callers):
        # `None` means "not one known subject" -- e.g. a departmental count.
        assert permitted_columns(["date_of_birth"], _context(callers, EMPLOYEE)) == ()

    async def test_no_context_can_be_widened_after_resolution(self, callers):
        # Frozen, so a tool that received one cannot edit its way out.
        with pytest.raises((AttributeError, TypeError)):
            _context(callers, EMPLOYEE).scope = DataScope.ORGANISATION  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# RAG authorization: which chunks.
# --------------------------------------------------------------------------- #


def _catalogue() -> DocumentCatalogue:
    """A manifest with one document per way of being reachable."""
    return DocumentCatalogue(
        records={
            "handbook": DocumentRecord(
                document_id="handbook",
                title="Employee handbook",
                access_level=AccessLevel.PUBLIC,
            ),
            "hr-internal": DocumentRecord(
                document_id="hr-internal",
                title="HR internal procedure",
                access_level=AccessLevel.TEAM,
                department=DEPT_HR,
                branch=BRANCH_HQ,
            ),
            "sales-internal": DocumentRecord(
                document_id="sales-internal",
                title="Sales playbook",
                access_level=AccessLevel.TEAM,
                department=DEPT_SLS,
                branch=BRANCH_HQ,
            ),
            "private-note": DocumentRecord(
                document_id="private-note",
                title="A private note",
                access_level=AccessLevel.PRIVATE,
                owner_employee_number="emp-id-EMP-0016",
            ),
        }
    )


class TestRagDocumentVisibility:
    async def test_everyone_authenticated_may_read_public_policy(self, callers):
        # An employee who cannot read the leave policy cannot check their leave.
        for caller in callers.values():
            assert "handbook" in allowed_document_ids(caller.context, _catalogue())

    async def test_the_admin_reads_every_team_document(self, callers):
        allowed = allowed_document_ids(_context(callers, ADMIN), _catalogue())

        assert {"hr-internal", "sales-internal"} <= allowed

    async def test_a_department_scoped_user_reads_only_their_department_s(self, callers):
        allowed = allowed_document_ids(_context(callers, HR_EMPLOYEE), _catalogue())

        assert "hr-internal" in allowed
        assert "sales-internal" not in allowed

    async def test_the_other_department_s_user_gets_the_mirror_image(self, callers):
        allowed = allowed_document_ids(_context(callers, EMPLOYEE_PLUS_PAYROLL), _catalogue())

        assert "hr-internal" not in allowed
        assert "sales-internal" not in allowed

    async def test_a_branch_scoped_user_reads_team_documents_in_their_branch(self, callers):
        allowed = allowed_document_ids(_context(callers, HR_MANAGER), _catalogue())

        assert {"hr-internal", "sales-internal"} <= allowed

    @pytest.mark.parametrize("identity", [EMPLOYEE, SALES_MANAGER])
    async def test_self_and_team_scope_reach_no_team_documents(self, callers, identity):
        # Neither rung carries departmental reach, and omitting the clause is
        # what makes that mean "none" rather than "all".
        allowed = allowed_document_ids(_context(callers, identity), _catalogue())

        assert "hr-internal" not in allowed
        assert "sales-internal" not in allowed

    async def test_a_private_document_reaches_only_its_owner(self, callers):
        owner = _context(callers, EMPLOYEE)
        assert "private-note" in allowed_document_ids(owner, _catalogue())

    async def test_not_even_the_admin_reads_someone_else_s_private_document(self, callers):
        allowed = allowed_document_ids(_context(callers, ADMIN), _catalogue())
        assert "private-note" not in allowed

    async def test_different_users_get_different_corpora(self, callers):
        corpora = {
            identity: allowed_document_ids(caller.context, _catalogue())
            for identity, caller in callers.items()
        }

        assert len(set(corpora.values())) > 1
