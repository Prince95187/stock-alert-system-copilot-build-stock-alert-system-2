#!/usr/bin/env python3
"""CLI entry-point — launches the Stock Alert System web dashboard.

Usage
-----
1. Install dependencies:
       pip install -r requirements.txt

2. (Optional) Copy .env.example to .env and adjust settings.

3. Start the server:
       python main.py

   The dashboard will be available at http://localhost:8000
   The built-in price simulator starts automatically so the UI is live
   immediately — no external API keys required for demo mode.

4. To run against a real WebSocket feed, set WS_FEED_URI in .env and
   replace the ``_price_simulator`` coroutine in app.py with a call to
   ``WebSocketIngestionClient.run()``.
"""

from __future__ import annotations

import logging

import uvicorn

from config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_level="info",
    )
