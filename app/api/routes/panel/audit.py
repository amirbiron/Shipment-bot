"""
לוג ביקורת — צפייה בפעולות מנהלתיות שבוצעו בתחנה

מאפשר לבעלי תחנה לצפות ב"מי שינה מה מ-X ל-Y" — חיוני לתחנות עם מספר בעלים.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TokenPayload
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator
from app.api.dependencies.auth import get_current_station_owner
from app.api.routes.panel.schemas import parse_date_param
from app.db.database import get_db
from app.db.models.audit_log import AuditActionType
from app.db.models.user import User
from app.domain.services.station_service import StationService

logger = get_logger(__name__)

router = APIRouter()


# ==================== סכמות ====================


# תרגום סוגי פעולות לעברית
ACTION_LABELS: dict[str, str] = {
    AuditActionType.OWNER_ADDED.value: "הוספת בעלים",
    AuditActionType.OWNER_REMOVED.value: "הסרת בעלים",
    AuditActionType.DISPATCHER_ADDED.value: "הוספת סדרן",
    AuditActionType.DISPATCHER_REMOVED.value: "הסרת סדרן",
    AuditActionType.BLACKLIST_ADDED.value: "הוספה לרשימה שחורה",
    AuditActionType.BLACKLIST_REMOVED.value: "הסרה מרשימה שחורה",
    AuditActionType.COMMISSION_RATE_UPDATED.value: "עדכון אחוז עמלה",
    AuditActionType.STATION_SETTINGS_UPDATED.value: "עדכון הגדרות תחנה",
    AuditActionType.GROUP_SETTINGS_UPDATED.value: "עדכון הגדרות קבוצות",
    AuditActionType.AUTO_BLOCK_SETTINGS_UPDATED.value: "עדכון חסימה אוטומטית",
    AuditActionType.MANUAL_CHARGE_CREATED.value: "יצירת חיוב ידני",
}


class AuditLogItemResponse(BaseModel):
    """רשומת לוג ביקורת בודדת"""
    id: int
    action: str
    action_label: str
    actor_user_id: int
    actor_name: str
    target_user_id: Optional[int] = None
    target_name: Optional[str] = None
    details: Optional[dict] = None
    created_at: str


class PaginatedAuditLogResponse(BaseModel):
    """תגובת לוג ביקורת עם pagination"""
    items: list[AuditLogItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class AuditActionTypeResponse(BaseModel):
    """סוג פעולה זמין לסינון"""
    value: str
    label: str


# ==================== Endpoints ====================


@router.get(
    "",
    response_model=PaginatedAuditLogResponse,
    summary="לוג ביקורת",
    description=(
        "מחזיר את לוג הביקורת של התחנה — רשימת פעולות מנהלתיות עם פרטי "
        "השינוי: מי ביצע, מה שונה, מ-X ל-Y. "
        "תומך בסינון לפי סוג פעולה, משתמש מבצע, וטווח תאריכים."
    ),
    responses={
        200: {"description": "לוג ביקורת"},
        401: {"description": "טוקן לא תקין"},
    },
    tags=["Panel - לוג ביקורת"],
)
async def get_audit_log(
    action: Optional[str] = Query(None, description="סוג פעולה לסינון"),
    actor_user_id: Optional[int] = Query(None, description="מזהה משתמש מבצע"),
    date_from: Optional[str] = Query(None, description="מתאריך (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="עד תאריך (YYYY-MM-DD)"),
    page: int = Query(1, ge=1, description="מספר עמוד"),
    page_size: int = Query(20, ge=1, le=100, description="גודל עמוד"),
    auth: TokenPayload = Depends(get_current_station_owner),
    db: AsyncSession = Depends(get_db),
) -> PaginatedAuditLogResponse:
    """שליפת לוג ביקורת עם סינון ו-pagination"""
    station_service = StationService(db)

    # ולידציית סוג פעולה
    action_filter: AuditActionType | None = None
    if action is not None:
        try:
            action_filter = AuditActionType(action)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"סוג פעולה לא תקין: {action}",
            )

    # פרסור תאריכים
    dt_from = parse_date_param(date_from, "date_from")
    dt_to = parse_date_param(date_to, "date_to", end_of_day=True)

    entries, total = await station_service.get_audit_logs(
        station_id=auth.station_id,
        action=action_filter,
        actor_user_id=actor_user_id,
        date_from=dt_from,
        date_to=dt_to,
        page=page,
        page_size=page_size,
    )

    # שליפת שמות משתמשים — batch query למניעת N+1
    user_ids: set[int] = set()
    for entry in entries:
        user_ids.add(entry.actor_user_id)
        if entry.target_user_id is not None:
            user_ids.add(entry.target_user_id)

    users_by_id: dict[int, User] = {}
    if user_ids:
        result = await db.execute(
            select(User).where(User.id.in_(list(user_ids)))
        )
        users_by_id = {u.id: u for u in result.scalars().all()}

    items: list[AuditLogItemResponse] = []
    for entry in entries:
        actor = users_by_id.get(entry.actor_user_id)
        target = users_by_id.get(entry.target_user_id) if entry.target_user_id else None

        items.append(AuditLogItemResponse(
            id=entry.id,
            action=entry.action.value if isinstance(entry.action, AuditActionType) else entry.action,
            action_label=ACTION_LABELS.get(
                entry.action.value if isinstance(entry.action, AuditActionType) else entry.action,
                "פעולה לא ידועה",
            ),
            actor_user_id=entry.actor_user_id,
            actor_name=actor.name or actor.full_name or "לא צוין" if actor else "לא צוין",
            target_user_id=entry.target_user_id,
            target_name=target.name or target.full_name or "לא צוין" if target else None,
            details=entry.details,
            created_at=entry.created_at.isoformat() if entry.created_at else "",
        ))

    return PaginatedAuditLogResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=max(1, (total + page_size - 1) // page_size),
    )


@router.get(
    "/actions",
    response_model=list[AuditActionTypeResponse],
    summary="סוגי פעולות זמינים",
    description="מחזיר רשימת סוגי פעולות זמינים לסינון בלוג הביקורת.",
    tags=["Panel - לוג ביקורת"],
)
async def get_audit_action_types(
    auth: TokenPayload = Depends(get_current_station_owner),
) -> list[AuditActionTypeResponse]:
    """רשימת סוגי פעולות לסינון"""
    return [
        AuditActionTypeResponse(value=action.value, label=ACTION_LABELS.get(action.value, action.value))
        for action in AuditActionType
    ]
