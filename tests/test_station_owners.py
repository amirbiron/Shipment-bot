"""
בדיקות ריבוי בעלים לתחנה — StationOwner
"""
import pytest

from app.core.auth import create_access_token, store_otp
from app.db.models.user import UserRole
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_wallet import StationWallet
from app.db.models.courier_wallet import CourierWallet
from app.domain.services.station_service import StationService


class TestStationOwnerService:
    """בדיקות שכבת השירות — ניהול בעלים"""

    @pytest.mark.asyncio
    async def test_is_owner_of_station_via_junction(self, user_factory, db_session):
        """בעלים בטבלת station_owners מזוהה"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)

        owner_record = StationOwner(station_id=station.id, user_id=user.id)
        db_session.add(owner_record)
        await db_session.commit()

        service = StationService(db_session)
        assert await service.is_owner_of_station(user.id, station.id) is True

    @pytest.mark.asyncio
    async def test_is_owner_fallback_to_owner_id(self, user_factory, db_session):
        """fallback — אם אין רשומה ב-station_owners, בודק owner_id ישן"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        service = StationService(db_session)
        # אין רשומה ב-station_owners, אבל owner_id תואם
        assert await service.is_owner_of_station(user.id, station.id) is True

    @pytest.mark.asyncio
    async def test_non_owner_rejected(self, user_factory, db_session):
        """משתמש שאינו בעלים — נדחה"""
        owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        other = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        await db_session.commit()

        service = StationService(db_session)
        assert await service.is_owner_of_station(other.id, station.id) is False

    @pytest.mark.asyncio
    async def test_add_owner(self, user_factory, db_session):
        """הוספת בעלים לתחנה"""
        owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        new_owner = await user_factory(
            phone_number="+972502222222",
            name="בעלים חדש",
            role=UserRole.SENDER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        # הוספת הבעלים הראשון לטבלת junction
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        await db_session.commit()

        service = StationService(db_session)
        success, msg = await service.add_owner(station.id, "+972502222222")
        assert success is True

        # ולידציה שהבעלים החדש מזוהה
        assert await service.is_owner_of_station(new_owner.id, station.id) is True

    @pytest.mark.asyncio
    async def test_add_owner_updates_role(self, user_factory, db_session):
        """הוספת בעלים מעדכנת את תפקיד המשתמש ל-STATION_OWNER"""
        owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        new_owner = await user_factory(
            phone_number="+972502222222",
            role=UserRole.SENDER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        await db_session.commit()

        service = StationService(db_session)
        await service.add_owner(station.id, "+972502222222")

        await db_session.refresh(new_owner)
        assert new_owner.role == UserRole.STATION_OWNER

    @pytest.mark.asyncio
    async def test_add_owner_auto_creates_user(self, user_factory, db_session):
        """הוספת בעלים עם מספר טלפון שלא קיים במערכת — יוצרת משתמש אוטומטית"""
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

        service = StationService(db_session)
        # מספר טלפון שלא קיים במערכת
        success, msg = await service.add_owner(station.id, "+972509999999")
        assert success is True
        assert "נוסף בהצלחה" in msg

        # ולידציה שהמשתמש נוצר עם תפקיד STATION_OWNER
        from sqlalchemy import select
        from app.db.models.user import User
        result = await db_session.execute(
            select(User).where(User.phone_number == "+972509999999")
        )
        new_user = result.scalar_one_or_none()
        assert new_user is not None
        assert new_user.role == UserRole.STATION_OWNER
        assert await service.is_owner_of_station(new_user.id, station.id) is True

    @pytest.mark.asyncio
    async def test_add_duplicate_owner_rejected(self, user_factory, db_session):
        """הוספת בעלים כפול — נדחה"""
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

        service = StationService(db_session)
        success, msg = await service.add_owner(station.id, "+972501234567")
        assert success is False
        assert "כבר בעלים" in msg

    @pytest.mark.asyncio
    async def test_remove_owner(self, user_factory, db_session):
        """הסרת בעלים כשיש יותר מאחד"""
        owner1 = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        owner2 = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner1.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        db_session.add(StationOwner(station_id=station.id, user_id=owner1.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner2.id))
        await db_session.commit()

        service = StationService(db_session)
        success, msg = await service.remove_owner(station.id, owner2.id)
        assert success is True

        assert await service.is_owner_of_station(owner2.id, station.id) is False

    @pytest.mark.asyncio
    async def test_remove_original_owner_clears_fallback(self, user_factory, db_session):
        """הסרת הבעלים המקורי (owner_id) מעדכנת את owner_id לבעלים אחר — מונע fallback"""
        owner1 = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        owner2 = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        # owner1 הוא ה-owner_id המקורי של התחנה
        station = Station(name="תחנה", owner_id=owner1.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        db_session.add(StationOwner(station_id=station.id, user_id=owner1.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner2.id))
        await db_session.commit()

        service = StationService(db_session)
        success, msg = await service.remove_owner(station.id, owner1.id)
        assert success is True

        # owner1 לא אמור לקבל גישה דרך fallback של owner_id
        assert await service.is_owner_of_station(owner1.id, station.id) is False

        # owner_id של התחנה צריך להתעדכן ל-owner2
        await db_session.refresh(station)
        assert station.owner_id == owner2.id

    @pytest.mark.asyncio
    async def test_remove_owner_reverts_courier_role(self, user_factory, db_session):
        """הסרת בעלים שהיה שליח מחזירה אותו ל-COURIER"""
        original_owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        # משתמש שהיה שליח (יש לו ארנק שליח) והפך לבעל תחנה
        ex_courier = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        # יצירת ארנק שליח — מעיד שהמשתמש היה שליח
        courier_wallet = CourierWallet(courier_id=ex_courier.id, balance=0.0)
        db_session.add(courier_wallet)

        station = Station(name="תחנה", owner_id=original_owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=original_owner.id))
        db_session.add(StationOwner(station_id=station.id, user_id=ex_courier.id))
        await db_session.commit()

        service = StationService(db_session)
        success, msg = await service.remove_owner(station.id, ex_courier.id)
        assert success is True

        await db_session.refresh(ex_courier)
        assert ex_courier.role == UserRole.COURIER

    @pytest.mark.asyncio
    async def test_remove_owner_reverts_sender_role(self, user_factory, db_session):
        """הסרת בעלים שהיה שולח מחזירה אותו ל-SENDER"""
        original_owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        ex_sender = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        # אין ארנק שליח — המשתמש היה שולח רגיל

        station = Station(name="תחנה", owner_id=original_owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=original_owner.id))
        db_session.add(StationOwner(station_id=station.id, user_id=ex_sender.id))
        await db_session.commit()

        service = StationService(db_session)
        success, msg = await service.remove_owner(station.id, ex_sender.id)
        assert success is True

        await db_session.refresh(ex_sender)
        assert ex_sender.role == UserRole.SENDER

    @pytest.mark.asyncio
    async def test_remove_owner_keeps_role_if_still_owns_another(self, user_factory, db_session):
        """הסרת בעלים שעדיין בעלים בתחנה אחרת — לא משנה תפקיד"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        other_owner = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        station1 = Station(name="תחנה א", owner_id=user.id)
        station2 = Station(name="תחנה ב", owner_id=user.id)
        db_session.add_all([station1, station2])
        await db_session.flush()
        db_session.add(StationWallet(station_id=station1.id))
        db_session.add(StationWallet(station_id=station2.id))
        db_session.add(StationOwner(station_id=station1.id, user_id=user.id))
        db_session.add(StationOwner(station_id=station1.id, user_id=other_owner.id))
        db_session.add(StationOwner(station_id=station2.id, user_id=user.id))
        await db_session.commit()

        service = StationService(db_session)
        # מסירים מתחנה א — עדיין בעלים בתחנה ב
        success, msg = await service.remove_owner(station1.id, user.id)
        assert success is True

        await db_session.refresh(user)
        assert user.role == UserRole.STATION_OWNER

    @pytest.mark.asyncio
    async def test_cannot_remove_last_owner(self, user_factory, db_session):
        """לא ניתן להסיר את הבעלים האחרון"""
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

        service = StationService(db_session)
        success, msg = await service.remove_owner(station.id, owner.id)
        assert success is False
        assert "האחרון" in msg

    @pytest.mark.asyncio
    async def test_create_station_creates_junction_entry(self, user_factory, db_session):
        """יצירת תחנה חדשה יוצרת גם רשומה בטבלת station_owners"""
        owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )

        service = StationService(db_session)
        station = await service.create_station("תחנה חדשה", owner.id)
        await db_session.commit()

        # בדיקה שנוצרה רשומה ב-station_owners
        assert await service.is_owner_of_station(owner.id, station.id) is True

        # בדיקה שהבעלים מופיע ברשימת התחנות שלו (דרך junction, לא fallback)
        stations = await service.get_stations_by_owner(owner.id)
        assert len(stations) == 1
        assert stations[0].id == station.id

    @pytest.mark.asyncio
    async def test_get_stations_merges_junction_and_owner_id(self, user_factory, db_session):
        """get_stations_by_owner ממזג תוצאות מ-junction ומ-owner_id fallback"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        # תחנה א — עם junction entry
        station1 = Station(name="תחנה א (junction)", owner_id=user.id)
        db_session.add(station1)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station1.id))
        db_session.add(StationOwner(station_id=station1.id, user_id=user.id))

        # תחנה ב — רק owner_id, בלי junction entry (מדמה תחנה לפני מיגרציה)
        station2 = Station(name="תחנה ב (owner_id בלבד)", owner_id=user.id)
        db_session.add(station2)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station2.id))
        # לא מוסיפים StationOwner — מדמה תחנה שלא עברה מיגרציה

        await db_session.commit()

        service = StationService(db_session)
        stations = await service.get_stations_by_owner(user.id)
        # חייב להחזיר את שתי התחנות — גם junction וגם fallback
        assert len(stations) == 2
        station_ids = {s.id for s in stations}
        assert station1.id in station_ids
        assert station2.id in station_ids

    @pytest.mark.asyncio
    async def test_get_stations_by_owner_multiple(self, user_factory, db_session):
        """משתמש שהוא בעלים בכמה תחנות"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station1 = Station(name="תחנה א", owner_id=user.id)
        station2 = Station(name="תחנה ב", owner_id=user.id)
        db_session.add_all([station1, station2])
        await db_session.flush()
        db_session.add(StationWallet(station_id=station1.id))
        db_session.add(StationWallet(station_id=station2.id))
        db_session.add(StationOwner(station_id=station1.id, user_id=user.id))
        db_session.add(StationOwner(station_id=station2.id, user_id=user.id))
        await db_session.commit()

        service = StationService(db_session)
        stations = await service.get_stations_by_owner(user.id)
        assert len(stations) == 2


    @pytest.mark.asyncio
    async def test_create_station_auto_creates_user(self, db_session):
        """יצירת תחנה עם מספר טלפון שלא קיים במערכת — יוצרת משתמש אוטומטית"""
        from sqlalchemy import select
        from app.db.models.user import User

        service = StationService(db_session)

        # ניסיון יצירת תחנה דרך ה-API עם מספר טלפון שלא קיים
        from httpx import AsyncClient, ASGITransport
        from app.main import app
        from app.db.database import get_db

        async def override_get_db():
            yield db_session

        app.dependency_overrides[get_db] = override_get_db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/stations/", json={
                "name": "תחנת בדיקה",
                "owner_phone": "0509999999",
            })
        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "תחנת בדיקה"

        # ולידציה שהמשתמש נוצר עם תפקיד STATION_OWNER
        result = await db_session.execute(
            select(User).where(User.phone_number == "+972509999999")
        )
        new_user = result.scalar_one_or_none()
        assert new_user is not None
        assert new_user.role == UserRole.STATION_OWNER


class TestMultiOwnerAuth:
    """בדיקות אימות עם ריבוי בעלים"""

    @pytest.mark.asyncio
    async def test_second_owner_can_access_dashboard(
        self, test_client, user_factory, db_session,
    ):
        """בעלים שני יכול לגשת לדשבורד"""
        owner1 = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        owner2 = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה משותפת", owner_id=owner1.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        db_session.add(StationOwner(station_id=station.id, user_id=owner1.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner2.id))
        await db_session.commit()

        # בעלים שני מקבל token
        token = create_access_token(owner2.id, station.id, "station_owner")
        response = await test_client.get(
            "/api/panel/dashboard",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_verify_otp_station_picker(
        self, test_client, user_factory, db_session,
    ):
        """משתמש עם כמה תחנות — מקבל רשימה לבחירה"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station1 = Station(name="תחנה א", owner_id=user.id)
        station2 = Station(name="תחנה ב", owner_id=user.id)
        db_session.add_all([station1, station2])
        await db_session.flush()
        db_session.add(StationWallet(station_id=station1.id))
        db_session.add(StationWallet(station_id=station2.id))
        db_session.add(StationOwner(station_id=station1.id, user_id=user.id))
        db_session.add(StationOwner(station_id=station2.id, user_id=user.id))
        await db_session.commit()

        await store_otp(user.id, "123456")

        # אימות ללא station_id — מחזיר רשימה לבחירה
        response = await test_client.post("/api/panel/auth/verify-otp", json={
            "phone_number": "0501234567",
            "otp": "123456",
        })
        assert response.status_code == 200
        data = response.json()
        assert data.get("choose_station") is True
        assert len(data["stations"]) == 2

    @pytest.mark.asyncio
    async def test_verify_otp_with_station_selection(
        self, test_client, user_factory, db_session,
    ):
        """משתמש בוחר תחנה — מקבל token"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station1 = Station(name="תחנה א", owner_id=user.id)
        station2 = Station(name="תחנה ב", owner_id=user.id)
        db_session.add_all([station1, station2])
        await db_session.flush()
        db_session.add(StationWallet(station_id=station1.id))
        db_session.add(StationWallet(station_id=station2.id))
        db_session.add(StationOwner(station_id=station1.id, user_id=user.id))
        db_session.add(StationOwner(station_id=station2.id, user_id=user.id))
        await db_session.commit()

        await store_otp(user.id, "123456")

        # אימות עם station_id — מקבל token
        response = await test_client.post("/api/panel/auth/verify-otp", json={
            "phone_number": "0501234567",
            "otp": "123456",
            "station_id": station2.id,
        })
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["station_name"] == "תחנה ב"

    @pytest.mark.asyncio
    async def test_verify_otp_full_two_step_flow(
        self, test_client, user_factory, db_session,
    ):
        """זרימה מלאה: station picker ואז בחירת תחנה — OTP לא נצרך בשלב הראשון"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station1 = Station(name="תחנה א", owner_id=user.id)
        station2 = Station(name="תחנה ב", owner_id=user.id)
        db_session.add_all([station1, station2])
        await db_session.flush()
        db_session.add(StationWallet(station_id=station1.id))
        db_session.add(StationWallet(station_id=station2.id))
        db_session.add(StationOwner(station_id=station1.id, user_id=user.id))
        db_session.add(StationOwner(station_id=station2.id, user_id=user.id))
        await db_session.commit()

        await store_otp(user.id, "123456")

        # שלב 1: אימות ללא station_id — מחזיר station picker בלי לצרוך OTP
        response1 = await test_client.post("/api/panel/auth/verify-otp", json={
            "phone_number": "0501234567",
            "otp": "123456",
        })
        assert response1.status_code == 200
        data1 = response1.json()
        assert data1.get("choose_station") is True

        # שלב 2: אימות עם station_id — אותו OTP עדיין תקף
        response2 = await test_client.post("/api/panel/auth/verify-otp", json={
            "phone_number": "0501234567",
            "otp": "123456",
            "station_id": station1.id,
        })
        assert response2.status_code == 200
        data2 = response2.json()
        assert "access_token" in data2
        assert data2["station_name"] == "תחנה א"


