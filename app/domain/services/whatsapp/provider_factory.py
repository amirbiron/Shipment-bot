"""
Provider Factory — יצירת ספק WhatsApp לפי הגדרות.

מספק שלוש נקודות גישה:
- get_whatsapp_provider() — לשליחת הודעות פרטיות (Cloud API במצב hybrid, WPPConnect אחרת)
- get_whatsapp_group_provider() — לשליחת הודעות לקבוצות (תמיד WPPConnect)
- get_whatsapp_admin_provider() — לשליחת הודעות למנהלים (circuit breaker נפרד)
"""
from __future__ import annotations

import threading

from app.core.circuit_breaker import (
    get_whatsapp_circuit_breaker,
    get_whatsapp_admin_circuit_breaker,
    get_whatsapp_cloud_circuit_breaker,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.domain.services.whatsapp.base_provider import BaseWhatsAppProvider

logger = get_logger(__name__)

_provider: BaseWhatsAppProvider | None = None
_group_provider: BaseWhatsAppProvider | None = None
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

    if provider_type == "pywa":
        from app.domain.services.whatsapp.pywa_provider import PyWaProvider

        circuit_breaker = (
            get_whatsapp_admin_circuit_breaker() if is_admin
            else get_whatsapp_cloud_circuit_breaker()
        )
        return PyWaProvider(circuit_breaker=circuit_breaker)

    raise ValueError(f"סוג ספק WhatsApp לא מוכר: {provider_type}")


def get_whatsapp_provider() -> BaseWhatsAppProvider:
    """ספק WhatsApp להודעות פרטיות.

    במצב hybrid: מחזיר PyWa (Cloud API) לתמיכה בכפתורי inline.
    במצב רגיל: מחזיר WPPConnect (תאימות לאחור).
    """
    global _provider
    if _provider is None:
        with _lock:
            if _provider is None:
                if settings.WHATSAPP_HYBRID_MODE:
                    provider_type = "pywa"
                else:
                    provider_type = settings.WHATSAPP_PROVIDER
                _provider = _create_provider(provider_type, is_admin=False)
                logger.info(
                    "ספק WhatsApp אותחל",
                    extra_data={"provider": _provider.provider_name, "context": "private"},
                )
    return _provider


def get_whatsapp_group_provider() -> BaseWhatsAppProvider:
    """ספק WhatsApp להודעות קבוצה — תמיד WPPConnect.

    Cloud API לא תומך בשליחה לקבוצות לא רשמיות,
    לכן גם במצב hybrid נשתמש ב-WPPConnect לקבוצות.
    """
    global _group_provider
    if _group_provider is None:
        with _lock:
            if _group_provider is None:
                _group_provider = _create_provider("wppconnect", is_admin=False)
                logger.info(
                    "ספק WhatsApp אותחל",
                    extra_data={"provider": _group_provider.provider_name, "context": "group"},
                )
    return _group_provider


def get_whatsapp_admin_provider() -> BaseWhatsAppProvider:
    """ספק WhatsApp להודעות מנהלים (circuit breaker נפרד).

    תמיד WPPConnect — הודעות מנהלים נשלחות לקבוצות (@g.us)
    ש-Cloud API לא תומך בהן.
    """
    global _admin_provider
    if _admin_provider is None:
        with _lock:
            if _admin_provider is None:
                _admin_provider = _create_provider("wppconnect", is_admin=True)
                logger.info(
                    "ספק WhatsApp admin אותחל",
                    extra_data={"provider": _admin_provider.provider_name, "context": "admin"},
                )
    return _admin_provider


def reset_providers() -> None:
    """איפוס ספקים — לשימוש בבדיקות בלבד."""
    global _provider, _group_provider, _admin_provider
    with _lock:
        _provider = None
        _group_provider = None
        _admin_provider = None
