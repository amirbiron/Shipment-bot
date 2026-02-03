"""
Smoke tests for Render shell.

Runs lightweight HTTP checks against a running app instance:
- GET /health
- POST /api/telegram/webhook
- POST /api/whatsapp/webhook

Designed to validate "no crash / no regression" behavior (2xx responses),
even when external services (Telegram/WhatsApp gateway) are unavailable.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import httpx

# לאפשר הרצה מכל תיקיה (למשל `python scripts/smoke_webhooks.py` ב-Render Shell)
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.logging import get_logger, setup_logging  # noqa: E402


logger = get_logger(__name__)


def _base_url() -> str:
    port = os.environ.get("PORT", "8000")
    return os.environ.get("BASE_URL", f"http://127.0.0.1:{port}").rstrip("/")


def _timeout_seconds() -> float:
    return float(os.environ.get("SMOKE_TIMEOUT_SECONDS", "10"))


def _get_health_payload() -> dict:
    return {"timestamp": datetime.utcnow().isoformat() + "Z"}


def _telegram_payload() -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 12345, "type": "private"},
            "text": "תפריט",
            "date": 1700000000,
            "from": {"id": 999, "first_name": "Smoke"},
        },
    }


def _whatsapp_payload() -> dict:
    return {
        "messages": [
            {
                "from_number": "+972501234567",
                "sender_id": "+972501234567",
                "reply_to": "+972501234567",
                "message_id": "smoke-1",
                "text": "תפריט",
                "timestamp": 1700000000,
            }
        ]
    }


def _check_status(resp: httpx.Response, expected_family: int = 2) -> None:
    family = resp.status_code // 100
    if family != expected_family:
        raise RuntimeError(
            f"Unexpected status {resp.status_code} for {resp.request.method} {resp.request.url}. "
            f"Body: {(resp.text or '')[:500]}"
        )


def main() -> None:
    setup_logging(level="INFO", json_format=False, app_name="shipment-bot-smoke")

    base_url = _base_url()
    timeout = _timeout_seconds()

    logger.info("Starting smoke tests", extra_data={"base_url": base_url, "timeout_seconds": timeout})

    with httpx.Client(timeout=timeout) as client:
        # Health check
        health_url = f"{base_url}/health"
        logger.info("Checking health endpoint", extra_data={"url": health_url, **_get_health_payload()})
        resp = client.get(health_url)
        _check_status(resp, expected_family=2)

        # Telegram webhook
        telegram_url = f"{base_url}/api/telegram/webhook"
        logger.info("Posting telegram webhook payload", extra_data={"url": telegram_url})
        resp = client.post(telegram_url, json=_telegram_payload())
        _check_status(resp, expected_family=2)

        # WhatsApp webhook
        whatsapp_url = f"{base_url}/api/whatsapp/webhook"
        logger.info("Posting whatsapp webhook payload", extra_data={"url": whatsapp_url})
        resp = client.post(whatsapp_url, json=_whatsapp_payload())
        _check_status(resp, expected_family=2)

    logger.info("Smoke tests completed successfully")


if __name__ == "__main__":
    main()

