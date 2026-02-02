"""
User API Routes
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.db.models.user import User, UserRole

router = APIRouter()


class UserCreate(BaseModel):
    phone_number: str
    name: Optional[str] = None
    role: UserRole = UserRole.SENDER
    platform: str = "whatsapp"
    telegram_chat_id: Optional[str] = None


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


@router.patch("/{user_id}")
async def update_user(
    user_id: int,
    name: Optional[str] = None,
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    """Update user details"""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if name is not None:
        user.name = name
    if is_active is not None:
        user.is_active = is_active

    await db.commit()
    await db.refresh(user)
    return UserResponse.model_validate(user)
