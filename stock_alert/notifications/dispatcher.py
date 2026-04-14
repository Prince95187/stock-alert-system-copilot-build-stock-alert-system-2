"""Asynchronous notification dispatcher with idempotency guarantees.

Architecture note — Idempotency
---------------------------------
Each :class:`~stock_alert.rules.schema.TriggeredAlert` carries a stable
``id`` (UUID).  Before dispatching, the dispatcher:

1. Checks ``AlertLogStore.alert_exists(alert.id)`` — if the alert was
   already logged it is silently skipped (at-least-once → exactly-once).
2. Logs the alert via ``AlertLogStore.log_alert`` *before* notifying
   handlers, so a handler crash on first run doesn't prevent re-delivery
   on the next retry (idempotent from the handler's perspective).

Architecture note — Back-pressure
------------------------------------
Handlers run concurrently via ``asyncio.gather``.  If the notification
queue fills up, :meth:`enqueue` raises ``asyncio.QueueFull`` so the caller
can apply back-pressure or drop the alert with a warning.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Sequence

from stock_alert.notifications.handlers.base import NotificationHandler
from stock_alert.rules.schema import TriggeredAlert
from stock_alert.storage.interfaces import AlertLogStore

logger = logging.getLogger(__name__)


class NotificationDispatcher:
    """Async dispatcher that fans out each alert to all registered handlers.

    Parameters
    ----------
    handlers:
        One or more :class:`NotificationHandler` instances.  Alerts are
        dispatched to **all** handlers concurrently.
    log_store:
        Persistent log used to detect duplicate alerts.
    queue_size:
        Maximum pending alerts before :meth:`enqueue` raises ``QueueFull``.
    """

    def __init__(
        self,
        handlers: Sequence[NotificationHandler],
        log_store: AlertLogStore,
        queue_size: int = 1_000,
    ) -> None:
        self._handlers = list(handlers)
        self._log_store = log_store
        self._queue: asyncio.Queue[TriggeredAlert] = asyncio.Queue(
            maxsize=queue_size
        )
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background worker task."""
        self._task = asyncio.create_task(self._worker(), name="notification-dispatcher")
        logger.info("Notification dispatcher started with %d handler(s).", len(self._handlers))

    async def stop(self) -> None:
        """Drain the queue and stop the worker."""
        await self._queue.join()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Notification dispatcher stopped.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, alert: TriggeredAlert) -> None:
        """Put *alert* on the dispatch queue (non-blocking).

        Raises
        ------
        asyncio.QueueFull
            If the queue is at capacity — let the caller decide to drop or log.
        """
        self._queue.put_nowait(alert)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _worker(self) -> None:
        """Drain the queue and dispatch each alert to all handlers."""
        while True:
            alert = await self._queue.get()
            try:
                await self._dispatch(alert)
            except Exception:
                logger.exception("Unhandled error dispatching alert %s", alert.id)
            finally:
                self._queue.task_done()

    async def _dispatch(self, alert: TriggeredAlert) -> None:
        """Idempotent dispatch of a single alert."""
        # ── Idempotency check ────────────────────────────────────────────
        if await self._log_store.alert_exists(alert.id):
            logger.debug("Duplicate alert %s skipped.", alert.id)
            return

        # ── Persist first (before notifying) ────────────────────────────
        await self._log_store.log_alert(alert)

        # ── Fan out to all handlers concurrently ─────────────────────────
        results = await asyncio.gather(
            *[h.send(alert) for h in self._handlers],
            return_exceptions=True,
        )
        for handler, result in zip(self._handlers, results):
            if isinstance(result, Exception):
                logger.error(
                    "Handler %s failed for alert %s: %s",
                    type(handler).__name__,
                    alert.id,
                    result,
                )
