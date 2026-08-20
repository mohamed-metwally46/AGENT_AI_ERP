"""The application-side half of the write barrier (Plan.md 8.2, barrier 3)."""

from __future__ import annotations

import pytest

from erp_agent.domain.errors import ReadOnlyViolationError
from erp_agent.domain.policies.read_only_sql import assert_read_only, is_read_only

pytestmark = pytest.mark.unit


class TestReadsAreAccepted:
    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT 1",
            "select 1",
            "  \n  SELECT count(*) FROM employees  ",
            "SELECT * FROM employees;",
            "SELECT * FROM employees ;  ",
            "WITH active AS (SELECT 1) SELECT * FROM active",
            "-- a leading comment\nSELECT 1",
            "/* a leading block comment */ SELECT 1",
            "SELECT e.first_name FROM employees e WHERE e.employee_number = %(number)s",
            "SELECT count(*) FILTER (WHERE employment_status = 'active') FROM employees",
        ],
    )
    def test_a_plain_read_passes(self, statement):
        assert is_read_only(statement)
        assert_read_only(statement)  # does not raise

    def test_a_column_that_merely_contains_a_keyword_is_fine(self):
        # `updated_at` is one token, not the keyword UPDATE. A substring match
        # would reject half the schema.
        assert is_read_only("SELECT updated_at, created_by FROM employees")

    def test_a_keyword_inside_a_string_literal_is_data_not_code(self):
        assert is_read_only("SELECT 'DELETE FROM employees' AS example")

    def test_a_keyword_inside_a_quoted_identifier_is_fine(self):
        assert is_read_only('SELECT "drop" FROM employees')


class TestWritesAreRefused:
    @pytest.mark.parametrize(
        "statement",
        [
            "INSERT INTO employees (employee_number) VALUES ('X')",
            "UPDATE employees SET first_name = 'x'",
            "DELETE FROM employees",
            "DROP TABLE employees",
            "ALTER TABLE employees ADD COLUMN x integer",
            "TRUNCATE employees",
            "CREATE TABLE probe (i integer)",
            "GRANT SELECT ON employees TO someone",
            "REVOKE SELECT ON employees FROM someone",
            "COPY employees TO '/tmp/out.csv'",
            "MERGE INTO employees USING x ON true",
        ],
    )
    def test_every_forbidden_verb_is_refused(self, statement):
        assert not is_read_only(statement)
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only(statement)

    def test_set_is_refused_because_it_can_disable_the_database_barrier(self):
        # default_transaction_read_only is a USERSET GUC: a client that can
        # issue this switches off barrier 1's session half by itself. Verified
        # against the live server -- with it off, writes fail on privilege
        # instead, which is why the grants matter independently.
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only("SET default_transaction_read_only = off")

    def test_a_row_lock_is_a_write(self):
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only("SELECT * FROM employees FOR UPDATE")

    @pytest.mark.parametrize(
        "statement",
        [
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT lo_export(1, '/tmp/x')",
            "SELECT dblink('host=evil', 'SELECT 1')",
            "SELECT pg_terminate_backend(1)",
        ],
    )
    def test_reads_of_things_other_than_erp_rows_are_refused(self, statement):
        # A read-only transaction executes every one of these happily.
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only(statement)


class TestSmugglingIsRefused:
    def test_a_second_statement_is_refused(self):
        with pytest.raises(ReadOnlyViolationError, match="more than one statement"):
            assert_read_only("SELECT 1; DROP TABLE employees")

    def test_a_write_hidden_behind_a_line_comment_is_still_found(self):
        # The comment ends at the newline; what follows is a real statement.
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only("SELECT 1 -- harmless\n; DROP TABLE employees")

    def test_a_write_hidden_behind_a_block_comment_is_still_found(self):
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only("SELECT 1 /* harmless */ ; DELETE FROM employees")

    def test_a_dollar_quoted_body_cannot_carry_a_statement_out(self):
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only("DO $$ BEGIN PERFORM 1; END $$")

    def test_an_unterminated_literal_does_not_open_a_hole(self):
        # Everything after the opening quote is treated as literal, so the
        # statement reduces to `SELECT` alone and is refused rather than parsed
        # optimistically.
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only("SELECT 'unterminated")

    @pytest.mark.parametrize("statement", ["", "   ", "-- only a comment", "/* only */"])
    def test_an_empty_statement_is_refused(self, statement):
        with pytest.raises(ReadOnlyViolationError):
            assert_read_only(statement)


class TestTheRefusalDoesNotLeak:
    def test_the_rejected_sql_is_not_repeated_in_the_error(self):
        # Refusing a statement and then logging it verbatim defeats the refusal.
        secret_table = "salary_of_the_ceo"
        with pytest.raises(ReadOnlyViolationError) as caught:
            assert_read_only(f"DELETE FROM {secret_table}")

        assert secret_table not in caught.value.message
        assert secret_table not in str(caught.value.context)

    def test_the_error_explains_what_to_fix(self):
        with pytest.raises(ReadOnlyViolationError, match="DELETE"):
            assert_read_only("DELETE FROM employees")
