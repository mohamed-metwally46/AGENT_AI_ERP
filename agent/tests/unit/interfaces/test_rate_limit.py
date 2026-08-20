"""The token bucket, on an injected clock.

Rate limiting is one of the few things that is genuinely hard to test honestly:
the obvious test sleeps, which makes the suite slow and flaky at the same time.
The clock is injected instead, so these assert on the *arithmetic* -- refill
rate, burst ceiling, and the boundary a fixed window gets wrong.
"""

from __future__ import annotations

import pytest

from erp_agent.interfaces.api.middleware.rate_limit import TokenBucketLimiter

pytestmark = pytest.mark.unit


class _Clock:
    """A monotonic clock a test can advance."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class TestTheBurst:
    def test_a_fresh_caller_may_spend_the_whole_burst(self):
        limiter = TokenBucketLimiter(per_minute=60, burst=3, clock=_Clock())

        assert [limiter.check("a").allowed for _ in range(3)] == [True, True, True]

    def test_the_next_request_is_refused(self):
        limiter = TokenBucketLimiter(per_minute=60, burst=3, clock=_Clock())
        for _ in range(3):
            limiter.check("a")

        assert limiter.check("a").allowed is False

    def test_remaining_counts_down(self):
        limiter = TokenBucketLimiter(per_minute=60, burst=3, clock=_Clock())

        assert [limiter.check("a").remaining for _ in range(3)] == [2, 1, 0]


class TestTheRefill:
    def test_waiting_earns_a_request_back(self):
        clock = _Clock()
        limiter = TokenBucketLimiter(per_minute=60, burst=1, clock=clock)
        limiter.check("a")
        assert limiter.check("a").allowed is False

        clock.advance(1.0)  # 60/minute is one per second

        assert limiter.check("a").allowed is True

    def test_the_bucket_does_not_fill_past_the_burst(self):
        # An hour away does not buy an hour's worth of requests at once.
        clock = _Clock()
        limiter = TokenBucketLimiter(per_minute=60, burst=2, clock=clock)
        clock.advance(3_600.0)

        allowed = [limiter.check("a").allowed for _ in range(4)]

        assert allowed == [True, True, False, False]

    def test_the_boundary_a_fixed_window_gets_wrong(self):
        # A fixed window lets a caller spend the whole allowance in its last
        # instant and the whole next allowance in the first, which is twice the
        # intended burst. A bucket refills continuously, so it cannot.
        clock = _Clock()
        limiter = TokenBucketLimiter(per_minute=60, burst=5, clock=clock)
        for _ in range(5):
            limiter.check("a")

        clock.advance(1.0)
        allowed = [limiter.check("a").allowed for _ in range(2)]

        assert allowed == [True, False]

    def test_retrying_does_not_reset_the_wait(self):
        # A refused request must still record the refilled level, or a caller
        # who keeps hammering restarts their own clock each time.
        clock = _Clock()
        limiter = TokenBucketLimiter(per_minute=60, burst=1, clock=clock)
        limiter.check("a")

        clock.advance(0.5)
        assert limiter.check("a").allowed is False
        clock.advance(0.5)

        assert limiter.check("a").allowed is True


class TestTheKey:
    def test_buckets_are_independent(self):
        limiter = TokenBucketLimiter(per_minute=60, burst=1, clock=_Clock())
        limiter.check("a")

        assert limiter.check("a").allowed is False
        assert limiter.check("b").allowed is True

    def test_a_refusal_advertises_a_positive_wait(self):
        limiter = TokenBucketLimiter(per_minute=60, burst=1, clock=_Clock())
        limiter.check("a")

        decision = limiter.check("a")

        assert decision.allowed is False
        assert decision.retry_after_s >= 1
        assert decision.remaining == 0


class TestConstruction:
    @pytest.mark.parametrize(("per_minute", "burst"), [(0, 1), (1, 0), (-1, 1), (1, -1)])
    def test_a_limiter_that_could_not_limit_is_refused(self, per_minute, burst):
        with pytest.raises(ValueError):
            TokenBucketLimiter(per_minute=per_minute, burst=burst)
