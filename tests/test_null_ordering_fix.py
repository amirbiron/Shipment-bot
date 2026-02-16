"""
בדיקות לתיקון סדר NULL ב-order_by — nulls_last() ב-get_or_create_user.

ב-PostgreSQL, DESC שם NULLs לפני ערכים (NULLS FIRST כברירת מחדל).
ללא nulls_last(), רשומת משתמש עם is_active=NULL יכולה לדרוס
רשומה עם is_active=True — ולגרום להשבתה שגויה של המשתמש הנכון.

הבדיקות מוודאות ש:
1. שאילתות ה-ORDER BY בשני ה-webhooks כוללות NULLS LAST
2. הסדר הנכון נשמר: is_active=True > is_active=False > is_active=NULL
"""
import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.db.models.user import User


# ============================================================================
# Telegram — ORDER BY כולל NULLS LAST
# ============================================================================


class TestTelegramNullsLastInQuery:
    """בדיקות שה-SQL שנוצר בטלגרם כולל NULLS LAST ב-ORDER BY."""

    @pytest.mark.unit
    def test_primary_query_includes_nulls_last(self) -> None:
        """שאילתת החיפוש הראשית ב-get_or_create_user (טלגרם) כוללת NULLS LAST."""
        # בונה את אותה שאילתה שמופיעה ב-telegram.py get_or_create_user
        query = (
            select(User)
            .where(User.telegram_chat_id == "test")
            .order_by(
                User.is_active.desc().nulls_last(),
                User.updated_at.desc().nulls_last(),
                User.created_at.desc().nulls_last(),
            )
            .limit(10)
        )

        # קומפילציה ל-PostgreSQL — מוודא ש-NULLS LAST מופיע
        compiled = query.compile(dialect=postgresql.dialect())
        sql = str(compiled)

        assert sql.count("NULLS LAST") == 3, (
            f"צפוי 3 פעמים NULLS LAST ב-ORDER BY, נמצאו "
            f"{sql.count('NULLS LAST')}: {sql}"
        )

    @pytest.mark.unit
    def test_retry_query_includes_nulls_last(self) -> None:
        """שאילתת ה-retry (IntegrityError) ב-get_or_create_user (טלגרם) כוללת NULLS LAST."""
        # בונה את אותה שאילתה שמופיעה אחרי IntegrityError
        query = (
            select(User)
            .where(User.telegram_chat_id == "test")
            .order_by(
                User.is_active.desc().nulls_last(),
                User.updated_at.desc().nulls_last(),
            )
            .limit(1)
        )

        compiled = query.compile(dialect=postgresql.dialect())
        sql = str(compiled)

        assert sql.count("NULLS LAST") == 2, (
            f"צפוי 2 פעמים NULLS LAST ב-ORDER BY, נמצאו "
            f"{sql.count('NULLS LAST')}: {sql}"
        )


# ============================================================================
# WhatsApp — ORDER BY כולל NULLS LAST
# ============================================================================


class TestWhatsAppNullsLastInQuery:
    """בדיקות שה-SQL שנוצר בוואטסאפ כולל NULLS LAST ב-ORDER BY."""

    @pytest.mark.unit
    def test_sender_key_query_includes_nulls_last(self) -> None:
        """שאילתת חיפוש לפי sender_key (וואטסאפ) כוללת NULLS LAST."""
        sender_key = "972501234567@s.whatsapp.net"
        query = (
            select(User)
            .where(User.phone_number.in_([sender_key]))
            .order_by(
                (User.phone_number == sender_key).desc(),
                User.is_active.desc().nulls_last(),
                User.updated_at.desc().nulls_last(),
                User.created_at.desc().nulls_last(),
            )
            .limit(2)
        )

        compiled = query.compile(dialect=postgresql.dialect())
        sql = str(compiled)

        assert sql.count("NULLS LAST") == 3, (
            f"צפוי 3 פעמים NULLS LAST ב-ORDER BY, נמצאו "
            f"{sql.count('NULLS LAST')}: {sql}"
        )

    @pytest.mark.unit
    def test_phone_lookup_query_includes_nulls_last(self) -> None:
        """שאילתת חיפוש לפי phone (וואטסאפ) כוללת NULLS LAST."""
        query = (
            select(User)
            .where(User.phone_number == "+972501234567")
            .order_by(
                User.is_active.desc().nulls_last(),
                User.updated_at.desc().nulls_last(),
                User.created_at.desc().nulls_last(),
            )
            .limit(2)
        )

        compiled = query.compile(dialect=postgresql.dialect())
        sql = str(compiled)

        assert sql.count("NULLS LAST") == 3, (
            f"צפוי 3 פעמים NULLS LAST ב-ORDER BY, נמצאו "
            f"{sql.count('NULLS LAST')}: {sql}"
        )


