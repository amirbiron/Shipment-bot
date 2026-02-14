"""
Provider Factory — יצירת ספק WhatsApp לפי הגדרות.

מספק שתי נקודות גישה:
- get_whatsapp_provider() — לשליחת הודעות למשתמשים (circuit breaker רגיל)
- get_whatsapp_admin_provider() — לשליחת הודעות למנהלים (circuit breaker נפרד)
"""
from __future__ import annotations

import threading

from app.core.circuit_breaker import get_whatsapp_circuit_breaker, get_whatsapp_admin_circuit_breaker
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.services.whatsapp.base_provider import BaseWhatsAppProvider

logger = get_logger(__name__)

_provider: BaseWhatsAppProvider | None = None
_admin_provider: BaseWhatsAppProvider | None = None
_lock = threading.Lock()


def _create_provider(provider_type: str, *, is_admin: bool = False) -> BaseWhatsAppProvider:
    """יצירת ספק לפי סוג."""
    if provider_type == "wppconnect":
        from app.domain.services.whatsapp.wppconnect_provider import WPPConnectProvider

        circuit_breaker = (
            get_whatsapp_admin_circuit_breaker() if is_admin
            else get_whatsapp_circuit_breaker()
        )
        return WPPConnectProvider(circuit_breaker=circuit_breaker)

    # בעתיד: תמיכה ב-"pywa" (Cloud API)
    raise ValueError(f"סוג ספק WhatsApp לא מוכר: {provider_type}")


def get_whatsapp_provider() -> BaseWhatsAppProvider:
    """ספק WhatsApp להודעות משתמשים."""
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                provider_type = settings.WHATSAPP_PROVIDER
                _provider = _create_provider(provider_type, is_admin=False)
                logger.info(
                    "ספק WhatsApp אותחל",
                    extra_data={"provider": _provider.provider_name, "context": "user"},
                )
    return _provider


def get_whatsapp_admin_provider() -> BaseWhatsAppProvider:
    """ספק WhatsApp להודעות מנהלים (circuit breaker נפרד)."""
    global _admin_provider
    if _admin_provider is None:
        with _lock:
            if _admin_provider is None:
                provider_type = settings.WHATSAPP_PROVIDER
                _admin_provider = _create_provider(provider_type, is_admin=True)
                logger.info(
                    "ספק WhatsApp admin אותחל",
                    extra_data={"provider": _admin_provider.provider_name, "context": "admin"},
                )
    return _admin_provider


def reset_providers() -> None:
    """איפוס ספקים — לשימוש בבדיקות בלבד."""
    global _provider, _admin_provider
    with _lock:
        _provider = None
        _admin_provider = None
