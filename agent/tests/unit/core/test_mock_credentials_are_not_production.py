"""Mock credentials must not be able to authorise a production deployment.

The roster and the tokens that reach it are committed to this repository. One of
them grants organisation scope. So the interesting question is not whether they
work -- it is whether a deployment can end up using them by *omission*, which is
how this kind of thing actually happens: nobody sets the override, and the
packaged default quietly applies.

Two refusals, deliberately independent:

*The composition root refuses.* It knows which adapter it is about to build.

*Start-up refuses again.* ``principal_resolver`` is a ``cached_property``, so
the first refusal only fires if something touches it. A process that served a
health check before its first question would have started successfully. The
check in :func:`run_startup_checks` runs unconditionally, so the failure is at
boot and not at the first authenticated request.

Both are tested, because a test that only covered one would keep passing while
the other was deleted.
"""

from __future__ import annotations

import pytest

from erp_agent.core.bootstrap import load_settings, run_startup_checks
from erp_agent.core.container import Container
from erp_agent.domain.errors import ConfigurationError

pytestmark = [pytest.mark.unit, pytest.mark.security]

_GOOD_DSN = "postgresql://mock_hr_readonly:readonly_password@localhost:5433/mock_hr"


def _settings(**overrides: str):
    return load_settings(
        {
            "ERP_DATABASE_URL": _GOOD_DSN,
            "LOG_FORMAT": "json",
            **overrides,
        },
        use_env_file=False,
    )


class TestStartUpRefusesThePackagedRoster:
    def test_production_with_no_override_fails_to_start(self):
        with pytest.raises(ConfigurationError, match="ERP_PRINCIPAL_FIXTURE_PATH"):
            run_startup_checks(_settings(APP_ENV="production"))

    def test_production_with_no_token_override_fails_to_start(self):
        # Pointing the roster elsewhere is not enough on its own: the
        # credentials that reach it are a separate file.
        with pytest.raises(ConfigurationError, match="ERP_ACCESS_TOKEN_FIXTURE_PATH"):
            run_startup_checks(
                _settings(
                    APP_ENV="production",
                    ERP_PRINCIPAL_FIXTURE_PATH=_packaged_roster(),
                )
            )

    def test_development_starts_with_the_packaged_files(self):
        assert "mock-credentials" in run_startup_checks(_settings(APP_ENV="development"))

    def test_the_check_is_reported_as_having_run(self):
        # A check nobody can see having run is a check that can be deleted
        # without anybody noticing.
        assert "mock-credentials" in run_startup_checks(_settings())


class TestTheContainerRefusesToo:
    def test_building_the_resolver_in_production_raises(self):
        container = Container(_settings(APP_ENV="production"))

        with pytest.raises(ConfigurationError, match="ERP_PRINCIPAL_FIXTURE_PATH"):
            _ = container.principal_resolver

    def test_building_the_token_adapter_in_production_raises(self):
        container = Container(_settings(APP_ENV="production"))

        with pytest.raises(ConfigurationError, match="ERP_ACCESS_TOKEN_FIXTURE_PATH"):
            _ = container.access_tokens

    def test_development_builds_both(self):
        container = Container(_settings(APP_ENV="development"))

        assert container.principal_resolver is not None
        assert container.access_tokens is not None


def _packaged_roster() -> str:
    from erp_agent.infrastructure.security.mock_identity import (
        default_identity_fixture_path,
    )

    return str(default_identity_fixture_path())
