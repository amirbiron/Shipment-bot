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
- כרטיס נהג למנהלים בסיום רישום שליח (PENDING_APPROVAL)
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


# ============================================================================
# TestCourierRegistrationNotification — כרטיס נהג ב-Cloud webhook
# ============================================================================


class TestCourierRegistrationNotification:
    """בדיקות שכרטיס נהג נשלח למנהלים כששליח משלים רישום דרך Cloud API."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pending_approval_triggers_post_processing(self) -> None:
        """שליח עובר ל-PENDING_APPROVAL — _handle_courier_post_processing נקרא."""
        from app.api.webhooks.whatsapp_cloud import _route_message_to_handler
        from app.db.models.user import UserRole, ApprovalStatus
        from app.state_machine.states import CourierState

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.role = UserRole.COURIER
        mock_user.approval_status = ApprovalStatus.PENDING
        mock_user.phone_number = "+972501234567"

        mock_response = MagicMock()
        mock_response.text = "ממתין לאישור"
        mock_response.keyboard = None

        with patch(
            "app.api.webhooks.whatsapp_cloud.StateManager",
        ) as MockSM, patch(
            "app.api.webhooks.whatsapp_cloud.CourierStateHandler",
        ) as MockHandler, patch(
            "app.api.webhooks.whatsapp_cloud._handle_courier_post_processing",
            new_callable=AsyncMock,
        ) as mock_post_process, patch(
            "app.api.webhooks.whatsapp_cloud._resolve_contact_phone",
            return_value="+972501234567",
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            mock_sm = AsyncMock()
            mock_sm.get_current_state = AsyncMock(
                return_value=CourierState.REGISTER_TERMS.value
            )
            MockSM.return_value = mock_sm

            mock_handler = AsyncMock()
            mock_handler.handle_message = AsyncMock(
                return_value=(mock_response, CourierState.PENDING_APPROVAL.value)
            )
            MockHandler.return_value = mock_handler

            await _route_message_to_handler(
                mock_db, mock_user, "מאשר", None, mock_bg, "+972501234567"
            )

        # וידוא שהפונקציה המשותפת נקראה עם הפרמטרים הנכונים
        mock_post_process.assert_called_once()
        call_kwargs = mock_post_process.call_args
        assert call_kwargs.kwargs["previous_state"] == CourierState.REGISTER_TERMS.value
        assert call_kwargs.kwargs["new_state"] == CourierState.PENDING_APPROVAL.value
        assert call_kwargs.kwargs["platform"] == "whatsapp"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_non_pending_state_still_calls_post_processing(self) -> None:
        """שליח בתפריט — הפונקציה נקראת אבל לא שולחת כרטיס."""
        from app.api.webhooks.whatsapp_cloud import _route_message_to_handler
        from app.db.models.user import UserRole, ApprovalStatus
        from app.state_machine.states import CourierState

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 42
        mock_user.role = UserRole.COURIER
        mock_user.approval_status = ApprovalStatus.APPROVED
        mock_user.phone_number = "+972501234567"

        mock_response = MagicMock()
        mock_response.text = "תפריט שליח"
        mock_response.keyboard = [["משלוחים"]]

        with patch(
            "app.api.webhooks.whatsapp_cloud.StateManager",
        ) as MockSM, patch(
            "app.api.webhooks.whatsapp_cloud.CourierStateHandler",
        ) as MockHandler, patch(
            "app.api.webhooks.whatsapp_cloud._handle_courier_post_processing",
            new_callable=AsyncMock,
        ) as mock_post_process, patch(
            "app.api.webhooks.whatsapp_cloud._resolve_contact_phone",
            return_value="+972501234567",
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ):
            mock_sm = AsyncMock()
            mock_sm.get_current_state = AsyncMock(
                return_value=CourierState.MENU.value
            )
            MockSM.return_value = mock_sm

            mock_handler = AsyncMock()
            mock_handler.handle_message = AsyncMock(
                return_value=(mock_response, CourierState.VIEW_AVAILABLE.value)
            )
            MockHandler.return_value = mock_handler

            await _route_message_to_handler(
                mock_db, mock_user, "משלוחים", None, mock_bg, "+972501234567"
            )

        # וידוא שהפונקציה המשותפת נקראה (תמיד נקראת)
        mock_post_process.assert_called_once()
        call_kwargs = mock_post_process.call_args
        assert call_kwargs.kwargs["new_state"] == CourierState.VIEW_AVAILABLE.value

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_post_processing_fires_notification(self) -> None:
        """בדיקת הפונקציה המשותפת — מעבר ל-PENDING_APPROVAL שולח כרטיס נהג."""
        from app.api.webhooks.whatsapp import _handle_courier_post_processing
        from app.db.models.user import ApprovalStatus
        from app.state_machine.states import CourierState
        from datetime import datetime, timezone

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 10
        mock_user.approval_status = ApprovalStatus.PENDING
        mock_user.terms_accepted_at = datetime(2026, 2, 15, tzinfo=timezone.utc)
        mock_user.full_name = "שליח חדש"
        mock_user.name = "שליח"
        mock_user.service_area = "ירושלים"
        mock_user.id_document_url = "doc.jpg"
        mock_user.vehicle_category = "motorcycle"
        mock_user.selfie_file_id = "selfie.jpg"
        mock_user.vehicle_photo_file_id = "vehicle.jpg"
        mock_user.phone_number = "+972501234567"

        with patch(
            "app.api.webhooks.whatsapp._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp._mark_message_completed",
            new_callable=AsyncMock,
        ) as mock_mark, patch(
            "app.api.webhooks.whatsapp.AdminNotificationService",
        ):
            await _handle_courier_post_processing(
                db=mock_db,
                user=mock_user,
                previous_state=CourierState.REGISTER_TERMS.value,
                new_state=CourierState.PENDING_APPROVAL.value,
                contact_phone="+972501234567",
                photo_file_id=None,
                platform="whatsapp",
                background_tasks=mock_bg,
            )

        # וידוא ש-add_task נקרא עם notify_new_courier_registration
        assert mock_bg.add_task.called
        mock_mark.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_post_processing_idempotency_blocks_duplicate(self) -> None:
        """שליחה כפולה — _try_acquire_message מחזיר False, לא שולח כרטיס."""
        from app.api.webhooks.whatsapp import _handle_courier_post_processing
        from app.db.models.user import ApprovalStatus
        from app.state_machine.states import CourierState
        from datetime import datetime, timezone

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 10
        mock_user.approval_status = ApprovalStatus.PENDING
        mock_user.terms_accepted_at = datetime(2026, 2, 15, tzinfo=timezone.utc)

        with patch(
            "app.api.webhooks.whatsapp._try_acquire_message",
            new_callable=AsyncMock,
            return_value=False,
        ):
            await _handle_courier_post_processing(
                db=mock_db,
                user=mock_user,
                previous_state=CourierState.REGISTER_TERMS.value,
                new_state=CourierState.PENDING_APPROVAL.value,
                contact_phone="+972501234567",
                photo_file_id=None,
                platform="whatsapp",
                background_tasks=mock_bg,
            )

        # לא נקרא add_task — השליחה נחסמה
        mock_bg.add_task.assert_not_called()


# ============================================================================
# TestMediaOnlyMessage — הודעת מדיה ללא טקסט
# ============================================================================


class TestMediaOnlyMessage:
    """בדיקות שהודעת מדיה ללא טקסט לא גורמת לקריסה."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_media_only_message_no_crash(self) -> None:
        """הודעת תמונה ללא כיתוב — לא קורסת על text.strip()."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message
        from app.db.models.user import UserRole

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 7
        mock_user.role = UserRole.COURIER
        mock_user.phone_number = "+972501234567"
        mock_user.name = "שליח"

        # הודעת תמונה ללא טקסט — text="" ו-media_id="img_123"
        msg = {
            "id": "wamid.media_only",
            "from": "972501234567",
            "type": "image",
            "image": {"id": "img_123", "mime_type": "image/jpeg"},
        }
        value = {"messaging_product": "whatsapp"}

        mock_response = MagicMock()
        mock_response.text = "תמונה התקבלה"
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
            "app.api.webhooks.whatsapp_cloud._is_whatsapp_admin_any",
            return_value=False,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._route_message_to_handler",
            new_callable=AsyncMock,
            return_value=("תמונה התקבלה", "COURIER.MENU"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_whatsapp_message",
            new_callable=AsyncMock,
        ), patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        # לא קרס — הגיע לניתוב רגיל
        assert result is not None
        assert result["response"] == "תמונה התקבלה"


# ============================================================================
# TestCloudApiMediaDownload — הורדת מדיה מ-Cloud API
# ============================================================================


class TestCloudApiMediaDownload:
    """בדיקות הורדת מדיה מ-Cloud API והמרה ל-data URI."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_success(self) -> None:
        """media_id תקין — מוריד ומחזיר data URI."""
        from app.domain.services.admin_notification_service import AdminNotificationService

        fake_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        mock_meta_response = MagicMock()
        mock_meta_response.status_code = 200
        mock_meta_response.json.return_value = {"url": "https://lookaside.fbsbx.com/media_download"}

        mock_download_response = MagicMock()
        mock_download_response.status_code = 200
        mock_download_response.content = fake_content
        mock_download_response.headers = {"content-type": "image/png"}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_meta_response, mock_download_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async def _run_fn(fn):
            return await fn()

        with patch("app.domain.services.admin_notification_service.httpx.AsyncClient", return_value=mock_client), \
             patch("app.domain.services.admin_notification_service.settings") as mock_settings, \
             patch("app.domain.services.admin_notification_service.get_whatsapp_cloud_circuit_breaker") as mock_cb:
            mock_settings.WHATSAPP_CLOUD_API_TOKEN = "test_token"
            # circuit breaker פשוט מריץ את הפונקציה
            mock_cb.return_value.execute = _run_fn

            result = await AdminNotificationService._download_cloud_api_media_as_data_url("media_123456")

        assert result is not None
        assert result.startswith("data:image/png;base64,")

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_download_no_token(self) -> None:
        """אין Cloud API token — מחזיר None."""
        from app.domain.services.admin_notification_service import AdminNotificationService

        with patch("app.domain.services.admin_notification_service.settings") as mock_settings:
            mock_settings.WHATSAPP_CLOUD_API_TOKEN = ""

            result = await AdminNotificationService._download_cloud_api_media_as_data_url("media_123456")

        assert result is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_resolve_whatsapp_media_calls_download(self) -> None:
        """_resolve_whatsapp_media_url עבור WhatsApp media_id — קורא להורדה."""
        from app.domain.services.admin_notification_service import AdminNotificationService

        with patch.object(
            AdminNotificationService,
            "_download_cloud_api_media_as_data_url",
            new_callable=AsyncMock,
            return_value="data:image/jpeg;base64,/9j/...",
        ) as mock_download:
            result = await AdminNotificationService._resolve_whatsapp_media_url(
                file_id="cloud_media_id_123", platform="whatsapp"
            )

        mock_download.assert_called_once_with("cloud_media_id_123")
        assert result == "data:image/jpeg;base64,/9j/..."

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_resolve_whatsapp_media_url_returns_as_is(self) -> None:
        """file_id שהוא URL — מחזיר כמו שהוא ללא הורדה."""
        from app.domain.services.admin_notification_service import AdminNotificationService

        result = await AdminNotificationService._resolve_whatsapp_media_url(
            file_id="https://example.com/photo.jpg", platform="whatsapp"
        )
        assert result == "https://example.com/photo.jpg"


