"""WebSocket ingestion client with automatic reconnection and rate limiting.

Architecture note — Reconnection strategy
------------------------------------------
The client uses *exponential back-off with full jitter* (as recommended by
the AWS Architecture Blog) rather than a fixed retry delay.  This avoids the
"thundering herd" problem where thousands of clients all reconnect at the
same instant after a server restart.

    delay = random(0, min(cap, base * 2^attempt))

Architecture note — Back-pressure
-----------------------------------
Parsed ticks are placed on an ``asyncio.Queue``.  If the downstream
processing pipeline falls behind, the queue fills up and ``put_nowait``
raises ``QueueFull``; the client then drops the tick and increments
``dropped_ticks`` so the operator can observe the back-pressure signal.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable, Coroutine
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosedError, WebSocketException

from stock_alert.ingestion.rate_limiter import TokenBucketRateLimiter
from stock_alert.rules.schema import TickData

logger = logging.getLogger(__name__)

# Reconnection parameters (seconds).
_BACKOFF_BASE = 0.5
_BACKOFF_CAP = 60.0

TickHandler = Callable[[TickData], Coroutine[Any, Any, None]]


class WebSocketIngestionClient:
    """Connects to a streaming JSON WebSocket feed and pushes ticks downstream.

    Parameters
    ----------
    uri:
        WebSocket URI of the upstream price feed.
    tick_queue:
        Downstream asyncio queue that receives :class:`TickData` objects.
    rate_limiter:
        Optional token-bucket limiter.  If omitted, no rate limiting is applied.
    max_queue_size:
        Maximum items allowed in *tick_queue* before ticks are dropped.
    """

    def __init__(
        self,
        uri: str,
        tick_queue: asyncio.Queue[TickData],
        rate_limiter: TokenBucketRateLimiter | None = None,
        max_queue_size: int = 10_000,
    ) -> None:
        self._uri = uri
        self._queue = tick_queue
        self._limiter = rate_limiter
        self._max_queue_size = max_queue_size
        self._running = False

        # Observable counters — expose as Prometheus gauges in production.
        self.received_ticks: int = 0
        self.dropped_ticks: int = 0
        self.parse_errors: int = 0
        self.reconnect_count: int = 0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Connect and process ticks indefinitely (until :meth:`stop` is called)."""
        self._running = True
        attempt = 0
        while self._running:
            try:
                logger.info("Connecting to %s (attempt %d)…", self._uri, attempt + 1)
                async with websockets.connect(self._uri) as ws:
                    attempt = 0  # Reset back-off on successful connection.
                    logger.info("Connected to %s", self._uri)
                    await self._consume(ws)
            except (ConnectionClosedError, WebSocketException, OSError) as exc:
                if not self._running:
                    break
                delay = _jittered_backoff(attempt)
                logger.warning(
                    "WebSocket error (%s). Reconnecting in %.1fs…", exc, delay
                )
                self.reconnect_count += 1
                attempt += 1
                await asyncio.sleep(delay)
            except asyncio.CancelledError:
                break
        logger.info("WebSocket ingestion client stopped.")

    async def stop(self) -> None:
        """Signal the run-loop to exit after the current iteration."""
        self._running = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _consume(self, ws: websockets.WebSocketClientProtocol) -> None:
        """Inner receive loop; raises on connection loss so the caller reconnects."""
        async for raw in ws:
            if not self._running:
                return

            # ── Rate limiting ────────────────────────────────────────────
            if self._limiter is not None:
                if not self._limiter.try_acquire():
                    # Non-blocking: drop the tick and move on rather than
                    # awaiting a token — this keeps ingestion latency minimal.
                    self.dropped_ticks += 1
                    continue

            # ── Parse ────────────────────────────────────────────────────
            tick = self._parse(raw)
            if tick is None:
                continue

            self.received_ticks += 1

            # ── Back-pressure guard ──────────────────────────────────────
            if self._queue.qsize() >= self._max_queue_size:
                self.dropped_ticks += 1
                logger.debug("Queue full — dropping tick for %s", tick.ticker)
                continue

            await self._queue.put(tick)

    def _parse(self, raw: str | bytes) -> TickData | None:
        """Deserialise one WebSocket message into a :class:`TickData`."""
        try:
            data = json.loads(raw)
            return TickData(
                ticker=data["ticker"],
                price=float(data["price"]),
                timestamp=float(data.get("timestamp", time.time())),
            )
        except (KeyError, ValueError, TypeError):
            self.parse_errors += 1
            logger.debug("Malformed tick dropped: %r", raw)
            return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _jittered_backoff(attempt: int) -> float:
    """Return a jittered exponential back-off delay (seconds)."""
    ceiling = min(_BACKOFF_CAP, _BACKOFF_BASE * (2 ** attempt))
    return random.uniform(0, ceiling)
