"""Error codes are derived, and security failures are never retryable."""

from __future__ import annotations

import pytest

from erp_agent.domain.errors import (
    ConfigurationError,
    DataSourceError,
    DataSourceUnavailableError,
    ErpAgentError,
    QueryTimeoutError,
    ReadOnlyViolationError,
    WriteBarrierError,
)

pytestmark = pytest.mark.unit


class TestCodes:
    @pytest.mark.parametrize(
        ("error_type", "expected"),
        [
            (ConfigurationError, "CONFIGURATION_ERROR"),
            (DataSourceError, "DATA_SOURCE_ERROR"),
            (DataSourceUnavailableError, "DATA_SOURCE_UNAVAILABLE_ERROR"),
            (QueryTimeoutError, "QUERY_TIMEOUT_ERROR"),
            (ReadOnlyViolationError, "READ_ONLY_VIOLATION_ERROR"),
            (WriteBarrierError, "WRITE_BARRIER_ERROR"),
        ],
    )
    def test_the_code_is_derived_from_the_class_name(self, error_type, expected):
        # Derived rather than declared, so it cannot drift from the class.
        assert error_type("x").code == expected


class TestRetryability:
    @pytest.mark.parametrize("error_type", [DataSourceUnavailableError, QueryTimeoutError])
    def test_transient_failures_are_retryable(self, error_type):
        assert error_type("x").retryable

    @pytest.mark.parametrize(
        "error_type",
        [ConfigurationError, ReadOnlyViolationError, WriteBarrierError, DataSourceError],
    )
    def test_security_and_configuration_failures_are_not_retryable(self, error_type):
        # Re-running a refused query does not make it allowed.
        assert not error_type("x").retryable

    def test_an_occurrence_can_override_the_class_default(self):
        assert DataSourceError("x", retryable=True).retryable


class TestHierarchy:
    @pytest.mark.parametrize(
        "error_type",
        [
            ConfigurationError,
            DataSourceError,
            DataSourceUnavailableError,
            QueryTimeoutError,
            ReadOnlyViolationError,
            WriteBarrierError,
        ],
    )
    def test_every_error_is_catchable_as_one_type(self, error_type):
        assert issubclass(error_type, ErpAgentError)

    @pytest.mark.parametrize(
        "error_type", [DataSourceUnavailableError, QueryTimeoutError, ReadOnlyViolationError]
    )
    def test_data_source_failures_share_a_parent(self, error_type):
        assert issubclass(error_type, DataSourceError)


class TestContext:
    def test_context_defaults_to_empty_and_is_copied(self):
        supplied = {"role": "mock_hr_readonly"}
        error = ErpAgentError("x", context=supplied)
        supplied["role"] = "postgres"

        assert error.context == {"role": "mock_hr_readonly"}
        assert ErpAgentError("x").context == {}

    def test_repr_does_not_expand_the_context(self):
        error = DataSourceError("failed", context={"parameters": ("employee_number",)})

        assert "employee_number" not in repr(error)
        assert "DATA_SOURCE_ERROR" in repr(error)
