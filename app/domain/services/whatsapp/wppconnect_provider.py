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

        async def _send_with_retry() -> None:
            async with httpx.AsyncClient(timeout=30.0) as client:
                for attempt in range(self._max_retries):
                    try:
                        response = await client.post(
                            f"{self._gateway_url}/send",
                            json={
                                "phone": to,
                                "message": formatted_text,
                                "keyboard": keyboard,
                            },
                        )
                        if response.status_code == 200:
                            return

                        if (
                            response.status_code in self._transient_status_codes
                            and attempt < self._max_retries - 1
                        ):
                            backoff = 2 ** attempt
                            logger.warning(
                                "שגיאה זמנית בשליחת WhatsApp, מנסה שוב",
                                extra_data={
                                    "phone": PhoneNumberValidator.mask(to),
                                    "status_code": response.status_code,
                                    "attempt": attempt + 1,
                                    "max_retries": self._max_retries,
                                    "backoff_seconds": backoff,
                                },
                            )
                            await asyncio.sleep(backoff)
                            continue

                        raise WhatsAppError.from_response(
                            "send",
                            response,
                            message=f"gateway /send returned status {response.status_code}",
                        )
                    except httpx.TimeoutException:
                        if attempt < self._max_retries - 1:
                            backoff = 2 ** attempt
                            logger.warning(
                                "WhatsApp send timeout, מנסה שוב",
                                extra_data={
                                    "phone": PhoneNumberValidator.mask(to),
                                    "attempt": attempt + 1,
                                    "backoff_seconds": backoff,
                                },
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise WhatsAppError(
                            message="gateway /send timeout after retries",
                            details={"timeout": True, "attempts": self._max_retries},
                        )
                    except httpx.RequestError as exc:
                        if attempt < self._max_retries - 1:
                            backoff = 2 ** attempt
                            logger.warning(
                                "שגיאת רשת בשליחת WhatsApp, מנסה שוב",
                                extra_data={
                                    "phone": PhoneNumberValidator.mask(to),
                                    "error": str(exc),
                                    "attempt": attempt + 1,
                                    "backoff_seconds": backoff,
                                },
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise WhatsAppError(
                            message=f"gateway /send network error: {str(exc)}",
                            details={"network_error": True, "attempts": self._max_retries},
                        )

        await self._circuit_breaker.execute(_send_with_retry)

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

        async def _send_with_retry() -> bool:
            payload: dict = {
                "phone": to,
                "media_url": media_url,
                "media_type": media_type,
            }
            if caption:
                payload["caption"] = self.format_text(caption)

            async with httpx.AsyncClient(timeout=30.0) as client:
                for attempt in range(self._max_retries):
                    try:
                        response = await client.post(
                            f"{self._gateway_url}/send-media",
                            json=payload,
                        )
                        if response.status_code == 200:
                            return True

                        if (
                            response.status_code in self._transient_status_codes
                            and attempt < self._max_retries - 1
                        ):
                            backoff = 2 ** attempt
                            logger.warning(
                                "שגיאה זמנית בשליחת מדיה WhatsApp, מנסה שוב",
                                extra_data={
                                    "phone": PhoneNumberValidator.mask(to),
                                    "status_code": response.status_code,
                                    "attempt": attempt + 1,
                                    "backoff_seconds": backoff,
                                },
                            )
                            await asyncio.sleep(backoff)
                            continue

                        raise WhatsAppError.from_response(
                            "send-media",
                            response,
                            message=f"gateway /send-media returned status {response.status_code}",
                        )
                    except httpx.TimeoutException:
                        if attempt < self._max_retries - 1:
                            backoff = 2 ** attempt
                            logger.warning(
                                "WhatsApp send-media timeout, מנסה שוב",
                                extra_data={
                                    "phone": PhoneNumberValidator.mask(to),
                                    "attempt": attempt + 1,
                                    "backoff_seconds": backoff,
                                },
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise WhatsAppError(
                            message="gateway /send-media timeout after retries",
                            details={"timeout": True, "attempts": self._max_retries},
                        )
                    except httpx.RequestError as exc:
                        if attempt < self._max_retries - 1:
                            backoff = 2 ** attempt
                            logger.warning(
                                "שגיאת רשת בשליחת מדיה WhatsApp, מנסה שוב",
                                extra_data={
                                    "phone": PhoneNumberValidator.mask(to),
                                    "error": str(exc),
                                    "attempt": attempt + 1,
                                    "backoff_seconds": backoff,
                                },
                            )
                            await asyncio.sleep(backoff)
                            continue
                        raise WhatsAppError(
                            message=f"gateway /send-media network error: {str(exc)}",
                            details={"network_error": True, "attempts": self._max_retries},
                        )
            return False  # לא אמור להגיע לכאן

        try:
            return await self._circuit_breaker.execute(_send_with_retry)
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
