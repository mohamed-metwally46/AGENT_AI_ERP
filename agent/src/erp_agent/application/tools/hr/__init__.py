"""The six MVP HR tools (Plan.md section 6.3).

A static set. Adding a seventh is a visible change to this file, which is what
makes the registry an allowlist rather than a suggestion (Plan.md section 8.2,
barrier 4). There is deliberately no `execute_sql`, and no tool that accepts a
table name, a column name or a SQL fragment.
"""

from __future__ import annotations

from erp_agent.application.tools.hr.attendance import GetAttendanceSummaryTool
from erp_agent.application.tools.hr.employees import CountEmployeesTool, GetEmployeeTool
from erp_agent.application.tools.hr.leave import (
    GetLeaveBalanceTool,
    GetLeavePolicyTool,
    ListLeaveRequestsTool,
)

__all__ = [
    "MVP_TOOL_NAMES",
    "CountEmployeesTool",
    "GetAttendanceSummaryTool",
    "GetEmployeeTool",
    "GetLeaveBalanceTool",
    "GetLeavePolicyTool",
    "ListLeaveRequestsTool",
]

#: The six names Plan.md section 6.3 defines. Asserted by the security suite, so
#: a seventh tool cannot appear without the test that guards the allowlist
#: failing first.
MVP_TOOL_NAMES: frozenset[str] = frozenset(
    {
        GetEmployeeTool.name,
        CountEmployeesTool.name,
        GetLeaveBalanceTool.name,
        ListLeaveRequestsTool.name,
        GetLeavePolicyTool.name,
        GetAttendanceSummaryTool.name,
    }
)
