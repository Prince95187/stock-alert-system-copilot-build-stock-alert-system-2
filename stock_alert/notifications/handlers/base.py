"""Abstract base class for notification handlers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from stock_alert.rules.schema import TriggeredAlert


class NotificationHandler(ABC):
    """A pluggable handler that delivers a single :class:`TriggeredAlert`."""

    @abstractmethod
    async def send(self, alert: TriggeredAlert) -> bool:
        """Deliver *alert* through this channel.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on failure.
        """
