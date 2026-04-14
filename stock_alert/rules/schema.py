"""Pydantic schemas for alert rules and tick data.

These are the core domain objects shared by every other module.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class RuleType(str, Enum):
    """Supported rule kinds."""

    PRICE_THRESHOLD = "price_threshold"
    PERCENTAGE_MOVE = "percentage_move"


class Condition(str, Enum):
    """Direction of the threshold comparison."""

    ABOVE = "above"
    BELOW = "below"


class AlertRule(BaseModel):
    """A single user-defined alert rule.

    Examples
    --------
    Price threshold — alert when AAPL rises above $180::

        AlertRule(
            ticker="AAPL",
            rule_type=RuleType.PRICE_THRESHOLD,
            condition=Condition.ABOVE,
            threshold=180.0,
        )

    Percentage move — alert when TSLA moves ±5 % within 60 minutes::

        AlertRule(
            ticker="TSLA",
            rule_type=RuleType.PERCENTAGE_MOVE,
            condition=Condition.ABOVE,
            threshold=5.0,
            window_seconds=3600,
        )
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    ticker: str
    rule_type: RuleType = RuleType.PRICE_THRESHOLD
    condition: Condition = Condition.ABOVE
    # For PRICE_THRESHOLD: absolute price level.
    # For PERCENTAGE_MOVE: percentage (e.g. 5.0 means ±5 %).
    threshold: float
    # Only meaningful for PERCENTAGE_MOVE rules.
    window_seconds: int = 3600
    # Human-readable label shown in the UI / notifications.
    label: Optional[str] = None
    enabled: bool = True

    @field_validator("ticker", mode="before")
    @classmethod
    def normalise_ticker(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("threshold")
    @classmethod
    def positive_threshold(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("threshold must be positive")
        return v

    def display_label(self) -> str:
        """Return a terse human-readable description of the rule."""
        if self.label:
            return self.label
        if self.rule_type == RuleType.PRICE_THRESHOLD:
            direction = ">" if self.condition == Condition.ABOVE else "<"
            return f"{self.ticker} {direction} ${self.threshold:,.2f}"
        direction = "+" if self.condition == Condition.ABOVE else "-"
        return f"{self.ticker} moves {direction}{self.threshold:.1f}% in {self.window_seconds // 60}m"


class TickData(BaseModel):
    """A single price update from the upstream feed."""

    ticker: str
    price: float
    timestamp: float  # Unix epoch seconds

    @field_validator("ticker", mode="before")
    @classmethod
    def normalise_ticker(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("price")
    @classmethod
    def positive_price(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("price must be positive")
        return v


class TriggeredAlert(BaseModel):
    """Immutable record of a fired alert."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    rule_id: str
    rule_label: str
    ticker: str
    trigger_price: float
    threshold: float
    condition: Condition
    rule_type: RuleType
    fired_at: float  # Unix epoch seconds
