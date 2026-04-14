"""Console (stdout) notification handler — the default / always-on channel."""

from __future__ import annotations

import logging
import time

from stock_alert.notifications.handlers.base import NotificationHandler
from stock_alert.rules.schema import TriggeredAlert

logger = logging.getLogger(__name__)


class ConsoleHandler(NotificationHandler):
    """Writes a human-readable alert line to the application log."""

    async def send(self, alert: TriggeredAlert) -> bool:
        direction = "▲" if alert.condition.value == "above" else "▼"
        fired_dt = time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(alert.fired_at)
        )
        logger.warning(
            "🚨 ALERT  %s  %s  $%.2f  [threshold %s $%.2f]  @ %s",
            alert.ticker,
            direction,
            alert.trigger_price,
            alert.condition.value,
            alert.threshold,
            fired_dt,
        )
        return True
