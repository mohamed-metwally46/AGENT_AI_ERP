"""What this agent requires of whatever database it is pointed at.

Plan.md R7 is schema drift between the documented ERP and the real one, and the
mitigation it names is "tools fail loudly on missing columns". Failing loudly at
the first question is better than answering wrongly, and failing loudly *before
the first question* is better still. This is the declaration that makes the
second possible.

**Why a declaration and not an inference.** The columns could be scraped out of
the query layer's SQL at runtime, and that would never drift -- but it would
also never be *reviewed*. A contract with the ERP owners has to be something a
person can read in one screen and disagree with. So it is written here, and a
test asserts it matches what the queries actually read, which gives the same
protection from drift without giving up the review.

**Why only these ten tables.** The documented ERP has 85. A real one will drift
in most of them constantly, and a report that lists all of it is a report nobody
reads. Drift that matters is drift in what the agent reads: everything here is
reachable from one of the seven tools, and nothing here is optional.

The type names are PostgreSQL's own, as ``information_schema`` reports them.
They are deliberately coarse -- a ``varchar`` that became a ``text`` breaks
nothing, and flagging it would train people to ignore the report.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["REQUIRED_TABLES", "SCHEMA", "RequiredTable", "required_columns"]

#: The schema the agent reads. Named rather than assumed: a replica that
#: presents the ERP under a different schema is a configuration difference, not
#: a code change.
SCHEMA = "public"


@dataclass(frozen=True, slots=True)
class RequiredTable:
    """One table the agent cannot work without.

    Attributes:
        name: The table.
        columns: Every column the query layer selects, joins on, or filters by.
        why: Which capability breaks when it is missing. Written for whoever
            reads a drift report at 3am, who needs to know the impact before
            the detail.
    """

    name: str
    columns: frozenset[str]
    why: str


#: Every table the query layer touches, with the columns it touches.
#:
#: Derived by reading ``queries/hr_directory.py`` and written down; a test in
#: ``tests/unit/infrastructure/test_schema_contract`` re-derives it from that
#: module and fails if the two disagree, so this cannot quietly fall behind the
#: code it describes.
REQUIRED_TABLES: tuple[RequiredTable, ...] = (
    RequiredTable(
        name="employees",
        columns=frozenset(
            {
                "employee_id",
                "employee_number",
                "first_name",
                "last_name",
                "employment_status",
                "employment_type",
                "hire_date",
                "dept_id",
                "branch_id",
                "position_id",
                "job_grade_id",
                "manager_id",
                "email_work",
                "email_personal",
                "phone_mobile",
                "date_of_birth",
            }
        ),
        why="every tool; also the spine every scope predicate is built on",
    ),
    RequiredTable(
        name="branch_departments",
        columns=frozenset({"dept_id", "dept_name"}),
        why="department filtering and DEPARTMENT scope",
    ),
    RequiredTable(
        name="organization_branches",
        columns=frozenset({"branch_id", "branch_name"}),
        why="branch filtering and BRANCH scope",
    ),
    RequiredTable(
        name="positions",
        columns=frozenset({"position_id", "position_title"}),
        why="get_employee",
    ),
    RequiredTable(
        name="job_grades",
        columns=frozenset({"job_grade_id", "grade_code", "grade_name"}),
        why="get_employee; grade-scoped leave policies",
    ),
    RequiredTable(
        name="leave_types",
        columns=frozenset({"leave_type_id", "leave_code", "leave_name"}),
        why="every leave tool",
    ),
    RequiredTable(
        name="leave_balances",
        columns=frozenset(
            {
                "employee_id",
                "leave_type_id",
                "fiscal_year",
                "opening_balance",
                "accrued_ytd",
                "used_ytd",
                "pending_ytd",
                "is_active",
            }
        ),
        why="get_leave_balance",
    ),
    RequiredTable(
        name="leave_requests",
        columns=frozenset(
            {
                "employee_id",
                "leave_type_id",
                "start_date",
                "end_date",
                "working_days",
                "status",
                "is_half_day",
                "reviewed_at",
                "is_active",
            }
        ),
        why="list_leave_requests",
    ),
    RequiredTable(
        name="leave_policies",
        columns=frozenset(
            {
                "leave_type_id",
                "scope",
                "scope_id",
                "annual_entitlement_days",
                "accrual_method",
                "accrual_rate",
                "max_carry_forward_days",
                "min_service_months",
                "max_consecutive_days",
                "notice_days",
                "allow_negative_balance",
                "gender_eligibility",
                "is_active",
            }
        ),
        why="get_leave_policy; the structured half of every hybrid answer",
    ),
    RequiredTable(
        name="attendance_summaries",
        columns=frozenset(
            {
                "employee_id",
                "year",
                "month",
                "working_days_in_month",
                "days_present",
                "days_absent",
                "days_on_leave",
                "total_hours_worked",
                "total_late_minutes",
                "total_overtime_minutes",
                "total_early_departure_minutes",
                "is_active",
            }
        ),
        why="get_attendance_summary",
    ),
)


def required_columns() -> dict[str, frozenset[str]]:
    """The contract as a plain mapping, for comparison against a catalogue."""
    return {table.name: table.columns for table in REQUIRED_TABLES}
