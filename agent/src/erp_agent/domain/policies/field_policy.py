"""Field-level restrictions, as an allowlist the model never negotiates with.

Plan.md section 8.3 in executable form. Two rules shape it:

*Restriction is by column name, decided before the query runs.* A column the
principal may not read is not selected, so it never reaches the model and cannot
be leaked by it. Asking the model to be discreet about a value it has already
been given is not a control.

*"Never returned" means never.* Identity documents, banking details and
health-or-belief data have no caller who may read them -- not HR, not the
employee themselves, not an administrator. There is deliberately no role that
unlocks them, because the moment one exists it becomes the role everybody has.

The remaining categories are conditional, and the condition is always evaluated
against the :class:`SecurityContext`, never against a tool argument.

**The condition is a permission, not a role name.** Until the mock identity
phase these categories were gated on hard-coded role-name sets -- ``HR_ROLES``,
``PAYROLL_ROLES`` -- which meant the policy shipped its own opinion about which
roles an organisation has. They are gone. The question asked here is now
``may this principal read hr.contact_pii?``, and the answer comes from the grant
their roles resolved to, which the identity store supplies and an ERP row will
supply after cutover. Behaviour is unchanged for the roles that already existed;
what changed is where the grant is written down.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from erp_agent.domain.errors import RestrictedFieldError
from erp_agent.domain.models.authorization import Action, Permission, Resource
from erp_agent.domain.models.security_context import SecurityContext

__all__ = [
    "COMPENSATION",
    "CONTACT_PII",
    "NEVER_RETURNED",
    "READ_COMPENSATION",
    "READ_CONTACT_PII",
    "assert_columns_permitted",
    "is_never_returned",
    "permitted_columns",
]

#: Returned to nobody, under any scope or role (Plan.md section 8.3).
#: Identity documents, banking details, and health or belief data.
NEVER_RETURNED: frozenset[str] = frozenset(
    {
        # Identity documents.
        "national_id",
        "national_id_type",
        "passport_number",
        "passport_expiry",
        # Banking.
        "bank_account",
        "bank_iban",
        "bank_name",
        "bank_branch",
        "iban",
        # Health and belief.
        "religion",
        "blood_type",
        "medical_certificate_path",
        "medical_notes",
        "disability_status",
    }
)

#: Readable by the subject themselves, or by a principal granted
#: :data:`READ_COMPENSATION`. Never by a peer, and never by a manager by
#: default -- managing somebody is not a reason to see their salary.
COMPENSATION: frozenset[str] = frozenset(
    {
        "basic_salary",
        "gross_salary",
        "net_salary",
        "salary_amount",
        "housing_allowance",
        "transport_allowance",
        "other_allowances",
        "total_allowances",
        "bonus_amount",
        "loan_amount",
        "deduction_amount",
    }
)

#: Personal contact detail. Readable by the subject, or by a principal granted
#: :data:`READ_CONTACT_PII`.
CONTACT_PII: frozenset[str] = frozenset(
    {
        "date_of_birth",
        "place_of_birth",
        "phone_home",
        "phone_mobile",
        "email_personal",
        "address_line1",
        "address_line2",
        "postal_code",
        "marital_status",
    }
)

#: The permission that unlocks contact detail about *other* employees. Held by
#: whichever roles the identity store grants it to -- this module no longer
#: names them.
READ_CONTACT_PII = Permission(Resource.HR_CONTACT_PII, Action.READ)

#: The permission that unlocks compensation about other employees.
READ_COMPENSATION = Permission(Resource.HR_COMPENSATION, Action.READ)


def is_never_returned(column: str) -> bool:
    """Whether a column may never appear in any answer.

    Args:
        column: Column name, compared case-insensitively.

    Returns:
        ``True`` if the column is unconditionally withheld.
    """
    return column.strip().lower() in NEVER_RETURNED


def _may_read_about(
    permission: Permission,
    context: SecurityContext,
    subject_employee_id: str | None,
) -> bool:
    """Whether a conditional category is readable for a given subject.

    Two ways to qualify, and they are different in kind. The subject reading
    their *own* record needs no grant -- that is a relationship, not a
    permission. Reading about somebody else needs the permission, and nothing
    else will do.
    """
    if subject_employee_id is not None and subject_employee_id == context.employee_id:
        return True
    return context.may(permission)


def permitted_columns(
    columns: Sequence[str],
    context: SecurityContext,
    *,
    subject_employee_id: str | None = None,
) -> tuple[str, ...]:
    """Narrow a requested column list to what this principal may read.

    Args:
        columns: Columns a tool would like to select.
        context: The resolved security context. The only source of authority.
        subject_employee_id: Whose record is being read, when that is known. A
            principal reading their own record may see their own compensation
            and contact details; ``None`` means "not a single known subject",
            which is treated as reading about someone else.

    Returns:
        The subset that may be selected, in the order given and de-duplicated.
    """
    allowed: list[str] = []
    seen: set[str] = set()

    for column in columns:
        name = column.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)

        if name in NEVER_RETURNED:
            continue
        if name in COMPENSATION and not _may_read_about(
            READ_COMPENSATION, context, subject_employee_id
        ):
            continue
        if name in CONTACT_PII and not _may_read_about(
            READ_CONTACT_PII, context, subject_employee_id
        ):
            continue

        allowed.append(column)

    return tuple(allowed)


def assert_columns_permitted(
    columns: Iterable[str],
    context: SecurityContext,
    *,
    subject_employee_id: str | None = None,
) -> None:
    """Refuse a query that names a column this principal may not read.

    The complement of :func:`permitted_columns`. Narrowing is right when a tool
    offers an optional field; refusing is right when a fixed query template names
    a restricted column, because that is a bug in the template rather than a
    request to be trimmed.

    Args:
        columns: Columns the query names.
        context: The resolved security context.
        subject_employee_id: Whose record is being read, when known.

    Raises:
        RestrictedFieldError: Naming the offending columns. Column names are
            schema, not data, so they are safe to report; no value is included.
    """
    requested = [column.strip().lower() for column in columns if column.strip()]
    permitted = {
        column.lower()
        for column in permitted_columns(requested, context, subject_employee_id=subject_employee_id)
    }

    if refused := sorted(set(requested) - permitted):
        raise RestrictedFieldError(
            f"the query names restricted column(s): {', '.join(refused)}",
            context={"columns": tuple(refused), "scope": context.scope.value},
        )
