"""
Alert Service — שירות התראות בזמן אמת לפאנל תחנה

מפרסם אירועים ל-Redis Pub/Sub ושומר היסטוריית התראות ב-Redis.
SSE endpoint מאזין לערוץ ומשדר ללקוח.

סוגי התראות:
- delivery_created: משלוח חדש נוצר בתחנה
- delivery_captured: שליח תפס משלוח
- delivery_delivered: משלוח נמסר
- delivery_cancelled: משלוח בוטל
- wallet_threshold: יתרת ארנק מתחת לסף
- uncollected_shipment: משלוח לא נאסף זמן ממושך
"""
import enum
import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.core.redis_client import get_redis

logger = get_logger(__name__)

# ערוץ Redis Pub/Sub לכל תחנה
_CHANNEL_PREFIX = "station_alerts"
# מפתח Redis להיסטוריית התראות — רשימה מוגבלת
_HISTORY_PREFIX = "station_alert_history"
# מפתח Redis להגדרות סף ארנק
_THRESHOLD_PREFIX = "station_wallet_threshold"
# גודל מקסימלי של היסטוריית התראות לכל תחנה
_MAX_HISTORY_SIZE = 100
# ברירת מחדל לסף ארנק (₪)
DEFAULT_WALLET_THRESHOLD = 0.0
# שעות מקסימום למשלוח שלא נאסף לפני התראה
DEFAULT_UNCOLLECTED_HOURS = 2


class AlertType(str, enum.Enum):
    """סוגי התראות"""
    DELIVERY_CREATED = "delivery_created"
    DELIVERY_CAPTURED = "delivery_captured"
    DELIVERY_DELIVERED = "delivery_delivered"
    DELIVERY_CANCELLED = "delivery_cancelled"
    WALLET_THRESHOLD = "wallet_threshold"
    UNCOLLECTED_SHIPMENT = "uncollected_shipment"


# תיאורים בעברית לכל סוג התראה
_ALERT_DESCRIPTIONS: dict[AlertType, str] = {
    AlertType.DELIVERY_CREATED: "משלוח חדש נוצר",
    AlertType.DELIVERY_CAPTURED: "משלוח נתפס על ידי שליח",
    AlertType.DELIVERY_DELIVERED: "משלוח נמסר",
    AlertType.DELIVERY_CANCELLED: "משלוח בוטל",
    AlertType.WALLET_THRESHOLD: "יתרת ארנק מתחת לסף",
    AlertType.UNCOLLECTED_SHIPMENT: "משלוח לא נאסף זמן ממושך",
}


def channel_name(station_id: int) -> str:
    """שם ערוץ Pub/Sub לתחנה"""
    return f"{_CHANNEL_PREFIX}:{station_id}"


def _history_key(station_id: int) -> str:
    """מפתח Redis להיסטוריית התראות"""
    return f"{_HISTORY_PREFIX}:{station_id}"


def _threshold_key(station_id: int) -> str:
    """מפתח Redis לסף ארנק"""
    return f"{_THRESHOLD_PREFIX}:{station_id}"


