"""
שירות מחירון (iDriver) — סשן 7

מנהל שליפת מחירים מומלצים לנסיעות לפי מוצא ויעד.
פקודה: "מחירון בב ים" (מוצא → יעד)
"""
from dataclasses import dataclass
from html import escape

from app.core.logging import get_logger
from app.core.validation import TextSanitizer
from app.domain.services.city_abbreviation_service import CityAbbreviationService

logger = get_logger(__name__)

# טבלת מחירים בסיסית — מחירים מומלצים לפי מרחק/אזורים (בש"ח)
# מפתח: (עיר_מוצא, עיר_יעד) → (מחיר_מינימום, מחיר_מקסימום)
# ערים מנורמלות לשם מלא
_PRICE_TABLE: dict[tuple[str, str], tuple[int, int]] = {
    # תל אביב ↔ ירושלים
    ("תל אביב", "ירושלים"): (120, 180),
    ("ירושלים", "תל אביב"): (120, 180),
    # תל אביב ↔ חיפה
    ("תל אביב", "חיפה"): (200, 300),
    ("חיפה", "תל אביב"): (200, 300),
    # תל אביב ↔ באר שבע
    ("תל אביב", "באר שבע"): (200, 280),
    ("באר שבע", "תל אביב"): (200, 280),
    # בני ברק ↔ ירושלים
    ("בני ברק", "ירושלים"): (100, 160),
    ("ירושלים", "בני ברק"): (100, 160),
    # בני ברק ↔ חיפה
    ("בני ברק", "חיפה"): (200, 280),
    ("חיפה", "בני ברק"): (200, 280),
    # פתח תקווה ↔ ירושלים
    ("פתח תקווה", "ירושלים"): (120, 180),
    ("ירושלים", "פתח תקווה"): (120, 180),
    # ראשון לציון ↔ ירושלים
    ("ראשון לציון", "ירושלים"): (130, 190),
    ("ירושלים", "ראשון לציון"): (130, 190),
    # חיפה ↔ ירושלים
    ("חיפה", "ירושלים"): (280, 400),
    ("ירושלים", "חיפה"): (280, 400),
    # נתניה ↔ תל אביב
    ("נתניה", "תל אביב"): (100, 150),
    ("תל אביב", "נתניה"): (100, 150),
    # אשדוד ↔ תל אביב
    ("אשדוד", "תל אביב"): (120, 180),
    ("תל אביב", "אשדוד"): (120, 180),
    # אשדוד ↔ ירושלים
    ("אשדוד", "ירושלים"): (150, 220),
    ("ירושלים", "אשדוד"): (150, 220),
    # מודיעין ↔ ירושלים
    ("מודיעין", "ירושלים"): (80, 120),
    ("ירושלים", "מודיעין"): (80, 120),
    # מודיעין ↔ תל אביב
    ("מודיעין", "תל אביב"): (80, 130),
    ("תל אביב", "מודיעין"): (80, 130),
    # רמת גן ↔ ירושלים
    ("רמת גן", "ירושלים"): (110, 170),
    ("ירושלים", "רמת גן"): (110, 170),
    # חולון ↔ ירושלים
    ("חולון", "ירושלים"): (120, 180),
    ("ירושלים", "חולון"): (120, 180),
    # הרצליה ↔ ירושלים
    ("הרצליה", "ירושלים"): (130, 200),
    ("ירושלים", "הרצליה"): (130, 200),
    # אילת ↔ תל אביב
    ("אילת", "תל אביב"): (400, 600),
    ("תל אביב", "אילת"): (400, 600),
    # אילת ↔ ירושלים
    ("אילת", "ירושלים"): (380, 550),
    ("ירושלים", "אילת"): (380, 550),
    # אילת ↔ באר שבע
    ("אילת", "באר שבע"): (250, 380),
    ("באר שבע", "אילת"): (250, 380),
    # ביתר עילית ↔ ירושלים
    ("ביתר עילית", "ירושלים"): (50, 80),
    ("ירושלים", "ביתר עילית"): (50, 80),
    # ביתר עילית ↔ בני ברק
    ("ביתר עילית", "בני ברק"): (120, 180),
    ("בני ברק", "ביתר עילית"): (120, 180),
    # מודיעין עילית ↔ ירושלים
    ("מודיעין עילית", "ירושלים"): (70, 110),
    ("ירושלים", "מודיעין עילית"): (70, 110),
    # מודיעין עילית ↔ בני ברק
    ("מודיעין עילית", "בני ברק"): (80, 130),
    ("בני ברק", "מודיעין עילית"): (80, 130),
    # אלעד ↔ ירושלים
    ("אלעד", "ירושלים"): (100, 150),
    ("ירושלים", "אלעד"): (100, 150),
    # אלעד ↔ בני ברק
    ("אלעד", "בני ברק"): (50, 80),
    ("בני ברק", "אלעד"): (50, 80),
}


