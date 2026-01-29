"""
Wallet Service - Handles courier wallet and credit operations
"""
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.courier_wallet import CourierWallet
from app.db.models.wallet_ledger import WalletLedger, LedgerEntryType
from app.core.config import settings


class WalletService:
    """Service for managing courier wallets"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_wallet(self, courier_id: int) -> CourierWallet:
        """Get existing wallet or create new one"""
        result = await self.db.execute(
            select(CourierWallet).where(CourierWallet.courier_id == courier_id)
        )
        wallet = result.scalar_one_or_none()

        if not wallet:
            wallet = CourierWallet(
                courier_id=courier_id,
                balance=0.0,
                credit_limit=settings.DEFAULT_CREDIT_LIMIT
            )
            self.db.add(wallet)
            await self.db.commit()
            await self.db.refresh(wallet)

        return wallet

    async def get_balance(self, courier_id: int) -> float:
        """Get current balance for courier"""
        wallet = await self.get_or_create_wallet(courier_id)
        return wallet.balance

    async def check_can_capture(
        self,
        courier_id: int,
        fee: float
    ) -> Tuple[bool, str]:
        """
        Check if courier can capture a delivery based on credit.
        Returns (can_capture, reason_message)
        """
        wallet = await self.get_or_create_wallet(courier_id)
        future_balance = wallet.balance - fee

        if future_balance < wallet.credit_limit:
            return False, f"יתרה לא מספיקה. יתרה נוכחית: {wallet.balance}₪, מגבלת אשראי: {wallet.credit_limit}₪"

        return True, "OK"

    async def debit_for_capture(
        self,
        courier_id: int,
        delivery_id: int,
        fee: float
    ) -> Optional[WalletLedger]:
        """
        Debit wallet for delivery capture.
        Returns ledger entry or None if debit failed.
        Note: This should be called within an atomic transaction.
        """
        wallet = await self.get_or_create_wallet(courier_id)

        # Calculate new balance
        new_balance = wallet.balance - fee

        # Check credit limit
        if new_balance < wallet.credit_limit:
            return None

        # Update wallet balance
        wallet.balance = new_balance

        # Create ledger entry
        ledger_entry = WalletLedger(
            courier_id=courier_id,
            delivery_id=delivery_id,
            entry_type=LedgerEntryType.DELIVERY_FEE_DEBIT,
            amount=-fee,
            balance_after=new_balance,
            description=f"עמלה עבור משלוח #{delivery_id}"
        )
        self.db.add(ledger_entry)

        return ledger_entry

    async def credit_for_delivery(
        self,
        courier_id: int,
        delivery_id: int,
        amount: float
    ) -> WalletLedger:
        """Credit wallet for completed delivery"""
        wallet = await self.get_or_create_wallet(courier_id)

        new_balance = wallet.balance + amount
        wallet.balance = new_balance

        ledger_entry = WalletLedger(
            courier_id=courier_id,
            delivery_id=delivery_id,
            entry_type=LedgerEntryType.DELIVERY_COMPLETED_CREDIT,
            amount=amount,
            balance_after=new_balance,
            description=f"תשלום עבור משלוח #{delivery_id}"
        )
        self.db.add(ledger_entry)
        await self.db.commit()

        return ledger_entry

    async def get_ledger_history(
        self,
        courier_id: int,
        limit: int = 20
    ) -> list:
        """Get transaction history for courier"""
        result = await self.db.execute(
            select(WalletLedger)
            .where(WalletLedger.courier_id == courier_id)
            .order_by(WalletLedger.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