async def publish_alert(
    station_id: int,
    alert_type: AlertType,
    data: dict[str, Any],
    title: Optional[str] = None,
) -> None:
    """פרסום התראה ל-Redis Pub/Sub + שמירה בהיסטוריה.

    Args:
        station_id: מזהה התחנה
        alert_type: סוג ההתראה
        data: נתוני ההתראה (משתנים לפי סוג)
        title: כותרת מותאמת (אופציונלי — ברירת מחדל מתוך _ALERT_DESCRIPTIONS)
    """
    try:
        payload = {
            "type": alert_type.value,
            "title": title or _ALERT_DESCRIPTIONS.get(alert_type, alert_type.value),
            "data": data,
            "station_id": station_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        message = json.dumps(payload, ensure_ascii=False, default=str)

        redis = await get_redis()
        # פרסום לערוץ — לקוחות SSE מאזינים
        await redis.publish(channel_name(station_id), message)
        # שמירה בהיסטוריה — LPUSH + LTRIM לגודל מוגבל
        history_key = _history_key(station_id)
        await redis.lpush(history_key, message)
        await redis.ltrim(history_key, 0, _MAX_HISTORY_SIZE - 1)

        logger.info(
            "התראה פורסמה",
            extra_data={
                "station_id": station_id,
                "alert_type": alert_type.value,
            },
        )
    except Exception as e:
        # כשלון בפרסום התראה לא צריך לעצור את הפעולה העסקית
        logger.error(
            "כשלון בפרסום התראה",
            extra_data={
                "station_id": station_id,
                "alert_type": alert_type.value,
                "error": str(e),
            },
            exc_info=True,
        )


async def get_alert_history(
    station_id: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """שליפת היסטוריית התראות אחרונות לתחנה.

    Args:
        station_id: מזהה התחנה
        limit: מספר התראות מקסימלי (ברירת מחדל: 50)

    Returns:
        רשימת התראות מהחדשה לישנה
    """
    try:
        redis = await get_redis()
        raw_items = await redis.lrange(_history_key(station_id), 0, limit - 1)
        return [json.loads(item) for item in raw_items]
    except Exception as e:
        logger.error(
            "כשלון בשליפת היסטוריית התראות",
            extra_data={"station_id": station_id, "error": str(e)},
            exc_info=True,
        )
        return []


async def get_wallet_threshold(station_id: int) -> float:
    """שליפת סף ארנק מוגדר לתחנה — ברירת מחדל 0."""
    try:
        redis = await get_redis()
        value = await redis.get(_threshold_key(station_id))
        return float(value) if value is not None else DEFAULT_WALLET_THRESHOLD
    except Exception as e:
        logger.error(
            "כשלון בשליפת סף ארנק",
            extra_data={"station_id": station_id, "error": str(e)},
            exc_info=True,
        )
        return DEFAULT_WALLET_THRESHOLD


async def set_wallet_threshold(station_id: int, threshold: float) -> None:
    """הגדרת סף ארנק לתחנה.

    Args:
        station_id: מזהה התחנה
        threshold: סף יתרה ב-₪ — כשהיתרה יורדת מתחת, נשלחת התראה

    Raises:
        ValueError: אם הסף שלילי
    """
    if threshold < 0:
        raise ValueError("סף ארנק חייב להיות 0 או יותר")
    try:
        redis = await get_redis()
        await redis.set(_threshold_key(station_id), str(threshold))
        logger.info(
            "סף ארנק עודכן",
            extra_data={"station_id": station_id, "threshold": threshold},
        )
    except Exception as e:
        logger.error(
            "כשלון בעדכון סף ארנק",
            extra_data={"station_id": station_id, "error": str(e)},
            exc_info=True,
        )
        raise


# ==================== פונקציות עזר לפרסום התראות ספציפיות ====================


async def publish_delivery_created(
    station_id: int,
    delivery_id: int,
    pickup_address: str,
    dropoff_address: str,
    fee: float,
) -> None:
    """פרסום התראת משלוח חדש"""
    await publish_alert(
        station_id=station_id,
        alert_type=AlertType.DELIVERY_CREATED,
        data={
            "delivery_id": delivery_id,
            "pickup_address": pickup_address,
            "dropoff_address": dropoff_address,
            "fee": fee,
        },
    )


async def publish_delivery_captured(
    station_id: int,
    delivery_id: int,
    courier_name: str,
) -> None:
    """פרסום התראת משלוח נתפס"""
    await publish_alert(
        station_id=station_id,
        alert_type=AlertType.DELIVERY_CAPTURED,
        data={
            "delivery_id": delivery_id,
            "courier_name": courier_name,
        },
    )


async def publish_delivery_delivered(
    station_id: int,
    delivery_id: int,
    courier_name: str,
) -> None:
    """פרסום התראת משלוח נמסר"""
    await publish_alert(
        station_id=station_id,
        alert_type=AlertType.DELIVERY_DELIVERED,
        data={
            "delivery_id": delivery_id,
            "courier_name": courier_name,
        },
    )


async def publish_delivery_cancelled(
    station_id: int,
    delivery_id: int,
) -> None:
    """פרסום התראת משלוח בוטל"""
    await publish_alert(
        station_id=station_id,
        alert_type=AlertType.DELIVERY_CANCELLED,
        data={"delivery_id": delivery_id},
    )


async def publish_wallet_threshold_alert(
    station_id: int,
    current_balance: float,
    threshold: float,
) -> None:
    """פרסום התראת סף ארנק"""
    await publish_alert(
        station_id=station_id,
        alert_type=AlertType.WALLET_THRESHOLD,
        data={
            "current_balance": current_balance,
            "threshold": threshold,
        },
        title=f"יתרת ארנק ({current_balance:.2f}₪) מתחת לסף ({threshold:.2f}₪)",
    )


async def publish_uncollected_shipment_alert(
    station_id: int,
    delivery_id: int,
    hours_open: float,
    pickup_address: str,
) -> None:
    """פרסום התראת משלוח שלא נאסף"""
    await publish_alert(
        station_id=station_id,
        alert_type=AlertType.UNCOLLECTED_SHIPMENT,
        data={
            "delivery_id": delivery_id,
            "hours_open": round(hours_open, 1),
            "pickup_address": pickup_address,
        },
        title=f"משלוח #{delivery_id} ממתין כבר {hours_open:.1f} שעות",
    )
