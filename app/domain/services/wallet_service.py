"""
Wallet Service - Handles courier wallet and credit operations
"""
from decimal import Decimal
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

    async def get_or_create_wallet(
        self, courier_id: int, for_update: bool = False
    ) -> CourierWallet:
        """Get existing wallet or create new one"""
        query = select(CourierWallet).where(CourierWallet.courier_id == courier_id)
        if for_update:
            query = query.with_for_update()
        result = await self.db.execute(query)
        wallet = result.scalar_one_or_none()

        if not wallet:
            wallet = CourierWallet(
                courier_id=courier_id,
                balance=Decimal("0.00"),
                credit_limit=Decimal(str(settings.DEFAULT_CREDIT_LIMIT))
            )
            self.db.add(wallet)
            await self.db.commit()
            await self.db.refresh(wallet)

        return wallet

    async def get_balance(self, courier_id: int) -> Decimal:
        """קבלת יתרה נוכחית של שליח"""
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
        future_balance = wallet.balance - Decimal(str(fee))

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
        wallet = await self.get_or_create_wallet(courier_id, for_update=True)

        # Calculate new balance
        fee_decimal = Decimal(str(fee))
        new_balance = wallet.balance - fee_decimal

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
            amount=-fee_decimal,
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
        wallet = await self.get_or_create_wallet(courier_id, for_update=True)

        amount_decimal = Decimal(str(amount))
        new_balance = wallet.balance + amount_decimal
        wallet.balance = new_balance

        ledger_entry = WalletLedger(
            courier_id=courier_id,
            delivery_id=delivery_id,
            entry_type=LedgerEntryType.DELIVERY_COMPLETED_CREDIT,
            amount=amount_decimal,
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
