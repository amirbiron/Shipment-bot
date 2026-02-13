"""
Wallet API Routes
"""
from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.domain.services.wallet_service import WalletService

router = APIRouter()


class WalletResponse(BaseModel):
    courier_id: int
    balance: float
    credit_limit: float

    class Config:
        from_attributes = True


class LedgerEntryResponse(BaseModel):
    id: int
    entry_type: str
    amount: float
    balance_after: float
    description: str | None

    class Config:
        from_attributes = True


@router.get(
    "/{courier_id}",
    response_model=WalletResponse,
    summary="קבלת ארנק של שליח",
    description="מחזיר את הארנק של השליח, או יוצר חדש אם לא קיים.",
)
async def get_wallet(
    courier_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get wallet for courier"""
    service = WalletService(db)
    wallet = await service.get_or_create_wallet(courier_id)
    return wallet


@router.get(
    "/{courier_id}/balance",
    summary="קבלת יתרה נוכחית",
    description="מחזיר רק את היתרה (balance) של השליח.",
)
async def get_balance(
    courier_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get current balance for courier"""
    service = WalletService(db)
    balance = await service.get_balance(courier_id)
    return {"courier_id": courier_id, "balance": float(balance)}


@router.get(
    "/{courier_id}/history",
    response_model=List[LedgerEntryResponse],
    summary="קבלת היסטוריית תנועות בארנק",
    description="מחזיר רשימת תנועות (ledger) עבור שליח, בסדר יורד, עם הגבלת כמות.",
)
async def get_transaction_history(
    courier_id: int,
    limit: int = 20,
    db: AsyncSession = Depends(get_db)
):
    """Get transaction history for courier"""
    service = WalletService(db)
    history = await service.get_ledger_history(courier_id, limit)
    return history


@router.get(
    "/{courier_id}/can-capture",
    summary="בדיקה אם שליח יכול לתפוס משלוח",
    description="בודק האם לשליח יש מספיק אשראי/יתרה לתפיסת משלוח עם עמלה נתונה.",
)
async def check_can_capture(
    courier_id: int,
    fee: float = 10.0,
    db: AsyncSession = Depends(get_db)
):
    """Check if courier can capture a delivery with given fee"""
    service = WalletService(db)
    can_capture, message = await service.check_can_capture(courier_id, fee)
    return {"can_capture": can_capture, "message": message}
