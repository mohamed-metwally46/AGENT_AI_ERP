"""Typed results for the six MVP HR tools (Plan.md section 6.3).

Every field here is one a tool may return. That is the point of typing them:
``SELECT *`` is banned (Plan.md section 6.4 rule 4), and a record type with named
fields is the same guarantee expressed where a reviewer will actually see it. A
new sensitive column added to ``employees`` cannot reach an answer without
someone adding it to one of these classes first.

Nothing in this module carries a national id, passport number, bank detail,
salary, religion or blood type. Not conditionally, not for HR, not for the
subject themselves -- those are the "never returned" categories of Plan.md
section 8.3, and the safest way to honour that is for there to be no field to
put them in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

__all__ = [
    "AttendanceSummary",
    "EmployeeCandidate",
    "EmployeeLookup",
    "EmployeeSummary",
    "Headcount",
    "LeaveBalance",
    "LeavePolicy",
    "LeaveRequestSummary",
]


@dataclass(frozen=True, slots=True)
class EmployeeSummary:
    """One employee, as an answer may describe them.

    Attributes:
        employee_number: Stable business key. The identifier a tool accepts and
            an answer quotes -- never the internal uuid.
        full_name: Given and family name, joined.
        department: Department name, or ``None`` if unassigned.
        branch: Branch name, or ``None``.
        position_title: Current position title, or ``None``.
        job_grade: Grade code, which drives leave entitlement.
        employment_status: One of the eight documented statuses.
        employment_type: One of the five documented types.
        hire_date: When they joined.
        manager_employee_number: Their manager's business key, or ``None``.
        email_work: Work address. Corporate contact detail, not personal PII.
        date_of_birth: Populated only when the caller may read contact PII;
            ``None`` otherwise. The distinction between "no date on file" and
            "not permitted" is deliberately *not* expressed here -- an answer
            that says "you may not see this" leaks the same bit as showing it.
        phone_mobile: As :attr:`date_of_birth`.
        email_personal: As :attr:`date_of_birth`.
    """

    employee_number: str
    full_name: str
    employment_status: str
    employment_type: str
    hire_date: date
    department: str | None = None
    branch: str | None = None
    position_title: str | None = None
    job_grade: str | None = None
    manager_employee_number: str | None = None
    email_work: str | None = None
    date_of_birth: date | None = None
    phone_mobile: str | None = None
    email_personal: str | None = None


@dataclass(frozen=True, slots=True)
class EmployeeCandidate:
    """One of several employees a name matched.

    Carries the minimum needed to tell two people apart. A disambiguation list is
    shown to a user who has not yet proved which record they meant, so it stays
    as thin as it can be while still being useful.

    Attributes:
        employee_number: The key to re-ask with.
        full_name: The matched name.
        department: Where they sit, which is usually what distinguishes them.
    """

    employee_number: str
    full_name: str
    department: str | None = None


@dataclass(frozen=True, slots=True)
class EmployeeLookup:
    """The outcome of an employee lookup.

    Three outcomes, kept distinct because they mean different things to an
    answer: found, not found, and *ambiguous*. Collapsing the third into the
    first by guessing is how a name-based lookup crosses a permission boundary
    (Plan.md section 8.5, gap S5).

    Attributes:
        employee: The single match, when exactly one row matched.
        candidates: The matches, when more than one did.
    """

    employee: EmployeeSummary | None = None
    candidates: tuple[EmployeeCandidate, ...] = ()

    def __post_init__(self) -> None:
        """Validate the outcome is exactly one of the three.

        Raises:
            ValueError: If a single match and a candidate list are both present.
        """
        if self.employee is not None and self.candidates:
            raise ValueError("a lookup is either resolved or ambiguous, never both")

    @property
    def is_found(self) -> bool:
        """Whether exactly one employee matched."""
        return self.employee is not None

    @property
    def is_ambiguous(self) -> bool:
        """Whether the lookup needs the user to choose."""
        return bool(self.candidates)


@dataclass(frozen=True, slots=True)
class Headcount:
    """How many employees matched a filter.

    Attributes:
        total: Employees matching the filter, whatever their status.
        active: Of those, how many have ``employment_status = 'active'``.
            Reported alongside the total rather than instead of it, because
            "how many people are in Sales" has two defensible answers and an
            answer that silently picks one is not reproducible.
        department: The department filtered on, echoed back.
        branch: The branch filtered on, echoed back.
        employment_status: The status filtered on, echoed back.
    """

    total: int
    active: int
    department: str | None = None
    branch: str | None = None
    employment_status: str | None = None

    def __post_init__(self) -> None:
        """Validate the counts are coherent."""
        if self.total < 0 or self.active < 0:
            raise ValueError("headcounts must not be negative")
        if self.active > self.total:
            raise ValueError(f"active ({self.active}) cannot exceed total ({self.total})")


@dataclass(frozen=True, slots=True)
class LeaveBalance:
    """One employee's balance for one leave type in one fiscal year.

    Attributes:
        employee_number: Whose balance this is.
        leave_code: Leave type code, e.g. ``ANN``.
        leave_name: Human-readable leave type.
        fiscal_year: The year the balance belongs to. Always stated in an
            answer: a balance without its year is not an answer.
        opening_balance: Carried in at the start of the year.
        accrued_ytd: Accrued so far.
        used_ytd: Taken so far.
        pending_ytd: Requested and awaiting approval. Subtracted from
            :attr:`remaining` because a day already asked for is not a day
            still available.
    """

    employee_number: str
    leave_code: str
    leave_name: str
    fiscal_year: int
    opening_balance: Decimal
    accrued_ytd: Decimal
    used_ytd: Decimal
    pending_ytd: Decimal

    @property
    def remaining(self) -> Decimal:
        """Days still available: opening + accrued - used - pending.

        Computed here rather than in SQL so the arithmetic is visible, testable
        and identical for every caller. It is the number a hybrid question turns
        on, so it must not be something one query gets subtly different.
        """
        return self.opening_balance + self.accrued_ytd - self.used_ytd - self.pending_ytd


@dataclass(frozen=True, slots=True)
class LeaveRequestSummary:
    """One leave request.

    Attributes:
        employee_number: Who requested it.
        leave_code: Leave type code.
        leave_name: Human-readable leave type.
        start_date: First day of leave.
        end_date: Last day of leave.
        working_days: Working days consumed, which is not ``end - start``.
        status: Request status.
        is_half_day: Whether it is a half day.
        reviewed_at: When it was decided, if it has been.
    """

    employee_number: str
    leave_code: str
    leave_name: str
    start_date: date
    end_date: date
    working_days: Decimal
    status: str
    is_half_day: bool = False
    reviewed_at: date | None = None


@dataclass(frozen=True, slots=True)
class LeavePolicy:
    """An entitlement rule, as structured data rather than prose.

    This is what makes hybrid questions checkable. "Does Ahmed have enough leave
    under company policy?" can be answered from the database *and* cross-checked
    against the policy document, and where the two disagree the answer says so
    (Plan.md section 11, Phase 5 acceptance criterion 3).

    Attributes:
        leave_code: Leave type code.
        leave_name: Human-readable leave type.
        scope: ``all``, ``grade`` or ``dept``.
        scope_label: What the scope points at, resolved to a name where one
            exists; ``None`` for organisation-wide policies.
        annual_entitlement_days: Days granted per year.
        accrual_method: How entitlement accrues.
        accrual_rate: Rate for the method, where one applies.
        max_carry_forward_days: Days that may cross a year boundary.
        min_service_months: Service required before the policy applies.
        max_consecutive_days: Longest single absence permitted.
        notice_days: Notice required.
        allow_negative_balance: Whether the balance may go below zero.
        gender_eligibility: Restriction where one exists.
    """

    leave_code: str
    leave_name: str
    scope: str
    annual_entitlement_days: Decimal
    scope_label: str | None = None
    accrual_method: str | None = None
    accrual_rate: Decimal | None = None
    max_carry_forward_days: Decimal | None = None
    min_service_months: int | None = None
    max_consecutive_days: int | None = None
    notice_days: int | None = None
    allow_negative_balance: bool = False
    gender_eligibility: str | None = None


@dataclass(frozen=True, slots=True)
class AttendanceSummary:
    """One employee's attendance for one month.

    Read from ``attendance_summaries`` rather than aggregated from
    ``attendance_records`` (Plan.md section 6.4 rule 6): the pre-aggregated table
    exists precisely so a monthly question does not scan thousands of daily rows.

    Attributes:
        employee_number: Whose attendance this is.
        year: Calendar year.
        month: Calendar month, 1-12.
        working_days_in_month: Expected working days.
        days_present: Days attended.
        days_absent: Days missed without leave.
        days_on_leave: Days on approved leave.
        total_hours_worked: Hours worked in the month.
        total_late_minutes: Cumulative lateness.
        total_overtime_minutes: Cumulative overtime.
        total_early_departure_minutes: Cumulative early departure.
    """

    employee_number: str
    year: int
    month: int
    working_days_in_month: int
    days_present: int
    days_absent: int
    days_on_leave: int
    total_hours_worked: Decimal = field(default_factory=lambda: Decimal(0))
    total_late_minutes: int = 0
    total_overtime_minutes: int = 0
    total_early_departure_minutes: int = 0

    def __post_init__(self) -> None:
        """Validate the month is a real month."""
        if not 1 <= self.month <= 12:
            raise ValueError(f"month must be between 1 and 12, got {self.month}")
