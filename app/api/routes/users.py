"""
User API Routes
"""
from typing import List, Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator, NameValidator, TextSanitizer

logger = get_logger(__name__)

router = APIRouter()


class UserCreate(BaseModel):
    """Schema for creating a new user with validation"""
    phone_number: str
    name: Optional[str] = None
    role: UserRole = UserRole.SENDER
    platform: Literal["whatsapp", "telegram"] = "whatsapp"
    telegram_chat_id: Optional[str] = None

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


@router.post("/", response_model=UserResponse)
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


@router.get("/{user_id}", response_model=UserResponse)
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


@router.get("/phone/{phone_number}", response_model=UserResponse)
async def get_user_by_phone(
    phone_number: str,
    db: AsyncSession = Depends(get_db)
):
    """Get user by phone number"""
    result = await db.execute(
        select(User).where(User.phone_number == phone_number)
    )
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.get("/couriers/", response_model=List[UserResponse])
async def get_couriers(db: AsyncSession = Depends(get_db)):
    """Get all active couriers"""
    result = await db.execute(
        select(User).where(
            User.role == UserRole.COURIER,
            User.is_active == True
        )
    )
    return list(result.scalars().all())


@router.patch("/{user_id}", response_model=UserResponse)
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
