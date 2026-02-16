"""
בדיקות לוג ביקורת (Audit Trail) — סעיף 5 מתוך Issue #210

בדיקות שכבת השירות (StationService):
- רישום פעולות מנהלתיות בלוג ביקורת
- שאילתת לוג עם סינון ו-pagination
- ערכים ישנים וחדשים נשמרים נכון ב-details

בדיקות מודל:
- AuditLog נוצר עם כל השדות הנדרשים
- AuditActionType מכיל את כל סוגי הפעולות
"""
import pytest
from datetime import datetime, timedelta
from decimal import Decimal

from app.db.models.user import User, UserRole
from app.db.models.station import Station
from app.db.models.station_owner import StationOwner
from app.db.models.station_wallet import StationWallet
from app.db.models.audit_log import AuditLog, AuditActionType
from app.domain.services.station_service import StationService


# ============================================================================
# עזרים ליצירת נתוני בדיקה
# ============================================================================


class TestAuditTrailBase:
    """בסיס משותף ליצירת תחנה ובעלים לבדיקות audit"""

    async def _create_station_with_owner(self, user_factory, db_session):
        """יצירת תחנה עם בעלים ראשי וארנק"""
        owner = await user_factory(
            phone_number="+972501234567",
            name="בעלים ראשי",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=owner.id)
        db_session.add(station)
        await db_session.flush()
        db_session.add(StationWallet(station_id=station.id))
        db_session.add(StationOwner(station_id=station.id, user_id=owner.id))
        await db_session.commit()
        return owner, station

    async def _create_second_owner(self, user_factory, db_session, station):
        """יצירת בעלים נוסף לתחנה"""
        second_owner = await user_factory(
            phone_number="+972509999999",
            name="בעלים שני",
            role=UserRole.STATION_OWNER,
        )
        db_session.add(StationOwner(station_id=station.id, user_id=second_owner.id))
        await db_session.commit()
        return second_owner


# ============================================================================
# בדיקות מודל
# ============================================================================


class TestAuditLogModel:
    """בדיקות מודל AuditLog"""

    @pytest.mark.unit
    def test_action_types_exist(self):
        """כל סוגי הפעולות המנהלתיות קיימים"""
        expected_actions = [
            "owner_added", "owner_removed",
            "dispatcher_added", "dispatcher_removed",
            "blacklist_added", "blacklist_removed",
            "commission_rate_updated",
            "station_settings_updated",
            "group_settings_updated",
            "auto_block_settings_updated",
            "manual_charge_created",
        ]
        actual_actions = [a.value for a in AuditActionType]
        for expected in expected_actions:
            assert expected in actual_actions, f"חסר סוג פעולה: {expected}"

    @pytest.mark.asyncio
    async def test_create_audit_log_entry(self, user_factory, db_session):
        """יצירת רשומת audit עם כל השדות"""
        user = await user_factory(
            phone_number="+972501234567",
            role=UserRole.STATION_OWNER,
        )
        station = Station(name="תחנת בדיקה", owner_id=user.id)
        db_session.add(station)
        await db_session.flush()

        entry = AuditLog(
            station_id=station.id,
            actor_user_id=user.id,
            action=AuditActionType.OWNER_ADDED,
            target_user_id=user.id,
            details={"target_name": "בדיקה"},
        )
        db_session.add(entry)
        await db_session.commit()
        await db_session.refresh(entry)

        assert entry.id is not None
        assert entry.station_id == station.id
        assert entry.actor_user_id == user.id
        assert entry.action == AuditActionType.OWNER_ADDED
        assert entry.target_user_id == user.id
        assert entry.details["target_name"] == "בדיקה"
        assert entry.created_at is not None


# ============================================================================
# בדיקות שכבת השירות — רישום audit
# ============================================================================


