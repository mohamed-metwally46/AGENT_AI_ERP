"""Credential → identity → principal → security context.

Four steps, three of them refusals. This is the only path by which a
:class:`SecurityContext` comes into existence for an HTTP request, and it lives
in the application layer rather than in the API because §5.2 is explicit that
the API layer holds transport and nothing else. A middleware that resolved
scope would be a middleware that could get scope wrong.

The chain matters more than any one link:

``credential`` → :class:`~erp_agent.domain.ports.access_tokens.AccessTokenPort`
    Who is this? A token is opaque; nothing here reads claims out of it. A token
    whose contents are trusted is a token whose holder chooses their own scope.

``identity`` → :class:`~erp_agent.domain.ports.principal_resolver.PrincipalResolverPort`
    What were they granted? From the identity store, never from the request.

``principal`` → :meth:`~erp_agent.domain.models.principal.Principal.to_security_context`
    What can they actually reach? The grant plus the reporting tree, computed
    server-side.

Nothing in the request body participates in any of these steps. There is
deliberately no parameter on :meth:`Authenticate.__call__` through which one
could -- that is Plan.md section 11's Phase 7 acceptance criterion 2, enforced
by the signature rather than by a check somebody has to remember to write.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from erp_agent.domain.models.principal import Principal
from erp_agent.domain.models.security_context import SecurityContext
from erp_agent.domain.ports.access_tokens import AccessTokenPort
from erp_agent.domain.ports.principal_resolver import PrincipalResolverPort

__all__ = ["Authenticate", "AuthenticatedCaller"]

#: Resolves the employees reporting to a principal, transitively. Injected
#: because it is a database read, and because ``TEAM`` scope is the one rung of
#: the ladder that cannot be answered from the grant alone.
ManagerOf = Callable[[Principal], Awaitable[frozenset[str]]]


class AuthenticatedCaller:
    """Who is asking, and what they may read.

    Attributes:
        identity: The authenticated subject.
        principal: The grant, as the identity store states it.
        context: The reach that grant computes to. The only value the agent,
            the tools and the retrieval filter ever see.
    """

    __slots__ = ("context", "identity", "principal")

    def __init__(
        self,
        *,
        identity: str,
        principal: Principal,
        context: SecurityContext,
    ) -> None:
        """Hold the resolved caller."""
        self.identity = identity
        self.principal = principal
        self.context = context


class Authenticate:
    """Turns a bearer credential into a caller, or refuses."""

    def __init__(
        self,
        *,
        tokens: AccessTokenPort,
        principals: PrincipalResolverPort,
        manager_of: ManagerOf | None = None,
    ) -> None:
        """Wire the chain.

        Args:
            tokens: Authenticates the credential.
            principals: Resolves the identity to its grant.
            manager_of: Resolves the reporting tree for ``TEAM`` scope. ``None``
                means nobody reports to anybody, which narrows ``TEAM`` to the
                principal themselves -- the safe direction to be wrong in.
        """
        self._tokens = tokens
        self._principals = principals
        self._manager_of = manager_of

    async def __call__(self, credential: str) -> AuthenticatedCaller:
        """Authenticate and resolve.

        Args:
            credential: The presented bearer token. The *only* argument, on
                purpose: there is no second parameter a request body could
                reach, so no caller can contribute to their own scope.

        Returns:
            The authenticated caller.

        Raises:
            AuthenticationError: If the credential is not valid.
            PrincipalNotFoundError: If it authenticates to an identity the
                identity store does not know. Deliberately not a fallback to
                ``SELF``: an identity the system cannot place has no scope, and
                the only safe reading of "no scope" is "no access".
        """
        identity = await self._tokens.identify(credential)
        principal = await self._principals.resolve(identity)

        reports: frozenset[str] = frozenset()
        if self._manager_of is not None:
            reports = await self._manager_of(principal)

        return AuthenticatedCaller(
            identity=identity,
            principal=principal,
            context=principal.to_security_context(manager_of=reports),
        )
