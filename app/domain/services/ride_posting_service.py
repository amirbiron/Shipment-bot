"""
שירות פרסום נסיעות (iDriver) — סשן 7

מנהל פרסור פקודות פרסום נסיעה חופשית והפצה לקבוצות רלוונטיות.
פורמט פקודה: "בב ים 5 מק 150 ש״ח" (מוצא, יעד, מקומות, מחיר)
"""
import re
from dataclasses import dataclass
from html import escape
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.validation import TextSanitizer
from app.db.models.station import Station
from app.db.models.user import User
from app.domain.services.city_abbreviation_service import CityAbbreviationService

logger = get_logger(__name__)

# regex לזיהוי מחיר בסוף הפקודה: "150 ש״ח" / "150 שח" / "150ש״ח" / "150₪"
# חובה סיומת מטבע — מספר חשוף בסוף לא מספיק כדי למנוע false-positive
_PRICE_PATTERN = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:ש[״\"]?ח|₪)$"
)

# regex לזיהוי מספר מקומות: "5 מק" / "5מק"
# עוגן סוף-מילה (\b) מונע התאמה למילים כמו "מקום" / "מקומות"
_SEATS_PATTERN = re.compile(r"(\d+)\s*מק\b")


@dataclass
class ParsedRidePosting:
    """תוצאת פרסור פקודת פרסום נסיעה"""

    origin: str  # עיר מוצא (שם מלא)
    destination: str  # עיר יעד (שם מלא)
    seats: int  # מספר מקומות פנויים
    price: float  # מחיר בש"ח


