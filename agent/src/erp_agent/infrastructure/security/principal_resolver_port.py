"""Port: turning an authenticated identity into a principal.

This is the seam Phase 9 implements against the real Platform tables --
``users`` joined to ``employees`` for the org placement, and ``user_roles``
joined through ``role_permissions`` for the grant. Until those tables exist in
``mock_hr`` (Plan.md section 8.5, gap S6), the only implementation is
fixture-backed.

Nothing above this interface may know which one it got. That is the point of the
seam: swapping a YAML file for a SQL join in Phase 9 must not be visible to the
agent, the tools, or the API.

``resolve`` is asynchronous even though the fixture implementation needs no
concurrency. The real implementation will query the database, and a port whose
signature has to change when the adapter changes is not a seam.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from erp_agent.domain.models.principal import Principal

__all__ = ["PrincipalResolverPort"]


class PrincipalResolverPort(ABC):
    """Resolves an authenticated identity to the access it was granted."""

    @abstractmethod
    async def resolve(self, identity: str) -> Principal:
        """Resolve an authenticated identity.

        Args:
            identity: The authenticated subject, established by the transport
                layer before this is called. Never taken from a request body,
                and never from model output.

        Returns:
            The principal, including the scope their roles grant.

        Raises:
            PrincipalNotFoundError: If the identity resolves to nothing. There
                is deliberately no fallback: an unknown identity has no scope,
                and the only safe reading of "no scope" is "no access". A
                default of ``SELF`` would turn an unknown caller into a valid
                one with a real employee's reach.
        """
