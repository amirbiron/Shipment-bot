"""
WhatsApp Provider Abstraction Layer

שכבת הפשטה לשליחת הודעות WhatsApp.
מאפשרת מעבר בין ספקים (WPPConnect / Cloud API) ללא שינוי בלוגיקה העסקית.
"""
from app.domain.services.whatsapp.base_provider import BaseWhatsAppProvider
from app.domain.services.whatsapp.provider_factory import (
    get_whatsapp_provider,
    get_whatsapp_admin_provider,
)

__all__ = [
    "BaseWhatsAppProvider",
    "get_whatsapp_provider",
    "get_whatsapp_admin_provider",
]
