"""Tests for the token-bucket rate limiter."""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from stock_alert.ingestion.rate_limiter import TokenBucketRateLimiter


class TestTokenBucketRateLimiter:
    def test_rejects_non_positive_rate(self):
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=0)
        with pytest.raises(ValueError):
            TokenBucketRateLimiter(rate=-1)

    def test_try_acquire_consumes_token(self):
        limiter = TokenBucketRateLimiter(rate=10, burst=10)
        # Fresh bucket is full — should succeed.
        assert limiter.try_acquire() is True

    def test_try_acquire_fails_when_bucket_empty(self):
        limiter = TokenBucketRateLimiter(rate=1, burst=1)
        assert limiter.try_acquire() is True   # drains the one token
        assert limiter.try_acquire() is False  # bucket empty

    def test_bucket_refills_over_time(self):
        limiter = TokenBucketRateLimiter(rate=100, burst=1)
        assert limiter.try_acquire() is True  # drain
        time.sleep(0.02)                      # wait for refill (100/s → 0.01s per token)
        assert limiter.try_acquire() is True  # should have refilled

    @pytest.mark.asyncio
    async def test_acquire_waits_for_token(self):
        limiter = TokenBucketRateLimiter(rate=50, burst=1)
        limiter.try_acquire()  # drain
        start = time.monotonic()
        await limiter.acquire()  # should wait ~0.02 s
        elapsed = time.monotonic() - start
        assert elapsed >= 0.01  # waited at least a bit

    def test_burst_above_rate(self):
        limiter = TokenBucketRateLimiter(rate=1, burst=5)
        successes = sum(limiter.try_acquire() for _ in range(6))
        assert successes == 5  # burst capacity = 5
