"""Tests for the Rule Engine and evaluation helpers."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest

from stock_alert.rules.engine import (
    RuleEngine,
    _RuleShard,
    _check_percentage_move,
    _check_price_threshold,
    _evaluate_rule,
)
from stock_alert.rules.schema import AlertRule, Condition, RuleType, TickData


# ── Pure helpers ─────────────────────────────────────────────────

class TestCheckPriceThreshold:
    def _rule(self, condition, threshold):
        return AlertRule(ticker="AAPL", threshold=threshold, condition=condition)

    def test_above_fires_when_price_gte_threshold(self):
        assert _check_price_threshold(self._rule(Condition.ABOVE, 150), 150)
        assert _check_price_threshold(self._rule(Condition.ABOVE, 150), 155)

    def test_above_does_not_fire_below_threshold(self):
        assert not _check_price_threshold(self._rule(Condition.ABOVE, 150), 149.99)

    def test_below_fires_when_price_lte_threshold(self):
        assert _check_price_threshold(self._rule(Condition.BELOW, 200), 200)
        assert _check_price_threshold(self._rule(Condition.BELOW, 200), 195)

    def test_below_does_not_fire_above_threshold(self):
        assert not _check_price_threshold(self._rule(Condition.BELOW, 200), 200.01)


class TestCheckPercentageMove:
    def _pct_rule(self, threshold, condition=Condition.ABOVE, window=300):
        return AlertRule(
            ticker="TSLA",
            rule_type=RuleType.PERCENTAGE_MOVE,
            threshold=threshold,
            condition=condition,
            window_seconds=window,
        )

    def _history(self, prices):
        now = time.time()
        dq = deque()
        for i, p in enumerate(prices):
            dq.append(TickData(ticker="TSLA", price=p, timestamp=now + i))
        return dq

    def test_fires_when_pct_move_exceeds_threshold(self):
        rule = self._pct_rule(5.0)
        history = self._history([100.0, 105.0, 106.0])
        tick = TickData(ticker="TSLA", price=106.0, timestamp=time.time())
        assert _check_percentage_move(rule, tick, history) is True

    def test_does_not_fire_when_pct_move_below_threshold(self):
        rule = self._pct_rule(10.0)
        history = self._history([100.0, 103.0])
        tick = TickData(ticker="TSLA", price=103.0, timestamp=time.time())
        assert _check_percentage_move(rule, tick, history) is False

    def test_returns_false_with_single_price_point(self):
        rule = self._pct_rule(1.0)
        history = self._history([100.0])
        tick = TickData(ticker="TSLA", price=100.0, timestamp=time.time())
        assert _check_percentage_move(rule, tick, history) is False


# ── _RuleShard ───────────────────────────────────────────────────

class TestRuleShard:
    def _tick(self, ticker="AAPL", price=160.0):
        return TickData(ticker=ticker, price=price, timestamp=time.time())

    def test_fires_above_threshold(self):
        shard = _RuleShard(window_seconds=3600)
        rule = AlertRule(ticker="AAPL", threshold=150, condition=Condition.ABOVE)
        shard.add_rule(rule)
        alerts = shard.evaluate(self._tick("AAPL", 160.0), cooldown_seconds=0)
        assert len(alerts) == 1
        assert alerts[0].ticker == "AAPL"

    def test_cooldown_prevents_repeat_fire(self):
        shard = _RuleShard(window_seconds=3600)
        rule = AlertRule(ticker="AAPL", threshold=150, condition=Condition.ABOVE)
        shard.add_rule(rule)
        shard.evaluate(self._tick("AAPL", 160.0), cooldown_seconds=300)
        # Second evaluation within cooldown window — should NOT fire.
        alerts = shard.evaluate(self._tick("AAPL", 161.0), cooldown_seconds=300)
        assert len(alerts) == 0

    def test_does_not_fire_for_unrelated_ticker(self):
        shard = _RuleShard(window_seconds=3600)
        rule = AlertRule(ticker="AAPL", threshold=150, condition=Condition.ABOVE)
        shard.add_rule(rule)
        alerts = shard.evaluate(self._tick("NVDA", 900.0), cooldown_seconds=0)
        assert alerts == []

    def test_remove_rule_stops_firing(self):
        shard = _RuleShard(window_seconds=3600)
        rule = AlertRule(ticker="AAPL", threshold=150, condition=Condition.ABOVE)
        shard.add_rule(rule)
        shard.remove_rule(rule)
        alerts = shard.evaluate(self._tick("AAPL", 200.0), cooldown_seconds=0)
        assert alerts == []

    def test_disabled_rule_does_not_fire(self):
        shard = _RuleShard(window_seconds=3600)
        rule = AlertRule(ticker="AAPL", threshold=150, condition=Condition.ABOVE, enabled=False)
        shard.add_rule(rule)
        alerts = shard.evaluate(self._tick("AAPL", 200.0), cooldown_seconds=0)
        assert alerts == []


# ── RuleEngine integration ────────────────────────────────────────

@pytest.mark.asyncio
async def test_engine_routes_tick_to_correct_shard():
    """Ticks must reach the shard worker and produce alerts."""
    from stock_alert.storage.in_memory_rules import InMemoryRuleStore
    from stock_alert.storage.in_memory_state import InMemoryAlertLogStore, InMemoryStateStore

    rule_store = InMemoryRuleStore()
    state_store = InMemoryStateStore()
    log_store = InMemoryAlertLogStore()

    fired_alerts = []

    class _CapturingDispatcher:
        async def start(self): pass
        async def stop(self): pass
        def enqueue(self, alert): fired_alerts.append(alert)

    engine = RuleEngine(
        rule_store=rule_store,
        state_store=state_store,
        dispatcher=_CapturingDispatcher(),
        shard_count=4,
        window_seconds=3600,
        cooldown_seconds=0,
    )
    await engine.start()

    rule = AlertRule(ticker="MSFT", threshold=200, condition=Condition.ABOVE)
    await engine.add_rule(rule)
    await engine.ingest(TickData(ticker="MSFT", price=210.0, timestamp=time.time()))

    # Give shard workers a moment to process.
    await asyncio.sleep(0.05)
    await engine.stop()

    assert len(fired_alerts) == 1
    assert fired_alerts[0].ticker == "MSFT"
