"""
בדיקות עדכון אחוז עמלה — שירות, פאנל ובוט
"""
from decimal import Decimal

import pytest

from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_wallet import StationWallet
from app.domain.services.station_service import StationService


# ==================== Helpers ====================


async def _create_station_with_owner(db_session, user_factory):
    """יצירת תחנה עם בעלים וארנק — helper משותף לבדיקות"""
    owner = await user_factory(
        phone_number="+972501234567",
        role=UserRole.STATION_OWNER,
    )
    station = Station(name="תחנה", owner_id=owner.id)
    db_session.add(station)
    await db_session.flush()
    wallet = StationWallet(station_id=station.id)
    db_session.add(wallet)
    db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
    await db_session.commit()
    return owner, station, wallet


# ==================== בדיקות שכבת שירות ====================


class TestUpdateCommissionRateService:
    """בדיקות StationService.update_commission_rate"""

    @pytest.mark.asyncio
    async def test_update_commission_rate_success(self, user_factory, db_session):
        """עדכון אחוז עמלה לערך תקין"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        service = StationService(db_session)

        success, msg = await service.update_commission_rate(station.id, 0.08)
        assert success is True
        assert "8%" in msg

        await db_session.refresh(wallet)
        assert wallet.commission_rate == Decimal("0.08")

    @pytest.mark.asyncio
    async def test_update_commission_rate_min_boundary(self, user_factory, db_session):
        """עדכון לערך מינימלי — 6%"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        service = StationService(db_session)

        success, msg = await service.update_commission_rate(station.id, 0.06)
        assert success is True

        await db_session.refresh(wallet)
        assert wallet.commission_rate == Decimal("0.06")

    @pytest.mark.asyncio
    async def test_update_commission_rate_max_boundary(self, user_factory, db_session):
        """עדכון לערך מקסימלי — 12%"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        service = StationService(db_session)

        success, msg = await service.update_commission_rate(station.id, 0.12)
        assert success is True

        await db_session.refresh(wallet)
        assert wallet.commission_rate == Decimal("0.12")

    @pytest.mark.asyncio
    async def test_update_commission_rate_below_min_rejected(self, user_factory, db_session):
        """ערך מתחת למינימום — נדחה"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        service = StationService(db_session)

        success, msg = await service.update_commission_rate(station.id, 0.05)
        assert success is False
        assert "6%" in msg and "12%" in msg

        # ולידציה שהערך לא השתנה
        await db_session.refresh(wallet)
        assert wallet.commission_rate == Decimal("0.10")

    @pytest.mark.asyncio
    async def test_update_commission_rate_above_max_rejected(self, user_factory, db_session):
        """ערך מעל מקסימום — נדחה"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        service = StationService(db_session)

        success, msg = await service.update_commission_rate(station.id, 0.15)
        assert success is False
        assert "6%" in msg and "12%" in msg

        await db_session.refresh(wallet)
        assert wallet.commission_rate == Decimal("0.10")

    @pytest.mark.asyncio
    async def test_update_commission_rate_preserves_balance(self, user_factory, db_session):
        """עדכון עמלה לא משנה את היתרה"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        wallet.balance = Decimal("500.00")
        await db_session.commit()

        service = StationService(db_session)
        await service.update_commission_rate(station.id, 0.07)

        await db_session.refresh(wallet)
        assert wallet.balance == Decimal("500.00")
        assert wallet.commission_rate == Decimal("0.07")


# ==================== בדיקות Panel API ====================


