"""Start-up translates configuration failures into one domain error type."""

from __future__ import annotations

import pathlib

import pytest

from erp_agent.config import Settings
from erp_agent.core.bootstrap import load_settings, run_startup_checks
from erp_agent.core.container import Container
from erp_agent.domain.errors import ConfigurationError

pytestmark = pytest.mark.unit

_GOOD_DSN = "postgresql://mock_hr_readonly:readonly_password@localhost:5433/mock_hr"


def _env(**overrides: str) -> dict[str, str]:
    return {"ERP_DATABASE_URL": _GOOD_DSN, **overrides}


def _a_readable_file() -> str:
    """Any existing file: these checks only ask whether a path was configured."""
    return str(pathlib.Path(__file__).resolve())


class TestLoadSettings:
    def test_a_valid_environment_loads(self):
        settings = load_settings(_env(), use_env_file=False)

        assert settings.database.parts.database == "mock_hr"

    def test_a_pydantic_failure_becomes_a_domain_error(self):
        # Callers upstream catch one thing. config raises pydantic's error
        # because it imports no other layer; translating it is this layer's job.
        with pytest.raises(ConfigurationError) as caught:
            load_settings({"ERP_DATABASE_URL": ""}, use_env_file=False)

        assert "invalid configuration" in caught.value.message
        assert caught.value.__cause__ is not None

    def test_the_message_names_what_to_fix(self):
        with pytest.raises(ConfigurationError, match="read-only role"):
            load_settings(
                _env(ERP_DATABASE_URL="postgresql://hr_user:pw@h:5433/db"), use_env_file=False
            )


class TestStartupChecks:
    def test_a_sane_local_configuration_passes(self):
        settings = load_settings(_env(), use_env_file=False)

        assert run_startup_checks(settings) == ("logging", "database-role", "mock-credentials")

    def test_production_must_emit_queryable_logs(self):
        settings = load_settings(
            _env(APP_ENV="production", LOG_FORMAT="console"), use_env_file=False
        )

        with pytest.raises(ConfigurationError, match="LOG_FORMAT"):
            run_startup_checks(settings)

    def test_production_with_json_logs_passes(self):
        # Production must also point the identity fixtures somewhere real; the
        # packaged development copies are refused. See
        # tests/unit/core/test_mock_credentials_are_not_production.py.
        settings = load_settings(
            _env(
                APP_ENV="production",
                LOG_FORMAT="json",
                ERP_PRINCIPAL_FIXTURE_PATH=_a_readable_file(),
                ERP_ACCESS_TOKEN_FIXTURE_PATH=_a_readable_file(),
            ),
            use_env_file=False,
        )

        assert "logging" in run_startup_checks(settings)

    def test_the_checks_touch_no_network(self):
        # A configuration check that needs the database is useless exactly when
        # the database is the problem. This DSN points nowhere routable.
        settings = load_settings(
            {"ERP_DATABASE_URL": "postgresql://readonly:pw@203.0.113.1:9/none"},
            use_env_file=False,
        )

        assert run_startup_checks(settings)


class TestContainerConstruction:
    def test_the_object_graph_builds_without_a_database(self):
        # Building the graph must not be a network operation: `open()` is what
        # makes it live.
        settings = Settings.from_environment(_env(), use_env_file=False)
        container = Container(settings)

        assert container.data_source is not None

    def test_the_data_source_is_a_singleton_within_a_container(self):
        settings = Settings.from_environment(_env(), use_env_file=False)
        container = Container(settings)

        assert container.data_source is container.data_source

    def test_the_data_source_is_exposed_as_the_port(self):
        # Typed as the port so nothing downstream can quietly depend on psycopg.
        from erp_agent.domain.ports.data_source import ReadOnlyDataSource

        settings = Settings.from_environment(_env(), use_env_file=False)

        assert isinstance(Container(settings).data_source, ReadOnlyDataSource)
