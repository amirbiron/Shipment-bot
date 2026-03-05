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
from app.db.models.user import User, UserRole, ApprovalStatus

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

    # שליפת session — כשלא סוננה פלטפורמה, מחזירים את ה-session
    # שעודכן לאחרונה (מונע MultipleResultsFound עבור משתמשים דו-פלטפורמיים)
    query = (
        select(ConversationSession)
        .where(ConversationSession.user_id == user_id)
        .order_by(ConversationSession.updated_at.desc())
    )
    if platform:
        query = query.where(ConversationSession.platform == platform)

    result = await db.execute(query)
    session = result.scalars().first()

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


# ---------------------------------------------------------------------------
# ניהול תפקידים — חיפוש משתמש + שינוי תפקיד + דף ווב
# ---------------------------------------------------------------------------

# תפקידים תקינים לשינוי
_VALID_ROLES = [r.value for r in UserRole]


class RoleSearchResponse(BaseModel):
    """תוצאת חיפוש משתמש לניהול תפקידים"""
    user_id: int
    name: str | None
    phone: str | None
    telegram_chat_id: str | None
    role: str
    approval_status: str | None


class RoleChangeRequest(BaseModel):
    """בקשת שינוי תפקיד"""
    role: str = Field(..., description="תפקיד חדש")

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in _VALID_ROLES:
            raise ValueError(f"תפקיד לא תקין: {v}. תפקידים אפשריים: {_VALID_ROLES}")
        return v


class RoleChangeResponse(BaseModel):
    """תוצאת שינוי תפקיד"""
    user_id: int
    name: str | None
    old_role: str
    new_role: str
    approval_status: str | None


