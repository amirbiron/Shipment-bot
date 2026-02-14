"""
ממשק בסיסי לספק WhatsApp — Dependency Inversion.

כל ספק (WPPConnect, Cloud API/pywa) חייב לממש את הממשק הזה.
שכבת הלוגיקה העסקית תלויה רק בממשק ולא במימוש ספציפי.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class BaseWhatsAppProvider(ABC):
    """
    ממשק אחיד לשליחת הודעות WhatsApp.

    כל מימוש אחראי על:
    - שליחת HTTP / SDK
    - המרת פורמט (HTML → markdown/formatting)
    - retry + circuit breaker
    - נרמול טלפון לפורמט הנדרש ע"י הספק
    """

    # ── שליחת הודעות ──

    @abstractmethod
    async def send_text(
        self,
        to: str,
        text: str,
        keyboard: Optional[list[list[str]]] = None,
    ) -> None:
        """
        שליחת הודעת טקסט.

        הטקסט נשלח כמו שהוא. אם הקלט מכיל HTML, הקורא אחראי
        לקרוא ל-format_text() לפני השליחה.

        Args:
            to: מספר טלפון או מזהה קבוצה (בפורמט הספק).
            text: טקסט ההודעה — נשלח as-is.
            keyboard: רשימת שורות של כפתורים (אופציונלי).
                      כל שורה = רשימת מחרוזות. לדוגמה: [["כן", "לא"], ["ביטול"]]

        Raises:
            WhatsAppError: בכשלון שליחה.
        """

    @abstractmethod
    async def send_media(
        self,
        to: str,
        media_url: str,
        media_type: str = "image",
        caption: Optional[str] = None,
    ) -> None:
        """
        שליחת מדיה (תמונה/מסמך).

        Args:
            to: מספר טלפון או מזהה קבוצה.
            media_url: קישור למדיה — URL ציבורי, data URI (base64), או מזהה ספק.
            media_type: סוג המדיה ("image", "document", "video").
            caption: כיתוב אופציונלי.

        Raises:
            WhatsAppError: בכשלון שליחה או כש-media_url ריק.
        """

    # ── עיצוב טקסט ──

    @abstractmethod
    def format_text(self, html_text: str) -> str:
        """
        המרת טקסט HTML לפורמט הנתמך ע"י הספק.

        לדוגמה:
        - WPPConnect: <b>bold</b> → *bold*
        - Cloud API: עשוי לתמוך בפורמטים אחרים

        Args:
            html_text: טקסט עם תגי HTML פשוטים.

        Returns:
            טקסט בפורמט הספק.
        """

    # ── נרמול טלפון ──

    @abstractmethod
    def normalize_phone(self, phone: str) -> str:
        """
        נרמול מספר טלפון לפורמט E.164 הנדרש ע"י הספק.

        לדוגמה:
        - "0501234567" → "+972501234567"
        - "972501234567" → "+972501234567"

        Args:
            phone: מספר טלפון בפורמט כלשהו.

        Returns:
            מספר טלפון מנורמל.
        """

    # ── זיהוי ספק ──

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """שם הספק לשימוש בלוגים ודיאגנוסטיקה."""
