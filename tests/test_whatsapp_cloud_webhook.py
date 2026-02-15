"""
בדיקות ל-WhatsApp Cloud API webhook handler.

מכסה:
- אימות webhook (GET verification)
- אימות חתימת HMAC-SHA256
- חילוץ טקסט מהודעות Cloud API (כולל interactive buttons)
- חילוץ מדיה מהודעות Cloud API
- אבטחה: דחיית webhook כשסוד ריק
- retry: הודעות כושלות נשארות ב-processing
- ברוכים הבאים: שליחה עם כפתורים
- פקודות אדמין (אישור/דחיית שליחים) ב-Cloud webhook
- תפיסת משלוח מקישור wa.me דרך CaptureService
- אישור/דחיית משלוחים (סדרנים) ב-Cloud webhook
"""
import hashlib
import hmac

import pytest
from unittest.mock import patch, AsyncMock, MagicMock

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


# ============================================================================
# TestCloudApiVerifyChallenge — hub_challenge null check (תיקון 2)
# ============================================================================


class TestCloudApiVerifyChallenge:
    """בדיקות ל-hub_challenge חסר — אסור לקרוס ב-TypeError."""

    @pytest.mark.unit
    async def test_verify_missing_challenge(self, test_client, monkeypatch) -> None:
        """hub.challenge חסר — 403 במקום TypeError."""
        test_token = "my_test_verify_token"
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_VERIFY_TOKEN", test_token)

        response = await test_client.get(
            "/api/whatsapp-cloud/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": test_token,
                # hub.challenge לא נשלח
            },
        )
        assert response.status_code == 403


# ============================================================================
# TestSignatureGuard — דחיית webhook כשסוד ריק (תיקון 5)
# ============================================================================


class TestSignatureGuard:
    """בדיקות שה-webhook POST נדחה כשסוד האפליקציה לא מוגדר."""

    @pytest.mark.unit
    async def test_webhook_rejects_when_secret_empty(self, test_client, monkeypatch) -> None:
        """סוד ריק — webhook POST מחזיר 403."""
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_APP_SECRET", "")

        response = await test_client.post(
            "/api/whatsapp-cloud/webhook",
            json={"entry": []},
        )
        assert response.status_code == 403

    @pytest.mark.unit
    async def test_webhook_rejects_invalid_signature(self, test_client, monkeypatch) -> None:
        """חתימה שגויה — 403."""
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_APP_SECRET", "my_secret")

        response = await test_client.post(
            "/api/whatsapp-cloud/webhook",
            json={"entry": []},
            headers={"X-Hub-Signature-256": "sha256=invalid"},
        )
        assert response.status_code == 403


# ============================================================================
# TestRetryPattern — הודעות כושלות נשארות ב-processing (תיקון 1)
# ============================================================================