# ============================================================================
# TestConfigValidation — ולידציית credentials
# ============================================================================


class TestConfigValidation:
    """בדיקות שולידציית הגדרות Cloud API מכסה גם WHATSAPP_PROVIDER=pywa."""

    @pytest.mark.unit
    def test_pywa_provider_without_credentials_raises(self, monkeypatch) -> None:
        """WHATSAPP_PROVIDER=pywa ללא credentials — זורק ValueError."""
        from app.core.config import Settings

        monkeypatch.setenv("WHATSAPP_PROVIDER", "pywa")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_TOKEN", "")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_PHONE_ID", "")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_APP_SECRET", "")
        monkeypatch.setenv("WHATSAPP_HYBRID_MODE", "false")
        monkeypatch.setenv("DEBUG", "true")

        with pytest.raises(ValueError, match="WHATSAPP_PROVIDER=pywa"):
            Settings()

    @pytest.mark.unit
    def test_pywa_provider_with_credentials_ok(self, monkeypatch) -> None:
        """WHATSAPP_PROVIDER=pywa עם credentials — לא זורק."""
        from app.core.config import Settings

        monkeypatch.setenv("WHATSAPP_PROVIDER", "pywa")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_TOKEN", "test_token")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_PHONE_ID", "123456")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_APP_SECRET", "secret123")
        monkeypatch.setenv("WHATSAPP_HYBRID_MODE", "false")
        monkeypatch.setenv("DEBUG", "true")

        # לא זורק — הגדרות תקינות
        s = Settings()
        assert s.WHATSAPP_PROVIDER == "pywa"

    @pytest.mark.unit
    def test_hybrid_mode_without_gateway_url_raises(self, monkeypatch) -> None:
        """WHATSAPP_HYBRID_MODE=True עם WHATSAPP_GATEWAY_URL ריק — זורק ValueError."""
        from app.core.config import Settings

        monkeypatch.setenv("WHATSAPP_HYBRID_MODE", "true")
        monkeypatch.setenv("WHATSAPP_GATEWAY_URL", "")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_TOKEN", "test_token")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_PHONE_ID", "123456")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_APP_SECRET", "secret123")
        monkeypatch.setenv("DEBUG", "true")

        with pytest.raises(ValueError, match="WHATSAPP_GATEWAY_URL ריק"):
            Settings()

    @pytest.mark.unit
    def test_hybrid_mode_with_gateway_url_ok(self, monkeypatch) -> None:
        """WHATSAPP_HYBRID_MODE=True עם gateway URL תקין — לא זורק."""
        from app.core.config import Settings

        monkeypatch.setenv("WHATSAPP_HYBRID_MODE", "true")
        monkeypatch.setenv("WHATSAPP_GATEWAY_URL", "http://wppconnect:3000")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_TOKEN", "test_token")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_PHONE_ID", "123456")
        monkeypatch.setenv("WHATSAPP_CLOUD_API_APP_SECRET", "secret123")
        monkeypatch.setenv("DEBUG", "true")

        s = Settings()
        assert s.WHATSAPP_HYBRID_MODE is True
        assert s.WHATSAPP_GATEWAY_URL == "http://wppconnect:3000"


