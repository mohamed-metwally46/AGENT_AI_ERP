"""The schema contract, pinned to the code it claims to describe.

A contract with the ERP owners is only worth having if it is true. It is written
by hand so a person can read and argue with it, which means it can fall behind
the queries -- and a contract that has fallen behind is worse than none, because
a drift check against it reports COMPATIBLE for a database that breaks a tool.

So this re-derives the contract from ``queries/hr_directory.py`` and asserts the
two agree. The derivation is crude on purpose: it reads aliases out of ``FROM``
and ``JOIN`` clauses and column references out of ``alias.column``, which is
exactly the shape that module's SQL is written in. If somebody writes SQL it
cannot parse, this test fails rather than passing vacuously -- which is the
right direction to fail in.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from erp_agent.infrastructure.database.schema_contract import REQUIRED_TABLES, required_columns

pytestmark = [pytest.mark.unit]

_QUERIES = (
    Path(__file__).resolve().parents[3]
    / "src/erp_agent/infrastructure/database/queries/hr_directory.py"
)

#: `FROM employees e` / `JOIN leave_types lt` -- table then alias.
_ALIASED = re.compile(r"\b(?:FROM|JOIN)\s+([a-z_]+)\s+([a-z_]+)\b")

#: `alias.column`, the only way the queries reference a column.
_REFERENCE = re.compile(r"\b([a-z_]+)\.([a-z_]+)\b")


def _read_by_the_queries() -> dict[str, set[str]]:
    """Which columns of which tables the query layer actually touches."""
    source = _QUERIES.read_text(encoding="utf-8")

    aliases = {alias: table for table, alias in _ALIASED.findall(source)}
    found: dict[str, set[str]] = {}
    for alias, column in _REFERENCE.findall(source):
        if alias in aliases:
            found.setdefault(aliases[alias], set()).add(column)
    return found


class TestTheContractMatchesTheQueries:
    def test_the_derivation_finds_something(self):
        # A parser that silently matched nothing would make every assertion
        # below pass while checking nothing at all.
        derived = _read_by_the_queries()

        assert len(derived) >= 10
        assert "employees" in derived
        assert len(derived["employees"]) >= 10

    def test_every_table_the_queries_read_is_in_the_contract(self):
        # A table the agent reads and the contract omits is a table a drift
        # check would never look at.
        declared = set(required_columns())

        assert set(_read_by_the_queries()) <= declared

    def test_every_column_the_queries_read_is_in_the_contract(self):
        declared = required_columns()
        derived = _read_by_the_queries()

        undeclared = {
            table: sorted(columns - declared[table])
            for table, columns in derived.items()
            if table in declared and columns - declared[table]
        }

        assert undeclared == {}

    def test_the_contract_declares_nothing_the_queries_do_not_read(self):
        # The other direction. A contract that over-claims turns an irrelevant
        # schema change into a false alarm, and false alarms are how a check
        # stops being run.
        declared = required_columns()
        derived = _read_by_the_queries()

        unused = {
            table: sorted(columns - derived.get(table, set()))
            for table, columns in declared.items()
            if columns - derived.get(table, set())
        }

        assert unused == {}


class TestTheContractIsUsable:
    def test_every_required_table_says_what_it_breaks(self):
        # Read at 3am by somebody who needs the impact before the detail.
        for table in REQUIRED_TABLES:
            assert table.why.strip(), table.name

    def test_no_table_is_declared_twice(self):
        names = [table.name for table in REQUIRED_TABLES]

        assert len(names) == len(set(names))

    def test_no_table_is_declared_with_no_columns(self):
        for table in REQUIRED_TABLES:
            assert table.columns, table.name

    def test_the_join_keys_are_declared(self):
        # A join key that vanished breaks a tool as surely as a selected column,
        # and is easier to forget because it never appears in a result.
        declared = required_columns()

        assert "dept_id" in declared["employees"]
        assert "dept_id" in declared["branch_departments"]
        assert "leave_type_id" in declared["leave_balances"]
        assert "leave_type_id" in declared["leave_types"]

    def test_the_scope_spine_is_declared(self):
        # Every scope predicate is built on these. Losing one does not degrade
        # the agent, it removes the boundary.
        declared = required_columns()

        assert {"employee_id", "dept_id", "branch_id", "manager_id"} <= declared["employees"]