@dataclass
class PriceEstimate:
    """תוצאת שליפת מחירון"""

    origin: str
    destination: str
    min_price: int
    max_price: int


class PricingService:
    """שירות מחירון — חישוב מחיר מומלץ למסלולים"""

    @staticmethod
    def is_pricing_command(text: str) -> bool:
        """
        בדיקה אם הטקסט הוא פקודת מחירון.

        פורמט: "מחירון <מוצא> <יעד>"
        למשל: "מחירון בב ים"

        Args:
            text: טקסט ההודעה

        Returns:
            True אם זו פקודת מחירון
        """
        stripped = text.strip()
        return stripped.startswith("מחירון ")

    @staticmethod
    def parse_pricing_command(text: str) -> tuple[str, str] | None:
        """
        פרסור פקודת מחירון לעיר מוצא ועיר יעד.

        פורמט: "מחירון <מוצא> <יעד>"

        Args:
            text: טקסט הפקודה

        Returns:
            tuple של (origin, destination), או None אם הפרסור נכשל
        """
        stripped = text.strip()
        if not PricingService.is_pricing_command(stripped):
            return None

        # הסרת "מחירון" מההתחלה
        parts = stripped.split()
        if len(parts) < 3:
            return None

        args = parts[1:]  # בלי "מחירון"

        # ולידציית בטיחות
        raw = " ".join(args)
        is_safe, _pattern = TextSanitizer.check_for_injection(raw)
        if not is_safe:
            logger.warning(
                "ניסיון injection בפקודת מחירון",
                extra_data={"input": raw[:50]},
            )
            return None

        resolve = CityAbbreviationService.resolve_or_raw

        if len(args) == 2:
            origin = resolve(args[0])
            destination = resolve(args[1])
            if not origin or not destination:
                return None
            return origin, destination

        # תמיכה בשמות מרובי מילים — ניסיון כל נקודת פיצול
        for split_at in range(1, len(args)):
            origin_text = " ".join(args[:split_at])
            dest_text = " ".join(args[split_at:])
            origin_resolved = CityAbbreviationService.resolve(origin_text)
            dest_resolved = CityAbbreviationService.resolve(dest_text)
            if origin_resolved and dest_resolved:
                return origin_resolved, dest_resolved

        # fallback — מילה ראשונה = מוצא, השאר = יעד
        if len(args) >= 2:
            origin = resolve(args[0])
            destination = resolve(" ".join(args[1:]))
            if origin and destination:
                return origin, destination

        return None

    @staticmethod
    def get_price_estimate(origin: str, destination: str) -> PriceEstimate | None:
        """
        שליפת מחיר מומלץ למסלול.

        Args:
            origin: שם עיר המוצא
            destination: שם עיר היעד

        Returns:
            PriceEstimate עם טווח מחירים, או None אם לא נמצא מחיר
        """
        key = (origin.strip(), destination.strip())

        price_range = _PRICE_TABLE.get(key)
        if price_range:
            return PriceEstimate(
                origin=origin,
                destination=destination,
                min_price=price_range[0],
                max_price=price_range[1],
            )

        logger.info(
            "לא נמצא מחיר מומלץ למסלול",
            extra_data={"origin": origin, "destination": destination},
        )
        return None

    @staticmethod
    def format_price_response(estimate: PriceEstimate) -> str:
        """
        פורמט תגובת מחירון להצגה לנהג.

        Args:
            estimate: תוצאת שליפת מחיר

        Returns:
            הודעה מפורמטת ב-HTML
        """
        return (
            f"💰 <b>מחירון</b>\n\n"
            f"📍 <b>מ:</b> {escape(estimate.origin)}\n"
            f"📍 <b>אל:</b> {escape(estimate.destination)}\n\n"
            f"💵 <b>טווח מחירים מומלץ:</b>\n"
            f"    {estimate.min_price} - {estimate.max_price} ₪\n\n"
            f"ℹ️ המחיר המומלץ מבוסס על מחירים מקובלים במסלול.\n"
            f"המחיר הסופי נקבע בין הנהג לנוסע."
        )

    @staticmethod
    def format_not_found_response(origin: str, destination: str) -> str:
        """
        פורמט תגובה כשלא נמצא מחיר למסלול.

        Args:
            origin: עיר מוצא
            destination: עיר יעד

        Returns:
            הודעה מפורמטת ב-HTML
        """
        return (
            f"💰 <b>מחירון</b>\n\n"
            f"📍 <b>מ:</b> {escape(origin)}\n"
            f"📍 <b>אל:</b> {escape(destination)}\n\n"
            f"❌ לא נמצא מחיר מומלץ למסלול זה.\n"
            f"נסה מסלול אחר או קבע מחיר לפי שיקול דעתך."
        )
