"""
Admin Debug Endpoints — endpoints דיאגנוסטיים לניטור ותחזוקה ללא גישה ישירה ל-DB.

שלושה כלים עיקריים:
1. סטטוס circuit breakers (Telegram/WhatsApp)
2. שאילתת הודעות כושלות עם אפשרות retry ידני
3. בדיקת מצב state machine של משתמש (דיבוג משתמשים תקועים)
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.admin_auth import require_admin_api_key
from app.core.circuit_breaker import (
    CircuitBreaker,
    get_telegram_circuit_breaker,
    get_whatsapp_circuit_breaker,
    get_whatsapp_admin_circuit_breaker,
)
from app.core.logging import get_logger
from app.db.database import get_db
from app.db.models.conversation_session import ConversationSession
from app.db.models.outbox_message import MessageStatus, OutboxMessage
from app.db.models.user import User

logger = get_logger(__name__)

router = APIRouter()


# ─── Pydantic models ────────────────────────────────────────────────────────

class CircuitBreakerStatusResponse(BaseModel):
    """סטטוס של circuit breaker בודד"""
    service: str
    state: str = Field(description="closed | open | half_open")
    failure_count: int
    success_count: int
    half_open_calls: int
    retry_after_seconds: float = Field(
        description="שניות עד שניסיון חוזר אפשרי (0 אם לא פתוח)"
    )


class OutboxMessageResponse(BaseModel):
    """הודעת outbox בודדת"""
    id: int
    platform: str
    recipient_id: str
    message_type: str
    status: str
    retry_count: int
    max_retries: int
    last_error: str | None
    next_retry_at: datetime | None
    created_at: datetime | None
    processed_at: datetime | None

    class Config:
        from_attributes = True


class OutboxRetryResponse(BaseModel):
    """תשובה לפעולת retry על הודעה"""
    message_id: int
    previous_status: str
    new_status: str
    retry_count: int


class OutboxSummaryResponse(BaseModel):
    """סיכום כמותי של הודעות outbox"""
    pending: int = 0
    processing: int = 0
    sent: int = 0
    failed: int = 0
    total: int = 0


class UserStateResponse(BaseModel):
    """מצב state machine של משתמש"""
    user_id: int
    user_name: str | None
    user_role: str | None
    platform: str
    current_state: str
    context_data: dict
    updated_at: datetime | None
    last_activity_at: datetime | None


class ForceStateRequest(BaseModel):
    """בקשה לאיפוס state machine של משתמש"""
    platform: str
    new_state: str
    clear_context: bool = True

    @field_validator("platform")
    @classmethod
    def validate_platform(cls, v: str) -> str:
        if v not in ("telegram", "whatsapp"):
            raise ValueError("platform חייב להיות telegram או whatsapp")
        return v


# ─── 1. Circuit Breakers ────────────────────────────────────────────────────

def _cb_to_response(cb: CircuitBreaker) -> CircuitBreakerStatusResponse:
    """המרת circuit breaker למודל תשובה"""
    return CircuitBreakerStatusResponse(
        service=cb.service_name,
        state=cb.state.value,
        failure_count=cb._state.failure_count,
        success_count=cb._state.success_count,
        half_open_calls=cb._state.half_open_calls,
        retry_after_seconds=round(cb.get_retry_after(), 1),
    )


@router.get(
    "/circuit-breakers",
    response_model=list[CircuitBreakerStatusResponse],
    summary="סטטוס circuit breakers",
    description=(
        "מחזיר את המצב הנוכחי של כל circuit breaker רשום (Telegram, WhatsApp, WhatsApp Admin). "
        "שימושי לניטור זמינות שירותים חיצוניים."
    ),
    responses={
        200: {"description": "רשימת סטטוס כל circuit breakers"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API שגוי"},
    },
)
async def get_circuit_breaker_status(
    _: None = Depends(require_admin_api_key),
) -> list[CircuitBreakerStatusResponse]:
    """סטטוס כל ה-circuit breakers הרשומים"""
    # אתחול כל ה-circuit breakers הידועים (כדי שיהיו ב-_instances)
    breakers = [
        get_telegram_circuit_breaker(),
        get_whatsapp_circuit_breaker(),
        get_whatsapp_admin_circuit_breaker(),
    ]
    return [_cb_to_response(cb) for cb in breakers]


# ─── 2. הודעות כושלות + retry ────────────────────────────────────────────────

@router.get(
    "/outbox/summary",
    response_model=OutboxSummaryResponse,
    summary="סיכום כמותי של הודעות outbox",
    description="מחזיר ספירה לפי סטטוס של כל הודעות ה-outbox.",
    responses={
        200: {"description": "סיכום כמותי"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API שגוי"},
    },
)
async def get_outbox_summary(
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> OutboxSummaryResponse:
    """ספירת הודעות outbox לפי סטטוס"""
    result = await db.execute(
        select(OutboxMessage.status, func.count(OutboxMessage.id))
        .group_by(OutboxMessage.status)
    )
    counts: dict[str, int] = {}
    for row_status, count in result.all():
        counts[row_status.value if hasattr(row_status, "value") else str(row_status)] = count

    total = sum(counts.values())
    return OutboxSummaryResponse(
        pending=counts.get("pending", 0),
        processing=counts.get("processing", 0),
        sent=counts.get("sent", 0),
        failed=counts.get("failed", 0),
        total=total,
    )


@router.get(
    "/outbox/messages",
    response_model=list[OutboxMessageResponse],
    summary="שאילתת הודעות outbox",
    description=(
        "שליפת הודעות outbox עם סינון לפי סטטוס. "
        "ברירת מחדל: הודעות כושלות (failed) בלבד."
    ),
    responses={
        200: {"description": "רשימת הודעות מסוננת"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API שגוי"},
    },
)
async def get_outbox_messages(
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
    message_status: Optional[str] = Query(
        default="failed",
        description="סינון לפי סטטוס: pending, processing, sent, failed",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="מספר הודעות מקסימלי"),
) -> list[OutboxMessageResponse]:
    """שליפת הודעות outbox עם סינון אופציונלי לפי סטטוס"""
    query = select(OutboxMessage).order_by(OutboxMessage.created_at.desc()).limit(limit)

    if message_status:
        # ולידציה שהסטטוס תקין
        valid_statuses = {s.value for s in MessageStatus}
        if message_status not in valid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"סטטוס לא תקין. אפשרויות: {', '.join(sorted(valid_statuses))}",
            )
        query = query.where(OutboxMessage.status == MessageStatus(message_status))

    result = await db.execute(query)
    messages = result.scalars().all()

    return [
        OutboxMessageResponse(
            id=msg.id,
            platform=msg.platform.value if hasattr(msg.platform, "value") else str(msg.platform),
            recipient_id=msg.recipient_id,
            message_type=msg.message_type,
            status=msg.status.value if hasattr(msg.status, "value") else str(msg.status),
            retry_count=msg.retry_count,
            max_retries=msg.max_retries,
            last_error=msg.last_error,
            next_retry_at=msg.next_retry_at,
            created_at=msg.created_at,
            processed_at=msg.processed_at,
        )
        for msg in messages
    ]


@router.post(
    "/outbox/messages/{message_id}/retry",
    response_model=OutboxRetryResponse,
    summary="retry ידני להודעה כושלת",
    description=(
        "מאפס את סטטוס ההודעה ל-pending כדי ש-worker ישלח אותה מחדש. "
        "עובד רק על הודעות בסטטוס failed."
    ),
    responses={
        200: {"description": "ההודעה סומנה ל-retry"},
        400: {"description": "ההודעה לא בסטטוס failed"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API שגוי"},
        404: {"description": "הודעה לא נמצאה"},
    },
)
async def retry_outbox_message(
    message_id: int,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> OutboxRetryResponse:
    """retry ידני — מאפס סטטוס ל-pending עבור הודעות כושלות"""
    result = await db.execute(
        select(OutboxMessage).where(OutboxMessage.id == message_id)
    )
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"הודעה {message_id} לא נמצאה",
        )

    previous_status = message.status.value if hasattr(message.status, "value") else str(message.status)

    if message.status != MessageStatus.FAILED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"אפשר לעשות retry רק להודעות בסטטוס failed, הסטטוס הנוכחי: {previous_status}",
        )

    message.status = MessageStatus.PENDING
    message.next_retry_at = None
    await db.commit()

    logger.info(
        "retry ידני להודעת outbox",
        extra_data={"message_id": message_id, "previous_status": previous_status},
    )

    return OutboxRetryResponse(
        message_id=message.id,
        previous_status=previous_status,
        new_status="pending",
        retry_count=message.retry_count,
    )


# ─── 3. State Machine של משתמש ──────────────────────────────────────────────

@router.get(
    "/users/{user_id}/state",
    response_model=UserStateResponse | None,
    summary="בדיקת מצב state machine של משתמש",
    description=(
        "מחזיר את המצב הנוכחי של שיחת המשתמש כולל context data. "
        "שימושי לדיבוג משתמשים שתקועים בזרימה."
    ),
    responses={
        200: {"description": "מצב ה-state machine של המשתמש"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API שגוי"},
        404: {"description": "משתמש או session לא נמצאו"},
    },
)
async def get_user_state(
    user_id: int,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
    platform: Optional[str] = Query(
        default=None,
        description="סינון לפי פלטפורמה (telegram או whatsapp). ברירת מחדל: מחזיר הראשון שנמצא.",
    ),
) -> UserStateResponse:
    """שליפת מצב state machine של משתמש"""
    # שליפת המשתמש
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"משתמש {user_id} לא נמצא",
        )

    # שליפת session
    query = select(ConversationSession).where(ConversationSession.user_id == user_id)
    if platform:
        query = query.where(ConversationSession.platform == platform)

    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"לא נמצא session למשתמש {user_id}"
            + (f" בפלטפורמה {platform}" if platform else ""),
        )

    return UserStateResponse(
        user_id=user.id,
        user_name=user.full_name or user.name,
        user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
        platform=session.platform,
        current_state=session.current_state,
        context_data=session.context_data or {},
        updated_at=session.updated_at,
        last_activity_at=session.last_activity_at,
    )


@router.post(
    "/users/{user_id}/force-state",
    response_model=UserStateResponse,
    summary="איפוס כפוי של state machine",
    description=(
        "מאפס את מצב ה-state machine של משתמש למצב חדש. "
        "שימושי לשחרור משתמשים שתקועים בזרימה שבורה. "
        "פעולה זו עוקפת ולידציית מעברים."
    ),
    responses={
        200: {"description": "ה-state עודכן בהצלחה"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API שגוי"},
        404: {"description": "משתמש או session לא נמצאו"},
    },
)
async def force_user_state(
    user_id: int,
    body: ForceStateRequest,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> UserStateResponse:
    """איפוס כפוי של state machine — עוקף ולידציית מעברים"""
    # שליפת המשתמש
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"משתמש {user_id} לא נמצא",
        )

    # שליפת session
    result = await db.execute(
        select(ConversationSession).where(
            ConversationSession.user_id == user_id,
            ConversationSession.platform == body.platform,
        )
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"לא נמצא session למשתמש {user_id} בפלטפורמה {body.platform}",
        )

    old_state = session.current_state
    session.current_state = body.new_state
    if body.clear_context:
        session.context_data = {}
    await db.commit()
    await db.refresh(session)

    logger.info(
        "force-state בוצע ע\"י אדמין",
        extra_data={
            "user_id": user_id,
            "platform": body.platform,
            "old_state": old_state,
            "new_state": body.new_state,
            "context_cleared": body.clear_context,
        },
    )

    return UserStateResponse(
        user_id=user.id,
        user_name=user.full_name or user.name,
        user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
        platform=session.platform,
        current_state=session.current_state,
        context_data=session.context_data or {},
        updated_at=session.updated_at,
        last_activity_at=session.last_activity_at,
    )
