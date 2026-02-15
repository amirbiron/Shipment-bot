"""
PyWa Provider — מימוש ממשק BaseWhatsAppProvider מעל Cloud API (Meta).

משתמש בספריית pywa לשליחת הודעות דרך WhatsApp Cloud API.
תומך ב-inline buttons, מדיה עשירה, ו-retry עם circuit breaker.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Optional

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import settings
from app.core.exceptions import WhatsAppError
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator, convert_html_to_whatsapp
from app.domain.services.whatsapp.base_provider import BaseWhatsAppProvider

logger = get_logger(__name__)

# מספר כפתורי reply מקסימלי ב-Cloud API
_MAX_REPLY_BUTTONS = 3


class PyWaProvider(BaseWhatsAppProvider):
    """
    מימוש ספק WhatsApp מעל Cloud API (Meta) באמצעות ספריית pywa.

    תומך ב-inline buttons עם callback data, מדיה עשירה,
    ו-retry + circuit breaker כמו WPPConnectProvider.
    """

    def __init__(self, circuit_breaker: CircuitBreaker) -> None:
        self._circuit_breaker = circuit_breaker
        self._max_retries = settings.WHATSAPP_MAX_RETRIES

        # אתחול עצלן — נטען רק כשנדרש, מונע import errors בבדיקות
        self._client = None

    def _get_client(self):
        """אתחול עצלן של pywa client."""
        if self._client is None:
            from pywa_async import WhatsApp as PyWaClient

            self._client = PyWaClient(
                phone_id=settings.WHATSAPP_CLOUD_API_PHONE_ID,
                token=settings.WHATSAPP_CLOUD_API_TOKEN,
            )
        return self._client

    # ── ממשק ציבורי ──

    @property
    def provider_name(self) -> str:
        return "pywa"

    def normalize_phone(self, phone: str) -> str:
        """נרמול מספר טלפון לפורמט Cloud API (ללא + בהתחלה)."""
        if PhoneNumberValidator.validate(phone):
            normalized = PhoneNumberValidator.normalize(phone)
            # Cloud API רוצה 972501234567 ולא +972501234567
            return normalized.lstrip("+")
        return phone

    def format_text(self, html_text: str) -> str:
        """המרת HTML → WhatsApp markdown (Cloud API משתמש באותו פורמט)."""
        return convert_html_to_whatsapp(html_text)

    # ── retry helper פנימי ──

    async def _execute_with_retry(
        self,
        operation: str,
        phone_masked: str,
        func,
    ) -> None:
        """הרצה עם retry ו-exponential backoff.

        עוטף כל קריאה ל-Cloud API.
        זורק WhatsAppError אם כל הניסיונות נכשלו.
        """
        last_error: Exception | None = None

        for attempt in range(self._max_retries):
            try:
                await func()
                return
            except Exception as exc:
                last_error = exc
                if attempt < self._max_retries - 1:
                    backoff = 2 ** attempt
                    logger.warning(
                        f"שגיאה ב-{operation}, מנסה שוב",
                        extra_data={
                            "phone": phone_masked,
                            "error": str(exc),
                            "attempt": attempt + 1,
                            "max_retries": self._max_retries,
                            "backoff_seconds": backoff,
                        },
                    )
                    await asyncio.sleep(backoff)
                    continue

        raise WhatsAppError(
            message=f"Cloud API {operation} נכשל אחרי {self._max_retries} ניסיונות",
            details={
                "phone": phone_masked,
                "error": str(last_error),
                "attempts": self._max_retries,
            },
        )

    # ── כפתורים ──

    @staticmethod
    def _build_buttons(keyboard: list[list[str]] | None):
        """המרת keyboard (שורות של מחרוזות) לכפתורי pywa.

        Cloud API תומך בעד 3 reply buttons. אם יש יותר — מחזיר None
        ו-caller צריך לשלוח טקסט עם הנחיות טקסטואליות.
        """
        if not keyboard:
            return None

        from pywa import types as pywa_types

        # שטוח את כל הכפתורים לרשימה אחת
        all_labels: list[str] = []
        for row in keyboard:
            if isinstance(row, list):
                all_labels.extend(row)
            else:
                all_labels.append(str(row))

        if not all_labels:
            return None

        # Cloud API מגביל ל-3 reply buttons
        if len(all_labels) > _MAX_REPLY_BUTTONS:
            return None

        buttons = []
        for label in all_labels:
            # כפתור Cloud API — title עד 20 תווים (תצוגה), callback_data עד 256 תווים (ערך מלא)
            title = label[:20]
            buttons.append(
                pywa_types.Button(title=title, callback_data=label[:256])
            )
        return buttons

    @staticmethod
    def _keyboard_to_text_instructions(keyboard: list[list[str]] | None) -> str:
        """המרת כפתורים להנחיות טקסטואליות (fallback כשיש יותר מ-3 כפתורים)."""
        if not keyboard:
            return ""

        all_labels: list[str] = []
        for row in keyboard:
            if isinstance(row, list):
                all_labels.extend(row)
            else:
                all_labels.append(str(row))

        if not all_labels or len(all_labels) <= _MAX_REPLY_BUTTONS:
            return ""

        lines = ["\n\nהקלד אחת מהאפשרויות:"]
        for i, label in enumerate(all_labels, 1):
            lines.append(f"{i}. {label}")
        return "\n".join(lines)

    # ── שליחת הודעות ──

    async def send_text(
        self,
        to: str,
        text: str,
        keyboard: Optional[list[list[str]]] = None,
    ) -> None:
        """שליחת טקסט דרך Cloud API עם retry ו-circuit breaker.

        הטקסט נשלח as-is — אם הקלט מכיל HTML, הקורא אחראי
        לקרוא ל-format_text() לפני השליחה.
        """
        to = self.normalize_phone(to)
        phone_masked = PhoneNumberValidator.mask(to)

        buttons = self._build_buttons(keyboard)

        # fallback: אם יש יותר מ-3 כפתורים — הנחיות טקסטואליות
        text_suffix = self._keyboard_to_text_instructions(keyboard)
        final_text = text + text_suffix

        client = self._get_client()

        async def _send_single() -> None:
            await client.send_message(
                to=to,
                text=final_text,
                buttons=buttons,
            )

        async def _send_with_retry() -> None:
            await self._execute_with_retry("send_text", phone_masked, _send_single)

        await self._circuit_breaker.execute(_send_with_retry)

    async def send_media(
        self,
        to: str,
        media_url: str,
        media_type: str = "image",
        caption: Optional[str] = None,
    ) -> None:
        """שליחת מדיה דרך Cloud API עם retry ו-circuit breaker.

        זורק WhatsAppError בכשלון — הקורא אחראי על טיפול בשגיאות.
        """
        if not media_url:
            raise WhatsAppError(
                message="לא סופק media_url לשליחה",
                details={"phone": PhoneNumberValidator.mask(to)},
            )

        to = self.normalize_phone(to)
        phone_masked = PhoneNumberValidator.mask(to)

        formatted_caption = self.format_text(caption) if caption else None
        client = self._get_client()

        # pywa לא תומך ב-data URIs ישירות — ממירים ל-bytes
        media_content: str | bytes = media_url
        if media_url.startswith("data:"):
            try:
                # פורמט: data:image/jpeg;base64,/9j/4AAQ...
                header, b64_data = media_url.split(",", 1)
                media_content = base64.b64decode(b64_data)
            except Exception:
                logger.warning(
                    "כשלון בפענוח data URI — שולח כמחרוזת",
                    extra_data={"phone": phone_masked},
                )

        async def _send_single() -> None:
            if media_type == "image":
                await client.send_image(
                    to=to,
                    image=media_content,
                    caption=formatted_caption,
                )
            elif media_type == "document":
                await client.send_document(
                    to=to,
                    document=media_content,
                    caption=formatted_caption,
                )
            elif media_type == "video":
                await client.send_video(
                    to=to,
                    video=media_content,
                    caption=formatted_caption,
                )
            else:
                # fallback — שולח כתמונה
                await client.send_image(
                    to=to,
                    image=media_content,
                    caption=formatted_caption,
                )

        async def _send_with_retry() -> None:
            await self._execute_with_retry("send_media", phone_masked, _send_single)

        await self._circuit_breaker.execute(_send_with_retry)
