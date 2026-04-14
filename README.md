# Stock Alert System

A production-ready, event-driven stock alert system with a real-time dashboard inspired by [Jitter.video](https://jitter.video/templates/) — dark glassmorphism aesthetic, animated ticker tape, live price cards, and instant alert notifications.

---

## ✨ Features

| Capability | Detail |
|---|---|
| **Real-time prices** | Simulated WebSocket feed; swap for any broker API |
| **Price threshold alerts** | `AAPL > $185` or `NVDA < $850` |
| **Percentage-move alerts** | `TSLA moves ±3% within 5 minutes` |
| **< 500 ms latency** | Tick → alert in a single asyncio event-loop iteration |
| **Hot-key safe** | 8-shard rule engine; popular tickers don't block others |
| **Idempotent notifications** | Same alert can never fire twice (dedup by alert ID) |
| **Pluggable notifiers** | Console + mock email; add SMS, Slack, webhook trivially |
| **Live dashboard** | Server-Sent Events push prices & alerts to the browser |
| **Zero-config demo** | Built-in price simulator; no API key required |

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. (Optional) copy and edit .env
cp .env.example .env

# 3. Start the dashboard
python main.py
# → http://localhost:8000
```

---

## 🗂 Project Structure

```
stock-alert-system/
├── app.py                          # FastAPI web app (SSE + REST API)
├── main.py                         # CLI launcher (uvicorn)
├── config.py                       # Pydantic Settings (all env vars)
├── requirements.txt
├── .env.example
│
├── stock_alert/
│   ├── ingestion/
│   │   ├── websocket_client.py     # WebSocket client + auto-reconnect
│   │   └── rate_limiter.py         # Token-bucket rate limiter
│   │
│   ├── rules/
│   │   ├── schema.py               # AlertRule, TickData, TriggeredAlert
│   │   └── engine.py               # Sharded Rule Engine (core)
│   │
│   ├── storage/
│   │   ├── interfaces.py           # Abstract interfaces (RuleStore, StateStore, AlertLogStore)
│   │   ├── in_memory_rules.py      # In-memory RuleStore (dev / testing)
│   │   └── in_memory_state.py      # In-memory StateStore + AlertLogStore
│   │
│   └── notifications/
│       ├── dispatcher.py           # Async idempotent dispatcher
│       └── handlers/
│           ├── base.py             # NotificationHandler ABC
│           ├── console.py          # Console (stdout) handler
│           └── email_handler.py    # Mock email handler
│
├── templates/
│   └── index.html                  # Dashboard (Jitter-inspired SPA)
├── static/
│   ├── css/style.css               # Dark glassmorphism design system
│   └── js/app.js                   # SSE client, rule CRUD, live UI
│
└── tests/
    ├── test_rate_limiter.py        # Token-bucket unit tests
    ├── test_rule_engine.py         # Rule evaluation + engine integration tests
    └── test_dispatcher.py          # Dispatcher idempotency tests
```

---

## 🏗 Architecture

### 1. Data Ingestion (`stock_alert/ingestion/`)

```
WebSocket Feed ──► WebSocketIngestionClient ──► asyncio.Queue ──► RuleEngine
                   (exponential backoff)         (back-pressure)
```

- **Reconnection**: Exponential back-off with *full jitter* — `delay = random(0, min(cap, base × 2ⁿ))`.  
  Prevents thundering-herd when many clients reconnect simultaneously.
- **Rate limiting**: Token-bucket (`TokenBucketRateLimiter`).  
  Refills at `rate` tokens/second; non-blocking `try_acquire()` drops ticks rather than blocking the event loop.
- **Back-pressure**: If the downstream `asyncio.Queue` is full, ticks are dropped and `dropped_ticks` is incremented (observable as a Prometheus gauge in production).

### 2. Rule Engine (`stock_alert/rules/engine.py`)

**The scaling problem**: With 50 000 rules and 10 000 ticks/second, brute-force O(R) evaluation = 500 M comparisons/second.

**Solution — two-level indexing + sharding**:

```
Tick arrives for "AAPL"
   │
   ▼
shard_id = hash("AAPL") % 8        ← O(1) hot-key partitioning
   │
   ▼
shard._index["AAPL"]               ← O(1) dict lookup → [rule1, rule2, …]
   │
   ▼
evaluate each rule for this ticker  ← typically 1–5 rules, not 50 000
```

| Technique | Benefit |
|---|---|
| **Ticker index** `dict[str, list[Rule]]` | O(1) — only evaluate rules for the arriving ticker |
| **8 shard queues** | NVDA burst can't block AAPL/TSLA evaluation |
| **Cooldown per rule** | Prevents alert storms; configurable via `ALERT_COOLDOWN_SECONDS` |
| **Sliding window deque** | O(1) append/pop for percentage-move history |

**Rule types**:
- `PRICE_THRESHOLD` — fires when `price ≥ threshold` (above) or `price ≤ threshold` (below)
- `PERCENTAGE_MOVE` — fires when `|Δprice / baseline| × 100 ≥ threshold` within the configured window

### 3. Storage Layer (`stock_alert/storage/`)

Three abstract interfaces decouple business logic from any concrete backend:

| Interface | Responsibility | Dev impl | Production hint |
|---|---|---|---|
| `RuleStore` | Persistent alert rules | `InMemoryRuleStore` | PostgreSQL — index on `ticker` |
| `StateStore` | Latest prices + history | `InMemoryStateStore` | Redis (`HSET` + Sorted Sets) |
| `AlertLogStore` | Triggered alert log | `InMemoryAlertLogStore` | TimescaleDB / InfluxDB |

### 4. Notification Dispatcher (`stock_alert/notifications/`)

```
RuleEngine fires alert
      │
      ▼
NotificationDispatcher.enqueue()   ← asyncio.Queue (non-blocking)
      │
      ▼
_worker() [background coroutine]
  1. alert_exists(alert.id)?  → skip (idempotency)
  2. log_alert(alert)          → persist before notifying
  3. asyncio.gather(handlers)  → fan-out concurrently
```

- **Idempotency**: Every `TriggeredAlert` has a UUID. The dispatcher checks `AlertLogStore.alert_exists()` before dispatching, so replaying the same alert (e.g., after a crash/restart) is a no-op.
- **Resilience**: `asyncio.gather(return_exceptions=True)` — a crashing handler never prevents other handlers from running.
- **Adding a new channel**: subclass `NotificationHandler`, implement `async def send(alert) -> bool`, pass to `NotificationDispatcher(handlers=[…])`.

### 5. Web Dashboard

```
Browser  ←──  SSE /sse/prices  ←──  _price_simulator  ──►  RuleEngine
         ←──  REST /api/*            (asyncio coroutine)
```

- **Server-Sent Events** push price updates and triggered alerts at ~1 Hz with no polling.
- **Live ticker tape** — animated horizontal scroll with flash effects on price changes.
- **Glassmorphism cards** — dark design system with purple/cyan gradient accents, inspired by Jitter.video motion templates.

---

## ⚙ Configuration

All settings are read from environment variables (or `.env`):

| Variable | Default | Description |
|---|---|---|
| `WS_FEED_URI` | `ws://localhost:8765` | Upstream WebSocket price feed |
| `RATE_LIMIT_TICKS_PER_SECOND` | `500` | Token-bucket ingestion limit |
| `ENGINE_SHARD_COUNT` | `8` | Number of rule engine shards |
| `PERCENTAGE_WINDOW_SECONDS` | `3600` | Sliding window for % move rules |
| `ALERT_COOLDOWN_SECONDS` | `300` | Minimum seconds between repeat alerts |
| `HOST` | `0.0.0.0` | Web server bind address |
| `PORT` | `8000` | Web server port |

---

## 🧪 Running Tests

```bash
pytest
```

All 24 tests cover: token-bucket rate limiter, price threshold evaluation, percentage-move evaluation, shard routing, cooldown logic, dispatcher idempotency, and handler failure isolation.

---

## 🔌 Connecting a Real Price Feed

Replace the `_price_simulator` in `app.py` with:

```python
from stock_alert.ingestion.websocket_client import WebSocketIngestionClient
from stock_alert.ingestion.rate_limiter import TokenBucketRateLimiter

queue = asyncio.Queue()
client = WebSocketIngestionClient(
    uri=settings.ws_feed_uri,
    tick_queue=queue,
    rate_limiter=TokenBucketRateLimiter(rate=settings.rate_limit_ticks_per_second),
)
asyncio.create_task(client.run())

# Drain the queue and feed ticks into the engine:
async def _drain():
    while True:
        tick = await queue.get()
        await engine.ingest(tick)
asyncio.create_task(_drain())
```

The feed must emit JSON: `{"ticker": "AAPL", "price": 175.50, "timestamp": 1678888888}`