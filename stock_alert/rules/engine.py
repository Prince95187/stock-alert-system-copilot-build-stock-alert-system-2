"""Rule Engine — the heart of the alert system.

Architecture: sharding + O(1) indexed lookup
=============================================

Brute-force evaluation (loop over every rule for every tick) is O(R) per tick
where R is the number of rules.  With 50 000 rules and 10 000 ticks/second
that's 500 M comparisons per second — clearly unacceptable.

This implementation uses two complementary techniques:

1. **Ticker index**
   ``_RuleShard._index`` is a ``{ticker: [AlertRule, …]}`` dict so that a
   tick for "AAPL" only evaluates rules that reference AAPL — typically a
   handful out of 50 000.  Lookup is O(1) (hash map).

2. **Shard workers** (hot-key mitigation)
   Tickers are hashed to one of N independent ``asyncio`` worker coroutines.
   Because each shard has its own dict and lock, a burst of NVDA ticks cannot
   block evaluation of TSLA or AAPL rules running in a different shard.

   Shard assignment:  ``shard_id = hash(ticker) % shard_count``

   In production, shard workers could be mapped 1-to-1 with OS threads (via
   ``loop.run_in_executor``) or separate processes to achieve true parallelism
   for CPU-bound workloads.

3. **Sliding-window history** (percentage-move rules)
   Each shard keeps a ``{ticker: deque[TickData]}`` of recent prices.
   On every tick we append the new price and scan backward until we find the
   oldest price still within the configured window.  The deque's O(1) append
   and popleft keep this efficient.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Sequence

from stock_alert.notifications.dispatcher import NotificationDispatcher
from stock_alert.rules.schema import (
    AlertRule,
    Condition,
    RuleType,
    TickData,
    TriggeredAlert,
)
from stock_alert.storage.interfaces import RuleStore, StateStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal per-shard state
# ---------------------------------------------------------------------------

class _RuleShard:
    """State for a single shard: rule index + per-ticker price history."""

    def __init__(self, window_seconds: int) -> None:
        self._window = window_seconds
        # O(1) ticker → rules lookup.
        self._index: dict[str, list[AlertRule]] = defaultdict(list)
        # Per-ticker sliding window of recent ticks.
        self._history: dict[str, deque[TickData]] = defaultdict(deque)
        # Cooldown: track the last time each rule fired to prevent re-alerts
        # while the price stays on the wrong side.  {rule_id: last_fired_ts}
        self._last_fired: dict[str, float] = {}

    def add_rule(self, rule: AlertRule) -> None:
        self._index[rule.ticker].append(rule)

    def remove_rule(self, rule: AlertRule) -> None:
        bucket = self._index.get(rule.ticker, [])
        try:
            bucket.remove(rule)
        except ValueError:
            pass
        self._last_fired.pop(rule.id, None)

    def evaluate(
        self, tick: TickData, cooldown_seconds: float
    ) -> list[TriggeredAlert]:
        """Evaluate all rules for *tick.ticker* and return any that fired."""
        rules = self._index.get(tick.ticker, [])
        if not rules:
            return []

        # Update sliding window.
        dq = self._history[tick.ticker]
        dq.append(tick)
        cutoff = tick.timestamp - self._window
        while dq and dq[0].timestamp < cutoff:
            dq.popleft()

        fired: list[TriggeredAlert] = []
        now = tick.timestamp

        for rule in rules:
            if not rule.enabled:
                continue

            # Cooldown: skip if this rule already fired recently.
            last = self._last_fired.get(rule.id, 0.0)
            if now - last < cooldown_seconds:
                continue

            triggered = _evaluate_rule(rule, tick, dq)
            if triggered:
                self._last_fired[rule.id] = now
                fired.append(
                    TriggeredAlert(
                        rule_id=rule.id,
                        rule_label=rule.display_label(),
                        ticker=tick.ticker,
                        trigger_price=tick.price,
                        threshold=rule.threshold,
                        condition=rule.condition,
                        rule_type=rule.rule_type,
                        fired_at=now,
                    )
                )

        return fired


# ---------------------------------------------------------------------------
# Rule evaluation helpers (pure functions — easy to unit test)
# ---------------------------------------------------------------------------

def _evaluate_rule(
    rule: AlertRule, tick: TickData, history: deque[TickData]
) -> bool:
    """Return ``True`` if *tick* satisfies *rule*."""
    if rule.rule_type == RuleType.PRICE_THRESHOLD:
        return _check_price_threshold(rule, tick.price)
    if rule.rule_type == RuleType.PERCENTAGE_MOVE:
        return _check_percentage_move(rule, tick, history)
    return False


def _check_price_threshold(rule: AlertRule, current_price: float) -> bool:
    if rule.condition == Condition.ABOVE:
        return current_price >= rule.threshold
    return current_price <= rule.threshold


def _check_percentage_move(
    rule: AlertRule, tick: TickData, history: deque[TickData]
) -> bool:
    """Return ``True`` if price moved ≥ rule.threshold % within the window."""
    if len(history) < 2:
        return False
    # Oldest price in the window is the baseline.
    baseline_price = history[0].price
    if baseline_price == 0:
        return False
    pct_change = abs((tick.price - baseline_price) / baseline_price) * 100.0
    if rule.condition == Condition.ABOVE:
        return pct_change >= rule.threshold
    # BELOW for percentage move means the change is *less than* the threshold.
    return pct_change <= rule.threshold


# ---------------------------------------------------------------------------
# Public Rule Engine
# ---------------------------------------------------------------------------

class RuleEngine:
    """Distributes incoming ticks across N shards and fires alerts.

    Parameters
    ----------
    rule_store:
        Persistent rule store used to pre-load rules on startup.
    state_store:
        State store — the engine updates latest prices here after each tick.
    dispatcher:
        Notification dispatcher — receives every :class:`TriggeredAlert`.
    shard_count:
        Number of independent processing shards.  Each shard processes
        a disjoint subset of tickers (based on ``hash(ticker) % shard_count``).
    window_seconds:
        Sliding-window size for percentage-move rule evaluation.
    cooldown_seconds:
        Minimum seconds between repeated firings of the same rule.
    """

    def __init__(
        self,
        rule_store: RuleStore,
        state_store: StateStore,
        dispatcher: NotificationDispatcher,
        shard_count: int = 8,
        window_seconds: int = 3600,
        cooldown_seconds: float = 300,
    ) -> None:
        self._rule_store = rule_store
        self._state_store = state_store
        self._dispatcher = dispatcher
        self._cooldown = cooldown_seconds
        self._shard_count = shard_count

        # One queue + shard per slot.
        self._queues: list[asyncio.Queue[TickData]] = [
            asyncio.Queue() for _ in range(shard_count)
        ]
        self._shards: list[_RuleShard] = [
            _RuleShard(window_seconds) for _ in range(shard_count)
        ]
        self._workers: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Load rules from the store and launch shard workers."""
        rules = await self._rule_store.list_rules()
        for rule in rules:
            self._shard_for(rule.ticker).add_rule(rule)
        logger.info(
            "Rule engine started: %d rule(s) across %d shard(s).",
            len(rules),
            self._shard_count,
        )
        for i, (queue, shard) in enumerate(zip(self._queues, self._shards)):
            task = asyncio.create_task(
                self._shard_worker(queue, shard), name=f"rule-engine-shard-{i}"
            )
            self._workers.append(task)

    async def stop(self) -> None:
        """Stop all shard workers."""
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("Rule engine stopped.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ingest(self, tick: TickData) -> None:
        """Route *tick* to the correct shard queue (non-blocking)."""
        queue = self._queues[self._shard_index(tick.ticker)]
        await queue.put(tick)
        # Update the shared state store so the REST API can serve live prices.
        await self._state_store.set_latest_price(
            tick.ticker, tick.price, tick.timestamp
        )

    async def add_rule(self, rule: AlertRule) -> None:
        """Register a new rule at runtime (hot-reload, no restart needed)."""
        await self._rule_store.add_rule(rule)
        self._shard_for(rule.ticker).add_rule(rule)

    async def remove_rule(self, rule_id: str) -> bool:
        """Unregister a rule at runtime."""
        rule = await self._rule_store.get_rule(rule_id)
        if rule is None:
            return False
        await self._rule_store.delete_rule(rule_id)
        self._shard_for(rule.ticker).remove_rule(rule)
        return True

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _shard_index(self, ticker: str) -> int:
        return hash(ticker) % self._shard_count

    def _shard_for(self, ticker: str) -> _RuleShard:
        return self._shards[self._shard_index(ticker)]

    async def _shard_worker(
        self, queue: asyncio.Queue[TickData], shard: _RuleShard
    ) -> None:
        """Drain the shard queue and evaluate rules for each tick."""
        while True:
            tick = await queue.get()
            try:
                alerts = shard.evaluate(tick, self._cooldown)
                for alert in alerts:
                    try:
                        self._dispatcher.enqueue(alert)
                    except asyncio.QueueFull:
                        logger.warning(
                            "Notification queue full — dropping alert for %s",
                            alert.ticker,
                        )
            except Exception:
                logger.exception("Error evaluating tick %s", tick)
            finally:
                queue.task_done()
