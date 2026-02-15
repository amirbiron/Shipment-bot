"""
בדיקות ל-WhatsApp Cloud API webhook handler.

מכסה:
- אימות webhook (GET verification)
- אימות חתימת HMAC-SHA256
- חילוץ טקסט מהודעות Cloud API (כולל interactive buttons)
- חילוץ מדיה מהודעות Cloud API
"""
import hashlib
import hmac

import pytest
from unittest.mock import patch

from app.api.webhooks.whatsapp_cloud import (
    _verify_signature,
    _extract_text_from_message,
    _extract_media_from_message,
)
from app.core.config import settings


# ============================================================================
# TestCloudApiVerify — אימות webhook מול Meta
# ============================================================================


class TestCloudApiVerify:
    """בדיקות לנקודת הקצה GET /webhook — אימות webhook מול Meta."""

    @pytest.mark.unit
    async def test_verify_success(self, test_client, monkeypatch) -> None:
        """verify_token תקין — מחזיר hub.challenge."""
        test_token = "my_test_verify_token"
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_VERIFY_TOKEN", test_token)

        response = await test_client.get(
            "/api/whatsapp-cloud/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "1234567890",
                "hub.verify_token": test_token,
            },
        )
        assert response.status_code == 200
        assert response.json() == 1234567890

    @pytest.mark.unit
    async def test_verify_wrong_token(self, test_client, monkeypatch) -> None:
        """verify_token שגוי — 403."""
        monkeypatch.setattr(
            settings, "WHATSAPP_CLOUD_API_VERIFY_TOKEN", "correct_token"
        )

        response = await test_client.get(
            "/api/whatsapp-cloud/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.challenge": "1234567890",
                "hub.verify_token": "wrong_token",
            },
        )
        assert response.status_code == 403


# ============================================================================
# TestVerifySignature — אימות חתימת HMAC-SHA256 של Meta
# ============================================================================


class TestVerifySignature:
    """בדיקות לפונקציית _verify_signature — אימות חתימת payload."""

    @pytest.mark.unit
    def test_valid_signature(self, monkeypatch) -> None:
        """חתימת HMAC תקינה — מחזיר True."""
        app_secret = "test_app_secret_123"
        monkeypatch.setattr(
            settings, "WHATSAPP_CLOUD_API_APP_SECRET", app_secret
        )

        body = b'{"entry":[]}'
        expected_hash = hmac.new(
            app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        signature_header = f"sha256={expected_hash}"

        assert _verify_signature(body, signature_header) is True

    @pytest.mark.unit
    def test_invalid_signature(self, monkeypatch) -> None:
        """חתימת HMAC שגויה — מחזיר False."""
        monkeypatch.setattr(
            settings, "WHATSAPP_CLOUD_API_APP_SECRET", "test_app_secret_123"
        )

        body = b'{"entry":[]}'
        signature_header = "sha256=0000000000000000000000000000000000000000000000000000000000000000"

        assert _verify_signature(body, signature_header) is False

    @pytest.mark.unit
    def test_missing_prefix(self, monkeypatch) -> None:
        """חתימה ללא קידומת sha256= — מחזיר False."""
        monkeypatch.setattr(
            settings, "WHATSAPP_CLOUD_API_APP_SECRET", "test_app_secret_123"
        )

        body = b'{"entry":[]}'
        # חתימה תקינה אבל בלי קידומת
        valid_hash = hmac.new(
            b"test_app_secret_123", body, hashlib.sha256
        ).hexdigest()
        signature_header = valid_hash  # חסר "sha256="

        assert _verify_signature(body, signature_header) is False


# ============================================================================
# TestExtractText — חילוץ טקסט מהודעות Cloud API
# ============================================================================


class TestExtractText:
    """בדיקות לפונקציית _extract_text_from_message — חילוץ טקסט."""

    @pytest.mark.unit
    def test_text_message(self) -> None:
        """הודעת טקסט רגילה — מחזיר את תוכן ההודעה."""
        msg = {"type": "text", "text": {"body": "hello"}}
        assert _extract_text_from_message(msg) == "hello"

    @pytest.mark.unit
    def test_interactive_button_reply(self) -> None:
        """לחיצה על כפתור interactive — מחזיר את ה-ID של הכפתור."""
        msg = {
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "menu"},
            },
        }
        assert _extract_text_from_message(msg) == "menu"

    @pytest.mark.unit
    def test_empty_message(self) -> None:
        """הודעת טקסט ריקה — מחזיר מחרוזת ריקה."""
        msg = {"type": "text", "text": {"body": ""}}
        assert _extract_text_from_message(msg) == ""


# ============================================================================
# TestExtractMedia — חילוץ מדיה מהודעות Cloud API
# ============================================================================


class TestExtractMedia:
    """בדיקות לפונקציית _extract_media_from_message — חילוץ מדיה."""

    @pytest.mark.unit
    def test_image_message(self) -> None:
        """הודעת תמונה — מחזיר (media_id, 'image')."""
        msg = {
            "type": "image",
            "image": {"id": "img_123", "mime_type": "image/jpeg"},
        }
        media_id, media_type = _extract_media_from_message(msg)
        assert media_id == "img_123"
        assert media_type == "image"

    @pytest.mark.unit
    def test_document_message(self) -> None:
        """הודעת מסמך — מחזיר (media_id, 'document')."""
        msg = {
            "type": "document",
            "document": {"id": "doc_456", "mime_type": "application/pdf"},
        }
        media_id, media_type = _extract_media_from_message(msg)
        assert media_id == "doc_456"
        assert media_type == "document"

    @pytest.mark.unit
    def test_text_no_media(self) -> None:
        """הודעת טקסט — אין מדיה, מחזיר (None, None)."""
        msg = {"type": "text", "text": {"body": "שלום"}}
        media_id, media_type = _extract_media_from_message(msg)
        assert media_id is None
        assert media_type is None
