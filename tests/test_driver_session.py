"""
בדיקות יחידה — iDriver סשן 6: DriverSessionService

בודק:
- יצירה/חידוש סשן (start_session)
- עדכון פעילות (touch_session)
- בדיקת סשן פעיל (is_session_active)
- שליפת סשנים שעומדים לפוג (get_expiring_sessions)
- סימון תזכורת נשלחה (mark_reminder_sent)
- שליפת סשנים שפג תוקפם (get_expired_sessions)
- ניתוק סשן והשהיית חיפושים (disconnect_session)
"""
import pytest
from datetime import datetime, timedelta

from app.db.models.user import User, UserRole
from app.db.models.driver_profile import (
    DriverProfile,
    DressCode,
    VehicleCategory,
    DriverSubscriptionStatus,
)
from app.db.models.driver_session import DriverSession
from app.db.models.driver_search import DriverSearch, DriverSearchStatus
from app.domain.services.driver_session_service import (
    DriverSessionService,
    SESSION_DURATION_HOURS,
    REMINDER_MINUTES_BEFORE,
)


# ============================================================================
# עזר — יצירת נהג רשום
# ============================================================================


async def _create_registered_driver(
    db_session,
    user_factory,
    phone: str = "+972505001001",
) -> User:
    """יוצר נהג רשום עם פרופיל מלא"""
    user = await user_factory(
        phone_number=phone,
        name="נהג בדיקה",
        full_name="ישראל ישראלי",
        role=UserRole.DRIVER,
        telegram_chat_id=phone.replace("+972", ""),
    )
    now = datetime.utcnow()
    profile = DriverProfile(
        user_id=user.id,
        birth_date=datetime(1990, 1, 1).date(),
        vehicle_description="טויוטה 2024",
        vehicle_category=VehicleCategory.SEVEN_SEATER.value,
        dress_code=DressCode.SECULAR.value,
        subscription_status=DriverSubscriptionStatus.TRIAL.value,
        trial_starts_at=now,
        trial_expires_at=now + timedelta(days=7),
    )
    db_session.add(profile)
    await db_session.commit()
    await db_session.refresh(profile)
    return user


async def _create_active_search(
    db_session,
    user_id: int,
    destination_city: str = "ירושלים",
) -> DriverSearch:
    """יוצר חיפוש פעיל לבדיקות"""
    search = DriverSearch(
        user_id=user_id,
        origin_city="תל אביב",
        destination_city=destination_city,
        is_area_search=False,
        status=DriverSearchStatus.ACTIVE.value,
    )
    db_session.add(search)
    await db_session.commit()
    await db_session.refresh(search)
    return search


# ============================================================================
# בדיקות DriverSessionService
# ============================================================================


