"""In-memory implementation of :class:`~stock_alert.storage.interfaces.RuleStore`.

Used for development, testing, and the demo web app.  Not suitable for
production because state is lost on restart.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from typing import Optional

from stock_alert.rules.schema import AlertRule
from stock_alert.storage.interfaces import RuleStore


class InMemoryRuleStore(RuleStore):
    """Thread-safe, async-compatible in-memory rule store.

    Internally maintains two indexes:
    *  ``_rules``  – ``{rule_id: AlertRule}`` for O(1) id-based lookups.
    *  ``_by_ticker`` – ``{ticker: [AlertRule, …]}`` for O(1) ticker lookups.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._rules: dict[str, AlertRule] = {}
        self._by_ticker: dict[str, list[AlertRule]] = defaultdict(list)

    # ------------------------------------------------------------------
    async def add_rule(self, rule: AlertRule) -> AlertRule:
        async with self._lock:
            self._rules[rule.id] = rule
            self._by_ticker[rule.ticker].append(rule)
            return rule

    async def get_rule(self, rule_id: str) -> Optional[AlertRule]:
        return self._rules.get(rule_id)

    async def list_rules(self) -> Sequence[AlertRule]:
        return list(self._rules.values())

    async def get_rules_for_ticker(self, ticker: str) -> Sequence[AlertRule]:
        return [r for r in self._by_ticker.get(ticker, []) if r.enabled]

    async def delete_rule(self, rule_id: str) -> bool:
        async with self._lock:
            rule = self._rules.pop(rule_id, None)
            if rule is None:
                return False
            bucket = self._by_ticker.get(rule.ticker, [])
            try:
                bucket.remove(rule)
            except ValueError:
                pass
            return True

    async def update_rule(self, rule: AlertRule) -> Optional[AlertRule]:
        async with self._lock:
            if rule.id not in self._rules:
                return None
            old = self._rules[rule.id]
            # Remove from old ticker bucket if ticker changed.
            if old.ticker != rule.ticker:
                try:
                    self._by_ticker[old.ticker].remove(old)
                except ValueError:
                    pass
                self._by_ticker[rule.ticker].append(rule)
            else:
                bucket = self._by_ticker[rule.ticker]
                try:
                    idx = bucket.index(old)
                    bucket[idx] = rule
                except ValueError:
                    bucket.append(rule)
            self._rules[rule.id] = rule
            return rule
