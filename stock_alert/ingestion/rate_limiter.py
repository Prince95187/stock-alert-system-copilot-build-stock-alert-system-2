"""Token-bucket rate limiter for the incoming WebSocket tick stream.

Architecture note
-----------------
A *token bucket* is chosen over a leaky bucket because it natively handles
burst traffic — a momentary spike is absorbed by the accumulated token
reserve rather than immediately dropped.  This is important for markets
open/close prints where many tickers update simultaneously.

The implementation is ``asyncio``-safe (no threading locks needed) because
Python's event loop is single-threaded and coroutines yield cooperatively.
"""

from __future__ import annotations

import asyncio
import time


class TokenBucketRateLimiter:
    """Async token-bucket rate limiter.

    Parameters
    ----------
    rate:
        Maximum number of tokens (ticks) to allow **per second**.
    burst:
        Maximum token reserve.  Defaults to ``rate`` (no burst above steady
        state).  Set higher to absorb short spikes.
    """

    def __init__(self, rate: float, burst: float | None = None) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        self._rate = rate
        self._burst = burst if burst is not None else rate
        self._tokens: float = self._burst
        self._last_refill: float = time.monotonic()
        # asyncio.Lock serialises concurrent ``acquire`` calls so that two
        # coroutines cannot both observe the same token balance.
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def acquire(self) -> None:
        """Wait until a token is available, then consume one.

        This is a *cooperative* wait — it never blocks the event loop.
        """
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                # Calculate how long until the next token arrives and yield
                # the event loop for (slightly less than) that duration.
                deficit = 1.0 - self._tokens
                sleep_for = deficit / self._rate
                await asyncio.sleep(sleep_for)

    def try_acquire(self) -> bool:
        """Non-blocking attempt to consume one token.

        Returns
        -------
        bool
            ``True`` if a token was consumed, ``False`` if the bucket is empty.
        """
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Add tokens proportional to the elapsed time since the last call."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
