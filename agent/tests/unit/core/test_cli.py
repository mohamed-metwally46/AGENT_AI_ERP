"""The diagnostics command reports without leaking."""

from __future__ import annotations

import io

import pytest

from erp_agent.__main__ import main

pytestmark = pytest.mark.unit

_GOOD_DSN = "postgresql://mock_hr_readonly:readonly_password@localhost:5433/mock_hr"


def _run(*argv: str, **env: str) -> tuple[int, str]:
    """Run the CLI with an explicit environment and capture its output."""
    stream = io.StringIO()
    code = main(list(argv), env={"ERP_DATABASE_URL": _GOOD_DSN, **env}, stream=stream)
    return code, stream.getvalue()


class TestUsage:
    @pytest.mark.parametrize("flag", ["--help", "-h"])
    def test_help_is_available(self, flag):
        code, output = _run(flag)

        assert code == 0
        assert "--check" in output

    def test_unrecognised_arguments_fail_loudly(self):
        code, output = _run("--wat")

        assert code == 1
        assert "unrecognised arguments" in output


class TestListSettings:
    def test_every_variable_is_listed(self):
        code, output = _run("--list-settings")

        assert code == 0
        assert "ERP_DATABASE_URL" in output

    def test_it_works_even_when_the_configuration_is_broken(self):
        # This is most useful precisely when nothing else runs.
        code, output = _run("--list-settings", ERP_DATABASE_URL="")

        assert code == 0
        assert "ERP_DATABASE_URL" in output


class TestReport:
    def test_a_valid_configuration_is_reported(self):
        code, output = _run()

        assert code == 0
        assert "mock_hr_readonly" in output
        assert "startup checks" in output

    def test_the_password_is_never_printed(self):
        # Holding the DSN as a SecretStr is defeated by one command that
        # renders it.
        _, output = _run()

        assert "readonly_password" not in output

    def test_an_invalid_configuration_exits_non_zero(self):
        code, output = _run(ERP_DATABASE_URL="postgresql://hr_user:pw@localhost:5433/mock_hr")

        assert code == 1
        assert "configuration error" in output
        assert "read-only role" in output

    def test_the_default_path_touches_no_network(self):
        # Points nowhere routable; must still report configuration successfully.
        code, _ = _run(ERP_DATABASE_URL="postgresql://readonly:pw@203.0.113.1:9/none")

        assert code == 0
