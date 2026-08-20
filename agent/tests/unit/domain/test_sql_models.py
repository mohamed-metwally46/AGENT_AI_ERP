"""SqlStatement makes an unsafe statement unrepresentable."""

from __future__ import annotations

import pytest

from erp_agent.domain.errors import ReadOnlyViolationError
from erp_agent.domain.models.sql import DEFAULT_MAX_ROWS, QueryResult, SqlStatement

pytestmark = pytest.mark.unit


class TestConstruction:
    def test_a_read_is_accepted(self):
        statement = SqlStatement("SELECT count(*) FROM employees")

        assert statement.text.startswith("SELECT")
        assert statement.max_rows == DEFAULT_MAX_ROWS

    @pytest.mark.parametrize(
        "text",
        [
            "INSERT INTO employees (employee_number) VALUES ('X')",
            "UPDATE employees SET first_name = 'x'",
            "DELETE FROM employees",
            "DROP TABLE employees",
            "SELECT 1; DROP TABLE employees",
            "SET default_transaction_read_only = off",
        ],
    )
    def test_a_write_cannot_be_constructed_at_all(self, text):
        # The validation is in __post_init__, so there is no window in which a
        # write statement exists as a valid object waiting to be checked.
        with pytest.raises(ReadOnlyViolationError):
            SqlStatement(text)

    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    def test_blank_text_is_refused(self, text):
        with pytest.raises(ValueError, match="non-empty"):
            SqlStatement(text)

    @pytest.mark.parametrize("max_rows", [0, -1, -100])
    def test_a_non_positive_row_cap_is_refused(self, max_rows):
        with pytest.raises(ValueError, match="max_rows"):
            SqlStatement("SELECT 1", max_rows=max_rows)


class TestParameters:
    def test_parameters_are_frozen_after_construction(self):
        supplied = {"number": "EMP-0001"}
        statement = SqlStatement("SELECT 1 WHERE %(number)s IS NOT NULL", supplied)

        # Mutating the original must not reach inside the statement, and the
        # statement's own mapping must refuse mutation outright.
        supplied["number"] = "EMP-9999"
        assert statement.parameters["number"] == "EMP-0001"
        with pytest.raises(TypeError):
            statement.parameters["number"] = "EMP-0002"  # type: ignore[index]

    def test_parameter_names_are_sorted_and_carry_no_values(self):
        statement = SqlStatement(
            "SELECT 1 WHERE %(zulu)s = %(alpha)s",
            {"zulu": "secret", "alpha": "also secret"},
        )

        assert statement.parameter_names == ("alpha", "zulu")
        assert "secret" not in str(statement.parameter_names)

    def test_parameters_default_to_empty(self):
        assert SqlStatement("SELECT 1").parameters == {}


class TestQueryResult:
    def test_an_empty_result_is_not_an_error(self):
        result = QueryResult(rows=())

        assert result.is_empty
        assert result.row_count == 0
        assert not result.truncated

    def test_row_count_reflects_the_rows(self):
        result = QueryResult(rows=({"a": 1}, {"a": 2}))

        assert result.row_count == 2
        assert not result.is_empty

    def test_truncation_is_reported_not_hidden(self):
        result = QueryResult(rows=({"a": 1},), truncated=True)

        assert result.truncated

    def test_a_negative_duration_is_refused(self):
        with pytest.raises(ValueError, match="duration_ms"):
            QueryResult(rows=(), duration_ms=-1.0)
