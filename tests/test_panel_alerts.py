"""
בדיקות התראות בזמן אמת — AlertService, SSE endpoint, היסטוריה וסף ארנק
"""
import json

import pytest
from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.delivery import Delivery, DeliveryStatus
from app.domain.services.alert_service import (
    AlertType,
    publish_alert,
    publish_delivery_created,
    publish_delivery_captured,
    publish_delivery_delivered,
    publish_delivery_cancelled,
    publish_wallet_threshold_alert,
    publish_uncollected_shipment_alert,
    get_alert_history,
    get_wallet_threshold,
    set_wallet_threshold,
    DEFAULT_WALLET_THRESHOLD,
)


class TestAlertService:
    """בדיקות שירות התראות"""

    @pytest.mark.asyncio
    async def test_publish_alert_stores_in_history(self, fake_redis):
        """פרסום התראה שומר בהיסטוריה"""
        await publish_alert(
            station_id=1,
            alert_type=AlertType.DELIVERY_CREATED,
            data={"delivery_id": 42},
        )
        history = await get_alert_history(1)
        assert len(history) == 1
        assert history[0]["type"] == "delivery_created"
        assert history[0]["data"]["delivery_id"] == 42
        assert history[0]["station_id"] == 1

    @pytest.mark.asyncio
    async def test_publish_alert_publishes_to_redis(self, fake_redis):
        """פרסום התראה שולח ל-Redis Pub/Sub"""
        await publish_alert(
            station_id=5,
            alert_type=AlertType.DELIVERY_CAPTURED,
            data={"delivery_id": 10, "courier_name": "יוסי"},
        )
        assert len(fake_redis._published) == 1
        channel, message = fake_redis._published[0]
        assert channel == "station_alerts:5"
        parsed = json.loads(message)
        assert parsed["type"] == "delivery_captured"
        assert parsed["data"]["courier_name"] == "יוסי"

    @pytest.mark.asyncio
    async def test_publish_alert_custom_title(self, fake_redis):
        """כותרת מותאמת עוברת כפי שהועברה"""
        await publish_alert(
            station_id=1,
            alert_type=AlertType.WALLET_THRESHOLD,
            data={},
            title="כותרת מותאמת",
        )
        history = await get_alert_history(1)
        assert history[0]["title"] == "כותרת מותאמת"

    @pytest.mark.asyncio
    async def test_history_returns_newest_first(self, fake_redis):
        """היסטוריה מחזירה מהחדש לישן"""
        await publish_alert(1, AlertType.DELIVERY_CREATED, {"id": 1})
        await publish_alert(1, AlertType.DELIVERY_CAPTURED, {"id": 2})
        await publish_alert(1, AlertType.DELIVERY_DELIVERED, {"id": 3})

        history = await get_alert_history(1)
        assert len(history) == 3
        # LPUSH שומר מהחדש לישן
        assert history[0]["type"] == "delivery_delivered"
        assert history[2]["type"] == "delivery_created"

    @pytest.mark.asyncio
    async def test_history_respects_limit(self, fake_redis):
        """היסטוריה מכבדת מגבלת תוצאות"""
        for i in range(10):
            await publish_alert(1, AlertType.DELIVERY_CREATED, {"id": i})
        history = await get_alert_history(1, limit=3)
        assert len(history) == 3

    @pytest.mark.asyncio
    async def test_history_empty_station(self, fake_redis):
        """היסטוריה ריקה לתחנה ללא התראות"""
        history = await get_alert_history(999)
        assert history == []

    @pytest.mark.asyncio
    async def test_wallet_threshold_default(self, fake_redis):
        """סף ארנק ברירת מחדל הוא 0"""
        threshold = await get_wallet_threshold(1)
        assert threshold == DEFAULT_WALLET_THRESHOLD

    @pytest.mark.asyncio
    async def test_set_and_get_wallet_threshold(self, fake_redis):
        """הגדרה ושליפת סף ארנק"""
        await set_wallet_threshold(1, 500.0)
        threshold = await get_wallet_threshold(1)
        assert threshold == 500.0

    @pytest.mark.asyncio
    async def test_publish_delivery_created_helper(self, fake_redis):
        """פונקציית עזר לפרסום משלוח חדש"""
        await publish_delivery_created(
            station_id=1,
            delivery_id=42,
            pickup_address="רחוב הרצל 1",
            dropoff_address="רחוב בן יהודה 50",
            fee=10.0,
        )
        history = await get_alert_history(1)
        assert len(history) == 1
        assert history[0]["type"] == "delivery_created"
        assert history[0]["data"]["delivery_id"] == 42
        assert history[0]["data"]["pickup_address"] == "רחוב הרצל 1"

    @pytest.mark.asyncio
    async def test_publish_delivery_captured_helper(self, fake_redis):
        """פונקציית עזר לפרסום משלוח נתפס"""
        await publish_delivery_captured(
            station_id=1, delivery_id=42, courier_name="יוסי",
        )
        history = await get_alert_history(1)
        assert history[0]["type"] == "delivery_captured"
        assert history[0]["data"]["courier_name"] == "יוסי"

    @pytest.mark.asyncio
    async def test_publish_delivery_delivered_helper(self, fake_redis):
        """פונקציית עזר לפרסום משלוח נמסר"""
        await publish_delivery_delivered(
            station_id=1, delivery_id=42, courier_name="יוסי",
        )
        history = await get_alert_history(1)
        assert history[0]["type"] == "delivery_delivered"

    @pytest.mark.asyncio
    async def test_publish_delivery_cancelled_helper(self, fake_redis):
        """פונקציית עזר לפרסום משלוח בוטל"""
        await publish_delivery_cancelled(station_id=1, delivery_id=42)
        history = await get_alert_history(1)
        assert history[0]["type"] == "delivery_cancelled"

    @pytest.mark.asyncio
    async def test_publish_wallet_threshold_alert_helper(self, fake_redis):
        """פונקציית עזר לפרסום התראת סף ארנק"""
        await publish_wallet_threshold_alert(
            station_id=1, current_balance=50.0, threshold=100.0,
        )
        history = await get_alert_history(1)
        assert history[0]["type"] == "wallet_threshold"
        assert history[0]["data"]["current_balance"] == 50.0
        assert "50.00" in history[0]["title"]

    @pytest.mark.asyncio
    async def test_publish_uncollected_shipment_alert_helper(self, fake_redis):
        """פונקציית עזר לפרסום משלוח שלא נאסף"""
        await publish_uncollected_shipment_alert(
            station_id=1,
            delivery_id=42,
            hours_open=3.5,
            pickup_address="רחוב הרצל 1",
        )
        history = await get_alert_history(1)
        assert history[0]["type"] == "uncollected_shipment"
        assert history[0]["data"]["hours_open"] == 3.5

    @pytest.mark.asyncio
    async def test_separate_histories_per_station(self, fake_redis):
        """היסטוריה נפרדת לכל תחנה"""
        await publish_alert(1, AlertType.DELIVERY_CREATED, {"id": 1})
        await publish_alert(2, AlertType.DELIVERY_CAPTURED, {"id": 2})

        history_1 = await get_alert_history(1)
        history_2 = await get_alert_history(2)

        assert len(history_1) == 1
        assert len(history_2) == 1
        assert history_1[0]["type"] == "delivery_created"
        assert history_2[0]["type"] == "delivery_captured"


