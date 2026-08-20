"""Domain policies: rules that are pure functions of their inputs."""

from __future__ import annotations

from erp_agent.domain.policies.field_policy import (
    COMPENSATION,
    CONTACT_PII,
    NEVER_RETURNED,
    assert_columns_permitted,
    is_never_returned,
    permitted_columns,
)
from erp_agent.domain.policies.grounding import (
    COMPARABLE_RULES,
    NOT_FOUND_PHRASE,
    ComparableRule,
    GroundedAnswer,
    GroundingLedger,
    PolicyDiscrepancy,
    SourceKind,
    SourceReference,
    cites_both_sources,
    policy_discrepancies,
    render_discrepancies,
)
from erp_agent.domain.policies.read_only_sql import (
    FORBIDDEN_FUNCTIONS,
    FORBIDDEN_KEYWORDS,
    PERMITTED_LEADING_KEYWORDS,
    assert_read_only,
    is_read_only,
)
from erp_agent.domain.policies.scope import EmployeeVisibility, visible_employees

__all__ = [
    "COMPARABLE_RULES",
    "COMPENSATION",
    "CONTACT_PII",
    "FORBIDDEN_FUNCTIONS",
    "FORBIDDEN_KEYWORDS",
    "NEVER_RETURNED",
    "NOT_FOUND_PHRASE",
    "PERMITTED_LEADING_KEYWORDS",
    "ComparableRule",
    "EmployeeVisibility",
    "GroundedAnswer",
    "GroundingLedger",
    "PolicyDiscrepancy",
    "SourceKind",
    "SourceReference",
    "assert_columns_permitted",
    "assert_read_only",
    "cites_both_sources",
    "is_never_returned",
    "is_read_only",
    "permitted_columns",
    "policy_discrepancies",
    "render_discrepancies",
    "visible_employees",
]
