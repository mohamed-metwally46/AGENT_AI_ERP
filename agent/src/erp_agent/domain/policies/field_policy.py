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
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from erp_agent.domain.errors import RestrictedFieldError
from erp_agent.domain.models.security_context import SecurityContext

__all__ = [
    "COMPENSATION",
    "CONTACT_PII",
    "HR_ROLES",
    "NEVER_RETURNED",
    "PAYROLL_ROLES",
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

#: Readable by the subject themselves, or by a payroll/finance role holding an
#: explicit permission. Never by a peer, and never by a manager by default.
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

#: Personal contact detail. Readable by the subject, or by an HR role.
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

#: Roles trusted with contact PII about other employees.
HR_ROLES: frozenset[str] = frozenset({"hr", "hr_manager", "hr_admin", "people_ops"})

#: Roles trusted with compensation about other employees.
PAYROLL_ROLES: frozenset[str] = frozenset({"payroll", "finance", "finance_manager", "cfo"})


def is_never_returned(column: str) -> bool:
    """Whether a column may never appear in any answer.

    Args:
        column: Column name, compared case-insensitively.

    Returns:
        ``True`` if the column is unconditionally withheld.
    """
    return column.strip().lower() in NEVER_RETURNED


def _may_read_about(
    trusted_roles: frozenset[str],
    context: SecurityContext,
    subject_employee_id: str | None,
) -> bool:
    """Whether a conditional category is readable for a given subject."""
    # The subject reading their own record is always permitted.
    if subject_employee_id is not None and subject_employee_id == context.employee_id:
        return True
    return context.has_any_role(*trusted_roles)


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
            PAYROLL_ROLES, context, subject_employee_id
        ):
            continue
        if name in CONTACT_PII and not _may_read_about(HR_ROLES, context, subject_employee_id):
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