class RidePostingService:
    """שירות פרסום נסיעות — פרסור, ולידציה והפצה לקבוצות"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    @staticmethod
    def is_ride_posting(text: str) -> bool:
        """
        בדיקה אם הטקסט הוא פקודת פרסום נסיעה.

        זיהוי לפי תבנית: שתי מילים לפחות + מספר מקומות ("מק") + מחיר.
        למשל: "בב ים 5 מק 150 ש״ח"

        Args:
            text: טקסט ההודעה

        Returns:
            True אם זו פקודת פרסום נסיעה
        """
        stripped = text.strip()
        if not stripped:
            return False

        # חייב להכיל "מק" (מקומות) — זה המזהה המרכזי של פרסום
        if not _SEATS_PATTERN.search(stripped):
            return False

        # חייב להכיל מחיר (מספר בסוף או "ש״ח")
        if not _PRICE_PATTERN.search(stripped):
            return False

        # חייב להתחיל לפחות עם מילה אחת (עיר מוצא או יעד)
        parts = stripped.split()
        if len(parts) < 3:
            return False

        return True

    @staticmethod
    def parse_ride_posting(text: str) -> ParsedRidePosting | None:
        """
        פרסור פקודת פרסום נסיעה.

        פורמט: "<מוצא> <יעד> <מקומות> מק <מחיר> ש״ח"
        למשל: "בב ים 5 מק 150 ש״ח"

        Args:
            text: טקסט הפקודה

        Returns:
            ParsedRidePosting עם הפרטים, או None אם הפרסור נכשל
        """
        stripped = text.strip()

        # ולידציית בטיחות
        is_safe, _pattern = TextSanitizer.check_for_injection(stripped)
        if not is_safe:
            logger.warning(
                "ניסיון injection בפרסום נסיעה",
                extra_data={"input": stripped[:50]},
            )
            return None

        # חילוץ מחיר
        price_match = _PRICE_PATTERN.search(stripped)
        if not price_match:
            return None

        price = float(price_match.group(1))
        if price <= 0:
            return None

        # הסרת המחיר מהטקסט לעיבוד נוסף
        remaining = stripped[:price_match.start()].strip()

        # חילוץ מקומות
        seats_match = _SEATS_PATTERN.search(remaining)
        if not seats_match:
            return None

        seats = int(seats_match.group(1))
        if seats <= 0 or seats > 50:
            return None

        # הסרת מקומות מהטקסט — נשאר עם מוצא + יעד
        remaining = remaining[:seats_match.start()].strip()

        # פיצול למוצא ויעד
        parts = remaining.split()
        if len(parts) < 2:
            return None

        resolve = CityAbbreviationService.resolve_or_raw

        if len(parts) == 2:
            origin = resolve(parts[0])
            destination = resolve(parts[1])
        else:
            # ניסיון לפצל: מוצא + יעד (תמיכה בשמות מרובי מילים)
            # מנסה כל נקודת פיצול ומחפש שניים שמזוהים כערים
            for split_at in range(1, len(parts)):
                origin_text = " ".join(parts[:split_at])
                dest_text = " ".join(parts[split_at:])
                origin_resolved = CityAbbreviationService.resolve(origin_text)
                dest_resolved = CityAbbreviationService.resolve(dest_text)
                if origin_resolved and dest_resolved:
                    return ParsedRidePosting(
                        origin=origin_resolved,
                        destination=dest_resolved,
                        seats=seats,
                        price=price,
                    )
            # fallback — מילה ראשונה = מוצא, השאר = יעד
            origin = resolve(parts[0])
            destination = resolve(" ".join(parts[1:]))

        if not origin or not destination:
            return None

        return ParsedRidePosting(
            origin=origin,
            destination=destination,
            seats=seats,
            price=price,
        )

    async def get_relevant_groups(
        self, origin: str, destination: str
    ) -> list[dict[str, str]]:
        """
        מציאת קבוצות רלוונטיות להפצת נסיעה לפי מוצא ויעד.

        מחפש תחנות פעילות עם קבוצה ציבורית שאזורי השירות שלהן
        חופפים למוצא או ליעד.

        Args:
            origin: שם עיר המוצא
            destination: שם עיר היעד

        Returns:
            רשימת מילונים עם group_chat_id ו-platform
        """
        # שליפת תחנות פעילות עם קבוצה ציבורית
        result = await self._db.execute(
            select(Station).where(
                Station.is_active.is_(True),
                Station.public_group_chat_id.isnot(None),
            )
        )
        stations = result.scalars().all()

        relevant: list[dict[str, str]] = []
        origin_lower = origin.lower().strip()
        destination_lower = destination.lower().strip()

        for station in stations:
            # אם לתחנה אין אזורי שירות מוגדרים — מפיצים לכולן
            if not station.service_areas:
                relevant.append({
                    "group_chat_id": station.public_group_chat_id,
                    "platform": station.public_group_platform or "telegram",
                    "station_name": station.name,
                })
                continue

            # בדיקה אם אחד מאזורי השירות חופף למוצא או ליעד
            areas = station.service_areas
            if isinstance(areas, list):
                area_names = [str(a).lower().strip() for a in areas]
                if origin_lower in area_names or destination_lower in area_names:
                    relevant.append({
                        "group_chat_id": station.public_group_chat_id,
                        "platform": station.public_group_platform or "telegram",
                        "station_name": station.name,
                    })

        logger.info(
            "קבוצות רלוונטיות לפרסום נסיעה",
            extra_data={
                "origin": origin,
                "destination": destination,
                "groups_found": len(relevant),
            },
        )

        return relevant

    @staticmethod
    def format_ride_message(
        posting: ParsedRidePosting,
        driver_name: str,
    ) -> str:
        """
        יצירת הודעת נסיעה מפורמטת להפצה בקבוצות.

        Args:
            posting: פרטי הנסיעה
            driver_name: שם הנהג

        Returns:
            הודעה מפורמטת ב-HTML
        """
        return (
            f"🚗 <b>נסיעה חדשה!</b>\n\n"
            f"📍 <b>מ:</b> {escape(posting.origin)}\n"
            f"📍 <b>אל:</b> {escape(posting.destination)}\n"
            f"👥 <b>מקומות:</b> {posting.seats}\n"
            f"💰 <b>מחיר:</b> {posting.price:.0f} ₪\n"
            f"🧑‍✈️ <b>נהג:</b> {escape(driver_name)}\n"
        )

    async def post_ride(
        self,
        user: User,
        posting: ParsedRidePosting,
    ) -> tuple[bool, str, int, int]:
        """
        פרסום נסיעה — הפצה לקבוצות רלוונטיות.

        Args:
            user: אובייקט המשתמש (נהג)
            posting: פרטי הנסיעה

        Returns:
            tuple של (הצליח, הודעת תוצאה, מספר קבוצות שנשלחו בהצלחה, סה"כ קבוצות רלוונטיות)
        """
        driver_name = user.full_name or user.name or "נהג"
        message = self.format_ride_message(posting, driver_name)

        groups = await self.get_relevant_groups(posting.origin, posting.destination)

        if not groups:
            logger.info(
                "לא נמצאו קבוצות רלוונטיות לפרסום",
                extra_data={
                    "user_id": user.id,
                    "origin": posting.origin,
                    "destination": posting.destination,
                },
            )
            return True, message, 0, 0

        sent_count = 0
        for group in groups:
            try:
                platform = group["platform"]
                group_chat_id = group["group_chat_id"]

                if platform == "telegram":
                    from app.api.webhooks.telegram import send_telegram_message

                    await send_telegram_message(group_chat_id, message)
                    sent_count += 1
                elif platform == "whatsapp":
                    from app.domain.services.whatsapp import (
                        get_whatsapp_group_provider,
                    )

                    wa_provider = get_whatsapp_group_provider()
                    if wa_provider:
                        # הסרת HTML tags לוואטסאפ
                        plain_message = re.sub(r"<[^>]+>", "", message)
                        await wa_provider.send_text(group_chat_id, plain_message)
                        sent_count += 1
                else:
                    logger.warning(
                        "פלטפורמה לא מוכרת לשליחת פרסום",
                        extra_data={"platform": platform},
                    )
            except Exception as e:
                logger.error(
                    "כשלון בשליחת פרסום נסיעה לקבוצה",
                    extra_data={
                        "user_id": user.id,
                        "group_chat_id": group.get("group_chat_id"),
                        "error": str(e),
                    },
                    exc_info=True,
                )

        logger.info(
            "פרסום נסיעה הושלם",
            extra_data={
                "user_id": user.id,
                "origin": posting.origin,
                "destination": posting.destination,
                "groups_total": len(groups),
                "groups_sent": sent_count,
            },
        )

        return True, message, sent_count, len(groups)

    async def notify_matching_drivers(
        self,
        posting: ParsedRidePosting,
        driver_name: str,
        driver_user_ids: list[int],
    ) -> int:
        """
        שליחת הודעה פרטית לנהגים עם חיפושים תואמים למסלול הנסיעה.

        Args:
            posting: פרטי הנסיעה
            driver_name: שם הנהג שפרסם
            driver_user_ids: רשימת מזהי משתמשים לשליחה

        Returns:
            מספר הודעות שנשלחו בהצלחה
        """
        if not driver_user_ids:
            return 0

        from app.db.models.user import User

        message = self.format_ride_message(posting, driver_name)
        notification_text = (
            f"🔔 <b>נסיעה תואמת לחיפוש שלך!</b>\n\n"
            f"{message}"
        )

        sent_count = 0
        for uid in driver_user_ids:
            try:
                result = await self._db.execute(
                    select(User.telegram_id).where(User.id == uid)
                )
                telegram_id = result.scalar_one_or_none()
                if not telegram_id:
                    continue

                from app.api.webhooks.telegram import send_telegram_message

                await send_telegram_message(str(telegram_id), notification_text)
                sent_count += 1
            except Exception as e:
                logger.error(
                    "כשלון בשליחת התראת נסיעה תואמת לנהג",
                    extra_data={"user_id": uid, "error": str(e)},
                    exc_info=True,
                )

        logger.info(
            "התראות נסיעה תואמת נשלחו לנהגים",
            extra_data={
                "driver_name": driver_name,
                "origin": posting.origin,
                "destination": posting.destination,
                "total_drivers": len(driver_user_ids),
                "sent_count": sent_count,
            },
        )
        return sent_count
