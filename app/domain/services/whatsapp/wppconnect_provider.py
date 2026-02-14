"""
WPPConnect Provider — מימוש ממשק BaseWhatsAppProvider מעל הגטוויי הקיים.

עוטף את הקריאות ל-WPPConnect Gateway (Node.js) בממשק אחיד,
כולל retry, circuit breaker, והמרת HTML → WhatsApp markdown.
"""
from __future__ import annotations

import asyncio
from typing import Optional

import httpx

from app.core.circuit_breaker import CircuitBreaker
from app.core.config import settings
from app.core.exceptions import WhatsAppError
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator, convert_html_to_whatsapp
from app.domain.services.whatsapp.base_provider import BaseWhatsAppProvider

logger = get_logger(__name__)


class WPPConnectProvider(BaseWhatsAppProvider):
    """
    מימוש ספק WhatsApp מעל WPPConnect Gateway.

    הגטוויי רץ כ-Node.js service ומספק:
    - POST /send — שליחת טקסט + כפתורים
    - POST /send-media — שליחת מדיה (תמונה/מסמך)
    """

    def __init__(self, circuit_breaker: CircuitBreaker) -> None:
        self._circuit_breaker = circuit_breaker
        self._gateway_url = settings.WHATSAPP_GATEWAY_URL
        self._max_retries = settings.WHATSAPP_MAX_RETRIES
        self._transient_status_codes = {
            int(code.strip())
            for code in settings.WHATSAPP_TRANSIENT_STATUS_CODES.split(",")
            if code.strip()
        }

    # ── ממשק ציבורי ──

    @property
    def provider_name(self) -> str:
        return "wppconnect"

    def _should_normalize(self, identifier: str) -> bool:
        """האם צריך לנרמל — רק מספרי טלפון רגילים, לא קבוצות/LID/placeholder."""
        if not identifier:
            return False
        if "@" in identifier:
            return False
        if identifier.startswith("wa:"):
            return False
        return True

    # ── retry helper פנימי ──

    async def _request_with_retry(
        self,
        endpoint: str,
        payload: dict,
        operation_name: str,
    ) -> None:
        """שליחת בקשה לגטוויי עם retry ו-exponential backoff.

        זורק WhatsAppError אם כל הניסיונות נכשלו.
        """
        phone_masked = PhoneNumberValidator.mask(payload.get("phone", ""))

        async with httpx.AsyncClient(timeout=30.0) as client:
            for attempt in range(self._max_retries):
                try:
                    response = await client.post(
                        f"{self._gateway_url}/{endpoint}",
                        json=payload,
                    )
                    if response.status_code == 200:
                        return

                    if (
                        response.status_code in self._transient_status_codes
                        and attempt < self._max_retries - 1
                    ):
                        backoff = 2 ** attempt
                        logger.warning(
                            f"שגיאה זמנית ב-{operation_name}, מנסה שוב",
                            extra_data={
                                "phone": phone_masked,
                                "status_code": response.status_code,
                                "attempt": attempt + 1,
                                "max_retries": self._max_retries,
                                "backoff_seconds": backoff,
                            },
                        )
                        await asyncio.sleep(backoff)
                        continue

                    raise WhatsAppError.from_response(
                        endpoint,
                        response,
                        message=f"gateway /{endpoint} returned status {response.status_code}",
                    )
                except httpx.TimeoutException:
                    if attempt < self._max_retries - 1:
                        backoff = 2 ** attempt
                        logger.warning(
                            f"{operation_name} timeout, מנסה שוב",
                            extra_data={
                                "phone": phone_masked,
                                "attempt": attempt + 1,
                                "backoff_seconds": backoff,
                            },
                        )
                        await asyncio.sleep(backoff)
                        continue
                    raise WhatsAppError(
                        message=f"gateway /{endpoint} timeout after retries",
                        details={"timeout": True, "attempts": self._max_retries},
                    )
                except httpx.RequestError as exc:
                    if attempt < self._max_retries - 1:
                        backoff = 2 ** attempt
                        logger.warning(
                            f"שגיאת רשת ב-{operation_name}, מנסה שוב",
                            extra_data={
                                "phone": phone_masked,
                                "error": str(exc),
                                "attempt": attempt + 1,
                                "backoff_seconds": backoff,
                            },
                        )
                        await asyncio.sleep(backoff)
                        continue
                    raise WhatsAppError(
                        message=f"gateway /{endpoint} network error: {str(exc)}",
                        details={"network_error": True, "attempts": self._max_retries},
                    )

    # ── שליחת הודעות ──

    async def send_text(
        self,
        to: str,
        text: str,
        keyboard: Optional[list[list[str]]] = None,
    ) -> None:
        """שליחת טקסט דרך WPPConnect Gateway עם retry ו-circuit breaker."""
        if self._should_normalize(to):
            to = self.normalize_phone(to)
        formatted_text = self.format_text(text)

        payload = {
            "phone": to,
            "message": formatted_text,
            "keyboard": keyboard,
        }

        async def _send() -> None:
            await self._request_with_retry("send", payload, "שליחת WhatsApp")

        await self._circuit_breaker.execute(_send)

    async def send_media(
        self,
        to: str,
        media_url: str,
        media_type: str = "image",
        caption: Optional[str] = None,
    ) -> bool:
        """שליחת מדיה דרך WPPConnect Gateway עם retry ו-circuit breaker."""
        if not media_url:
            logger.warning(
                "לא סופק media_url לשליחה",
                extra_data={"phone": PhoneNumberValidator.mask(to)},
            )
            return False

        if self._should_normalize(to):
            to = self.normalize_phone(to)

        payload: dict = {
            "phone": to,
            "media_url": media_url,
            "media_type": media_type,
        }
        if caption:
            payload["caption"] = self.format_text(caption)

        async def _send() -> None:
            await self._request_with_retry("send-media", payload, "שליחת מדיה WhatsApp")

        try:
            await self._circuit_breaker.execute(_send)
            return True
        except Exception as exc:
            logger.error(
                "כשלון בשליחת מדיה WhatsApp",
                extra_data={
                    "phone": PhoneNumberValidator.mask(to),
                    "error": str(exc),
                    "provider": self.provider_name,
                },
                exc_info=True,
            )
            return False

    def format_text(self, html_text: str) -> str:
        """המרת HTML → WhatsApp markdown (WPPConnect)."""
        return convert_html_to_whatsapp(html_text)

    def normalize_phone(self, phone: str) -> str:
        """נרמול מספר טלפון לפורמט E.164."""
        if PhoneNumberValidator.validate(phone):
            return PhoneNumberValidator.normalize(phone)
        return phone
