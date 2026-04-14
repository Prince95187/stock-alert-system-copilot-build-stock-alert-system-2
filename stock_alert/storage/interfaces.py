"""Abstract storage interfaces.

All concrete backends (PostgreSQL, Redis, InfluxDB, …) must implement these
ABCs.  In-memory implementations live in ``in_memory_rules.py`` and
``in_memory_state.py`` and are used for development / testing.

Design principles
-----------------
*  Every method is ``async`` so implementations can use any async driver
   (asyncpg, aioredis, …) without changing the call-sites.
*  The interfaces are intentionally narrow — only the operations the system
   actually needs are exposed, making it easy to swap backends.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Optional

from stock_alert.rules.schema import AlertRule, TickData, TriggeredAlert


# ---------------------------------------------------------------------------
# Rule Store  (backed by a relational DB in production, e.g. PostgreSQL)
# ---------------------------------------------------------------------------

class RuleStore(ABC):
    """Persistent storage for user-defined :class:`~stock_alert.rules.schema.AlertRule` objects.

    Production implementation hint
    --------------------------------
    Use a SQL table with an index on ``ticker`` so that
    :meth:`get_rules_for_ticker` is an O(log n) index scan rather than a full
    table scan.
    """

    @abstractmethod
    async def add_rule(self, rule: AlertRule) -> AlertRule:
        """Persist *rule* and return it (potentially with a server-assigned id)."""

    @abstractmethod
    async def get_rule(self, rule_id: str) -> Optional[AlertRule]:
        """Return the rule with *rule_id*, or ``None`` if not found."""

    @abstractmethod
    async def list_rules(self) -> Sequence[AlertRule]:
        """Return all stored rules."""

    @abstractmethod
    async def get_rules_for_ticker(self, ticker: str) -> Sequence[AlertRule]:
        """Return all *enabled* rules that reference *ticker*.

        This is the hot-path query — it must be O(log n) or better.
        """

    @abstractmethod
    async def delete_rule(self, rule_id: str) -> bool:
        """Delete *rule_id*.  Return ``True`` if it existed, ``False`` otherwise."""

    @abstractmethod
    async def update_rule(self, rule: AlertRule) -> Optional[AlertRule]:
        """Overwrite the rule matching ``rule.id``.  Return ``None`` if not found."""


# ---------------------------------------------------------------------------
# State Store  (backed by Redis in production)
# ---------------------------------------------------------------------------

class StateStore(ABC):
    """Fast key-value store for ephemeral real-time state.

    Production implementation hint
    --------------------------------
    Use Redis.  The latest price for each ticker fits in a Redis hash
    (``HSET prices AAPL 175.50``).  Price history for percentage-move rules
    can be stored in a Redis Sorted Set keyed by ``ticker:history`` where the
    score is the Unix timestamp — sliding-window queries become a single
    ``ZRANGEBYSCORE`` call.
    """

    @abstractmethod
    async def set_latest_price(self, ticker: str, price: float, timestamp: float) -> None:
        """Store the most recent price for *ticker*."""

    @abstractmethod
    async def get_latest_price(self, ticker: str) -> Optional[tuple[float, float]]:
        """Return ``(price, timestamp)`` for *ticker*, or ``None``."""

    @abstractmethod
    async def append_price_history(self, tick: TickData) -> None:
        """Append *tick* to the rolling price history for its ticker."""

    @abstractmethod
    async def get_price_history(
        self, ticker: str, since_timestamp: float
    ) -> Sequence[TickData]:
        """Return all ticks for *ticker* whose timestamp >= *since_timestamp*."""

    @abstractmethod
    async def evict_old_history(self, ticker: str, before_timestamp: float) -> None:
        """Remove ticks older than *before_timestamp* (TTL-style cleanup)."""


# ---------------------------------------------------------------------------
# Alert Log Store  (backed by a time-series DB in production, e.g. TimescaleDB)
# ---------------------------------------------------------------------------

class AlertLogStore(ABC):
    """Append-only log of every :class:`~stock_alert.rules.schema.TriggeredAlert`.

    Production implementation hint
    --------------------------------
    Use TimescaleDB (PostgreSQL extension) or InfluxDB.  Partition the table by
    ``fired_at`` so recent-alerts queries touch only the latest partitions.
    """

    @abstractmethod
    async def log_alert(self, alert: TriggeredAlert) -> None:
        """Append *alert* to the log."""

    @abstractmethod
    async def get_recent_alerts(
        self, limit: int = 100, ticker: Optional[str] = None
    ) -> Sequence[TriggeredAlert]:
        """Return the most recent alerts, optionally filtered by *ticker*."""

    @abstractmethod
    async def alert_exists(self, alert_id: str) -> bool:
        """Return ``True`` if an alert with *alert_id* was already logged.

        Used by the notification dispatcher to implement idempotency.
        """
