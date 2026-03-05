"""
User API Routes
"""
from typing import List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole, ApprovalStatus
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator, NameValidator, TextSanitizer
from app.api.dependencies.admin_auth import require_admin_api_key

logger = get_logger(__name__)

router = APIRouter()


class UserCreate(BaseModel):
    """Schema for creating a new user with validation"""
    phone_number: str
    name: Optional[str] = None
    role: UserRole = UserRole.SENDER
    platform: Literal["whatsapp", "telegram"] = "whatsapp"
    telegram_chat_id: Optional[str] = None

    @field_validator("role", mode="before")
    @classmethod
    def validate_role(cls, v: str | UserRole) -> UserRole:
        """תמיכה בערכי Enum גם בפורמט 'SENDER' וגם 'sender'"""
        if isinstance(v, UserRole):
            return v
        if isinstance(v, str):
            value = v.strip().lower()
            try:
                return UserRole(value)
            except ValueError as e:
                raise ValueError("Invalid role value") from e
        raise ValueError("Invalid role value")

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        """Validate and normalize phone number"""
        if not PhoneNumberValidator.validate(v):
            raise ValueError("Invalid phone number format")
        return PhoneNumberValidator.normalize(v)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        """Validate and sanitize name"""
        if v is None:
            return None
        is_valid, error = NameValidator.validate(v)
        if not is_valid:
            raise ValueError(error)
        return TextSanitizer.sanitize(v.strip(), max_length=100)

    @field_validator("telegram_chat_id")
    @classmethod
    def validate_telegram_id(cls, v: str | None) -> str | None:
        """Validate telegram chat ID (numeric string)"""
        if v is None:
            return None
        # Telegram chat IDs are numeric (can be negative for groups)
        v = v.strip()
        if not v.lstrip("-").isdigit():
            raise ValueError("Invalid Telegram chat ID format")
        return v


class UserUpdate(BaseModel):
    """Schema for updating a user"""
    name: Optional[str] = None
    is_active: Optional[bool] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        """Validate and sanitize name"""
        if v is None:
            return None
        is_valid, error = NameValidator.validate(v)
        if not is_valid:
            raise ValueError(error)
        return TextSanitizer.sanitize(v.strip(), max_length=100)


class UserResponse(BaseModel):
    id: int
    phone_number: str
    name: Optional[str]
    role: UserRole
    platform: str
    is_active: bool

    class Config:
        from_attributes = True

    @field_serializer("role")
    def serialize_role(self, v: UserRole) -> str:
        return v.value


@router.post(
    "/",
    response_model=UserResponse,
    summary="יצירת משתמש חדש",
    description="יצירת משתמש חדש במערכת (בדרך כלל נקרא אוטומטית ע\"י webhook-ים).",
    responses={
        200: {"description": "המשתמש נוצר בהצלחה"},
        400: {"description": "המשתמש כבר קיים"},
        422: {"description": "שגיאת ולידציה בנתוני הבקשה"},
    },
)
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new user"""
    # Check if user already exists
    result = await db.execute(
        select(User).where(User.phone_number == user_data.phone_number)
    )
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")

    user = User(**user_data.model_dump())
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get(
    "/{user_id}",
    response_model=UserResponse,
    summary="קבלת משתמש לפי מזהה",
    description="מחזיר משתמש לפי מזהה (ID).",
    responses={200: {"description": "המשתמש נמצא"}, 404: {"description": "המשתמש לא נמצא"}},
)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get user by ID"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get(
    "/phone/{phone_number}",
    response_model=UserResponse,
    summary="קבלת משתמש לפי מספר טלפון (אדמין בלבד)",
    description=(
        "מחזיר משתמש לפי מספר טלפון. "
        "דורש מפתח API של אדמין למניעת user enumeration."
    ),
    responses={
        200: {"description": "המשתמש נמצא"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API לא תקין"},
        404: {"description": "המשתמש לא נמצא"},
    },
)
async def get_user_by_phone(
    phone_number: str,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
):
    """חיפוש משתמש לפי מספר טלפון — מוגן למניעת enumeration"""
    result = await db.execute(
        select(User).where(User.phone_number == phone_number)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get(
    "/couriers/",
    response_model=List[UserResponse],
    summary="קבלת כל השליחים הפעילים",
    description="מחזיר רשימה של כל המשתמשים עם role=COURIER ו-is_active=true.",
)
async def get_couriers(db: AsyncSession = Depends(get_db)):
    """Get all active couriers"""
    result = await db.execute(
        select(User).where(
            User.role == UserRole.COURIER,
            User.is_active == True
        )
    )
    return list(result.scalars().all())


@router.patch(
    "/{user_id}",
    response_model=UserResponse,
    summary="עדכון משתמש",
    description=(
        "עדכון פרטי משתמש. תומך גם ב-query parameters (לתאימות לאחור) וגם ב-request body (מומלץ). "
        "אם שניהם נשלחים, ה-request body גובר."
    ),
    responses={200: {"description": "המשתמש עודכן"}, 404: {"description": "המשתמש לא נמצא"}},
)
async def update_user(
    user_id: int,
    # תמיכה ב-query parameters לתאימות לאחור
    name: Optional[str] = None,
    is_active: Optional[bool] = None,
    # תמיכה ב-request body (השיטה החדשה והמומלצת)
    user_update: Optional[UserUpdate] = None,
    db: AsyncSession = Depends(get_db)
) -> UserResponse:
    """
    Update user details.

    Supports both query parameters (legacy) and request body (recommended).
    If both are provided, request body takes precedence.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # מיזוג: body עדיף על query params
    final_name = None
    final_is_active = None

    # קודם query params
    if name is not None:
        # ולידציה ל-query param
        is_valid, error = NameValidator.validate(name)
        if not is_valid:
            raise HTTPException(status_code=422, detail=error)
        final_name = TextSanitizer.sanitize(name.strip(), max_length=100)
    if is_active is not None:
        final_is_active = is_active

    # אחר כך body (גובר על query params)
    if user_update is not None:
        if user_update.name is not None:
            final_name = user_update.name
        if user_update.is_active is not None:
            final_is_active = user_update.is_active

    # עדכון המשתמש
    updates = {}
    if final_name is not None:
        user.name = final_name
        updates["name"] = final_name
    if final_is_active is not None:
        user.is_active = final_is_active
        updates["is_active"] = final_is_active

    await db.commit()
    await db.refresh(user)

    logger.info(
        "User updated",
        extra_data={"user_id": user_id, "updates": updates}
    )
    return UserResponse.model_validate(user)


