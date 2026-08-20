"""Port: turning a bearer credential into an authenticated identity.

The step *before* :class:`~erp_agent.domain.ports.principal_resolver.PrincipalResolverPort`,
and deliberately a separate one. Authentication answers "who is this?"; the
resolver answers "what may they read?". Collapsing them would put the scope
ladder behind whatever the transport happened to send, which is exactly the
arrangement Plan.md section 9 forbids.

**This is not where real tokens will live.** The ERP holds them in
``_core.personal_access_tokens``, classified CRITICAL and
``NO_DIRECT_AI_ACCESS`` (Plan.md section 4): credential material the agent is
not permitted to read at all. So the real implementation of this port is a call
to the Platform, in Phase 9 -- not a query. The fixture adapter that exists
today stands in for that call the way the principal fixture stands in for the
``users`` join, and like it, it must never be the production answer.

``identify`` is asynchronous for the same reason ``resolve`` is: the real
implementation is a network call, and a port whose signature changes when the
adapter changes is not a seam.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

__all__ = ["AccessTokenPort"]


class AccessTokenPort(ABC):
    """Authenticates a bearer credential."""

    @abstractmethod
    async def identify(self, credential: str) -> str:
        """Authenticate a credential and return the identity it belongs to.

        Args:
            credential: The bearer token, as presented by the transport. Opaque
                here: nothing in this system parses a token for claims, because
                a token whose contents are trusted is a token whose holder
                chooses their own scope.

        Returns:
            The authenticated identity, to be resolved into a principal. Never
            a scope, a department or a role -- those come from the identity
            store, not from the credential.

        Raises:
            AuthenticationError: If the credential is unknown, malformed or
                expired. One error for all three: telling a caller *which* of
                those it was tells an attacker which tokens exist.
        """
