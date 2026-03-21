"""
התראות בזמן אמת — SSE endpoint, היסטוריה, והגדרות סף

SSE (Server-Sent Events) משמש לשידור חד-כיווני מהשרת ללקוח.
הלקוח מתחבר ל-/stream עם JWT token ומקבל אירועים בזמן אמת.
"""
import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.core.auth import TokenPayload, verify_token
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.db.database import AsyncSessionLocal
from app.api.dependencies.auth import get_current_station_owner, validate_station_owner
from app.domain.services.alert_service import (
    get_alert_history,
    get_wallet_threshold,
    set_wallet_threshold,
    channel_name,
    DEFAULT_WALLET_THRESHOLD,
)
from app.api.routes.panel.schemas import ActionResponse

logger = get_logger(__name__)

router = APIRouter()

# פרק זמן בשניות בין heartbeat messages — שומר חיבור SSE פתוח
_SSE_HEARTBEAT_INTERVAL = 30


# ==================== סכמות ====================


class AlertHistoryResponse(BaseModel):
    """תגובת היסטוריית התראות"""
    alerts: list[dict]
    count: int


class WalletThresholdResponse(BaseModel):
    """תגובת סף ארנק"""
    station_id: int
    threshold: float


class UpdateWalletThresholdRequest(BaseModel):
    """בקשת עדכון סף ארנק"""
    threshold: float

    @field_validator("threshold")
    @classmethod
    def validate_threshold(cls, v: float) -> float:
        if v < 0:
            raise ValueError("סף ארנק חייב להיות 0 או יותר")
        return v


# ==================== SSE Endpoint ====================


async def _sse_event_generator(
    station_id: int,
    request: Request,
) -> AsyncGenerator[str, None]:
    """מחולל אירועי SSE — מאזין ל-Redis Pub/Sub ומשדר ללקוח.

    שולח heartbeat כל _SSE_HEARTBEAT_INTERVAL שניות לשמירת החיבור.
    מפסיק כשהלקוח מתנתק.
    """
    redis = await get_redis()
    pubsub = redis.pubsub()
    channel = channel_name(station_id)

    try:
        await pubsub.subscribe(channel)
        logger.info(
            "SSE לקוח התחבר",
            extra_data={"station_id": station_id, "channel": channel},
        )

        while True:
            # בדיקה שהלקוח עדיין מחובר
            if await request.is_disconnected():
                logger.info(
                    "SSE לקוח התנתק",
                    extra_data={"station_id": station_id},
                )
                break

            # ניסיון לקבל הודעה עם timeout — מאפשר heartbeat ובדיקת ניתוק
            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=_SSE_HEARTBEAT_INTERVAL,
            )

            if message and message["type"] == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield f"data: {data}\n\n"
            else:
                # heartbeat — שומר את החיבור פתוח דרך proxies/load balancers
                yield f": heartbeat\n\n"

    except asyncio.CancelledError:
        logger.info(
            "SSE חיבור בוטל",
            extra_data={"station_id": station_id},
        )
    except Exception as e:
        logger.error(
            "SSE שגיאה בשידור",
            extra_data={"station_id": station_id, "error": str(e)},
            exc_info=True,
        )
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:
            pass
        logger.info(
            "SSE משאבים שוחררו",
            extra_data={"station_id": station_id},
        )


@router.get(
    "/stream",
    summary="שידור התראות בזמן אמת (SSE)",
    description=(
        "חיבור SSE (Server-Sent Events) לקבלת התראות בזמן אמת לתחנה.\n\n"
        "**אימות:** יש להעביר JWT token כ-query parameter `token`.\n\n"
        "**סוגי אירועים:**\n"
        "- `delivery_created` — משלוח חדש נוצר\n"
        "- `delivery_captured` — שליח תפס משלוח\n"
        "- `delivery_delivered` — משלוח נמסר\n"
        "- `delivery_cancelled` — משלוח בוטל\n"
        "- `wallet_threshold` — יתרת ארנק מתחת לסף\n"
        "- `uncollected_shipment` — משלוח לא נאסף זמן ממושך\n\n"
        "**דוגמת שימוש (JavaScript):**\n"
        "```js\n"
        "const es = new EventSource('/api/panel/alerts/stream?token=JWT_TOKEN');\n"
        "es.onmessage = (e) => console.log(JSON.parse(e.data));\n"
        "```"
    ),
    responses={
        200: {"description": "חיבור SSE נפתח בהצלחה", "content": {"text/event-stream": {}}},
        401: {"description": "טוקן לא תקין או פג תוקף"},
        403: {"description": "אין הרשאה — משתמש/תחנה לא פעילים או בעלות השתנתה"},
    },
    tags=["Panel - התראות"],
)
async def alerts_stream(
    request: Request,
    token: str = Query(..., description="JWT token לאימות"),
) -> StreamingResponse:
    """חיבור SSE — אימות JWT דרך query param (EventSource לא תומך ב-headers).

    שימוש ב-session קצר-חיים לולידציה — לא Depends(get_db) — כי FastAPI שומר
    yield-dependencies פתוחים עד סיום ה-response. בחיבור SSE שנמשך שעות,
    זה מחזיק חיבור DB מה-pool לכל זמן השידור ועלול לרוקן את ה-pool.
    """
    # אימות JWT — EventSource API לא מאפשר שליחת Authorization header
    token_data = verify_token(token)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="טוקן לא תקין או פג תוקף",
        )

    # ולידציה מלאה עם session קצר-חיים — משוחרר לפני תחילת השידור
    async with AsyncSessionLocal() as db:
        await validate_station_owner(token_data, db)

    station_id = token_data.station_id

    return StreamingResponse(
        _sse_event_generator(station_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx — מבטל buffering לשידור מיידי
        },
    )


