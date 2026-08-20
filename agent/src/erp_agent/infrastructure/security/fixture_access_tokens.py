"""An access-token adapter backed by a YAML fixture.

Stands in for the Platform's ``personal_access_tokens``, which the agent is not
permitted to read at all (Plan.md section 4: CRITICAL, ``NO_DIRECT_AI_ACCESS``).
Phase 9 replaces this with a call to the Platform; nothing above the port
changes.

Three properties make it safe to run, and one makes it unsafe to deploy.

*Tokens are compared in constant time.* A dictionary lookup on a secret leaks
its prefix through timing. The map is keyed by a digest and the comparison is
:func:`hmac.compare_digest`, so a wrong token costs the same as a right one.

*The file is validated once, at construction.* An unrecognised schema version, a
duplicate token or an unknown field raises before the process serves anything.

*There is no fallback.* An unknown credential raises. It never becomes an
anonymous identity, and it never becomes a valid one with a narrowed scope --
which would turn an authentication failure into a quietly successful answer.

*And it must not run in production.* These are static, non-expiring credentials
in a file in the repository. :meth:`FixtureAccessTokens.from_file` does not
enforce that -- the composition root does, because only it knows the
environment -- but nothing here should be read as fit for a real deployment.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from erp_agent.domain.errors import AuthenticationError, ConfigurationError
from erp_agent.domain.ports.access_tokens import AccessTokenPort

__all__ = ["SUPPORTED_SCHEMA_VERSION", "FixtureAccessTokens", "default_token_fixture_path"]

#: The only fixture layout this loader understands.
SUPPORTED_SCHEMA_VERSION = 1

_PACKAGED_FIXTURE = Path(__file__).parent / "fixtures" / "access_tokens.dev.yaml"

#: Shortest credential the fixture will accept. Not a security control -- the
#: file is not a security control -- but a token short enough to guess by hand
#: is a mistake worth refusing at load time.
_MIN_TOKEN_LENGTH = 16


def default_token_fixture_path() -> Path:
    """The dev token fixture shipped inside the package."""
    return _PACKAGED_FIXTURE


def _digest(credential: str) -> str:
    """Hash a credential so the in-memory map never holds the secret itself."""
    return hashlib.sha256(credential.encode("utf-8")).hexdigest()


class _TokenRow(BaseModel):
    """One fixture entry. ``extra='forbid'`` so a typo fails loudly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(min_length=_MIN_TOKEN_LENGTH)
    identity: str = Field(min_length=1)
    label: str | None = None


class FixtureAccessTokens(AccessTokenPort):
    """Authenticates bearer credentials against a validated YAML fixture."""

    def __init__(self, identities: dict[str, str], *, source: Path | None = None) -> None:
        """Hold an already-validated digest-to-identity map.

        Prefer :meth:`from_file`. This constructor exists so tests can build one
        without a file.

        Args:
            identities: Token digest to identity.
            source: Where they came from, for diagnostics.
        """
        self._identities = dict(identities)
        self._source = source

    def __len__(self) -> int:
        """How many credentials are configured."""
        return len(self._identities)

    @classmethod
    def from_file(cls, path: Path) -> FixtureAccessTokens:
        """Load and validate a token fixture.

        Args:
            path: The YAML fixture to read.

        Returns:
            An authenticator over the file's contents.

        Raises:
            ConfigurationError: If the file is missing, unparseable, declares an
                unrecognised ``schema_version``, contains an invalid row, or
                repeats a token. A repeated token is refused rather than
                last-one-wins: two identities behind one credential means an
                audit trail that cannot say who acted.
        """
        document = _read(path)

        version = document.get("schema_version")
        if version != SUPPORTED_SCHEMA_VERSION:
            raise ConfigurationError(
                f"access token fixture {path} declares schema_version {version!r}; "
                f"this build understands only {SUPPORTED_SCHEMA_VERSION}",
                context={"path": str(path)},
            )

        entries = document.get("tokens")
        if not isinstance(entries, list):
            raise ConfigurationError(
                f"access token fixture {path} must contain a 'tokens' list",
                context={"path": str(path)},
            )

        identities: dict[str, str] = {}
        for index, entry in enumerate(entries):
            row = _parse_row(entry, index=index, path=path)
            digest = _digest(row.token)
            if digest in identities:
                raise ConfigurationError(
                    f"access token fixture {path} entry {index} repeats a token "
                    f"already used by another identity",
                    context={"path": str(path), "index": index},
                )
            identities[digest] = row.identity

        if not identities:
            raise ConfigurationError(
                f"access token fixture {path} defines no tokens, so nobody could authenticate",
                context={"path": str(path)},
            )

        return cls(identities, source=path)

    async def identify(self, credential: str) -> str:
        """Authenticate a credential.

        Args:
            credential: The presented bearer token.

        Returns:
            The identity it belongs to.

        Raises:
            AuthenticationError: If it matches nothing. The credential is never
                echoed, not even truncated: a rejected secret written to a log
                is a secret in the log.
        """
        presented = _digest(credential.strip())

        # Compared against every entry rather than looked up, and with
        # `compare_digest` rather than `==`, so the time taken does not depend
        # on how much of a wrong token was right.
        matched: str | None = None
        for digest, identity in self._identities.items():
            if hmac.compare_digest(digest, presented):
                matched = identity

        if matched is None:
            raise AuthenticationError("the presented credential is not valid")
        return matched


def _read(path: Path) -> dict[str, Any]:
    """Read and parse the fixture document."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigurationError(
            f"access token fixture {path} could not be read: {exc.strerror}",
            context={"path": str(path)},
        ) from exc

    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"access token fixture {path} is not valid YAML", context={"path": str(path)}
        ) from exc

    if not isinstance(loaded, dict):
        raise ConfigurationError(
            f"access token fixture {path} must be a mapping at the top level",
            context={"path": str(path)},
        )
    return loaded


def _parse_row(entry: object, *, index: int, path: Path) -> _TokenRow:
    """Validate one entry, without ever putting the token in an error message."""
    if not isinstance(entry, dict):
        raise ConfigurationError(
            f"access token fixture {path} entry {index} is not a mapping",
            context={"path": str(path), "index": index},
        )

    try:
        return _TokenRow.model_validate(entry)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'row'}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigurationError(
            f"access token fixture {path} entry {index} is invalid: {problems}",
            context={"path": str(path), "index": index},
        ) from exc
