"""
בדיקות ניהול שולחים בפאנל
"""
import pytest
from decimal import Decimal

from app.core.auth import create_access_token
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_wallet import StationWallet
from app.db.models.delivery import Delivery, DeliveryStatus


class TestPanelSenders:
    """בדיקות שולחים"""

    async def _setup_station(self, user_factory, db_session):
        """הכנת תחנה עם בעלים"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעל תחנה",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()
        return owner, station

    async def _create_sender_with_deliveries(
        self,
        user_factory,
        db_session,
        station_id: int,
        phone: str,
        name: str,
        delivery_count: int = 3,
        delivered_count: int = 1,
    ):
        """יצירת שולח עם משלוחים לבדיקות"""
        sender = await user_factory(
            phone_number=phone,
            name=name,
            role=UserRole.SENDER,
        )

        # יצירת משלוחים שנמסרו
        for i in range(delivered_count):
            d = Delivery(
                sender_id=sender.id,
                station_id=station_id,
                pickup_address=f"רחוב {i+1}, תל אביב",
                dropoff_address=f"רחוב {i+10}, ירושלים",
                status=DeliveryStatus.DELIVERED,
                fee=Decimal("15.00"),
            )
            db_session.add(d)

        # יצירת משלוחים פתוחים
        remaining = delivery_count - delivered_count
        for i in range(remaining):
            d = Delivery(
                sender_id=sender.id,
                station_id=station_id,
                pickup_address=f"רחוב {i+20}, תל אביב",
                dropoff_address=f"רחוב {i+30}, ירושלים",
                status=DeliveryStatus.OPEN,
                fee=Decimal("10.00"),
            )
            db_session.add(d)

        await db_session.commit()
        return sender

    def _get_token(self, user_id: int, station_id: int) -> str:
        return create_access_token(user_id, station_id, "station_owner")

    # ==================== רשימת שולחים ====================

    @pytest.mark.asyncio
    async def test_list_senders_empty(
        self, test_client, user_factory, db_session,
    ):
        """רשימת שולחים ריקה — תחנה בלי משלוחים"""
        owner, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/senders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0
        assert data["page"] == 1

    @pytest.mark.asyncio
    async def test_list_senders_with_data(
        self, test_client, user_factory, db_session,
    ):
        """רשימת שולחים עם נתונים"""
        owner, station = await self._setup_station(user_factory, db_session)

        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="שולח א",
            delivery_count=5, delivered_count=3,
        )
        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972503333333", name="שולח ב",
            delivery_count=2, delivered_count=1,
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            "/api/panel/senders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

        # ברירת מחדל — מיון לפי deliveries_count יורד
        assert data["items"][0]["deliveries_count"] >= data["items"][1]["deliveries_count"]

    @pytest.mark.asyncio
    async def test_list_senders_pagination(
        self, test_client, user_factory, db_session,
    ):
        """pagination — עמוד ראשון עם גודל 1"""
        owner, station = await self._setup_station(user_factory, db_session)

        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="שולח א",
        )
        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972503333333", name="שולח ב",
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            "/api/panel/senders?page=1&page_size=1",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert len(data["items"]) == 1
        assert data["total_pages"] == 2

    @pytest.mark.asyncio
    async def test_list_senders_search(
        self, test_client, user_factory, db_session,
    ):
        """חיפוש לפי שם"""
        owner, station = await self._setup_station(user_factory, db_session)

        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="דוד כהן",
        )
        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972503333333", name="משה לוי",
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            "/api/panel/senders?search=דוד",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "דוד כהן"

    @pytest.mark.asyncio
    async def test_list_senders_invalid_sort(
        self, test_client, user_factory, db_session,
    ):
        """שדה מיון לא תקין"""
        owner, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/senders?sort_by=invalid_field",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_list_senders_phone_masked(
        self, test_client, user_factory, db_session,
    ):
        """מספר טלפון ממוסך בתגובה"""
        owner, station = await self._setup_station(user_factory, db_session)

        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="שולח",
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            "/api/panel/senders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        # הטלפון חייב להיות ממוסך — 4 ספרות אחרונות מוסתרות
        phone_masked = data["items"][0]["phone_masked"]
        assert "****" in phone_masked
        assert "+972502222222" not in phone_masked

    @pytest.mark.asyncio
    async def test_list_senders_excludes_other_stations(
        self, test_client, user_factory, db_session,
    ):
        """שולח של תחנה אחרת לא מוצג"""
        owner, station = await self._setup_station(user_factory, db_session)

        # תחנה נוספת
        other_station = Station(name="תחנה אחרת", owner_id=owner.id)
        db_session.add(other_station)
        await db_session.flush()
        other_wallet = StationWallet(station_id=other_station.id)
        db_session.add(other_wallet)
        await db_session.commit()

        # שולח בתחנה האחרת
        sender = await user_factory(
            phone_number="+972509999999",
            name="שולח אחר",
            role=UserRole.SENDER,
        )
        d = Delivery(
            sender_id=sender.id,
            station_id=other_station.id,
            pickup_address="א",
            dropoff_address="ב",
            status=DeliveryStatus.OPEN,
            fee=Decimal("10.00"),
        )
        db_session.add(d)
        await db_session.commit()

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            "/api/panel/senders",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["total"] == 0

    # ==================== שולחים מובילים ====================

    @pytest.mark.asyncio
    async def test_top_senders(
        self, test_client, user_factory, db_session,
    ):
        """שולחים מובילים — ממוינים לפי משלוחים שנמסרו"""
        owner, station = await self._setup_station(user_factory, db_session)

        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="שולח קטן",
            delivery_count=2, delivered_count=1,
        )
        await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972503333333", name="שולח גדול",
            delivery_count=5, delivered_count=4,
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            "/api/panel/senders/top?limit=10",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        # שולח גדול צריך להיות ראשון
        assert data[0]["delivered_count"] >= data[1]["delivered_count"]
        assert data[0]["name"] == "שולח גדול"

    @pytest.mark.asyncio
    async def test_top_senders_empty(
        self, test_client, user_factory, db_session,
    ):
        """שולחים מובילים — ריק"""
        owner, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/senders/top",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json() == []

    # ==================== פרטי שולח ====================

    @pytest.mark.asyncio
    async def test_get_sender_detail(
        self, test_client, user_factory, db_session,
    ):
        """פרטי שולח — שולח קיים בתחנה"""
        owner, station = await self._setup_station(user_factory, db_session)

        sender = await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="שולח לדוגמה",
            delivery_count=5, delivered_count=3,
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            f"/api/panel/senders/{sender.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == sender.id
        assert data["name"] == "שולח לדוגמה"
        assert data["deliveries_count"] == 5
        assert data["delivered_count"] == 3
        assert data["active_deliveries_count"] == 2
        assert data["total_volume"] == 45.0  # 3 × 15.00
        assert "****" in data["phone_masked"]

    @pytest.mark.asyncio
    async def test_get_sender_detail_not_found(
        self, test_client, user_factory, db_session,
    ):
        """פרטי שולח — שולח לא קיים"""
        owner, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/senders/99999",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_get_sender_detail_wrong_station(
        self, test_client, user_factory, db_session,
    ):
        """פרטי שולח — שולח של תחנה אחרת מחזיר 404"""
        owner, station = await self._setup_station(user_factory, db_session)

        # תחנה נוספת
        other_station = Station(name="תחנה אחרת", owner_id=owner.id)
        db_session.add(other_station)
        await db_session.flush()
        other_wallet = StationWallet(station_id=other_station.id)
        db_session.add(other_wallet)
        await db_session.commit()

        sender = await self._create_sender_with_deliveries(
            user_factory, db_session, other_station.id,
            phone="+972509999999", name="שולח אחר",
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            f"/api/panel/senders/{sender.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

    # ==================== משלוחי שולח ====================

    @pytest.mark.asyncio
    async def test_sender_deliveries(
        self, test_client, user_factory, db_session,
    ):
        """משלוחי שולח — רשימה עם pagination"""
        owner, station = await self._setup_station(user_factory, db_session)

        sender = await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="שולח",
            delivery_count=5, delivered_count=2,
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            f"/api/panel/senders/{sender.id}/deliveries",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 5
        assert len(data["items"]) == 5

    @pytest.mark.asyncio
    async def test_sender_deliveries_status_filter(
        self, test_client, user_factory, db_session,
    ):
        """סינון משלוחי שולח לפי סטטוס"""
        owner, station = await self._setup_station(user_factory, db_session)

        sender = await self._create_sender_with_deliveries(
            user_factory, db_session, station.id,
            phone="+972502222222", name="שולח",
            delivery_count=5, delivered_count=2,
        )

        token = self._get_token(owner.id, station.id)
        response = await test_client.get(
            f"/api/panel/senders/{sender.id}/deliveries?status_filter=delivered",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        for item in data["items"]:
            assert item["status"] == "delivered"

    @pytest.mark.asyncio
    async def test_sender_deliveries_invalid_status(
        self, test_client, user_factory, db_session,
    ):
        """סטטוס לא תקין מחזיר 400"""
        owner, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/senders/1/deliveries?status_filter=invalid",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_sender_deliveries_empty(
        self, test_client, user_factory, db_session,
    ):
        """משלוחי שולח — ללא משלוחים (שולח לא קיים בתחנה)"""
        owner, station = await self._setup_station(user_factory, db_session)
        token = self._get_token(owner.id, station.id)

        response = await test_client.get(
            "/api/panel/senders/99999/deliveries",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []

    # ==================== הרשאות ====================

    @pytest.mark.asyncio
    async def test_unauthorized_access(
        self, test_client,
    ):
        """גישה ללא טוקן מחזירה 403"""
        response = await test_client.get("/api/panel/senders")
        assert response.status_code == 403