# ============================================================================
# TestAdminProviderRouting — ניתוב admin provider לפי הגדרות
# ============================================================================


class TestAdminProviderRouting:
    """בדיקות ש-admin provider מכבד WHATSAPP_PROVIDER ומנתב קבוצות ל-WPPConnect."""

    @pytest.mark.unit
    def test_admin_provider_respects_pywa_setting(self, monkeypatch) -> None:
        """WHATSAPP_PROVIDER=pywa → admin provider הוא pywa."""
        from app.domain.services.whatsapp.provider_factory import (
            get_whatsapp_admin_provider, reset_providers
        )

        monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "pywa")
        monkeypatch.setattr(settings, "WHATSAPP_HYBRID_MODE", False)
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "test")
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_PHONE_ID", "123")
        reset_providers()

        admin_provider = get_whatsapp_admin_provider()
        assert admin_provider.provider_name == "pywa"
        reset_providers()

    @pytest.mark.unit
    def test_admin_provider_hybrid_uses_pywa(self, monkeypatch) -> None:
        """WHATSAPP_HYBRID_MODE=True → admin provider הוא pywa."""
        from app.domain.services.whatsapp.provider_factory import (
            get_whatsapp_admin_provider, reset_providers
        )

        monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", "wppconnect")
        monkeypatch.setattr(settings, "WHATSAPP_HYBRID_MODE", True)
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_TOKEN", "test")
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_PHONE_ID", "123")
        reset_providers()

        admin_provider = get_whatsapp_admin_provider()
        assert admin_provider.provider_name == "pywa"
        reset_providers()

    @pytest.mark.unit
    def test_get_admin_wa_provider_routes_group_to_wppconnect(self) -> None:
        """יעד קבוצתי (@g.us) → תמיד WPPConnect, גם אם admin provider הוא pywa."""
        from app.domain.services.admin_notification_service import AdminNotificationService

        with patch(
            "app.domain.services.admin_notification_service.get_whatsapp_group_provider"
        ) as mock_group, patch(
            "app.domain.services.admin_notification_service.get_whatsapp_admin_provider"
        ) as mock_admin:
            mock_group_instance = MagicMock()
            mock_group_instance.provider_name = "wppconnect"
            mock_group.return_value = mock_group_instance

            mock_admin_instance = MagicMock()
            mock_admin_instance.provider_name = "pywa"
            mock_admin.return_value = mock_admin_instance

            # קבוצה → WPPConnect
            provider = AdminNotificationService._get_admin_wa_provider("120363123456789@g.us")
            assert provider.provider_name == "wppconnect"

            # פרטי → admin provider (pywa)
            provider = AdminNotificationService._get_admin_wa_provider("+972501234567")
            assert provider.provider_name == "pywa"


