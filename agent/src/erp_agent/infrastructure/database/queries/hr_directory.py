"""The six MVP reads, as fixed statements over the read-only data source.

Every statement here obeys Plan.md section 6.4:

1. Parameterised only. No value is ever formatted into a string.
2. Read-only transactions, enforced by the data source.
3. A row cap on every query.
4. Explicit column lists. ``SELECT *`` is banned, so a sensitive column added to
   ``employees`` tomorrow cannot appear in an answer by accident.
5. ``is_active`` filtered unless history is asked for.
6. ``attendance_summaries``, not ``attendance_records``, for monthly questions.
7. Defensive joins. Roughly 26 columns in this schema look like foreign keys but
   have no constraint (Plan.md section 3.1, quirk 3), so every join to a parent
   is a ``LEFT JOIN`` and a missing parent reads as absent data rather than a
   vanished row. An ``INNER JOIN`` here would silently drop an employee whose
   department row is missing -- which is a wrong count, not an error.

The only thing interpolated into any statement is a scope clause built by
:mod:`erp_agent.infrastructure.database.predicates`, whose parameters are
namespaced so a tool argument cannot collide with them.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from erp_agent.domain.models.hr import (
    AttendanceSummary,
    EmployeeSummary,
    Headcount,
    LeaveBalance,
    LeavePolicy,
    LeaveRequestSummary,
)
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.models.sql import Row, SqlStatement
from erp_agent.domain.policies.field_policy import HR_ROLES
from erp_agent.domain.policies.scope import visible_employees
from erp_agent.domain.ports.data_source import ReadOnlyDataSource
from erp_agent.domain.ports.hr_directory import HrDirectory
from erp_agent.infrastructure.database.predicates import (
    Predicate,
    merge_parameters,
    scope_predicate,
    where_clause,
)

__all__ = ["PostgresHrDirectory"]

#: Contact detail that is only selected when the caller may read it. Held as a
#: constant so the conditional column list is assembled from code, never from
#: input.
_PII_COLUMNS = "e.date_of_birth, e.phone_mobile, e.email_personal"
_PII_ABSENT = (
    "NULL::date AS date_of_birth, NULL::text AS phone_mobile, NULL::text AS email_personal"
)


def _may_read_contact_pii(context: SecurityContext) -> bool:
    """Whether contact PII may be selected at all for this principal.

    Deliberately decided *before* the query rather than redacted after, so the
    values never enter the process. Two cases qualify: an HR role, which may
    read it about anyone; and ``SELF`` scope, where the only row the predicate
    can return is the principal's own.

    Conservative on purpose. A ``TEAM``-scoped manager reading their *own*
    record does not get their own date of birth, because at statement-build time
    we do not yet know which row will come back. Under-permission is the safe
    direction, and the column can be requested through a self-scoped path.
    """
    return context.is_self_scoped or context.has_any_role(*HR_ROLES)


def _optional(value: object) -> str | None:
    """Render a nullable column as a string, preserving ``None``."""
    return None if value is None else str(value)


def _decimal(value: object) -> Decimal:
    """Coerce a numeric column to ``Decimal``, treating NULL as zero."""
    if value is None:
        return Decimal(0)
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _optional_decimal(value: object) -> Decimal | None:
    """Coerce a nullable numeric column, preserving ``None``."""
    return None if value is None else _decimal(value)


def _int(value: object) -> int:
    """Coerce a nullable integer column, treating NULL as zero."""
    return 0 if value is None else int(value)  # type: ignore[call-overload]


class PostgresHrDirectory(HrDirectory):
    """Reads HR records through the read-only data source."""

    def __init__(self, source: ReadOnlyDataSource) -> None:
        """Hold the data source.

        Args:
            source: The read-only connection. Typed as the port, so this class
                cannot reach a driver directly.
        """
        self._source = source

    # ------------------------------------------------------------ employees #
    async def find_employees(
        self,
        context: SecurityContext,
        *,
        employee_number: str | None = None,
        full_name: str | None = None,
        limit: int = 10,
    ) -> tuple[EmployeeSummary, ...]:
        """Find employees by business key or name, within scope."""
        filters: list[Predicate] = [scope_predicate(visible_employees(context), alias="e")]
        arguments: dict[str, Any] = {}

        if employee_number is not None:
            filters.append(Predicate("e.employee_number = %(employee_number)s"))
            arguments["employee_number"] = employee_number.strip()
        if full_name is not None:
            # Concatenated then matched, so "Ahmed Hassan" finds a row whose
            # given and family names are stored separately. `%` and `_` in the
            # supplied value are escaped, or a caller could pass "%" and match
            # everyone their scope allows.
            filters.append(
                Predicate(
                    "lower(e.first_name || ' ' || e.last_name) LIKE "
                    "lower(%(full_name)s) ESCAPE '\\'"
                )
            )
            arguments["full_name"] = f"%{_escape_like(full_name.strip())}%"

        clause = where_clause(filters)
        pii = _PII_COLUMNS if _may_read_contact_pii(context) else _PII_ABSENT

        statement = SqlStatement(
            f"""
            SELECT e.employee_number,
                   e.first_name || ' ' || e.last_name AS full_name,
                   e.employment_status,
                   e.employment_type,
                   e.hire_date,
                   e.email_work,
                   d.dept_name        AS department,
                   b.branch_name      AS branch,
                   p.position_title,
                   g.grade_code       AS job_grade,
                   m.employee_number  AS manager_employee_number,
                   {pii}
              FROM employees e
              LEFT JOIN branch_departments    d ON d.dept_id      = e.dept_id
              LEFT JOIN organization_branches b ON b.branch_id    = e.branch_id
              LEFT JOIN positions             p ON p.position_id  = e.position_id
              LEFT JOIN job_grades            g ON g.job_grade_id = e.job_grade_id
              LEFT JOIN employees             m ON m.employee_id  = e.manager_id
              {clause.sql}
             ORDER BY e.employee_number
             LIMIT %(row_limit)s
            """,
            merge_parameters(clause, {**arguments, "row_limit": _capped(limit)}),
            max_rows=_capped(limit),
        )

        result = await self._source.fetch_all(statement)
        return tuple(_to_employee(row) for row in result.rows)

    async def count_employees(
        self,
        context: SecurityContext,
        *,
        department: str | None = None,
        branch: str | None = None,
        employment_status: str | None = None,
    ) -> Headcount:
        """Count employees matching a filter, within scope."""
        filters: list[Predicate] = [scope_predicate(visible_employees(context), alias="e")]
        arguments: dict[str, Any] = {}

        if department is not None:
            filters.append(Predicate("lower(d.dept_name) = lower(%(department)s)"))
            arguments["department"] = department.strip()
        if branch is not None:
            filters.append(Predicate("lower(b.branch_name) = lower(%(branch)s)"))
            arguments["branch"] = branch.strip()
        if employment_status is not None:
            filters.append(Predicate("e.employment_status = %(employment_status)s"))
            arguments["employment_status"] = employment_status.strip()

        clause = where_clause(filters)

        statement = SqlStatement(
            f"""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE e.employment_status = 'active') AS active
              FROM employees e
              LEFT JOIN branch_departments    d ON d.dept_id   = e.dept_id
              LEFT JOIN organization_branches b ON b.branch_id = e.branch_id
              {clause.sql}
            """,
            merge_parameters(clause, arguments),
            max_rows=1,
        )

        row = await self._source.fetch_one(statement)
        return Headcount(
            total=_int(row["total"]) if row else 0,
            active=_int(row["active"]) if row else 0,
            department=department,
            branch=branch,
            employment_status=employment_status,
        )

    # ---------------------------------------------------------------- leave #
    async def leave_balances(
        self,
        context: SecurityContext,
        *,
        employee_number: str,
        leave_code: str | None = None,
        fiscal_year: int | None = None,
    ) -> tuple[LeaveBalance, ...]:
        """Read leave balances for one employee, within scope."""
        filters: list[Predicate] = [
            scope_predicate(visible_employees(context), alias="e"),
            Predicate("e.employee_number = %(employee_number)s"),
            Predicate("lb.is_active"),
        ]
        arguments: dict[str, Any] = {"employee_number": employee_number.strip()}

        if leave_code is not None:
            filters.append(Predicate("lower(lt.leave_code) = lower(%(leave_code)s)"))
            arguments["leave_code"] = leave_code.strip()
        if fiscal_year is not None:
            filters.append(Predicate("lb.fiscal_year = %(fiscal_year)s"))
            arguments["fiscal_year"] = fiscal_year
        else:
            # The latest year on file for this employee, not the current
            # calendar year: a mock database, or a question asked in January,
            # would otherwise return nothing and read as "no entitlement".
            filters.append(
                Predicate(
                    "lb.fiscal_year = (SELECT max(inner_lb.fiscal_year) "
                    "FROM leave_balances inner_lb "
                    "WHERE inner_lb.employee_id = e.employee_id)"
                )
            )

        clause = where_clause(filters)

        statement = SqlStatement(
            f"""
            SELECT e.employee_number,
                   lt.leave_code,
                   lt.leave_name,
                   lb.fiscal_year,
                   lb.opening_balance,
                   lb.accrued_ytd,
                   lb.used_ytd,
                   lb.pending_ytd
              FROM leave_balances lb
              JOIN      employees  e  ON e.employee_id   = lb.employee_id
              LEFT JOIN leave_types lt ON lt.leave_type_id = lb.leave_type_id
              {clause.sql}
             ORDER BY lt.leave_code
             LIMIT 50
            """,
            merge_parameters(clause, arguments),
            max_rows=50,
        )

        result = await self._source.fetch_all(statement)
        return tuple(
            LeaveBalance(
                employee_number=str(row["employee_number"]),
                leave_code=str(row["leave_code"] or "?"),
                leave_name=str(row["leave_name"] or "unknown leave type"),
                fiscal_year=_int(row["fiscal_year"]),
                opening_balance=_decimal(row["opening_balance"]),
                accrued_ytd=_decimal(row["accrued_ytd"]),
                used_ytd=_decimal(row["used_ytd"]),
                pending_ytd=_decimal(row["pending_ytd"]),
            )
            for row in result.rows
        )

    async def leave_requests(
        self,
        context: SecurityContext,
        *,
        employee_number: str,
        status: str | None = None,
        start_from: date | None = None,
        start_to: date | None = None,
        limit: int = 50,
    ) -> tuple[LeaveRequestSummary, ...]:
        """List leave requests for one employee, within scope."""
        filters: list[Predicate] = [
            scope_predicate(visible_employees(context), alias="e"),
            Predicate("e.employee_number = %(employee_number)s"),
            Predicate("lr.is_active"),
        ]
        arguments: dict[str, Any] = {"employee_number": employee_number.strip()}

        if status is not None:
            filters.append(Predicate("lower(lr.status) = lower(%(status)s)"))
            arguments["status"] = status.strip()
        if start_from is not None:
            filters.append(Predicate("lr.start_date >= %(start_from)s"))
            arguments["start_from"] = start_from
        if start_to is not None:
            filters.append(Predicate("lr.start_date <= %(start_to)s"))
            arguments["start_to"] = start_to

        clause = where_clause(filters)

        statement = SqlStatement(
            f"""
            SELECT e.employee_number,
                   lt.leave_code,
                   lt.leave_name,
                   lr.start_date,
                   lr.end_date,
                   lr.working_days,
                   lr.status,
                   lr.is_half_day,
                   lr.reviewed_at
              FROM leave_requests lr
              JOIN      employees  e  ON e.employee_id    = lr.employee_id
              LEFT JOIN leave_types lt ON lt.leave_type_id = lr.leave_type_id
              {clause.sql}
             ORDER BY lr.start_date DESC
             LIMIT %(row_limit)s
            """,
            merge_parameters(clause, {**arguments, "row_limit": _capped(limit)}),
            max_rows=_capped(limit),
        )

        result = await self._source.fetch_all(statement)
        return tuple(
            LeaveRequestSummary(
                employee_number=str(row["employee_number"]),
                leave_code=str(row["leave_code"] or "?"),
                leave_name=str(row["leave_name"] or "unknown leave type"),
                start_date=row["start_date"],
                end_date=row["end_date"],
                working_days=_decimal(row["working_days"]),
                status=str(row["status"]),
                is_half_day=bool(row["is_half_day"]),
                reviewed_at=row["reviewed_at"],
            )
            for row in result.rows
        )

    async def leave_policies(
        self,
        *,
        leave_code: str,
        job_grade_code: str | None = None,
    ) -> tuple[LeavePolicy, ...]:
        """Read entitlement rules for a leave type.

        No scope predicate, deliberately: an entitlement rule is company policy
        and is exactly what an employee is entitled to be told about their own
        leave.
        """
        filters: list[Predicate] = [
            Predicate("lower(lt.leave_code) = lower(%(leave_code)s)"),
            Predicate("lp.is_active"),
        ]
        arguments: dict[str, Any] = {"leave_code": leave_code.strip()}

        if job_grade_code is not None:
            # Organisation-wide policies always apply; grade-scoped ones only
            # when the grade matches. Returning both, ordered, lets an answer
            # state which rule it applied rather than silently preferring one.
            filters.append(
                Predicate(
                    "(lp.scope = 'all' OR (lp.scope = 'grade' "
                    "AND lp.scope_id = (SELECT g.job_grade_id FROM job_grades g "
                    "WHERE lower(g.grade_code) = lower(%(job_grade_code)s))))"
                )
            )
            arguments["job_grade_code"] = job_grade_code.strip()

        clause = where_clause(filters)

        statement = SqlStatement(
            f"""
            SELECT lt.leave_code,
                   lt.leave_name,
                   lp.scope,
                   lp.annual_entitlement_days,
                   lp.accrual_method,
                   lp.accrual_rate,
                   lp.max_carry_forward_days,
                   lp.min_service_months,
                   lp.max_consecutive_days,
                   lp.notice_days,
                   lp.allow_negative_balance,
                   lp.gender_eligibility,
                   COALESCE(g.grade_name, d.dept_name) AS scope_label
              FROM leave_policies lp
              LEFT JOIN leave_types        lt ON lt.leave_type_id = lp.leave_type_id
              LEFT JOIN job_grades         g  ON lp.scope = 'grade' AND g.job_grade_id = lp.scope_id
              LEFT JOIN branch_departments d  ON lp.scope = 'dept'  AND d.dept_id      = lp.scope_id
              {clause.sql}
             ORDER BY CASE lp.scope WHEN 'all' THEN 0 WHEN 'grade' THEN 1 ELSE 2 END
             LIMIT 25
            """,
            merge_parameters(clause, arguments),
            max_rows=25,
        )

        result = await self._source.fetch_all(statement)
        return tuple(
            LeavePolicy(
                leave_code=str(row["leave_code"] or leave_code),
                leave_name=str(row["leave_name"] or "unknown leave type"),
                scope=str(row["scope"]),
                scope_label=_optional(row["scope_label"]),
                annual_entitlement_days=_decimal(row["annual_entitlement_days"]),
                accrual_method=_optional(row["accrual_method"]),
                accrual_rate=_optional_decimal(row["accrual_rate"]),
                max_carry_forward_days=_optional_decimal(row["max_carry_forward_days"]),
                min_service_months=(
                    None if row["min_service_months"] is None else int(row["min_service_months"])
                ),
                max_consecutive_days=(
                    None
                    if row["max_consecutive_days"] is None
                    else int(row["max_consecutive_days"])
                ),
                notice_days=None if row["notice_days"] is None else int(row["notice_days"]),
                allow_negative_balance=bool(row["allow_negative_balance"]),
                gender_eligibility=_optional(row["gender_eligibility"]),
            )
            for row in result.rows
        )

    # ----------------------------------------------------------- attendance #
    async def attendance_summaries(
        self,
        context: SecurityContext,
        *,
        employee_number: str,
        year: int,
        month: int | None = None,
    ) -> tuple[AttendanceSummary, ...]:
        """Read pre-aggregated monthly attendance, within scope."""
        filters: list[Predicate] = [
            scope_predicate(visible_employees(context), alias="e"),
            Predicate("e.employee_number = %(employee_number)s"),
            Predicate("a.year = %(year)s"),
            Predicate("a.is_active"),
        ]
        arguments: dict[str, Any] = {"employee_number": employee_number.strip(), "year": year}

        if month is not None:
            filters.append(Predicate("a.month = %(month)s"))
            arguments["month"] = month

        clause = where_clause(filters)

        statement = SqlStatement(
            f"""
            SELECT e.employee_number,
                   a.year,
                   a.month,
                   a.working_days_in_month,
                   a.days_present,
                   a.days_absent,
                   a.days_on_leave,
                   a.total_hours_worked,
                   a.total_late_minutes,
                   a.total_overtime_minutes,
                   a.total_early_departure_minutes
              FROM attendance_summaries a
              JOIN employees e ON e.employee_id = a.employee_id
              {clause.sql}
             ORDER BY a.year, a.month
             LIMIT 12
            """,
            merge_parameters(clause, arguments),
            max_rows=12,
        )

        result = await self._source.fetch_all(statement)
        return tuple(
            AttendanceSummary(
                employee_number=str(row["employee_number"]),
                year=_int(row["year"]),
                month=_int(row["month"]),
                working_days_in_month=_int(row["working_days_in_month"]),
                days_present=_int(row["days_present"]),
                days_absent=_int(row["days_absent"]),
                days_on_leave=_int(row["days_on_leave"]),
                total_hours_worked=_decimal(row["total_hours_worked"]),
                total_late_minutes=_int(row["total_late_minutes"]),
                total_overtime_minutes=_int(row["total_overtime_minutes"]),
                total_early_departure_minutes=_int(row["total_early_departure_minutes"]),
            )
            for row in result.rows
        )


def _capped(limit: int) -> int:
    """Clamp a caller's row limit to the agent's ceiling.

    MAX_ROWS_PER_TOOL_RESULT is 100 (Plan.md section 5.3). A tool argument may
    ask for fewer; it may not ask for more.
    """
    return max(1, min(limit, 100))


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards in a user-supplied fragment.

    Without this, ``full_name='%'`` matches every employee the principal's scope
    allows -- not a privilege escalation, but a way to enumerate a department
    from a question that looked like a name lookup.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _to_employee(row: Row) -> EmployeeSummary:
    """Map a result row to an employee summary."""
    return EmployeeSummary(
        employee_number=str(row["employee_number"]),
        full_name=str(row["full_name"]),
        employment_status=str(row["employment_status"]),
        employment_type=str(row["employment_type"]),
        hire_date=row["hire_date"],
        department=_optional(row["department"]),
        branch=_optional(row["branch"]),
        position_title=_optional(row["position_title"]),
        job_grade=_optional(row["job_grade"]),
        manager_employee_number=_optional(row["manager_employee_number"]),
        email_work=_optional(row["email_work"]),
        date_of_birth=row["date_of_birth"],
        phone_mobile=_optional(row["phone_mobile"]),
        email_personal=_optional(row["email_personal"]),
    )
