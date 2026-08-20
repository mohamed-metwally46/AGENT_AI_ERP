"""Configuration refuses a DSN that would hand the agent write access."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from erp_agent.config import Settings, parse_postgres_dsn

pytestmark = pytest.mark.unit

_GOOD_DSN = "postgresql://mock_hr_readonly:readonly_password@localhost:5433/mock_hr"


def _settings(**env: str) -> Settings:
    """Build settings from an explicit environment, ignoring any local .env."""
    return Settings.from_environment({"ERP_DATABASE_URL": _GOOD_DSN, **env}, use_env_file=False)


class TestDsnIsRequired:
    def test_a_missing_dsn_is_refused_at_startup(self):
        with pytest.raises(ValidationError, match="ERP_DATABASE_URL"):
            Settings.from_environment({"ERP_DATABASE_URL": ""}, use_env_file=False)

    @pytest.mark.parametrize(
        ("dsn", "expected"),
        [
            ("mysql://user@host/db", "scheme"),
            ("postgresql://host/db", "login role"),
            ("postgresql://user@host", "database"),
            ("postgresql://user@/db", "host"),
        ],
    )
    def test_a_malformed_dsn_is_refused_with_an_actionable_message(self, dsn, expected):
        with pytest.raises(ValidationError, match=expected):
            _settings(ERP_DATABASE_URL=dsn)


class TestWritableRolesAreRefused:
    @pytest.mark.parametrize("role", ["postgres", "hr_user", "root", "admin", "POSTGRES"])
    def test_a_schema_owning_role_is_refused(self, role):
        # The realistic accident is pointing the agent at the role that seeded
        # the database. No application-side check can revoke what that role
        # holds, so it is refused before a socket is opened.
        with pytest.raises(ValidationError, match="read-only role"):
            _settings(ERP_DATABASE_URL=f"postgresql://{role}:pw@localhost:5433/mock_hr")

    def test_a_read_only_role_is_accepted(self):
        assert _settings().database.parts.role == "mock_hr_readonly"

    def test_the_denylist_is_not_the_real_check(self):
        # A differently-named role holding write grants passes here and is
        # caught by the startup probe instead. Documented so nobody mistakes
        # this for the whole barrier.
        settings = _settings(ERP_DATABASE_URL="postgresql://sneaky:pw@localhost:5433/mock_hr")

        assert settings.database.parts.role == "sneaky"


class TestSecretsStaySecret:
    def test_the_safe_url_omits_the_password(self):
        settings = _settings()

        assert "readonly_password" not in settings.database.safe_url
        assert settings.database.safe_url == "postgresql://mock_hr_readonly@localhost:5433/mock_hr"

    def test_the_password_is_not_in_the_repr(self):
        assert "readonly_password" not in repr(_settings())


class TestPoolAndLimits:
    def test_a_minimum_larger_than_the_maximum_is_refused(self):
        with pytest.raises(ValidationError, match="ERP_DB_MIN_POOL_SIZE"):
            _settings(ERP_DB_MIN_POOL_SIZE="10", ERP_DB_MAX_POOL_SIZE="2")

    def test_the_defaults_match_the_plan(self):
        database = _settings().database

        # Plan.md section 5.3: TOOL_TIMEOUT_SECONDS = 15,
        # MAX_ROWS_PER_TOOL_RESULT = 100.
        assert database.statement_timeout_ms == 15_000
        assert database.max_rows == 100

    @pytest.mark.parametrize("value", ["0", "-1", "200000"])
    def test_an_out_of_range_statement_timeout_is_refused(self, value):
        with pytest.raises(ValidationError):
            _settings(ERP_DB_STATEMENT_TIMEOUT_MS=value)


class TestProcessSettings:
    def test_an_unknown_log_level_is_refused(self):
        with pytest.raises(ValidationError, match="LOG_LEVEL"):
            _settings(LOG_LEVEL="CHATTY")

    def test_the_log_level_is_normalised(self):
        assert _settings(LOG_LEVEL="debug").app.log_level == "DEBUG"

    def test_production_is_recognised(self):
        assert _settings(APP_ENV="production").app.is_production
        assert not _settings(APP_ENV="local").app.is_production


class TestIntrospection:
    def test_every_variable_is_discoverable(self):
        names = Settings.environment_variable_names()

        assert "ERP_DATABASE_URL" in names
        assert "APP_ENV" in names
        assert names == tuple(sorted(names))


class TestDsnParsing:
    def test_the_default_port_is_applied(self):
        assert parse_postgres_dsn("postgresql://role@host/db").port == 5432

    def test_a_percent_encoded_password_does_not_break_parsing(self):
        parts = parse_postgres_dsn("postgresql://role:p%40ss%3Aword@host:5433/db")

        assert parts.has_password
        assert parts.role == "role"

    def test_the_string_form_never_carries_the_password(self):
        parts = parse_postgres_dsn("postgresql://role:hunter2@host:5433/db")

        assert "hunter2" not in str(parts)
