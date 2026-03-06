"""
בדיקות יחידה ל-Wallets API Route — נקודות קצה ארנק שליח
"""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.db.models.user import UserRole
from app.db.models.courier_wallet import CourierWallet


@pytest.mark.unit
class TestGetWallet:
    """בדיקות GET /{courier_id}"""

    async def test_get_wallet_existing(
        self, test_client, user_factory, wallet_factory, db_session
    ):
        """שליפת ארנק קיים"""
        courier = await user_factory(
            phone_number="+972502222222", role=UserRole.COURIER
        )
        await wallet_factory(courier_id=courier.id, balance=50.0, credit_limit=-200.0)

        response = await test_client.get(f"/api/wallets/{courier.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["courier_id"] == courier.id
        assert data["balance"] == 50.0
        assert data["credit_limit"] == -200.0

    async def test_get_wallet_creates_if_not_exists(
        self, test_client, user_factory, db_session
    ):
        """שליפת ארנק לשליח ללא ארנק — צריך ליצור חדש"""
        courier = await user_factory(
            phone_number="+972502222222", role=UserRole.COURIER
        )

        response = await test_client.get(f"/api/wallets/{courier.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["courier_id"] == courier.id
        assert data["balance"] == 0.0


@pytest.mark.unit
class TestGetBalance:
    """בדיקות GET /{courier_id}/balance"""

    async def test_get_balance(
        self, test_client, user_factory, wallet_factory, db_session
    ):
        """שליפת יתרה"""
        courier = await user_factory(
            phone_number="+972502222222", role=UserRole.COURIER
        )
        await wallet_factory(courier_id=courier.id, balance=123.45)

        response = await test_client.get(f"/api/wallets/{courier.id}/balance")

        assert response.status_code == 200
        data = response.json()
        assert data["courier_id"] == courier.id
        assert data["balance"] == 123.45


@pytest.mark.unit
class TestGetHistory:
    """בדיקות GET /{courier_id}/history"""

    async def test_get_empty_history(
        self, test_client, user_factory, wallet_factory, db_session
    ):
        """היסטוריה ריקה"""
        courier = await user_factory(
            phone_number="+972502222222", role=UserRole.COURIER
        )
        await wallet_factory(courier_id=courier.id)

        response = await test_client.get(f"/api/wallets/{courier.id}/history")

        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.unit
class TestCanCapture:
    """בדיקות GET /{courier_id}/can-capture"""

    async def test_can_capture_with_balance(
        self, test_client, user_factory, wallet_factory, db_session
    ):
        """שליח עם יתרה מספקת — יכול לתפוס"""
        courier = await user_factory(
            phone_number="+972502222222", role=UserRole.COURIER
        )
        await wallet_factory(courier_id=courier.id, balance=100.0, credit_limit=-500.0)

        response = await test_client.get(
            f"/api/wallets/{courier.id}/can-capture?fee=10.0"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["can_capture"] is True

    async def test_cannot_capture_insufficient(
        self, test_client, user_factory, wallet_factory, db_session
    ):
        """שליח עם יתרה לא מספקת — לא יכול לתפוס"""
        courier = await user_factory(
            phone_number="+972502222222", role=UserRole.COURIER
        )
        await wallet_factory(courier_id=courier.id, balance=0.0, credit_limit=-5.0)

        response = await test_client.get(
            f"/api/wallets/{courier.id}/can-capture?fee=100.0"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["can_capture"] is False