class TestAuditTrailRecording(TestAuditTrailBase):
    """בדיקות רישום פעולות מנהלתיות בלוג ביקורת"""

    @pytest.mark.asyncio
    async def test_add_owner_creates_audit(self, user_factory, db_session):
        """הוספת בעלים — נרשמת בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        target = await user_factory(
            phone_number="+972508888888",
            name="בעלים חדש",
            role=UserRole.SENDER,
        )

        success, _ = await service.add_owner(
            station.id, "+972508888888", actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.OWNER_ADDED
        assert entries[0].actor_user_id == owner.id
        assert entries[0].target_user_id == target.id
        assert "target_phone" in entries[0].details

    @pytest.mark.asyncio
    async def test_remove_owner_creates_audit(self, user_factory, db_session):
        """הסרת בעלים — נרשמת בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        second = await self._create_second_owner(user_factory, db_session, station)
        service = StationService(db_session)

        success, _ = await service.remove_owner(
            station.id, second.id, actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.OWNER_REMOVED
        assert entries[0].target_user_id == second.id

    @pytest.mark.asyncio
    async def test_add_dispatcher_creates_audit(self, user_factory, db_session):
        """הוספת סדרן — נרשמת בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        await user_factory(
            phone_number="+972507777777",
            name="סדרן חדש",
        )

        success, _ = await service.add_dispatcher(
            station.id, "+972507777777", actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.DISPATCHER_ADDED
        assert entries[0].details["target_name"] == "סדרן חדש"

    @pytest.mark.asyncio
    async def test_remove_dispatcher_creates_audit(self, user_factory, db_session):
        """הסרת סדרן — נרשמת בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        dispatcher_user = await user_factory(
            phone_number="+972507777777",
            name="סדרן",
        )
        await service.add_dispatcher(
            station.id, "+972507777777", actor_user_id=owner.id,
        )

        success, _ = await service.remove_dispatcher(
            station.id, dispatcher_user.id, actor_user_id=owner.id,
        )
        assert success is True

        entries, _ = await service.get_audit_logs(
            station.id, action=AuditActionType.DISPATCHER_REMOVED,
        )
        assert len(entries) == 1
        assert entries[0].target_user_id == dispatcher_user.id

    @pytest.mark.asyncio
    async def test_add_to_blacklist_creates_audit(self, user_factory, db_session):
        """הוספה לרשימה שחורה — נרשמת בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        courier = await user_factory(
            phone_number="+972506666666",
            name="שליח",
            role=UserRole.COURIER,
        )

        success, _ = await service.add_to_blacklist(
            station.id, "+972506666666", "עבר על החוקים",
            actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.BLACKLIST_ADDED
        assert entries[0].target_user_id == courier.id
        assert entries[0].details["reason"] == "עבר על החוקים"

    @pytest.mark.asyncio
    async def test_remove_from_blacklist_creates_audit(self, user_factory, db_session):
        """הסרה מרשימה שחורה — נרשמת בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        courier = await user_factory(
            phone_number="+972506666666",
            name="שליח",
            role=UserRole.COURIER,
        )
        await service.add_to_blacklist(
            station.id, "+972506666666", "סיבה",
            actor_user_id=owner.id,
        )

        success, _ = await service.remove_from_blacklist(
            station.id, courier.id, actor_user_id=owner.id,
        )
        assert success is True

        entries, _ = await service.get_audit_logs(
            station.id, action=AuditActionType.BLACKLIST_REMOVED,
        )
        assert len(entries) == 1
        assert entries[0].target_user_id == courier.id

    @pytest.mark.asyncio
    async def test_update_commission_rate_creates_audit(self, user_factory, db_session):
        """עדכון אחוז עמלה — נרשם בלוג ביקורת עם ערך ישן וחדש"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        success, _ = await service.update_commission_rate(
            station.id, 0.08, actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.COMMISSION_RATE_UPDATED
        assert entries[0].details["new_value"] == "8%"

    @pytest.mark.asyncio
    async def test_update_settings_creates_audit(self, user_factory, db_session):
        """עדכון הגדרות תחנה — נרשם בלוג ביקורת עם שדות ששונו"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        success, _ = await service.update_station_settings(
            station_id=station.id,
            name="שם חדש",
            actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.STATION_SETTINGS_UPDATED
        assert "name" in entries[0].details["fields"]
        assert entries[0].details["changes"]["name"]["new_value"] == "שם חדש"

    @pytest.mark.asyncio
    async def test_update_groups_creates_audit(self, user_factory, db_session):
        """עדכון הגדרות קבוצות — נרשם בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        success, _ = await service.update_station_groups(
            station_id=station.id,
            public_group_chat_id="-100123456",
            actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.GROUP_SETTINGS_UPDATED
        assert entries[0].details["public_group"]["new_value"] == "-100123456"

    @pytest.mark.asyncio
    async def test_update_auto_block_creates_audit(self, user_factory, db_session):
        """עדכון הגדרות חסימה אוטומטית — נרשם בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        success, _ = await service.update_auto_block_settings(
            station_id=station.id,
            auto_block_enabled=True,
            auto_block_grace_months=3,
            actor_user_id=owner.id,
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.AUTO_BLOCK_SETTINGS_UPDATED
        assert entries[0].details["new_values"]["auto_block_enabled"] is True
        assert entries[0].details["new_values"]["auto_block_grace_months"] == 3

    @pytest.mark.asyncio
    async def test_manual_charge_creates_audit(self, user_factory, db_session):
        """יצירת חיוב ידני — נרשמת בלוג ביקורת"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        charge = await service.create_manual_charge(
            station_id=station.id,
            dispatcher_id=owner.id,
            driver_name="משה כהן",
            amount=50.0,
            description="חיוב בדיקה",
        )

        entries, total = await service.get_audit_logs(station.id)
        assert total == 1
        assert entries[0].action == AuditActionType.MANUAL_CHARGE_CREATED
        assert entries[0].actor_user_id == owner.id
        assert entries[0].details["driver_name"] == "משה כהן"
        assert entries[0].details["amount"] == 50.0

    @pytest.mark.asyncio
    async def test_no_audit_without_actor(self, user_factory, db_session):
        """כשלא מועבר actor_user_id — לא נוצרת רשומת audit"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        await user_factory(
            phone_number="+972508888888",
            name="בעלים חדש",
        )

        # הוספת בעלים ללא actor_user_id — תאימות לאחור עם הבוט
        success, _ = await service.add_owner(
            station.id, "+972508888888",
        )
        assert success is True

        entries, total = await service.get_audit_logs(station.id)
        assert total == 0


# ============================================================================
# בדיקות שאילתת לוג ביקורת
# ============================================================================


class TestAuditTrailQuery(TestAuditTrailBase):
    """בדיקות שאילתת לוג ביקורת עם סינון ו-pagination"""

    @pytest.mark.asyncio
    async def test_filter_by_action(self, user_factory, db_session):
        """סינון לפי סוג פעולה"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        # יצירת כמה רשומות מסוגים שונים
        await user_factory(phone_number="+972508888888", name="בעלים")
        await service.add_owner(station.id, "+972508888888", actor_user_id=owner.id)

        await user_factory(phone_number="+972507777777", name="סדרן")
        await service.add_dispatcher(station.id, "+972507777777", actor_user_id=owner.id)

        # סינון רק הוספת סדרנים
        entries, total = await service.get_audit_logs(
            station.id, action=AuditActionType.DISPATCHER_ADDED,
        )
        assert total == 1
        assert entries[0].action == AuditActionType.DISPATCHER_ADDED

    @pytest.mark.asyncio
    async def test_filter_by_actor(self, user_factory, db_session):
        """סינון לפי משתמש מבצע"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        second = await self._create_second_owner(user_factory, db_session, station)
        service = StationService(db_session)

        await user_factory(phone_number="+972501111111", name="סדרן1")
        await service.add_dispatcher(station.id, "+972501111111", actor_user_id=owner.id)

        await user_factory(phone_number="+972502222222", name="סדרן2")
        await service.add_dispatcher(station.id, "+972502222222", actor_user_id=second.id)

        # סינון לפי בעלים שני
        entries, total = await service.get_audit_logs(
            station.id, actor_user_id=second.id,
        )
        assert total == 1
        assert entries[0].actor_user_id == second.id

    @pytest.mark.asyncio
    async def test_pagination(self, user_factory, db_session):
        """pagination — עמוד 1 ועמוד 2"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        # יצירת 5 רשומות
        for i in range(5):
            phone = f"+97250{i:07d}"
            await user_factory(phone_number=phone, name=f"סדרן {i}")
            await service.add_dispatcher(station.id, phone, actor_user_id=owner.id)

        # עמוד 1 — 3 רשומות
        entries, total = await service.get_audit_logs(
            station.id, page=1, page_size=3,
        )
        assert total == 5
        assert len(entries) == 3

        # עמוד 2 — 2 רשומות
        entries, total = await service.get_audit_logs(
            station.id, page=2, page_size=3,
        )
        assert total == 5
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_order_by_created_at_desc(self, user_factory, db_session):
        """סדר מיון — מהחדש לישן"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        await user_factory(phone_number="+972508888888", name="ראשון")
        await service.add_dispatcher(station.id, "+972508888888", actor_user_id=owner.id)

        await user_factory(phone_number="+972507777777", name="שני")
        await service.add_dispatcher(station.id, "+972507777777", actor_user_id=owner.id)

        entries, _ = await service.get_audit_logs(station.id)
        # הרשומה השנייה (אחרונה ב-created_at) צריכה להיות ראשונה
        assert entries[0].created_at >= entries[1].created_at

    @pytest.mark.asyncio
    async def test_empty_audit_log(self, user_factory, db_session):
        """תחנה ללא רשומות — מחזיר רשימה ריקה"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        entries, total = await service.get_audit_logs(station.id)
        assert total == 0
        assert entries == []


# ============================================================================
# בדיקות אטומיות — audit בטרנזקציה עם הפעולה
# ============================================================================


class TestAuditTrailAtomicity(TestAuditTrailBase):
    """בדיקות שה-audit נוצר באותה טרנזקציה עם הפעולה"""

    @pytest.mark.asyncio
    async def test_failed_operation_no_audit(self, user_factory, db_session):
        """פעולה שנכשלת — לא נוצרת רשומת audit"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        # ניסיון להסיר את הבעלים האחרון — נכשל
        success, _ = await service.remove_owner(
            station.id, owner.id, actor_user_id=owner.id,
        )
        assert success is False

        entries, total = await service.get_audit_logs(station.id)
        assert total == 0

    @pytest.mark.asyncio
    async def test_validation_failure_no_audit(self, user_factory, db_session):
        """כשלון ולידציה — לא נוצרת רשומת audit"""
        owner, station = await self._create_station_with_owner(user_factory, db_session)
        service = StationService(db_session)

        # אחוז עמלה מחוץ לטווח — כשלון ולידציה
        success, _ = await service.update_commission_rate(
            station.id, 0.50, actor_user_id=owner.id,
        )
        assert success is False

        entries, total = await service.get_audit_logs(station.id)
        assert total == 0
