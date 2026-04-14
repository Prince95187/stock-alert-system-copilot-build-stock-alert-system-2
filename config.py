"""Application configuration loaded from environment variables via Pydantic Settings."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tuneable knobs for the Stock Alert System.

    Values are read from the environment (or a ``.env`` file).
    Sensible defaults let the app start immediately for local development.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Ingestion ────────────────────────────────────────────────────────────
    # URI of the upstream WebSocket price feed.
    ws_feed_uri: str = "ws://localhost:8765"

    # Token-bucket: maximum ticks accepted per second from the feed.
    rate_limit_ticks_per_second: int = 500

    # ── Rule Engine ──────────────────────────────────────────────────────────
    # Number of independent shards that distribute ticker processing.
    # Increasing this reduces hot-key contention on popular tickers.
    engine_shard_count: int = 8

    # Rolling window (seconds) used for percentage-move rules.
    percentage_window_seconds: int = 3600

    # ── Notification ─────────────────────────────────────────────────────────
    # Idempotency window: an alert for the same rule won't fire again
    # within this many seconds.
    alert_cooldown_seconds: int = 300

    # Maximum items queued for async dispatch before back-pressure kicks in.
    notification_queue_size: int = 1_000

    # ── Mock email (dev / testing) ────────────────────────────────────────────
    mock_email_from: str = "alerts@stockalert.dev"
    mock_email_to: str = "user@example.com"

    # ── Web server ────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False


# Singleton — import and use ``settings`` everywhere.
settings = Settings()