@router.get(
    "/roles/search",
    response_model=list[RoleSearchResponse],
    summary="חיפוש משתמשים לניהול תפקידים",
    description="חיפוש לפי שם, טלפון, מזהה טלגרם או user_id.",
    responses={200: {"description": "רשימת משתמשים תואמים"}},
)
async def search_users_for_roles(
    q: str = Query(..., min_length=1, description="מחרוזת חיפוש"),
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> list[RoleSearchResponse]:
    """חיפוש משתמשים לפי שם / טלפון / telegram_chat_id / user_id."""
    from app.core.validation import PhoneNumberValidator

    conditions = []
    # חיפוש לפי user_id מספרי
    if q.isdigit():
        conditions.append(User.id == int(q))
    # חיפוש לפי שם (ILIKE)
    conditions.append(User.full_name.ilike(f"%{q}%"))
    conditions.append(User.name.ilike(f"%{q}%"))
    # חיפוש לפי טלפון
    conditions.append(User.phone_number.ilike(f"%{q}%"))
    # חיפוש לפי telegram_chat_id
    conditions.append(User.telegram_chat_id == q)

    from sqlalchemy import or_
    result = await db.execute(
        select(User).where(or_(*conditions)).limit(20)
    )
    users = result.scalars().all()

    return [
        RoleSearchResponse(
            user_id=u.id,
            name=u.full_name or u.name,
            phone=PhoneNumberValidator.mask(u.phone_number) if u.phone_number else None,
            telegram_chat_id=u.telegram_chat_id,
            role=u.role.value if hasattr(u.role, "value") else str(u.role),
            approval_status=u.approval_status.value if u.approval_status else None,
        )
        for u in users
    ]


@router.patch(
    "/roles/{user_id}",
    response_model=RoleChangeResponse,
    summary="שינוי תפקיד משתמש",
    description="שינוי תפקיד של משתמש לפי user_id.",
    responses={
        200: {"description": "התפקיד שונה בהצלחה"},
        404: {"description": "משתמש לא נמצא"},
    },
)
async def change_user_role(
    user_id: int,
    body: RoleChangeRequest,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> RoleChangeResponse:
    """שינוי תפקיד משתמש."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="משתמש לא נמצא")

    old_role = user.role.value if hasattr(user.role, "value") else str(user.role)
    new_role = UserRole(body.role)
    user.role = new_role

    # שליח חדש — הגדרת APPROVED כדי לדלג על מסך ממתין
    if new_role == UserRole.COURIER:
        user.approval_status = ApprovalStatus.APPROVED
    # אדמין — ניקוי approval_status
    elif new_role == UserRole.ADMIN:
        user.approval_status = None

    await db.commit()

    logger.info(
        "שינוי תפקיד ע\"י אדמין (פאנל)",
        extra_data={
            "user_id": user_id,
            "old_role": old_role,
            "new_role": body.role,
        },
    )

    return RoleChangeResponse(
        user_id=user.id,
        name=user.full_name or user.name,
        old_role=old_role,
        new_role=body.role,
        approval_status=user.approval_status.value if user.approval_status else None,
    )


from fastapi.responses import HTMLResponse


@router.get(
    "/roles",
    response_class=HTMLResponse,
    summary="דף ניהול תפקידים",
    description="ממשק ווב לחיפוש משתמשים ושינוי תפקידים.",
    include_in_schema=False,
)
async def roles_management_page(
    _: None = Depends(require_admin_api_key),
) -> HTMLResponse:
    """דף HTML לניהול תפקידים — מוגן ב-API key."""
    html = """<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ניהול תפקידים</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
         background: #f5f5f5; color: #333; padding: 20px; direction: rtl; }
  .container { max-width: 800px; margin: 0 auto; }
  h1 { margin-bottom: 20px; color: #1a73e8; }
  .card { background: #fff; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,.12);
          padding: 20px; margin-bottom: 16px; }
  .search-box { display: flex; gap: 8px; margin-bottom: 20px; }
  .search-box input { flex: 1; padding: 10px 14px; border: 1px solid #ddd;
                       border-radius: 6px; font-size: 15px; }
  .search-box button { padding: 10px 20px; background: #1a73e8; color: #fff;
                        border: none; border-radius: 6px; cursor: pointer; font-size: 15px; }
  .search-box button:hover { background: #1557b0; }
  .api-key-box { margin-bottom: 16px; }
  .api-key-box input { width: 100%; padding: 10px 14px; border: 1px solid #ddd;
                        border-radius: 6px; font-size: 14px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 10px 12px; text-align: right; border-bottom: 1px solid #eee; }
  th { background: #f8f9fa; font-weight: 600; }
  select { padding: 6px 10px; border: 1px solid #ddd; border-radius: 4px; }
  .btn-change { padding: 6px 14px; background: #34a853; color: #fff; border: none;
                 border-radius: 4px; cursor: pointer; font-size: 13px; }
  .btn-change:hover { background: #2d8e47; }
  .msg { padding: 10px; border-radius: 6px; margin-bottom: 12px; display: none; }
  .msg.success { background: #e6f4ea; color: #137333; display: block; }
  .msg.error { background: #fce8e6; color: #c5221f; display: block; }
  .empty { text-align: center; color: #888; padding: 30px; }
</style>
</head>
<body>
<div class="container">
  <h1>ניהול תפקידים</h1>
  <div class="card">
    <div class="api-key-box">
      <label><b>מפתח API:</b></label>
      <input type="password" id="apiKey" placeholder="הזן X-Admin-API-Key">
    </div>
    <div class="search-box">
      <input type="text" id="searchInput" placeholder="חיפוש לפי שם, טלפון, ID או telegram_chat_id">
      <button onclick="searchUsers()">חיפוש</button>
    </div>
    <div id="message" class="msg"></div>
    <div id="results">
      <p class="empty">הזן מחרוזת חיפוש כדי למצוא משתמשים</p>
    </div>
  </div>
</div>
<script>
const ROLES = ['sender','courier','driver','station_owner','admin'];
const ROLE_LABELS = {
  sender: 'שולח', courier: 'שליח', driver: 'נהג',
  station_owner: 'בעל תחנה', admin: 'אדמין'
};

function getApiKey() {
  return document.getElementById('apiKey').value.trim();
}

function showMsg(text, type) {
  const el = document.getElementById('message');
  el.textContent = text;
  el.className = 'msg ' + type;
  setTimeout(() => { el.className = 'msg'; }, 5000);
}

async function searchUsers() {
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;
  const key = getApiKey();
  if (!key) { showMsg('נדרש מפתח API', 'error'); return; }

  try {
    const basePath = window.location.pathname.replace(/\\/roles\\/?$/, '');
    const resp = await fetch(basePath + '/roles/search?q=' + encodeURIComponent(q), {
      headers: { 'X-Admin-API-Key': key }
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      showMsg(err.detail || 'שגיאה בחיפוש', 'error');
      return;
    }
    const users = await resp.json();
    renderResults(users);
  } catch (e) {
    showMsg('שגיאת רשת: ' + e.message, 'error');
  }
}

function renderResults(users) {
  const el = document.getElementById('results');
  if (!users.length) {
    el.innerHTML = '<p class="empty">לא נמצאו משתמשים</p>';
    return;
  }
  let html = '<table><thead><tr><th>ID</th><th>שם</th><th>טלפון</th><th>Telegram</th><th>תפקיד</th><th>פעולה</th></tr></thead><tbody>';
  for (const u of users) {
    const opts = ROLES.map(r =>
      '<option value="' + r + '"' + (r === u.role ? ' selected' : '') + '>' + (ROLE_LABELS[r] || r) + '</option>'
    ).join('');
    html += '<tr>' +
      '<td>' + u.user_id + '</td>' +
      '<td>' + (u.name || '-') + '</td>' +
      '<td>' + (u.phone || '-') + '</td>' +
      '<td>' + (u.telegram_chat_id || '-') + '</td>' +
      '<td><select id="role-' + u.user_id + '">' + opts + '</select></td>' +
      '<td><button class="btn-change" onclick="changeRole(' + u.user_id + ')">שנה</button></td>' +
      '</tr>';
  }
  html += '</tbody></table>';
  el.innerHTML = html;
}

async function changeRole(userId) {
  const key = getApiKey();
  if (!key) { showMsg('נדרש מפתח API', 'error'); return; }
  const role = document.getElementById('role-' + userId).value;

  try {
    const basePath = window.location.pathname.replace(/\\/roles\\/?$/, '');
    const resp = await fetch(basePath + '/roles/' + userId, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-Admin-API-Key': key },
      body: JSON.stringify({ role: role })
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      showMsg(err.detail || 'שגיאה בשינוי תפקיד', 'error');
      return;
    }
    const data = await resp.json();
    showMsg('תפקיד שונה: ' + (ROLE_LABELS[data.old_role]||data.old_role) + ' -> ' + (ROLE_LABELS[data.new_role]||data.new_role), 'success');
  } catch (e) {
    showMsg('שגיאת רשת: ' + e.message, 'error');
  }
}

document.getElementById('searchInput').addEventListener('keydown', function(e) {
  if (e.key === 'Enter') searchUsers();
});
</script>
</body>
</html>"""
    return HTMLResponse(content=html)
