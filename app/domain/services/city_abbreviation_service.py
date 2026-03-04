"""
שירות קיצורי ערים (iDriver) — סשן 5

מנהל מילון קיצורים של ערים ישראליות ופרסור פקודות חיפוש.
פקודות נתמכות:
- "פ ים" — חיפוש ליעד (ירושלים)
- "פ בב ים" — חיפוש ממוצא ליעד (בני ברק → ירושלים)
- "פ א ספר" — חיפוש אזורי (אזור שפרעם)
- "פ ים א טבריה" — ממוצא ליעד אזורי
- "פ מיקום" — חיפוש לפי שיתוף מיקום GPS
"""
from dataclasses import dataclass
from app.core.logging import get_logger
from app.core.validation import TextSanitizer

logger = get_logger(__name__)

# מילון קיצורי ערים — ניתן להרחבה בעתיד (קובץ / DB)
CITY_ABBREVIATIONS: dict[str, str] = {
    # ערים מרכזיות
    "ים": "ירושלים",
    "תא": "תל אביב",
    "חי": "חיפה",
    "בש": "באר שבע",
    "בב": "בני ברק",
    "רג": "רמת גן",
    "פת": "פתח תקווה",
    "רל": "ראשון לציון",
    "חו": "חולון",
    "בי": "בת ים",
    "נת": "נתניה",
    "הר": "הרצליה",
    "רע": "רעננה",
    "כס": "כפר סבא",
    "הד": "הוד השרון",
    "אש": "אשדוד",
    "אק": "אשקלון",
    "גב": "גבעתיים",
    "לד": "לוד",
    "רמ": "רמלה",
    "מד": "מודיעין",
    "עפ": "עפולה",
    "טב": "טבריה",
    "צפ": "צפת",
    "עכ": "עכו",
    "קר": "קריית שמונה",
    "נצ": "נצרת",
    "אל": "אילת",
    "ער": "ערד",
    "דמ": "דימונה",
    "ספר": "שפרעם",
    "אד": "אור יהודה",
    "יב": "יבנה",
    "רח": "רחובות",
    "נס": "נס ציונה",
    "גד": "גדרה",
    "קג": "קריית גת",
    "מא": "מעלה אדומים",
    "ביש": "ביתר עילית",
    "אפ": "אלעד",
    "מב": "מודיעין עילית",
}


@dataclass
class ParsedSearchCommand:
    """תוצאת פרסור פקודת חיפוש"""

    origin: str | None  # עיר מוצא (None = לא צוין)
    destination: str  # עיר יעד
    is_area_search: bool  # חיפוש אזורי
    is_location_search: bool  # חיפוש לפי מיקום GPS