# ==================== ניהול תפקידים ====================


class RoleUpdateRequest(BaseModel):
    """סכמת בקשה לשינוי תפקיד"""
    role: UserRole

    @field_validator("role", mode="before")
    @classmethod
    def validate_role(cls, v: str | UserRole) -> UserRole:
        """תמיכה בערכי Enum גם בפורמט 'ADMIN' וגם 'admin'"""
        if isinstance(v, UserRole):
            return v
        if isinstance(v, str):
            value = v.strip().lower()
            try:
                return UserRole(value)
            except ValueError as e:
                raise ValueError(
                    f"תפקיד לא תקין. ערכים אפשריים: "
                    f"{', '.join(r.value for r in UserRole)}"
                ) from e
        raise ValueError("תפקיד לא תקין")


class RoleUpdateResponse(BaseModel):
    id: int
    phone_number: str
    name: Optional[str]
    role: UserRole
    previous_role: UserRole

    @field_serializer("role")
    def serialize_role(self, v: UserRole) -> str:
        return v.value

    @field_serializer("previous_role")
    def serialize_previous_role(self, v: UserRole) -> str:
        return v.value


async def apply_role_change(
    user: User,
    new_role: UserRole,
    db: AsyncSession,
) -> UserRole:
    """לוגיקה משותפת לשינוי תפקיד — משמשת גם את admin_debug.py.

    מחזירה את התפקיד הקודם.
    """
    previous_role = user.role
    user.role = new_role

    # שליח — הגדרת approval_status אם לא מוגדר
    if new_role == UserRole.COURIER and user.approval_status is None:
        user.approval_status = ApprovalStatus.APPROVED

    await db.commit()
    await db.refresh(user)

    logger.info(
        "תפקיד משתמש שונה",
        extra_data={
            "user_id": user.id,
            "previous_role": previous_role.value,
            "new_role": new_role.value,
        },
    )

    return previous_role


@router.patch(
    "/{user_id}/role",
    response_model=RoleUpdateResponse,
    summary="שינוי תפקיד משתמש (אדמין בלבד)",
    description=(
        "משנה את תפקיד המשתמש. דורש מפתח API של אדמין.\n\n"
        "תפקידים אפשריים: sender, courier, driver, station_owner, admin.\n\n"
        "שימוש: שליחת PATCH עם body: {\"role\": \"admin\"}"
    ),
    responses={
        200: {"description": "התפקיד שונה בהצלחה"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API לא תקין"},
        404: {"description": "המשתמש לא נמצא"},
        422: {"description": "שגיאת ולידציה — תפקיד לא תקין"},
    },
    tags=["Users"],
)
async def update_user_role(
    user_id: int,
    role_update: RoleUpdateRequest,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> RoleUpdateResponse:
    """שינוי תפקיד משתמש — מוגן ב-API key של אדמין."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="המשתמש לא נמצא")

    previous_role = await apply_role_change(user, role_update.role, db)

    return RoleUpdateResponse(
        id=user.id,
        phone_number=user.phone_number,
        name=user.name,
        role=user.role,
        previous_role=previous_role,
    )


@router.get(
    "/telegram/{chat_id}/role",
    response_model=UserResponse,
    summary="קבלת משתמש לפי Telegram Chat ID (אדמין בלבד)",
    description="מחזיר משתמש לפי מזהה צ'אט בטלגרם. שימושי למציאת ה-user_id לפני שינוי תפקיד.",
    responses={
        200: {"description": "המשתמש נמצא"},
        401: {"description": "חסר מפתח API"},
        403: {"description": "מפתח API לא תקין"},
        404: {"description": "המשתמש לא נמצא"},
    },
    tags=["Users"],
)
async def get_user_by_telegram_id(
    chat_id: str,
    _: None = Depends(require_admin_api_key),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """חיפוש משתמש לפי Telegram Chat ID — מוגן ב-API key של אדמין."""
    result = await db.execute(
        select(User).where(User.telegram_chat_id == chat_id)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="המשתמש לא נמצא")
    return user
