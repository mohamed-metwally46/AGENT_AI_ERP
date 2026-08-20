"""Authentication at the HTTP boundary.

A thin layer on purpose. It reads one header, hands the credential to the
application's :class:`~erp_agent.application.security.Authenticate`, and puts
the result on the request. It resolves nothing itself: §5.2 gives the API layer
transport and auth *plumbing*, and a middleware that computed scope would be a
middleware that could compute it wrongly.

What it does own is the header contract, and one rule inside it:

**The credential comes from ``Authorization``, and from nowhere else.** Not a
query parameter, not a cookie, not a JSON field. A credential in a query string
ends up in access logs and browser history; a credential accepted from a body
means the same request can carry both an identity and an instruction about what
that identity may see, which is the arrangement Plan.md section 9 exists to
prevent.

Failures are deliberately uniform. A missing header, a malformed one, an unknown
token and a token whose identity has no principal all produce the same 401 with
the same body. Distinguishing them tells a caller probing the endpoint which
tokens exist and which identities are real.
"""

from __future__ import annotations

from dataclasses import dataclass

from erp_agent.application.security import Authenticate, AuthenticatedCaller
from erp_agent.domain.errors import AuthenticationError, AuthorizationError

__all__ = ["BearerCredentials", "authenticate_header", "extract_bearer"]

_SCHEME = "bearer"

#: What every authentication failure says. One message for every cause: a
#: caller who can tell "no such token" from "no such principal" can enumerate.
_REFUSAL = "authentication failed"


@dataclass(frozen=True, slots=True)
class BearerCredentials:
    """A parsed ``Authorization: Bearer <token>`` header.

    Attributes:
        token: The credential, with surrounding whitespace removed.
    """

    token: str


def extract_bearer(header: str | None) -> BearerCredentials:
    """Parse an ``Authorization`` header.

    Args:
        header: The raw header value, or ``None`` when absent.

    Returns:
        The parsed credential.

    Raises:
        AuthenticationError: If the header is absent, is not a bearer scheme, or
            carries no token. The error never quotes the header: a malformed
            ``Authorization`` value is still a credential someone typed.
    """
    if header is None:
        raise AuthenticationError(_REFUSAL)

    scheme, _, token = header.partition(" ")
    if scheme.strip().lower() != _SCHEME:
        raise AuthenticationError(_REFUSAL)

    stripped = token.strip()
    if not stripped:
        raise AuthenticationError(_REFUSAL)

    return BearerCredentials(token=stripped)


async def authenticate_header(
    header: str | None, *, authenticate: Authenticate
) -> AuthenticatedCaller:
    """Turn an ``Authorization`` header into a resolved caller.

    Args:
        header: The raw header value.
        authenticate: The application-layer chain that does the real work.

    Returns:
        The authenticated caller, carrying the security context every layer
        below will be given.

    Raises:
        AuthenticationError: For any failure at any step, always with the same
            message. An identity that authenticates but resolves to no principal
            is folded in here rather than surfacing as an authorization failure,
            because the difference is only interesting to someone mapping which
            identities exist.
    """
    credentials = extract_bearer(header)

    try:
        return await authenticate(credentials.token)
    except (AuthenticationError, AuthorizationError) as exc:
        # Both, and re-raised rather than re-thrown, because the *messages*
        # differ even when the statuses do not: the token adapter says "the
        # presented credential is not valid" and this module says
        # "authentication failed", and a caller who sees which is which can
        # tell a malformed header from an unknown token from an unknown
        # identity. `PrincipalNotFoundError` is an AuthorizationError by type --
        # correctly, since an unplaceable identity has no scope -- and at this
        # boundary it has to look like everything else.
        raise AuthenticationError(_REFUSAL) from exc
