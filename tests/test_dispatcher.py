"""Tests for the NotificationDispatcher."""

from __future__ import annotations

import asyncio
import time

import pytest

from stock_alert.notifications.dispatcher import NotificationDispatcher
from stock_alert.notifications.handlers.base import NotificationHandler
from stock_alert.rules.schema import (
    AlertRule,
    Condition,
    RuleType,
    TriggeredAlert,
)
from stock_alert.storage.in_memory_state import InMemoryAlertLogStore


def _make_alert(**kwargs) -> TriggeredAlert:
    defaults = dict(
        rule_id="rule-1",
        rule_label="AAPL > $180",
        ticker="AAPL",
        trigger_price=185.0,
        threshold=180.0,
        condition=Condition.ABOVE,
        rule_type=RuleType.PRICE_THRESHOLD,
        fired_at=time.time(),
    )
    defaults.update(kwargs)
    return TriggeredAlert(**defaults)


class _RecordingHandler(NotificationHandler):
    def __init__(self):
        self.received: list[TriggeredAlert] = []
        self.should_fail = False

    async def send(self, alert: TriggeredAlert) -> bool:
        if self.should_fail:
            raise RuntimeError("Handler error")
        self.received.append(alert)
        return True


@pytest.mark.asyncio
async def test_dispatcher_delivers_to_handler():
    handler = _RecordingHandler()
    log_store = InMemoryAlertLogStore()
    dispatcher = NotificationDispatcher(handlers=[handler], log_store=log_store)
    await dispatcher.start()

    alert = _make_alert()
    dispatcher.enqueue(alert)
    await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert len(handler.received) == 1
    assert handler.received[0].id == alert.id


@pytest.mark.asyncio
async def test_dispatcher_idempotent_no_duplicate():
    handler = _RecordingHandler()
    log_store = InMemoryAlertLogStore()
    dispatcher = NotificationDispatcher(handlers=[handler], log_store=log_store)
    await dispatcher.start()

    alert = _make_alert()
    dispatcher.enqueue(alert)
    await asyncio.sleep(0.05)
    # Enqueue the SAME alert again — should be deduplicated.
    dispatcher.enqueue(alert)
    await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert len(handler.received) == 1


@pytest.mark.asyncio
async def test_dispatcher_logs_alert():
    handler = _RecordingHandler()
    log_store = InMemoryAlertLogStore()
    dispatcher = NotificationDispatcher(handlers=[handler], log_store=log_store)
    await dispatcher.start()

    alert = _make_alert()
    dispatcher.enqueue(alert)
    await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert await log_store.alert_exists(alert.id)


@pytest.mark.asyncio
async def test_dispatcher_continues_after_handler_failure():
    """A failing handler must not prevent other handlers from receiving the alert."""
    good = _RecordingHandler()
    bad = _RecordingHandler()
    bad.should_fail = True
    log_store = InMemoryAlertLogStore()
    dispatcher = NotificationDispatcher(handlers=[bad, good], log_store=log_store)
    await dispatcher.start()

    alert = _make_alert()
    dispatcher.enqueue(alert)
    await asyncio.sleep(0.05)
    await dispatcher.stop()

    assert len(good.received) == 1


@pytest.mark.asyncio
async def test_dispatcher_raises_queue_full():
    handler = _RecordingHandler()
    log_store = InMemoryAlertLogStore()
    dispatcher = NotificationDispatcher(
        handlers=[handler], log_store=log_store, queue_size=1
    )
    # Do NOT start the dispatcher so the queue never drains.
    dispatcher.enqueue(_make_alert())  # fills the queue

    with pytest.raises(asyncio.QueueFull):
        dispatcher.enqueue(_make_alert())
