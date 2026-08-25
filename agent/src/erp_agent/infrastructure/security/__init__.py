"""Resolving who is asking, before anything is read on their behalf."""

from __future__ import annotations

from erp_agent.infrastructure.security.fixture_access_tokens import (
    FixtureAccessTokens,
    default_token_fixture_path,
)
from erp_agent.infrastructure.security.mock_identity import (
    SUPPORTED_SCHEMA_VERSION,
    MockIdentityResolver,
    default_identity_fixture_path,
    packaged_role_catalogue,
)

__all__ = [
    "SUPPORTED_SCHEMA_VERSION",
    "FixtureAccessTokens",
    "MockIdentityResolver",
    "default_identity_fixture_path",
    "default_token_fixture_path",
    "packaged_role_catalogue",
]