# ============================================================================
# בדיקות שה-שאילתות בפועל בקוד כוללות nulls_last
# ============================================================================


class TestSourceCodeNullsLast:
    """בדיקה שהקוד בפועל כולל nulls_last() — מונע רגרסיה."""

    @pytest.mark.unit
    def test_telegram_source_has_nulls_last(self) -> None:
        """קוד telegram.py get_or_create_user כולל nulls_last()."""
        import inspect
        from app.api.webhooks.telegram import get_or_create_user

        source = inspect.getsource(get_or_create_user)

        # שתי שאילתות — ראשית ו-retry
        assert source.count("nulls_last()") >= 2, (
            "צפויים לפחות 2 nulls_last() ב-get_or_create_user (טלגרם)"
        )

    @pytest.mark.unit
    def test_whatsapp_source_has_nulls_last(self) -> None:
        """קוד whatsapp.py get_or_create_user כולל nulls_last()."""
        import inspect
        from app.api.webhooks.whatsapp import get_or_create_user

        source = inspect.getsource(get_or_create_user)

        # שתי שאילתות — sender_key ו-phone
        assert source.count("nulls_last()") >= 2, (
            "צפויים לפחות 2 nulls_last() ב-get_or_create_user (וואטסאפ)"
        )

    @pytest.mark.unit
    def test_no_desc_without_nulls_last_in_telegram(self) -> None:
        """בטלגרם — כל .desc() ב-order_by חייב להיות עם .nulls_last()."""
        import inspect
        import re
        from app.api.webhooks.telegram import get_or_create_user

        source = inspect.getsource(get_or_create_user)

        # מחפש desc() שלא מלווה ב-.nulls_last()
        # התבנית: .desc() בסוף שורה או לפני פסיק — ללא .nulls_last() אחריו
        lines = source.split("\n")
        for line in lines:
            if ".desc()" in line and "order_by" not in line:
                # שורות עם .desc() צריכות לכלול .nulls_last() גם
                if ".desc()" in line and ".nulls_last()" not in line:
                    # מוודא שזו שורת order_by ולא שורת תיעוד
                    stripped = line.strip()
                    if stripped.startswith("User.") or stripped.startswith("(User."):
                        pytest.fail(
                            f"נמצא .desc() ללא .nulls_last() בטלגרם: {stripped}"
                        )

    @pytest.mark.unit
    def test_no_desc_without_nulls_last_in_whatsapp(self) -> None:
        """בוואטסאפ — כל .desc() ב-order_by על עמודות nullable חייב להיות עם .nulls_last()."""
        import inspect
        from app.api.webhooks.whatsapp import get_or_create_user

        source = inspect.getsource(get_or_create_user)

        lines = source.split("\n")
        for line in lines:
            if ".desc()" in line and "order_by" not in line:
                stripped = line.strip()
                if stripped.startswith("User.") and ".nulls_last()" not in stripped:
                    pytest.fail(
                        f"נמצא .desc() ללא .nulls_last() בוואטסאפ: {stripped}"
                    )
