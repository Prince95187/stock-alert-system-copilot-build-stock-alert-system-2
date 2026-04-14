"""FastAPI web application — serves the dashboard and the REST / SSE APIs.

Endpoints
---------
GET  /                          Dashboard (HTML)
GET  /api/prices                Latest prices for all tracked tickers
GET  /api/rules                 List all alert rules
POST /api/rules                 Create a new alert rule
DELETE /api/rules/{rule_id}     Delete a rule
GET  /api/alerts                Recent triggered alerts
GET  /sse/prices                Server-Sent Events stream of live prices
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import time
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from config import settings
from stock_alert.notifications.dispatcher import NotificationDispatcher
from stock_alert.notifications.handlers.console import ConsoleHandler
from stock_alert.notifications.handlers.email_handler import MockEmailHandler
from stock_alert.rules.engine import RuleEngine
from stock_alert.rules.schema import AlertRule, Condition, RuleType
from stock_alert.storage.in_memory_rules import InMemoryRuleStore
from stock_alert.storage.in_memory_state import InMemoryAlertLogStore, InMemoryStateStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Globals initialised in the lifespan
# ---------------------------------------------------------------------------
rule_store: InMemoryRuleStore
state_store: InMemoryStateStore
log_store: InMemoryAlertLogStore
dispatcher: NotificationDispatcher
engine: RuleEngine
_simulator_task: asyncio.Task | None = None

# Tickers tracked by the built-in price simulator.
_DEMO_TICKERS = ["AAPL", "NVDA", "TSLA", "MSFT", "GOOGL", "AMZN", "META", "AMD"]
_BASE_PRICES: dict[str, float] = {
    "AAPL": 175.0, "NVDA": 870.0, "TSLA": 215.0, "MSFT": 410.0,
    "GOOGL": 165.0, "AMZN": 185.0, "META": 510.0, "AMD": 165.0,
}

# SSE broadcast: connected clients share this queue.
_sse_clients: list[asyncio.Queue] = []


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global rule_store, state_store, log_store, dispatcher, engine, _simulator_task

    rule_store = InMemoryRuleStore()
    state_store = InMemoryStateStore()
    log_store = InMemoryAlertLogStore()

    dispatcher = NotificationDispatcher(
        handlers=[
            ConsoleHandler(),
            MockEmailHandler(
                from_address=settings.mock_email_from,
                to_address=settings.mock_email_to,
            ),
        ],
        log_store=log_store,
        queue_size=settings.notification_queue_size,
    )
    await dispatcher.start()

    engine = RuleEngine(
        rule_store=rule_store,
        state_store=state_store,
        dispatcher=dispatcher,
        shard_count=settings.engine_shard_count,
        window_seconds=settings.percentage_window_seconds,
        cooldown_seconds=settings.alert_cooldown_seconds,
    )
    await engine.start()

    # Seed a few demo rules so the UI is non-empty on first load.
    demo_rules = [
        AlertRule(ticker="AAPL", rule_type=RuleType.PRICE_THRESHOLD, condition=Condition.ABOVE, threshold=185.0, label="AAPL breakout"),
        AlertRule(ticker="NVDA", rule_type=RuleType.PRICE_THRESHOLD, condition=Condition.BELOW, threshold=850.0, label="NVDA dip"),
        AlertRule(ticker="TSLA", rule_type=RuleType.PERCENTAGE_MOVE, condition=Condition.ABOVE, threshold=3.0, window_seconds=300, label="TSLA ±3% in 5m"),
    ]
    for rule in demo_rules:
        await engine.add_rule(rule)

    _simulator_task = asyncio.create_task(_price_simulator(), name="price-simulator")

    yield  # ── application runs ────────────────────────────────────────

    _simulator_task.cancel()
    try:
        await _simulator_task
    except asyncio.CancelledError:
        pass
    await engine.stop()
    await dispatcher.stop()


# ---------------------------------------------------------------------------
# Price simulator (replaces the real WebSocket feed in demo mode)
# ---------------------------------------------------------------------------

async def _price_simulator() -> None:
    """Randomly walk prices and broadcast ticks to the engine + SSE clients."""
    from stock_alert.rules.schema import TickData

    prices = dict(_BASE_PRICES)
    while True:
        await asyncio.sleep(1.0)
        updates: dict[str, float] = {}
        for ticker in _DEMO_TICKERS:
            # Geometric random walk: ±0.3 % per step.
            drift = random.gauss(0, 0.003)
            prices[ticker] = round(prices[ticker] * (1 + drift), 2)
            updates[ticker] = prices[ticker]

            tick = TickData(ticker=ticker, price=prices[ticker], timestamp=time.time())
            await engine.ingest(tick)

        # Broadcast all updates to SSE clients in one JSON payload.
        payload = json.dumps({"type": "prices", "data": updates, "ts": time.time()})
        dead: list[asyncio.Queue] = []
        for q in _sse_clients:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)

        # Also broadcast any new triggered alerts.
        recent = await log_store.get_recent_alerts(limit=1)
        if recent:
            alert = recent[0]
            alert_payload = json.dumps({
                "type": "alert",
                "data": alert.model_dump(),
                "ts": time.time(),
            })
            for q in list(_sse_clients):
                try:
                    q.put_nowait(alert_payload)
                except asyncio.QueueFull:
                    pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(title="Stock Alert System", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# ── SSE price stream ──────────────────────────────────────────────────────────

@app.get("/sse/prices")
async def sse_prices(request: Request):
    """Server-Sent Events endpoint — pushes price updates and alert notifications."""
    client_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
    _sse_clients.append(client_queue)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(client_queue.get(), timeout=15.0)
                    yield f"data: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            try:
                _sse_clients.remove(client_queue)
            except ValueError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── REST: prices ──────────────────────────────────────────────────────────────

@app.get("/api/prices")
async def get_prices() -> dict[str, Any]:
    all_prices = await state_store.get_all_prices()
    return {
        ticker: {"price": price, "timestamp": ts}
        for ticker, (price, ts) in all_prices.items()
    }


# ── REST: rules ───────────────────────────────────────────────────────────────

@app.get("/api/rules")
async def list_rules():
    rules = await rule_store.list_rules()
    return [r.model_dump() for r in rules]


@app.post("/api/rules", status_code=201)
async def create_rule(payload: dict[str, Any]):
    try:
        rule = AlertRule(**payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await engine.add_rule(rule)
    return rule.model_dump()


@app.delete("/api/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: str):
    deleted = await engine.remove_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")


# ── REST: triggered alerts ────────────────────────────────────────────────────

@app.get("/api/alerts")
async def get_alerts(limit: int = 50, ticker: str | None = None):
    alerts = await log_store.get_recent_alerts(limit=limit, ticker=ticker)
    return [a.model_dump() for a in alerts]


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
