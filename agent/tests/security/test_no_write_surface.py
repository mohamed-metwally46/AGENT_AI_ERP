"""Barriers 3 and 4: no write code path, and no arbitrary-SQL tool.

Static properties of the source tree. They need no database, so they run on
every developer machine and in every CI job, and they fail the moment someone
adds the thing they forbid.
"""

from __future__ import annotations

import ast
import inspect
import io
import tokenize
from pathlib import Path

import pytest

from erp_agent.domain.models.sql import SqlStatement
from erp_agent.domain.ports.data_source import ReadOnlyDataSource

pytestmark = pytest.mark.security

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "erp_agent"

_SOURCE_FILES = sorted(PACKAGE_ROOT.rglob("*.py"))

#: The one file allowed to contain write-statement text: the barrier probe
#: constructs writes precisely to prove the server refuses them.
#:
#: The policy module used to need an exemption too, until these guards started
#: scanning code rather than prose -- it only ever *named* the verbs it rejects,
#: in its docstrings.
_WRITES_EXPECTED_IN = frozenset(
    {PACKAGE_ROOT / "infrastructure" / "database" / "read_only_guard.py"}
)


def _code_only(path: Path) -> str:
    """Return a file's source with docstrings and comments blanked out.

    These guards search for forbidden constructs by substring, which cannot
    otherwise tell "this module implements `execute_sql`" from "this module
    documents that there is deliberately no `execute_sql`". Every exemption
    added for the second kind weakens the guard against the first.

    Ordinary string literals are deliberately *kept*: SQL lives in them, so
    stripping them would blind the guard to exactly what it is looking for.
    Lines are blanked rather than deleted, so line numbers stay usable.
    """
    source = path.read_text(encoding="utf-8-sig")
    lines = source.splitlines()

    # Docstrings and bare string expressions, from the AST.
    documentation: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            documentation.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))

    kept = ["" if number in documentation else line for number, line in enumerate(lines, start=1)]

    # Comments, from the tokeniser -- exact, unlike a regex that cannot tell a
    # `#` inside a string from one starting a comment.
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            row, column = token.start
            if row <= len(kept) and kept[row - 1]:
                kept[row - 1] = kept[row - 1][:column]

    return "\n".join(kept)


class TestThePortHasNoWriteVerb:
    def test_the_data_source_interface_exposes_only_reads(self):
        # Barrier 3 is "no write code path exists". The cheapest way to
        # guarantee that is to leave the verb out of the vocabulary, so adding
        # one becomes a visible change to the port rather than a quiet call in
        # an adapter.
        methods = {
            name
            for name, _ in inspect.getmembers(ReadOnlyDataSource, inspect.isfunction)
            if not name.startswith("_")
        }

        assert methods == {"fetch_all", "fetch_one", "probe"}

    @pytest.mark.parametrize(
        "forbidden", ["execute", "write", "insert", "update", "delete", "commit", "save"]
    )
    def test_no_mutating_verb_appears_in_the_port(self, forbidden):
        names = [name for name, _ in inspect.getmembers(ReadOnlyDataSource, inspect.isfunction)]

        assert not any(forbidden in name.lower() for name in names)


class TestNoArbitrarySqlTool:
    def test_no_execute_sql_style_tool_exists(self):
        # Barrier 4, and Plan.md section 6.2: `Agent -> execute_sql(query) ->
        # Database` is the forbidden shape. The registry is a static allowlist
        # of named, parameterised tools.
        offenders = [
            f"{path.relative_to(PACKAGE_ROOT)}"
            for path in _SOURCE_FILES
            for pattern in ("execute_sql", "run_sql", "raw_sql", "query_sql")
            if pattern in _code_only(path)
        ]

        assert offenders == []

    def test_no_select_star_is_shipped(self):
        # Plan.md section 6.4 rule 4: column allowlists per tool, so a newly
        # added sensitive column cannot appear in an answer by accident.
        offenders = [
            f"{path.relative_to(PACKAGE_ROOT)}"
            for path in _SOURCE_FILES
            if "SELECT *" in _code_only(path).upper()
        ]

        assert offenders == []


class TestWriteStatementsAreConfinedToTheProbe:
    @pytest.mark.parametrize(
        "verb", ["INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE", "TRUNCATE ", "ALTER TABLE"]
    )
    def test_only_the_barrier_probe_contains_a_write_statement(self, verb):
        # One file in the system constructs a write, and it does so to prove the
        # server refuses it, inside a forced-rollback savepoint.
        offenders = [
            f"{path.relative_to(PACKAGE_ROOT)}"
            for path in _SOURCE_FILES
            if path not in _WRITES_EXPECTED_IN and verb in _code_only(path).upper()
        ]

        assert offenders == []

    def test_the_exempt_files_still_exist(self):
        # An exemption list that names a deleted file silently exempts nothing
        # and hides the day someone moves the probe.
        assert all(path.exists() for path in _WRITES_EXPECTED_IN)

    def test_the_scanner_still_sees_sql_in_ordinary_string_literals(self):
        # A guard that stripped every string would pass forever. This proves
        # only documentation is removed: the barrier probe's write statements
        # live in plain literals and are still found.
        probe = PACKAGE_ROOT / "infrastructure" / "database" / "read_only_guard.py"

        assert "INSERT INTO" in _code_only(probe).upper()

    def test_the_scanner_ignores_a_forbidden_word_in_a_docstring(self):
        # The complement: the tools package documents that no `execute_sql`
        # exists, and that sentence must not read as a violation.
        tools = PACKAGE_ROOT / "application" / "tools" / "hr" / "__init__.py"

        assert "execute_sql" in tools.read_text(encoding="utf-8-sig")
        assert "execute_sql" not in _code_only(tools)


class TestInjectionCannotProduceAStatement:
    @pytest.mark.parametrize(
        "payload",
        [
            "SELECT 1; DROP TABLE employees",
            "SELECT 1; DELETE FROM employees; --",
            "SELECT 1 /**/; TRUNCATE employees",
            "SELECT 1 UNION SELECT 1; UPDATE employees SET first_name='x'",
            "select 1;\n-- comment\ndrop table employees",
            "SELECT 1; SET default_transaction_read_only = off",
        ],
    )
    def test_a_smuggled_write_never_becomes_a_statement_object(self, payload):
        from erp_agent.domain.errors import ReadOnlyViolationError

        with pytest.raises(ReadOnlyViolationError):
            SqlStatement(payload)

    def test_a_hostile_value_stays_a_value(self):
        # Values are bound, never formatted in. The payload is data and the
        # statement text is unchanged by it.
        payload = "'; DROP TABLE employees; --"
        statement = SqlStatement(
            "SELECT employee_id FROM employees WHERE employee_number = %(number)s",
            {"number": payload},
        )

        assert "DROP" not in statement.text.upper()
        assert statement.parameters["number"] == payload