# ==================== REST Endpoints ====================


@router.get(
    "/history",
    response_model=AlertHistoryResponse,
    summary="היסטוריית התראות",
    description="מחזיר את ההתראות האחרונות לתחנה, מהחדשה לישנה.",
    responses={
        200: {"description": "רשימת התראות"},
    },
    tags=["Panel - התראות"],
)
async def get_alerts_history(
    auth: TokenPayload = Depends(get_current_station_owner),
    limit: int = Query(50, ge=1, le=100, description="מספר התראות מקסימלי"),
) -> AlertHistoryResponse:
    """היסטוריית התראות — מסנן התראות uncollected_shipment של משלוחים שכבר לא פתוחים.

    שולף יותר מה-limit המבוקש כדי לפצות על התראות שיסוננו,
    ואז חותך לגודל הסופי.
    """
    from sqlalchemy import select
    from app.db.models.delivery import Delivery, DeliveryStatus

    # שליפת מאגר רחב יותר מ-Redis כדי לפצות על סינון
    _OVER_FETCH_FACTOR = 2
    raw_alerts = await get_alert_history(
        auth.station_id, limit=min(limit * _OVER_FETCH_FACTOR, 100)
    )

    # איסוף delivery_ids מהתראות uncollected_shipment
    uncollected_delivery_ids: set[int] = set()
    for alert in raw_alerts:
        if alert.get("type") == "uncollected_shipment":
            did = (alert.get("data") or {}).get("delivery_id")
            if did is not None:
                uncollected_delivery_ids.add(int(did))

    # סינון רק אם יש התראות uncollected_shipment
    if uncollected_delivery_ids:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Delivery.id, Delivery.status).where(
                    Delivery.id.in_(uncollected_delivery_ids)
                )
            )
            status_map: dict[int, DeliveryStatus] = {
                row.id: row.status for row in result
            }

        # דדופליקציה: לכל delivery_id נשמור רק את ההתראה הראשונה (החדשה ביותר,
        # כי raw_alerts ממוינות מהחדשה לישנה ב-Redis LPUSH).
        seen_uncollected: set[int] = set()

        def _keep(a: dict) -> bool:
            if a.get("type") != "uncollected_shipment":
                return True
            did = (a.get("data") or {}).get("delivery_id")
            if did is None:
                return True
            did = int(did)
            # משלוח שלא נמצא ב-DB — נשאיר את ההתראה לבטחון
            if status_map.get(did, DeliveryStatus.OPEN) != DeliveryStatus.OPEN:
                return False
            # דדופליקציה — רק ההתראה החדשה ביותר לכל משלוח
            if did in seen_uncollected:
                return False
            seen_uncollected.add(did)
            return True

        raw_alerts = [a for a in raw_alerts if _keep(a)]

    # חיתוך ל-limit המבוקש
    alerts = raw_alerts[:limit]
    return AlertHistoryResponse(alerts=alerts, count=len(alerts))


@router.get(
    "/threshold",
    response_model=WalletThresholdResponse,
    summary="סף ארנק נוכחי",
    description="מחזיר את סף יתרת הארנק המוגדר לתחנה. כשהיתרה יורדת מתחת לסף, נשלחת התראה.",
    tags=["Panel - התראות"],
)
async def get_threshold(
    auth: TokenPayload = Depends(get_current_station_owner),
) -> WalletThresholdResponse:
    """שליפת סף ארנק"""
    threshold = await get_wallet_threshold(auth.station_id)
    return WalletThresholdResponse(
        station_id=auth.station_id,
        threshold=threshold,
    )


@router.put(
    "/threshold",
    response_model=ActionResponse,
    summary="עדכון סף ארנק",
    description=(
        "הגדרת סף יתרת ארנק לתחנה.\n\n"
        "כשיתרת ארנק התחנה יורדת מתחת לסף שנקבע, תישלח התראה בזמן אמת.\n"
        "ערך 0 מבטל את ההתראה."
    ),
    responses={
        200: {"description": "סף ארנק עודכן בהצלחה"},
        422: {"description": "שגיאת ולידציה"},
    },
    tags=["Panel - התראות"],
)
async def update_threshold(
    body: UpdateWalletThresholdRequest,
    auth: TokenPayload = Depends(get_current_station_owner),
) -> ActionResponse:
    """עדכון סף ארנק"""
    await set_wallet_threshold(auth.station_id, body.threshold)
    logger.info(
        "סף ארנק עודכן דרך הפאנל",
        extra_data={
            "station_id": auth.station_id,
            "user_id": auth.user_id,
            "threshold": body.threshold,
        },
    )
    if body.threshold == 0:
        return ActionResponse(success=True, message="התראת סף ארנק בוטלה")
    return ActionResponse(
        success=True,
        message=f"סף ארנק הוגדר ל-{body.threshold:.2f}₪",
    )
