"""In-memory implementations of :class:`~stock_alert.storage.interfaces.StateStore`
and :class:`~stock_alert.storage.interfaces.AlertLogStore`.

Used for development, testing, and the demo web app.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Optional

from stock_alert.rules.schema import TickData, TriggeredAlert
from stock_alert.storage.interfaces import AlertLogStore, StateStore


class InMemoryStateStore(StateStore):
    """Stores latest prices and rolling price histories in plain Python dicts.

    The price history for each ticker is kept in a ``deque`` capped at
    *max_history_per_ticker* entries — this bounds memory even if :meth:`evict_old_history`
    is never called.
    """

    def __init__(self, max_history_per_ticker: int = 10_000) -> None:
        self._lock = asyncio.Lock()
        # {ticker: (price, timestamp)}
        self._latest: dict[str, tuple[float, float]] = {}
        # {ticker: deque[(tick, …)]}
        self._history: dict[str, deque[TickData]] = defaultdict(
            lambda: deque(maxlen=max_history_per_ticker)
        )

    async def set_latest_price(self, ticker: str, price: float, timestamp: float) -> None:
        async with self._lock:
            self._latest[ticker] = (price, timestamp)

    async def get_latest_price(self, ticker: str) -> Optional[tuple[float, float]]:
        return self._latest.get(ticker)

    async def append_price_history(self, tick: TickData) -> None:
        async with self._lock:
            self._history[tick.ticker].append(tick)

    async def get_price_history(
        self, ticker: str, since_timestamp: float
    ) -> Sequence[TickData]:
        return [t for t in self._history.get(ticker, []) if t.timestamp >= since_timestamp]

    async def evict_old_history(self, ticker: str, before_timestamp: float) -> None:
        async with self._lock:
            dq = self._history.get(ticker)
            if dq is None:
                return
            while dq and dq[0].timestamp < before_timestamp:
                dq.popleft()

    # Convenience: return all known tickers and their latest price.
    async def get_all_prices(self) -> dict[str, tuple[float, float]]:
        return dict(self._latest)


class InMemoryAlertLogStore(AlertLogStore):
    """Stores triggered alerts in memory, newest first."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._alerts: list[TriggeredAlert] = []
        self._ids: set[str] = set()

    async def log_alert(self, alert: TriggeredAlert) -> None:
        async with self._lock:
            self._alerts.insert(0, alert)  # Newest first.
            self._ids.add(alert.id)

    async def get_recent_alerts(
        self, limit: int = 100, ticker: Optional[str] = None
    ) -> Sequence[TriggeredAlert]:
        alerts = self._alerts
        if ticker:
            alerts = [a for a in alerts if a.ticker == ticker.upper()]
        return alerts[:limit]

    async def alert_exists(self, alert_id: str) -> bool:
        return alert_id in self._ids