class CityAbbreviationService:
    """שירות קיצורי ערים — פרסור פקודות חיפוש וזיהוי שמות ערים"""

    @staticmethod
    def resolve(abbreviation: str) -> str | None:
        """
        פתרון קיצור עיר לשם מלא.

        Args:
            abbreviation: קיצור העיר (למשל "בב", "ים")

        Returns:
            שם העיר המלא, או None אם לא נמצא
        """
        clean = abbreviation.strip()
        # נסיון קיצור
        result = CITY_ABBREVIATIONS.get(clean)
        if result:
            return result
        # אם הטקסט כבר שם עיר מלא — מחזיר אותו
        if clean in CITY_ABBREVIATIONS.values():
            return clean
        return None

    @staticmethod
    def resolve_or_raw(text: str) -> str:
        """
        פתרון קיצור עיר — אם לא נמצא, מחזיר את הטקסט המקורי כשם עיר.

        Args:
            text: קיצור או שם עיר

        Returns:
            שם העיר (מקיצור או כפי שהתקבל)
        """
        clean = text.strip()
        resolved = CityAbbreviationService.resolve(clean)
        if resolved:
            return resolved
        # ולידציית בטיחות — סינון injection
        is_safe, _pattern = TextSanitizer.check_for_injection(clean)
        if not is_safe:
            logger.warning(
                "ניסיון injection בשם עיר",
                extra_data={"input": clean[:50]},
            )
            return ""
        return TextSanitizer.sanitize(clean, max_length=100)

    @staticmethod
    def is_search_command(text: str) -> bool:
        """
        בדיקה אם הטקסט הוא פקודת חיפוש (מתחיל ב-"פ" או "פ ").

        Args:
            text: טקסט ההודעה

        Returns:
            True אם זו פקודת חיפוש
        """
        stripped = text.strip()
        return stripped == "פ" or stripped.startswith("פ ")

    @staticmethod
    def parse_search_command(text: str) -> ParsedSearchCommand | None:
        """
        פרסור פקודת חיפוש לפרמטרים מובנים.

        פורמטים נתמכים:
        - "פ ים" → יעד ירושלים
        - "פ בב ים" → מבני ברק לירושלים
        - "פ א ספר" → אזור שפרעם
        - "פ ים א טבריה" → מירושלים לאזור טבריה
        - "פ מיקום" → חיפוש לפי מיקום GPS

        Args:
            text: טקסט הפקודה

        Returns:
            ParsedSearchCommand עם הפרמטרים, או None אם הפרסור נכשל
        """
        stripped = text.strip()
        if not CityAbbreviationService.is_search_command(stripped):
            return None

        # הסרת "פ" מההתחלה
        parts = stripped.split()
        if len(parts) < 2:
            # רק "פ" ללא פרמטרים
            return None

        # הסרת התו "פ" (האלמנט הראשון)
        args = parts[1:]

        # חיפוש לפי מיקום
        if args[0] == "מיקום":
            return ParsedSearchCommand(
                origin=None,
                destination="מיקום",
                is_area_search=False,
                is_location_search=True,
            )

        resolve = CityAbbreviationService.resolve_or_raw

        # בדיקת "א" (אזורי) — יכול להופיע בעמדות שונות
        # פורמט 1: "פ א <יעד>" — אזורי ליעד (תמיכה בשם עיר מרובה מילים)
        if args[0] == "א":
            if len(args) < 2:
                return None
            dest_text = " ".join(args[1:])
            destination = (
                CityAbbreviationService.resolve(dest_text)
                if len(args) > 2
                else None
            ) or resolve(args[1])
            if not destination:
                return None
            return ParsedSearchCommand(
                origin=None,
                destination=destination,
                is_area_search=True,
                is_location_search=False,
            )

        # פורמט 2: "פ <מוצא> א <יעד>" — ממוצא ליעד אזורי (תמיכה בשם מרובה מילים)
        # מחפשים את "א" בכל מיקום — תומך גם בעיר מקור מרובת מילים
        # למשל: "פ תל אביב א טב"
        if len(args) >= 3 and "א" in args[1:]:
            area_idx = args.index("א", 1)
            origin_parts = args[:area_idx]
            dest_parts = args[area_idx + 1:]
            if not origin_parts or not dest_parts:
                return None
            origin_text = " ".join(origin_parts)
            origin = CityAbbreviationService.resolve(origin_text) or (
                resolve(origin_parts[0]) if len(origin_parts) == 1 else None
            )
            dest_text = " ".join(dest_parts)
            destination = CityAbbreviationService.resolve(dest_text) or (
                resolve(dest_parts[0]) if len(dest_parts) == 1 else None
            )
            if not origin or not destination:
                return None
            return ParsedSearchCommand(
                origin=origin,
                destination=destination,
                is_area_search=True,
                is_location_search=False,
            )

        # פורמט 3: "פ <מוצא> <יעד>" או "פ <שם עיר מרובה מילים>"
        # תמיכה ב-1+ מילים — נסיונות: יעד שלם → חלוקת מוצא/יעד
        if len(args) >= 2:
            # נסיון ראשון — כל המילים כשם עיר יחיד (למשל "תל אביב")
            joined = " ".join(args)
            joined_resolved = CityAbbreviationService.resolve(joined)
            if joined_resolved:
                return ParsedSearchCommand(
                    origin=None,
                    destination=joined_resolved,
                    is_area_search=False,
                    is_location_search=False,
                )
            # נסיון שני — חלוקה למוצא + יעד בכל נקודת פיצול אפשרית
            for split_at in range(1, len(args)):
                origin_text = " ".join(args[:split_at])
                dest_text = " ".join(args[split_at:])
                origin_resolved = CityAbbreviationService.resolve(origin_text)
                dest_resolved = CityAbbreviationService.resolve(dest_text)
                if origin_resolved and dest_resolved:
                    return ParsedSearchCommand(
                        origin=origin_resolved,
                        destination=dest_resolved,
                        is_area_search=False,
                        is_location_search=False,
                    )
            # fallback — אם בדיוק 2 מילים, מחזיר כ-raw (תואם התנהגות קודמת)
            if len(args) == 2:
                origin = resolve(args[0])
                destination = resolve(args[1])
                if not origin or not destination:
                    return None
                return ParsedSearchCommand(
                    origin=origin,
                    destination=destination,
                    is_area_search=False,
                    is_location_search=False,
                )
            return None

        # פורמט 4: "פ <יעד>" — יעד בלבד
        if len(args) == 1:
            destination = resolve(args[0])
            if not destination:
                return None
            return ParsedSearchCommand(
                origin=None,
                destination=destination,
                is_area_search=False,
                is_location_search=False,
            )

        return None

    @staticmethod
    def get_abbreviations_help() -> str:
        """
        מחזיר הודעת עזרה עם רשימת קיצורים נפוצים.

        Returns:
            טקסט עזרה בפורמט HTML
        """
        # קיצורים נפוצים בלבד — לא את כולם
        common = [
            ("ים", "ירושלים"),
            ("תא", "תל אביב"),
            ("חי", "חיפה"),
            ("בב", "בני ברק"),
            ("בש", "באר שבע"),
            ("פת", "פתח תקווה"),
            ("רל", "ראשון לציון"),
            ("נת", "נתניה"),
            ("אש", "אשדוד"),
            ("מד", "מודיעין"),
        ]
        lines = [f"  {abbr} = {full}" for abbr, full in common]
        return "\n".join(lines)