# ============================================================================
# TestNewUserCaptureLink — משתמש חדש עם capture link
# ============================================================================


class TestNewUserCaptureLink:
    """בדיקות שמשתמש חדש שלוחץ capture link מקבל welcome ראשון."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_new_user_capture_link_gets_welcome(self) -> None:
        """משתמש חדש עם capture_ — מקבל welcome, לא שגיאת capture."""
        from app.api.webhooks.whatsapp_cloud import _process_cloud_message
        from app.db.models.user import UserRole

        mock_db = AsyncMock()
        mock_bg = MagicMock()
        mock_user = MagicMock()
        mock_user.id = 99
        mock_user.role = UserRole.SENDER
        mock_user.phone_number = "+972501234567"
        mock_user.name = "חדש"

        msg = {
            "id": "wamid.new_user_capture",
            "from": "972501234567",
            "type": "text",
            "text": {"body": "capture_abc123"},
        }
        value = {"messaging_product": "whatsapp"}

        with patch(
            "app.api.webhooks.whatsapp_cloud._try_acquire_message",
            new_callable=AsyncMock,
            return_value=True,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.get_or_create_user",
            new_callable=AsyncMock,
            # is_new_user=True
            return_value=(mock_user, True, "+972501234567"),
        ), patch(
            "app.api.webhooks.whatsapp_cloud._is_whatsapp_admin_any",
            return_value=False,
        ), patch(
            "app.api.webhooks.whatsapp_cloud.send_welcome_message",
            new_callable=AsyncMock,
        ) as mock_welcome, patch(
            "app.api.webhooks.whatsapp_cloud._mark_message_completed",
            new_callable=AsyncMock,
        ):
            result = await _process_cloud_message(mock_db, msg, value, mock_bg)

        # קיבל welcome, לא שגיאת capture
        assert result is not None
        assert result.get("new_user") is True
        assert result["response"] == "welcome"
        # send_welcome_message נקרא דרך background_tasks.add_task
        mock_bg.add_task.assert_called_once_with(mock_welcome, "+972501234567")


# ============================================================================
# TestCaptureLinkGeneration — יצירת קישורי wa.me עם URL encoding
# ============================================================================


class TestCaptureLinkGeneration:
    """בדיקות שקישורי capture מקודדים נכון ומכבדים הגדרות."""

    @pytest.mark.unit
    def test_basic_token_encoded(self, monkeypatch) -> None:
        """טוקן רגיל — מקודד ב-URL (אותיות ומקפים נשמרים)."""
        from app.domain.services.whatsapp.wa_me_links import generate_capture_link

        monkeypatch.setattr(settings, "WHATSAPP_HYBRID_MODE", True)
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_PHONE_NUMBER", "972501234567")

        link = generate_capture_link("abc-123_XY")
        assert link == "https://wa.me/972501234567?text=capture_abc-123_XY"

    @pytest.mark.unit
    def test_special_chars_encoded(self, monkeypatch) -> None:
        """טוקן עם תווים מיוחדים — מקודד כך ש-URL לא נשבר."""
        from app.domain.services.whatsapp.wa_me_links import generate_capture_link

        monkeypatch.setattr(settings, "WHATSAPP_HYBRID_MODE", True)
        monkeypatch.setattr(settings, "WHATSAPP_CLOUD_API_PHONE_NUMBER", "972501234567")

        link = generate_capture_link("a+b&c=d")
        assert link is not None
        # תווים מיוחדים מקודדים — לא מופיעים כ-raw בקישור
        assert "a+b&c=d" not in link
        assert "capture_a" in link

    @pytest.mark.unit
    def test_returns_none_when_hybrid_disabled(self, monkeypatch) -> None:
        """מצב היברידי כבוי — מחזיר None."""
        from app.domain.services.whatsapp.wa_me_links import generate_capture_link

        monkeypatch.setattr(settings, "WHATSAPP_HYBRID_MODE", False)
        assert generate_capture_link("token123") is None