class TestRetryPattern:
    """בדיקות שהודעות שנכשלו לא מסומנות כ-completed."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failed_message_not_marked_completed(self) -> None:
        """עיבוד הודעה שנכשל — ההודעה לא מסומנת כ-completed."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        msg = {
            "id": "wamid.test123",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "שלום"},
        }
        value = {"messaging_product": "whatsapp"}

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            side_effect=Exception("DB connection error"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ) as mock_mark:
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        # הודעה שנכשלה — לא מסומנת כ-completed
        assert result is None
        mock_mark.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_successful_message_marked_completed(self) -> None:
        """עיבוד הודעה מוצלח — ההודעה מסומנת כ-completed."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.role = MagicMock(value="sender")

        msg = {
            "id": "wamid.test456",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "#"},
        }
        value = {"messaging_product": "whatsapp"}

        mock_response = MagicMock()
        mock_response.text = "תפריט ראשי"
        mock_response.keyboard = None

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._route_to_role_menu_wa",
            new_callable=AsyncMock,
            return_value=(mock_response, "SENDER_MENU"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ) as mock_mark:
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        # הודעה מוצלחת — מסומנת כ-completed
        assert result is not None
        mock_mark.assert_called_once_with(mock_db, "wamid.test456")


# ============================================================================
# TestWelcomeMessage — משתמש חדש מקבל ברוכים הבאים עם כפתורים (תיקון 3)
# ============================================================================


class TestWelcomeMessage:
    """בדיקות שמשתמש חדש מקבל send_welcome_message (עם כפתורים)."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_new_user_gets_welcome_with_keyboard(self) -> None:
        """משתמש חדש — מקבל send_welcome_message במקום טקסט בלבד."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.role = MagicMock(value="sender")

        msg = {
            "id": "wamid.new_user",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "שלום"},
        }
        value = {"messaging_product": "whatsapp"}

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, True, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_welcome_message",
            new_callable=AsyncMock,
        ) as mock_welcome, patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result.get("new_user") is True
        # וידוא ש-send_welcome_message נקרא (ולא send_whatsapp_message עם טקסט בלבד)
        mock_bg.add_task.assert_called_once_with(
            mock_welcome, "+972501234567"
        )


# ============================================================================
# TestAdminCommands — פקודות אדמין ב-Cloud webhook
# ============================================================================


class TestAdminCommands:
    """בדיקות שפקודות אדמין מנותבות ל-handle_admin_private_command."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_admin_command_routed_to_handler(self) -> None:
        """אדמין שולח 'אשר 123' — מנותב ל-handle_admin_private_command."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.role = MagicMock(value="sender")
        mock_user.phone_number = "+972501234567"
        mock_user.name = "Admin"

        msg = {
            "id": "wamid.admin_cmd",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "אשר 123"},
        }
        value = {"messaging_product": "whatsapp"}

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._is_whatsapp_admin_any",
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.handle_admin_private_command",
            new_callable=AsyncMock,
            return_value="✅ שליח 123 אושר בהצלחה",
        ) as mock_admin_cmd, patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result.get("admin_command") is True
        mock_admin_cmd.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_non_admin_not_routed_to_admin_handler(self) -> None:
        """לא-אדמין — לא מנותב ל-handle_admin_private_command."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.role = MagicMock(value="sender")
        mock_user.phone_number = "+972501234567"
        mock_user.name = "User"

        mock_response = MagicMock()
        mock_response.text = "תפריט"
        mock_response.keyboard = None

        msg = {
            "id": "wamid.non_admin",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "אשר 123"},
        }
        value = {"messaging_product": "whatsapp"}

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._is_whatsapp_admin_any",
            return_value=False,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.handle_admin_private_command",
            new_callable=AsyncMock,
        ) as mock_admin_cmd, patch(
            "app.api.webhooks.whatsapp_cloud._match_delivery_approval_command",
            return_value=None,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._route_message_to_handler",
            new_callable=AsyncMock,
            return_value=("תפריט", "SENDER_MENU"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result.get("admin_command") is None
        mock_admin_cmd.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_admin_non_command_falls_through(self) -> None:
        """אדמין שולח טקסט רגיל — handle_admin_private_command מחזיר None, ממשיך לניתוב רגיל."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.role = MagicMock(value="sender")
        mock_user.phone_number = "+972501234567"
        mock_user.name = "Admin"

        msg = {
            "id": "wamid.admin_text",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "שלום עולם"},
        }
        value = {"messaging_product": "whatsapp"}

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._is_whatsapp_admin_any",
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.handle_admin_private_command",
            new_callable=AsyncMock,
            return_value=None,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._match_delivery_approval_command",
            return_value=None,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._route_message_to_handler",
            new_callable=AsyncMock,
            return_value=("תפריט ראשי", "SENDER_MENU"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result.get("admin_command") is None
        assert result.get("response") == "תפריט ראשי"


# ============================================================================
# TestCaptureFromLink — תפיסת משלוח מקישור wa.me
# ============================================================================


class TestCaptureFromLink:
    """בדיקות תפיסת משלוח מקישור wa.me דרך CaptureService."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_capture_link_success(self) -> None:
        """שליח מאושר לוחץ על קישור תפיסה — CaptureService מחזיר הצלחה."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message
        from app.db.models.user import UserRole, ApprovalStatus

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 5
        mock_user.role = UserRole.COURIER
        mock_user.approval_status = ApprovalStatus.APPROVED
        mock_user.phone_number = "+972501234567"
        mock_user.name = "שליח"

        mock_delivery = MagicMock()
        mock_delivery.pickup_address = "תל אביב, דיזנגוף 50"
        mock_delivery.dropoff_address = "חיפה, הרצל 10"
        mock_delivery.fee = 25.0

        msg = {
            "id": "wamid.capture_test",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "capture_abc123token"},
        }
        value = {"messaging_product": "whatsapp"}

        mock_capture_instance = AsyncMock()
        mock_capture_instance.capture_delivery_by_token = AsyncMock(
            return_value=(True, "המשלוח נתפס בהצלחה", mock_delivery)
        )

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.CaptureService",
            return_value=mock_capture_instance,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result["response"] == "capture_success"
        mock_capture_instance.capture_delivery_by_token.assert_called_once_with(
            "abc123token", 5
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_capture_link_not_approved_courier(self) -> None:
        """משתמש שאינו שליח מאושר — מקבל הודעת שגיאה."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message
        from app.db.models.user import UserRole, ApprovalStatus

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 2
        mock_user.role = UserRole.SENDER
        mock_user.approval_status = ApprovalStatus.PENDING
        mock_user.phone_number = "+972501234567"
        mock_user.name = "שולח"

        msg = {
            "id": "wamid.capture_not_courier",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "capture_xyz456"},
        }
        value = {"messaging_product": "whatsapp"}

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result["response"] == "not_approved_courier"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_capture_link_service_error(self) -> None:
        """CaptureService זורק exception — הודעת שגיאה גנרית (לא str(exc))."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message
        from app.db.models.user import UserRole, ApprovalStatus

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 5
        mock_user.role = UserRole.COURIER
        mock_user.approval_status = ApprovalStatus.APPROVED
        mock_user.phone_number = "+972501234567"
        mock_user.name = "שליח"

        msg = {
            "id": "wamid.capture_error",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "capture_errortoken"},
        }
        value = {"messaging_product": "whatsapp"}

        mock_capture_instance = AsyncMock()
        mock_capture_instance.capture_delivery_by_token = AsyncMock(
            side_effect=Exception("DB connection lost")
        )

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.CaptureService",
            return_value=mock_capture_instance,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ) as mock_send, patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result["response"] == "capture_failed"
        # וידוא שההודעה שנשלחת למשתמש לא מכילה את פרטי השגיאה הפנימיים
        send_call_args = mock_bg.add_task.call_args_list
        # ה-add_task נקרא עם (send_whatsapp_message, phone, message)
        sent_message = send_call_args[0][0][2]  # הארגומנט השלישי
        assert "DB connection lost" not in sent_message
        assert "שגיאה" in sent_message


# ============================================================================
# TestDeliveryApproval — אישור/דחיית משלוחים (סדרנים) ב-Cloud webhook
# ============================================================================


class TestDeliveryApproval:
    """בדיקות שפקודות אישור/דחיית משלוח מנותבות ל-_handle_whatsapp_delivery_approval."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_dispatcher_approves_delivery(self) -> None:
        """סדרן שולח 'אשר משלוח 42' — מנותב לטיפול באישור משלוח."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 10
        mock_user.role = MagicMock(value="courier")
        mock_user.phone_number = "+972501234567"
        mock_user.name = "סדרן"

        # הגדרת mock למשלוח עם station_id
        mock_delivery = MagicMock()
        mock_delivery.station_id = 5

        # mock לתוצאת שאילתת DB
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_delivery
        mock_db.execute = AsyncMock(return_value=mock_result)

        msg = {
            "id": "wamid.approval_test",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "אשר משלוח 42"},
        }
        value = {"messaging_product": "whatsapp"}

        mock_station_instance = MagicMock()
        mock_station_instance.is_dispatcher_of_station = AsyncMock(return_value=True)

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._is_whatsapp_admin_any",
            return_value=False,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._match_delivery_approval_command",
            return_value=("approve", 42),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.StationService",
            return_value=mock_station_instance,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._handle_whatsapp_delivery_approval",
            new_callable=AsyncMock,
            return_value="✅ משלוח #42 אושר. נשלח לנהג.",
        ) as mock_approval, patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result.get("delivery_approval") is True
        mock_approval.assert_called_once_with(
            mock_db, "approve", 42, dispatcher_id=10,
        )

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_non_dispatcher_rejected(self) -> None:
        """משתמש שאינו סדרן — מקבל הודעת 'אין הרשאה'."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 20
        mock_user.role = MagicMock(value="sender")
        mock_user.phone_number = "+972509999999"
        mock_user.name = "משתמש"

        # משלוח עם station_id
        mock_delivery = MagicMock()
        mock_delivery.station_id = 5

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_delivery
        mock_db.execute = AsyncMock(return_value=mock_result)

        msg = {
            "id": "wamid.not_dispatcher",
            "from": "972509999999",
            "type": "text",
            "text": {"body": "אשר משלוח 42"},
        }
        value = {"messaging_product": "whatsapp"}

        mock_station_instance = MagicMock()
        mock_station_instance.is_dispatcher_of_station = AsyncMock(return_value=False)

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            return_value=(mock_user, False, "+972509999999"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._is_whatsapp_admin_any",
            return_value=False,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._match_delivery_approval_command",
            return_value=("approve", 42),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.StationService",
            return_value=mock_station_instance,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        assert result is not None
        assert result.get("delivery_approval") is True
        assert result["response"] == "not_authorized"