class TestOwnerPanelEndpoints:
    """בדיקות endpoints לניהול בעלים"""

    @pytest.mark.asyncio
    async def test_list_owners(self, test_client, user_factory, db_session):
        """רשימת בעלים"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעלים ראשון",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        await db_session.commit()

        token = create_access_token(owner.id, station.id, "station_owner")
        response = await test_client.get(
            "/api/panel/owners",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["name"] == "בעלים ראשון"

    @pytest.mark.asyncio
    async def test_add_owner_via_api(self, test_client, user_factory, db_session):
        """הוספת בעלים דרך ה-API"""
        owner = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        await user_factory(
            phone_number="+972502222222",
            name="בעלים חדש",
            role=UserRole.SENDER,
        )
        station = Station(name="תחנה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        await db_session.commit()

        token = create_access_token(owner.id, station.id, "station_owner")
        response = await test_client.post(
            "/api/panel/owners",
            json={"phone_number": "0502222222"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_add_owner_auto_creates_user_via_api(
        self, test_client, user_factory, db_session,
    ):
        """הוספת בעלים שלא קיים במערכת דרך ה-API — יוצרת משתמש אוטומטית"""
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

        token = create_access_token(owner.id, station.id, "station_owner")
        # מספר טלפון של משתמש שלא קיים במערכת
        response = await test_client.post(
            "/api/panel/owners",
            json={"phone_number": "0509999999"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_remove_owner_via_api(self, test_client, user_factory, db_session):
        """הסרת בעלים דרך ה-API"""
        owner1 = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        owner2 = await user_factory(
            phone_number="+972502222222",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנה", owner_id=owner1.id)
        db_session.add(station)
        await db_session.flush()
        wallet = StationWallet(station_id=station.id)
        db_session.add(wallet)
        db_session.add(StationOwner(station_id=station.id, user_id=owner1.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner2.id))
        await db_session.commit()

        token = create_access_token(owner1.id, station.id, "station_owner")
        response = await test_client.delete(
            f"/api/panel/owners/{owner2.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    @pytest.mark.asyncio
    async def test_cannot_remove_last_owner_via_api(
        self, test_client, user_factory, db_session,
    ):
        """לא ניתן להסיר את הבעלים האחרון דרך ה-API"""
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

        token = create_access_token(owner.id, station.id, "station_owner")
        response = await test_client.delete(
            f"/api/panel/owners/{owner.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