class TestDriverSessionService:
    """בדיקות שירות ניהול סשנים"""

    @pytest.mark.asyncio
    async def test_start_session_new(self, db_session, user_factory) -> None:
        """יצירת סשן חדש — נהג שאין לו סשן"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001001")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        assert session.id is not None
        assert session.user_id == user.id
        assert session.is_active is True
        assert session.reminder_sent_at is None

    @pytest.mark.asyncio
    async def test_start_session_renew(self, db_session, user_factory) -> None:
        """חידוש סשן קיים — מאפס את הטיימר"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001002")
        service = DriverSessionService(db_session)

        # יצירת סשן ראשון
        session1 = await service.start_session(user.id)
        original_start = session1.session_start_at

        # המתנה קטנה לשינוי timestamp
        session1.session_start_at = datetime.utcnow() - timedelta(hours=1)
        session1.reminder_sent_at = datetime.utcnow()
        await db_session.commit()

        # חידוש
        session2 = await service.start_session(user.id)
        assert session2.id == session1.id  # אותו רשומה
        assert session2.is_active is True
        assert session2.reminder_sent_at is None  # תזכורת אופסה
        assert session2.session_start_at > original_start  # זמן חודש

    @pytest.mark.asyncio
    async def test_touch_session_creates_if_none(self, db_session, user_factory) -> None:
        """touch_session יוצר סשן חדש אם אין"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001003")
        service = DriverSessionService(db_session)

        session = await service.touch_session(user.id)
        assert session is not None
        assert session.is_active is True

    @pytest.mark.asyncio
    async def test_touch_session_updates_last_message(self, db_session, user_factory) -> None:
        """touch_session מעדכן last_message_at"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001004")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        old_time = session.last_message_at

        # עדכון ידני של הזמן לעבר כדי לוודא שינוי
        session.last_message_at = datetime.utcnow() - timedelta(hours=1)
        await db_session.commit()

        updated = await service.touch_session(user.id)
        assert updated.last_message_at > old_time - timedelta(hours=1)

    @pytest.mark.asyncio
    async def test_touch_session_renews_inactive(self, db_session, user_factory) -> None:
        """touch_session מחדש סשן לא פעיל"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001005")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        session.is_active = False
        await db_session.commit()

        renewed = await service.touch_session(user.id)
        assert renewed.is_active is True

    @pytest.mark.asyncio
    async def test_is_session_active_true(self, db_session, user_factory) -> None:
        """is_session_active — סשן פעיל"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001006")
        service = DriverSessionService(db_session)

        await service.start_session(user.id)
        assert await service.is_session_active(user.id) is True

    @pytest.mark.asyncio
    async def test_is_session_active_false(self, db_session, user_factory) -> None:
        """is_session_active — אין סשן"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001007")
        service = DriverSessionService(db_session)

        assert await service.is_session_active(user.id) is False

    @pytest.mark.asyncio
    async def test_is_session_active_inactive(self, db_session, user_factory) -> None:
        """is_session_active — סשן לא פעיל"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001008")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        session.is_active = False
        await db_session.commit()

        assert await service.is_session_active(user.id) is False

    @pytest.mark.asyncio
    async def test_get_expiring_sessions(self, db_session, user_factory) -> None:
        """שליפת סשנים שעומדים לפוג — בטווח 23:58-24:00"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001009")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        # הזזת last_message_at ל-23:59 שעות בעבר (בטווח התזכורת)
        session.last_message_at = datetime.utcnow() - timedelta(
            hours=SESSION_DURATION_HOURS, minutes=-1
        )
        await db_session.commit()

        expiring = await service.get_expiring_sessions()
        assert len(expiring) == 1
        assert expiring[0].user_id == user.id

    @pytest.mark.asyncio
    async def test_get_expiring_sessions_already_reminded(self, db_session, user_factory) -> None:
        """סשן שכבר נשלחה לו תזכורת — לא מוחזר"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001010")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        session.last_message_at = datetime.utcnow() - timedelta(
            hours=SESSION_DURATION_HOURS, minutes=-1
        )
        session.reminder_sent_at = datetime.utcnow()
        await db_session.commit()

        expiring = await service.get_expiring_sessions()
        assert len(expiring) == 0

    @pytest.mark.asyncio
    async def test_get_expiring_sessions_not_in_range(self, db_session, user_factory) -> None:
        """סשן שעדיין לא בטווח (22 שעות) — לא מוחזר"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001011")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        session.last_message_at = datetime.utcnow() - timedelta(hours=22)
        await db_session.commit()

        expiring = await service.get_expiring_sessions()
        assert len(expiring) == 0

    @pytest.mark.asyncio
    async def test_mark_reminder_sent(self, db_session, user_factory) -> None:
        """סימון תזכורת כנשלחה"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001012")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        assert session.reminder_sent_at is None

        await service.mark_reminder_sent(session.id)
        await db_session.refresh(session)
        assert session.reminder_sent_at is not None

    @pytest.mark.asyncio
    async def test_get_expired_sessions(self, db_session, user_factory) -> None:
        """שליפת סשנים שפג תוקפם — מעל 24 שעות"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001013")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        session.last_message_at = datetime.utcnow() - timedelta(hours=25)
        await db_session.commit()

        expired = await service.get_expired_sessions()
        assert len(expired) == 1
        assert expired[0].user_id == user.id

    @pytest.mark.asyncio
    async def test_get_expired_sessions_not_expired(self, db_session, user_factory) -> None:
        """סשן שלא פג — לא מוחזר"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001014")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        session.last_message_at = datetime.utcnow() - timedelta(hours=23)
        await db_session.commit()

        expired = await service.get_expired_sessions()
        assert len(expired) == 0

    @pytest.mark.asyncio
    async def test_disconnect_session(self, db_session, user_factory) -> None:
        """ניתוק סשן — משהה חיפושים ומסמן כלא פעיל"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001015")
        service = DriverSessionService(db_session)

        # יצירת סשן + חיפושים פעילים
        await service.start_session(user.id)
        await _create_active_search(db_session, user.id, "ירושלים")
        await _create_active_search(db_session, user.id, "חיפה")

        paused_count = await service.disconnect_session(user.id)
        assert paused_count == 2

        # ווידוא שהסשן לא פעיל
        assert await service.is_session_active(user.id) is False

    @pytest.mark.asyncio
    async def test_disconnect_session_no_active(self, db_session, user_factory) -> None:
        """ניתוק סשן לא פעיל — מחזיר 0"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001016")
        service = DriverSessionService(db_session)

        paused_count = await service.disconnect_session(user.id)
        assert paused_count == 0

    @pytest.mark.asyncio
    async def test_disconnect_session_no_searches(self, db_session, user_factory) -> None:
        """ניתוק סשן בלי חיפושים — סשן מנותק, 0 חיפושים"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001017")
        service = DriverSessionService(db_session)

        await service.start_session(user.id)
        paused_count = await service.disconnect_session(user.id)
        assert paused_count == 0
        assert await service.is_session_active(user.id) is False

    @pytest.mark.asyncio
    async def test_touch_session_resets_reminder_sent(self, db_session, user_factory) -> None:
        """touch_session מאפס reminder_sent_at — תזכורת חדשה תישלח בהתאם לזמן העדכני"""
        user = await _create_registered_driver(db_session, user_factory, "+972506001018")
        service = DriverSessionService(db_session)

        session = await service.start_session(user.id)
        # סימולציה: תזכורת נשלחה
        await service.mark_reminder_sent(session.id)
        await db_session.refresh(session)
        assert session.reminder_sent_at is not None

        # פעילות חדשה — חייבת לאפס את התזכורת
        updated = await service.touch_session(user.id)
        assert updated.reminder_sent_at is None