class TestPanelAlertEndpoints:
    """בדיקות endpoints התראות בפאנל"""

    async def _setup_station(self, user_factory, db_session):
        """הכנת תחנה לבדיקות"""
        user = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id, balance=500.0)
        db_session.add(wallet)
        await db_session.commit()
        return user, station

    def _get_token(self, user_id: int, station_id: int) -> str:
        """יצירת token לבדיקות"""
        return create_access_token(user_id, station_id, "station_owner")

    @pytest.mark.asyncio
    async def test_get_alert_history_empty(
        self, test_client, user_factory, db_session,
    ):
        """היסטוריה ריקה מחזירה רשימה ריקה"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.get(
            "/api/panel/alerts/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["alerts"] == []
        assert data["count"] == 0

    @pytest.mark.asyncio
    async def test_get_alert_history_with_alerts(
        self, test_client, user_factory, db_session, fake_redis,
    ):
        """היסטוריה מחזירה התראות שפורסמו"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        # פרסום התראות
        await publish_delivery_created(
            station_id=station.id,
            delivery_id=1,
            pickup_address="רחוב 1",
            dropoff_address="רחוב 2",
            fee=10.0,
        )
        await publish_delivery_captured(
            station_id=station.id, delivery_id=1, courier_name="יוסי",
        )

        response = await test_client.get(
            "/api/panel/alerts/history",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        assert data["alerts"][0]["type"] == "delivery_captured"
        assert data["alerts"][1]["type"] == "delivery_created"

    @pytest.mark.asyncio
    async def test_get_alert_history_with_limit(
        self, test_client, user_factory, db_session, fake_redis,
    ):
        """היסטוריה מכבדת פרמטר limit"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        for i in range(5):
            await publish_delivery_created(
                station_id=station.id,
                delivery_id=i,
                pickup_address="רחוב 1",
                dropoff_address="רחוב 2",
                fee=10.0,
            )

        response = await test_client.get(
            "/api/panel/alerts/history?limit=2",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2

    @pytest.mark.asyncio
    async def test_get_threshold_default(
        self, test_client, user_factory, db_session,
    ):
        """סף ארנק ברירת מחדל"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.get(
            "/api/panel/alerts/threshold",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["threshold"] == 0.0
        assert data["station_id"] == station.id

    @pytest.mark.asyncio
    async def test_update_threshold(
        self, test_client, user_factory, db_session,
    ):
        """עדכון סף ארנק"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.put(
            "/api/panel/alerts/threshold",
            headers={"Authorization": f"Bearer {token}"},
            json={"threshold": 200.0},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "200.00" in data["message"]

        # ולידציה שהסף נשמר
        response = await test_client.get(
            "/api/panel/alerts/threshold",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.json()["threshold"] == 200.0

    @pytest.mark.asyncio
    async def test_update_threshold_zero_disables(
        self, test_client, user_factory, db_session,
    ):
        """עדכון סף ל-0 מבטל התראה"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.put(
            "/api/panel/alerts/threshold",
            headers={"Authorization": f"Bearer {token}"},
            json={"threshold": 0.0},
        )
        assert response.status_code == 200
        assert "בוטלה" in response.json()["message"]

    @pytest.mark.asyncio
    async def test_update_threshold_negative_rejected(
        self, test_client, user_factory, db_session,
    ):
        """סף שלילי נדחה"""
        user, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(user.id, station.id)

        response = await test_client.put(
            "/api/panel/alerts/threshold",
            headers={"Authorization": f"Bearer {token}"},
            json={"threshold": -100.0},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_alert_history_unauthorized(self, test_client):
        """גישה ללא token — 403"""
        response = await test_client.get("/api/panel/alerts/history")
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_sse_stream_invalid_token(self, test_client):
        """SSE עם token לא תקין — 401"""
        response = await test_client.get(
            "/api/panel/alerts/stream?token=invalid-token"
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_sse_stream_missing_token(self, test_client):
        """SSE ללא token — 422"""
        response = await test_client.get("/api/panel/alerts/stream")
        assert response.status_code == 422