class TestCommissionRatePanelEndpoint:
    """בדיקות PUT /api/panel/wallet/commission-rate"""

    @pytest.mark.asyncio
    async def test_update_commission_rate_via_api(self, test_client, user_factory, db_session):
        """עדכון אחוז עמלה דרך ה-API"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        token = create_access_token(owner.id, station.id, "station_owner")

        response = await test_client.put(
            "/api/panel/wallet/commission-rate",
            json={"commission_rate_percent": 8},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "8%" in data["message"]

    @pytest.mark.asyncio
    async def test_update_commission_rate_reflected_in_wallet(self, test_client, user_factory, db_session):
        """אחרי עדכון, GET /wallet מחזיר את הערך החדש"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        token = create_access_token(owner.id, station.id, "station_owner")

        await test_client.put(
            "/api/panel/wallet/commission-rate",
            json={"commission_rate_percent": 7},
            headers={"Authorization": f"Bearer {token}"},
        )

        response = await test_client.get(
            "/api/panel/wallet",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["commission_rate"] == pytest.approx(0.07)

    @pytest.mark.asyncio
    async def test_update_commission_rate_too_low_rejected(self, test_client, user_factory, db_session):
        """ערך מתחת ל-6% — נדחה ע"י ולידציה של Pydantic"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        token = create_access_token(owner.id, station.id, "station_owner")

        response = await test_client.put(
            "/api/panel/wallet/commission-rate",
            json={"commission_rate_percent": 5},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_commission_rate_too_high_rejected(self, test_client, user_factory, db_session):
        """ערך מעל 12% — נדחה"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        token = create_access_token(owner.id, station.id, "station_owner")

        response = await test_client.put(
            "/api/panel/wallet/commission-rate",
            json={"commission_rate_percent": 13},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_update_commission_rate_boundary_6(self, test_client, user_factory, db_session):
        """ערך מינימלי 6% מתקבל"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        token = create_access_token(owner.id, station.id, "station_owner")

        response = await test_client.put(
            "/api/panel/wallet/commission-rate",
            json={"commission_rate_percent": 6},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_update_commission_rate_boundary_12(self, test_client, user_factory, db_session):
        """ערך מקסימלי 12% מתקבל"""
        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        token = create_access_token(owner.id, station.id, "station_owner")

        response = await test_client.put(
            "/api/panel/wallet/commission-rate",
            json={"commission_rate_percent": 12},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_update_commission_rate_no_auth(self, test_client, user_factory, db_session):
        """בקשה ללא אימות — 403"""
        response = await test_client.put(
            "/api/panel/wallet/commission-rate",
            json={"commission_rate_percent": 8},
        )
        assert response.status_code == 403


# ==================== בדיקות Bot Handler ====================


class TestCommissionRateBotHandler:
    """בדיקות זרימת שינוי עמלה בבוט"""

    @pytest.mark.asyncio
    async def test_wallet_shows_commission_change_button(self, user_factory, db_session):
        """מסך ארנק מציג כפתור שינוי עמלה"""
        from app.state_machine.station_owner_handler import StationOwnerStateHandler
        from app.state_machine.states import StationOwnerState
        from app.state_machine.manager import StateManager

        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        platform = "telegram"

        # מעביר ל-MENU כדי שהניווט ל-ארנק יעבוד
        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, platform, StationOwnerState.MENU.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id, platform)
        response, state = await handler.handle_message(owner, "ארנק")

        assert state == StationOwnerState.VIEW_WALLET.value
        # בדיקה שהכפתור קיים
        all_buttons = [btn for row in response.keyboard for btn in row]
        assert any("עמלה" in btn for btn in all_buttons)

    @pytest.mark.asyncio
    async def test_commission_rate_selection_screen(self, user_factory, db_session):
        """לחיצה על שינוי עמלה — מציג כפתורי בחירה"""
        from app.state_machine.station_owner_handler import StationOwnerStateHandler
        from app.state_machine.states import StationOwnerState
        from app.state_machine.manager import StateManager

        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        platform = "telegram"

        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, platform, StationOwnerState.VIEW_WALLET.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id, platform)
        response, state = await handler.handle_message(owner, "שינוי אחוז עמלה")

        assert state == StationOwnerState.SET_COMMISSION_RATE.value
        # בדיקה שיש כפתורים עם אחוזים
        all_buttons = [btn for row in response.keyboard for btn in row]
        assert "6%" in all_buttons
        assert "12%" in all_buttons

    @pytest.mark.asyncio
    async def test_commission_rate_update_via_bot(self, user_factory, db_session):
        """בחירת אחוז חדש — מעדכן ומחזיר לארנק"""
        from app.state_machine.station_owner_handler import StationOwnerStateHandler
        from app.state_machine.states import StationOwnerState
        from app.state_machine.manager import StateManager

        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        platform = "telegram"

        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, platform, StationOwnerState.SET_COMMISSION_RATE.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id, platform)
        response, state = await handler.handle_message(owner, "8%")

        # אחרי עדכון מוצלח — חוזר למסך ארנק
        assert state == StationOwnerState.VIEW_WALLET.value
        assert "8%" in response.text

        await db_session.refresh(wallet)
        assert wallet.commission_rate == Decimal("0.08")

    @pytest.mark.asyncio
    async def test_commission_rate_invalid_input_stays(self, user_factory, db_session):
        """קלט לא תקין — נשאר במסך בחירה"""
        from app.state_machine.station_owner_handler import StationOwnerStateHandler
        from app.state_machine.states import StationOwnerState
        from app.state_machine.manager import StateManager

        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        platform = "telegram"

        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, platform, StationOwnerState.SET_COMMISSION_RATE.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id, platform)
        response, state = await handler.handle_message(owner, "שלום")

        assert state == StationOwnerState.SET_COMMISSION_RATE.value

    @pytest.mark.asyncio
    async def test_commission_rate_back_returns_to_wallet(self, user_factory, db_session):
        """לחיצה על חזרה — חוזר למסך ארנק"""
        from app.state_machine.station_owner_handler import StationOwnerStateHandler
        from app.state_machine.states import StationOwnerState
        from app.state_machine.manager import StateManager

        owner, station, wallet = await _create_station_with_owner(db_session, user_factory)
        platform = "telegram"

        state_mgr = StateManager(db_session)
        await state_mgr.force_state(
            owner.id, platform, StationOwnerState.SET_COMMISSION_RATE.value, {}
        )

        handler = StationOwnerStateHandler(db_session, station.id, platform)
        response, state = await handler.handle_message(owner, "🔙 חזרה")

        assert state == StationOwnerState.VIEW_WALLET.value
