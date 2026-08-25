"""The fixture access-token adapter.

It stands in for a Platform table the agent is not permitted to read at all, so
the tests are mostly about it failing correctly: an unknown credential must not
become an anonymous identity, a duplicate token must not silently pick a winner,
and a rejected secret must not appear in the error that rejects it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from erp_agent.domain.errors import AuthenticationError, ConfigurationError
from erp_agent.infrastructure.security.fixture_access_tokens import (
    FixtureAccessTokens,
    default_token_fixture_path,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

_VALID = "a-token-long-enough-to-pass"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "access_tokens.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _fixture(tmp_path: Path, *, token: str = _VALID, identity: str = "emp-1") -> Path:
    return _write(
        tmp_path,
        f'schema_version: 1\ntokens:\n  - token: "{token}"\n    identity: "{identity}"\n',
    )


class TestAuthenticating:
    async def test_a_known_token_resolves_to_its_identity(self, tmp_path):
        tokens = FixtureAccessTokens.from_file(_fixture(tmp_path))

        assert await tokens.identify(_VALID) == "emp-1"

    async def test_surrounding_whitespace_is_tolerated(self, tmp_path):
        tokens = FixtureAccessTokens.from_file(_fixture(tmp_path))

        assert await tokens.identify(f"  {_VALID}  ") == "emp-1"

    async def test_an_unknown_token_raises(self, tmp_path):
        tokens = FixtureAccessTokens.from_file(_fixture(tmp_path))

        with pytest.raises(AuthenticationError):
            await tokens.identify("something-else-entirely")

    async def test_it_never_falls_back_to_an_anonymous_identity(self, tmp_path):
        # An authentication failure that returns *something* is an
        # authentication failure that succeeded.
        tokens = FixtureAccessTokens.from_file(_fixture(tmp_path))

        with pytest.raises(AuthenticationError):
            await tokens.identify("")

    async def test_a_prefix_of_a_valid_token_is_refused(self, tmp_path):
        tokens = FixtureAccessTokens.from_file(_fixture(tmp_path))

        with pytest.raises(AuthenticationError):
            await tokens.identify(_VALID[:-1])

    async def test_the_rejected_credential_is_not_in_the_error(self, tmp_path):
        tokens = FixtureAccessTokens.from_file(_fixture(tmp_path))
        secret = "a-secret-somebody-typed-wrong"

        with pytest.raises(AuthenticationError) as caught:
            await tokens.identify(secret)

        assert secret not in caught.value.message
        assert secret not in str(caught.value.context)

    def test_the_secret_is_not_held_in_memory(self, tmp_path):
        # Digests, not tokens. A heap dump or a debugger session should not
        # hand somebody the credentials.
        tokens = FixtureAccessTokens.from_file(_fixture(tmp_path))

        assert _VALID not in str(tokens.__dict__)


class TestValidation:
    def test_the_packaged_dev_fixture_is_valid(self):
        assert len(FixtureAccessTokens.from_file(default_token_fixture_path())) >= 1

    def test_a_missing_file_is_refused(self, tmp_path):
        with pytest.raises(ConfigurationError, match="could not be read"):
            FixtureAccessTokens.from_file(tmp_path / "nowhere.yaml")

    def test_an_unrecognised_schema_version_is_refused(self, tmp_path):
        path = _write(tmp_path, "schema_version: 9\ntokens: []\n")

        with pytest.raises(ConfigurationError, match="schema_version"):
            FixtureAccessTokens.from_file(path)

    def test_a_fixture_with_no_tokens_is_refused(self, tmp_path):
        # Nobody could authenticate, which is a deployment nobody meant.
        path = _write(tmp_path, "schema_version: 1\ntokens: []\n")

        with pytest.raises(ConfigurationError, match="defines no tokens"):
            FixtureAccessTokens.from_file(path)

    def test_a_repeated_token_is_refused(self, tmp_path):
        # Two identities behind one credential means an audit trail that cannot
        # say who acted.
        path = _write(
            tmp_path,
            f'schema_version: 1\ntokens:\n  - token: "{_VALID}"\n    identity: "a"\n'
            f'  - token: "{_VALID}"\n    identity: "b"\n',
        )

        with pytest.raises(ConfigurationError, match="repeats a token"):
            FixtureAccessTokens.from_file(path)

    def test_a_short_token_is_refused(self, tmp_path):
        path = _write(tmp_path, 'schema_version: 1\ntokens:\n  - token: "abc"\n    identity: "a"\n')

        with pytest.raises(ConfigurationError):
            FixtureAccessTokens.from_file(path)

    def test_an_unknown_field_is_refused(self, tmp_path):
        path = _write(
            tmp_path,
            f'schema_version: 1\ntokens:\n  - token: "{_VALID}"\n    identity: "a"\n'
            f'    scope: "organisation"\n',
        )

        with pytest.raises(ConfigurationError, match="scope"):
            FixtureAccessTokens.from_file(path)

    def test_a_validation_error_never_quotes_the_token(self, tmp_path):
        # A malformed `Authorization` value is still a credential someone typed.
        leaked = "short"
        path = _write(
            tmp_path, f'schema_version: 1\ntokens:\n  - token: "{leaked}"\n    identity: "a"\n'
        )

        with pytest.raises(ConfigurationError) as caught:
            FixtureAccessTokens.from_file(path)

        assert leaked not in caught.value.message


class TestTheDevFixtureMatchesTheMockRoster:
    def test_every_dev_token_names_an_identity_the_roster_has(self):
        # A token pointing at an identity nobody can resolve is a 401 that looks
        # like a bad credential -- the most confusing failure available, since
        # the token itself is fine.
        assert _token_identities() <= _roster_identities()

    def test_every_mock_user_has_a_credential(self):
        # The other direction. A rostered user nobody can authenticate as is a
        # user nobody will notice is broken.
        assert _roster_identities() <= _token_identities()


def _token_identities() -> set[str]:
    import yaml

    document = yaml.safe_load(default_token_fixture_path().read_text(encoding="utf-8"))
    return {row["identity"] for row in document["tokens"]}


def _roster_identities() -> set[str]:
    import yaml

    from erp_agent.infrastructure.security import default_identity_fixture_path

    document = yaml.safe_load(default_identity_fixture_path().read_text(encoding="utf-8"))
    return {row["user_id"] for row in document["users"]}
