"""Mock e-mail notification handler.

Logs the e-mail content instead of actually sending it.  Replace the body of
:meth:`MockEmailHandler.send` with real ``smtplib`` / SES / SendGrid calls
when deploying to production.
"""

from __future__ import annotations

import logging
import time

from stock_alert.notifications.handlers.base import NotificationHandler
from stock_alert.rules.schema import TriggeredAlert

logger = logging.getLogger(__name__)


class MockEmailHandler(NotificationHandler):
    """Simulates sending an e-mail by logging the full message.

    Parameters
    ----------
    from_address:
        Sender address shown in the log.
    to_address:
        Recipient address shown in the log.
    """

    def __init__(self, from_address: str, to_address: str) -> None:
        self._from = from_address
        self._to = to_address

    async def send(self, alert: TriggeredAlert) -> bool:
        fired_dt = time.strftime(
            "%Y-%m-%d %H:%M:%S UTC", time.gmtime(alert.fired_at)
        )
        direction = "risen above" if alert.condition.value == "above" else "fallen below"
        subject = f"[Stock Alert] {alert.ticker} has {direction} ${alert.threshold:,.2f}"
        body = (
            f"From: {self._from}\n"
            f"To:   {self._to}\n"
            f"Subject: {subject}\n"
            f"\n"
            f"Stock Alert Notification\n"
            f"{'─' * 40}\n"
            f"Ticker     : {alert.ticker}\n"
            f"Rule       : {alert.rule_label}\n"
            f"Condition  : {alert.condition.value} ${alert.threshold:,.2f}\n"
            f"Current    : ${alert.trigger_price:,.2f}\n"
            f"Fired at   : {fired_dt}\n"
            f"{'─' * 40}\n"
            f"\n"
            f"[MOCK — no real e-mail was sent]"
        )
        logger.info("📧  Mock e-mail dispatched:\n%s", body)
        return True
