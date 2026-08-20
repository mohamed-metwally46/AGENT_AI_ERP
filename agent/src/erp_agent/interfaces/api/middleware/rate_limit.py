"""Per-principal rate limiting.

Plan.md R6 is the reason this exists, and it shapes the design: the risk is
*data exfiltration by aggregation* -- many individually-allowed questions
reconstructing a restricted whole. Every one of those questions is authorised.
No scope predicate stops them, because none of them is out of scope. What stops
them is a budget.

That is why the bucket is keyed on the **principal**, not the client address.
One authenticated caller behind one address is exactly the case R6 describes,
and an IP-keyed limiter would let them proceed at full speed while
rate-limiting a shared office NAT for no reason.

A token bucket rather than a fixed window: a fixed window lets a caller spend
the whole allowance in its last second and the whole next allowance in the
first, which is twice the intended burst at the boundary. The bucket refills
continuously, so the burst is whatever ``burst`` says and nothing larger.

In-process and unsynchronised across replicas, which is a real limitation and
stated rather than implied: two replicas mean twice the limit. A shared counter
is a deployment decision -- which backend, whose availability the request path
now depends on -- and inventing one here would be inventing a dependency nobody
reviewed. The interface is small enough to put behind a port when that decision
is made.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["RateLimitDecision", "TokenBucketLimiter"]

#: Principals tracked at once. Bounded because this is memory, and evicting the
#: least recently seen is safe: a caller who returns after eviction gets a full
#: bucket, which is the same thing a caller who waited would have got.
_MAX_TRACKED = 10_000


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Whether a request may proceed.

    Attributes:
        allowed: Whether to serve it.
        retry_after_s: Seconds until one request would be permitted. Zero when
            allowed. Rounded up, because advertising a delay that is a
            fraction too short invites an immediate second refusal.
        remaining: Whole requests still available right now.
    """

    allowed: bool
    retry_after_s: int = 0
    remaining: int = 0


class TokenBucketLimiter:
    """A per-key token bucket, refilling continuously."""

    def __init__(
        self,
        *,
        per_minute: int,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the limiter.

        Args:
            per_minute: Sustained rate, in requests per minute.
            burst: Bucket capacity -- how many may arrive at once.
            clock: Monotonic time source. Injected so tests do not sleep, and
                monotonic so a clock adjustment cannot hand out free requests.

        Raises:
            ValueError: If either bound is not positive. A limiter with a
                capacity of zero refuses everything; one with no rate is not a
                limiter.
        """
        if per_minute < 1:
            raise ValueError("per_minute must be >= 1")
        if burst < 1:
            raise ValueError("burst must be >= 1")

        self._rate_per_second = per_minute / 60.0
        self._capacity = float(burst)
        self._clock = clock
        self._buckets: OrderedDict[str, tuple[float, float]] = OrderedDict()

    def check(self, key: str) -> RateLimitDecision:
        """Take one token for ``key``, if there is one.

        Args:
            key: The principal being limited. Never a request field -- the
                caller supplies the question, not the bucket their question is
                counted against.

        Returns:
            Whether to serve the request, and when to try again if not.
        """
        now = self._clock()
        tokens, last_seen = self._buckets.get(key, (self._capacity, now))

        tokens = min(self._capacity, tokens + (now - last_seen) * self._rate_per_second)

        if tokens < 1.0:
            # Store the refilled level even on refusal, so a caller who keeps
            # hammering does not reset their own clock by trying again.
            self._remember(key, tokens, now)
            deficit = 1.0 - tokens
            return RateLimitDecision(
                allowed=False,
                retry_after_s=max(1, int(deficit / self._rate_per_second) + 1),
                remaining=0,
            )

        tokens -= 1.0
        self._remember(key, tokens, now)
        return RateLimitDecision(allowed=True, remaining=int(tokens))

    def _remember(self, key: str, tokens: float, now: float) -> None:
        """Store a bucket, evicting the least recently seen past the ceiling."""
        self._buckets[key] = (tokens, now)
        self._buckets.move_to_end(key)
        while len(self._buckets) > _MAX_TRACKED:
            self._buckets.popitem(last=False)
