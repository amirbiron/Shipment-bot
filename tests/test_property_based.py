"""
בדיקות property-based עם hypothesis לזרימות רב-שלביות.

בודקים אינווריאנטים על:
1. זרימת אישור/דחייה של שליחים — רצפי פעולות אקראיים
2. פירוס פקודות אישור/דחייה בוואטסאפ — קלט אקראי
"""
import itertools

import pytest
from hypothesis import given, assume, settings as h_settings, HealthCheck
from hypothesis.strategies import (
    sampled_from,
    text,
    integers,
    lists,
    one_of,
    just,
    none,
    composite,
)

from app.db.models.user import User, UserRole, ApprovalStatus
from app.domain.services.courier_approval_service import CourierApprovalService
from app.api.webhooks.whatsapp import _match_approval_command

# מונה גלובלי ליצירת מזהים ייחודיים בבדיקות property-based
_prop_counter = itertools.count(900000)


# ============================================================================
# אסטרטגיות (strategies)
# ============================================================================

# פעולות אפשריות בזרימת אישור/דחייה
APPROVAL_ACTIONS = sampled_from(["approve", "reject", "reject_with_note", "text_message", "timeout"])

# רצף פעולות אקראי (2-10 פעולות)
ACTION_SEQUENCES = lists(APPROVAL_ACTIONS, min_size=2, max_size=10)

# הערות דחייה אקראיות (טקסט עברי אמיתי + מחרוזות ריקות + None)
REJECTION_NOTES = one_of(
    just(None),
    just(""),
    just("התמונות לא ברורות"),
    just("חסר מסמך זהות"),
    just("צילום הרכב לא ברור"),
    text(min_size=1, max_size=200),
)


@composite
def approval_command_text(draw):
    """יוצר טקסט פקודת אישור/דחייה אקראית תקינה"""
    action = draw(sampled_from(["approve", "reject"]))
    user_id = draw(integers(min_value=1, max_value=999999))
    emoji = draw(sampled_from(["", "✅ ", "✔️ ", "☑️ ", "❌ ", "✖️ "]))
    prefix = draw(sampled_from(["", "שליח ", "נהג "]))
    bold = draw(sampled_from(["", "*"]))

    if action == "approve":
        verb = draw(sampled_from(["אשר", "אישור"]))
        return f"{bold}{emoji}{verb} {prefix}{user_id}{bold}"
    else:
        verb = draw(sampled_from(["דחה", "דחייה", "דחיה"]))
        note = draw(one_of(just(""), just(" התמונות לא ברורות"), just(" חסר מסמך")))
        return f"{bold}{emoji}{verb} {prefix}{user_id}{note}{bold}"


@composite
def garbage_text(draw):
    """יוצר טקסט שלא אמור להיות מזוהה כפקודת אישור/דחייה"""
    return draw(sampled_from([
        "שלום",
        "מה שלומך",
        "123",
        "אשר",  # בלי מספר
        "דחה",  # בלי מספר
        "הודעה רגילה",
        "תודה רבה על העזרה",
        "",
        " ",
        "🚗 רכב 4 מקומות",
        "קראתי ואני מאשר ✅",
        "אשר שליח",  # בלי מספר
        "דחה נהג",  # בלי מספר
    ]))


# ============================================================================
# בדיקות property-based לזרימת אישור/דחייה
# ============================================================================


class TestApprovalWorkflowProperties:
    """בדיקות property-based לאינווריאנטים של זרימת אישור/דחייה"""

    @pytest.mark.asyncio
    @given(actions=ACTION_SEQUENCES)
    @h_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    async def test_approval_status_always_valid(
        self, actions: list[str], db_session, user_factory
    ):
        """
        אינווריאנט: approval_status תמיד אחד מהערכים החוקיים,
        ללא קשר לרצף הפעולות.
        """
        uid = next(_prop_counter)
        user = await user_factory(
            phone_number=f"tg:prop_{uid}",
            name="Property Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id=f"prop_{uid}",
            approval_status=ApprovalStatus.PENDING,
        )

        valid_statuses = {s.value for s in ApprovalStatus}

        for action in actions:
            if action == "approve":
                await CourierApprovalService.approve(db_session, user.id)
            elif action == "reject":
                await CourierApprovalService.reject(db_session, user.id)
            elif action == "reject_with_note":
                await CourierApprovalService.reject(
                    db_session, user.id, rejection_note="הערת בדיקה"
                )
            elif action == "text_message":
                # סימולציה של הודעת טקסט רגילה — לא אמורה לשנות סטטוס
                pass
            elif action == "timeout":
                # סימולציה של timeout — לא אמורה לשנות סטטוס
                pass

            await db_session.refresh(user)
            assert user.approval_status.value in valid_statuses, (
                f"סטטוס לא חוקי: {user.approval_status} אחרי פעולה {action}"
            )

    @pytest.mark.asyncio
    @given(actions=ACTION_SEQUENCES)
    @h_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    async def test_approved_courier_cannot_become_rejected(
        self, actions: list[str], db_session, user_factory
    ):
        """
        אינווריאנט: שליח שאושר לא יכול לעבור ל-REJECTED
        דרך פעולות אישור/דחייה רגילות (ללא חסימת אדמין).
        """
        uid = next(_prop_counter)
        user = await user_factory(
            phone_number=f"tg:sim_{uid}",
            name="No Rollback Check",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id=f"sim_{uid}",
            approval_status=ApprovalStatus.PENDING,
        )

        was_approved = False
        for action in actions:
            if action == "approve":
                await CourierApprovalService.approve(db_session, user.id)
            elif action in ("reject", "reject_with_note"):
                note = "הערה" if action == "reject_with_note" else None
                await CourierApprovalService.reject(db_session, user.id, rejection_note=note)

            await db_session.refresh(user)
            if user.approval_status == ApprovalStatus.APPROVED:
                was_approved = True
            # ברגע שהשליח אושר, הוא לא יכול לחזור ל-REJECTED
            if was_approved:
                assert user.approval_status != ApprovalStatus.REJECTED, (
                    f"שליח שאושר עבר ל-REJECTED אחרי פעולה {action}"
                )

    @pytest.mark.asyncio
    @given(actions=ACTION_SEQUENCES, note=REJECTION_NOTES)
    @h_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    async def test_rejection_note_is_none_or_nonempty(
        self, actions: list[str], note: str | None, db_session, user_factory
    ):
        """
        אינווריאנט: rejection_note הוא תמיד None או מחרוזת לא-ריקה.
        מחרוזת ריקה ("") לא אמורה להישמר.
        """
        uid = next(_prop_counter)
        user = await user_factory(
            phone_number=f"tg:note_{uid}",
            name="Note Check",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id=f"note_{uid}",
            approval_status=ApprovalStatus.PENDING,
        )

        for action in actions:
            if action == "approve":
                await CourierApprovalService.approve(db_session, user.id)
            elif action == "reject":
                await CourierApprovalService.reject(db_session, user.id)
            elif action == "reject_with_note":
                # שימוש בהערה שנוצרה ע"י hypothesis
                actual_note = note if note else None
                await CourierApprovalService.reject(
                    db_session, user.id, rejection_note=actual_note
                )

            await db_session.refresh(user)
            # אם הסטטוס נדחה, ה-rejection_note חייב להיות None או לא-ריק
            if user.approval_status == ApprovalStatus.REJECTED:
                assert user.rejection_note is None or len(user.rejection_note) > 0, (
                    f"rejection_note לא חוקי: '{user.rejection_note}' "
                    f"(אמור להיות None או מחרוזת לא-ריקה)"
                )

    @pytest.mark.asyncio
    @given(actions=ACTION_SEQUENCES)
    @h_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
        deadline=None,
    )
    async def test_approve_after_approve_returns_failure(
        self, actions: list[str], db_session, user_factory
    ):
        """
        אינווריאנט: אישור שליח שכבר אושר תמיד נכשל (idempotency).
        """
        uid = next(_prop_counter)
        user = await user_factory(
            phone_number=f"tg:idem_{uid}",
            name="Idempotent Courier",
            role=UserRole.COURIER,
            platform="telegram",
            telegram_chat_id=f"idem_{uid}",
            approval_status=ApprovalStatus.PENDING,
        )

        approved_once = False
        for action in actions:
            if action == "approve":
                result = await CourierApprovalService.approve(db_session, user.id)
                if approved_once:
                    # אישור חוזר חייב להיכשל
                    assert result.success is False, (
                        "אישור כפול היה אמור להיכשל"
                    )
                elif result.success:
                    approved_once = True
            elif action in ("reject", "reject_with_note"):
                note = "הערה" if action == "reject_with_note" else None
                await CourierApprovalService.reject(db_session, user.id, rejection_note=note)

            await db_session.refresh(user)


# ============================================================================
# בדיקות property-based לפירוס פקודות WhatsApp
# ============================================================================


class TestApprovalCommandParsingProperties:
    """בדיקות property-based לפונקציית _match_approval_command"""

    @pytest.mark.unit
    @given(cmd=approval_command_text())
    @h_settings(max_examples=200, deadline=None)
    def test_valid_command_returns_tuple_or_none(self, cmd: str):
        """
        אינווריאנט: _match_approval_command תמיד מחזיר None
        או tuple בפורמט (str, int, str|None).
        """
        result = _match_approval_command(cmd)
        if result is not None:
            assert isinstance(result, tuple), f"תוצאה אמורה להיות tuple, קיבלנו: {type(result)}"
            assert len(result) == 3, f"אורך tuple אמור להיות 3, קיבלנו: {len(result)}"
            action, user_id, note = result
            assert action in ("approve", "reject"), f"פעולה לא חוקית: {action}"
            assert isinstance(user_id, int), f"user_id אמור להיות int: {type(user_id)}"
            assert user_id > 0, f"user_id אמור להיות חיובי: {user_id}"
            assert note is None or isinstance(note, str), f"note אמור להיות None או str: {type(note)}"

    @pytest.mark.unit
    @given(cmd=approval_command_text())
    @h_settings(max_examples=200, deadline=None)
    def test_rejection_note_is_none_or_nonempty_string(self, cmd: str):
        """
        אינווריאנט: אם הפירוס מחזיר הערת דחייה,
        היא תמיד None או מחרוזת לא-ריקה (לא "").
        """
        result = _match_approval_command(cmd)
        if result is not None:
            _, _, note = result
            if note is not None:
                assert len(note.strip()) > 0, (
                    f"הערת דחייה ריקה או רווחים בלבד: '{note}'"
                )

    @pytest.mark.unit
    @given(cmd=approval_command_text())
    @h_settings(max_examples=200, deadline=None)
    def test_approve_command_never_has_rejection_note(self, cmd: str):
        """
        אינווריאנט: פקודת אישור לעולם לא מחזירה הערת דחייה.
        """
        result = _match_approval_command(cmd)
        if result is not None:
            action, _, note = result
            if action == "approve":
                assert note is None, (
                    f"פקודת אישור עם הערה: '{note}' עבור קלט: '{cmd}'"
                )

    @pytest.mark.unit
    @given(cmd=garbage_text())
    @h_settings(max_examples=100, deadline=None)
    def test_garbage_input_returns_none(self, cmd: str):
        """
        אינווריאנט: טקסט שאינו פקודה מחזיר None.
        """
        result = _match_approval_command(cmd)
        assert result is None, (
            f"טקסט לא-פקודה זוהה כפקודה: '{cmd}' -> {result}"
        )

    @pytest.mark.unit
    @given(user_id=integers(min_value=1, max_value=999999))
    @h_settings(max_examples=100, deadline=None)
    def test_approve_preserves_user_id(self, user_id: int):
        """
        אינווריאנט: ה-user_id שנכנס הוא אותו user_id שיוצא.
        """
        result = _match_approval_command(f"אשר {user_id}")
        assert result is not None, f"פקודת אישור לא זוהתה: 'אשר {user_id}'"
        _, parsed_id, _ = result
        assert parsed_id == user_id, (
            f"user_id השתנה: {user_id} -> {parsed_id}"
        )

    @pytest.mark.unit
    @given(user_id=integers(min_value=1, max_value=999999))
    @h_settings(max_examples=100, deadline=None)
    def test_reject_preserves_user_id(self, user_id: int):
        """
        אינווריאנט: ה-user_id שנכנס הוא אותו user_id שיוצא בדחייה.
        """
        result = _match_approval_command(f"דחה {user_id}")
        assert result is not None, f"פקודת דחייה לא זוהתה: 'דחה {user_id}'"
        _, parsed_id, _ = result
        assert parsed_id == user_id, (
            f"user_id השתנה: {user_id} -> {parsed_id}"
        )

    @pytest.mark.unit
    @given(
        user_id=integers(min_value=1, max_value=999999),
        note_text=text(min_size=1, max_size=100).filter(lambda s: s.strip()),
    )
    @h_settings(max_examples=100, deadline=None)
    def test_reject_with_note_preserves_note_content(self, user_id: int, note_text: str):
        """
        אינווריאנט: הערת הדחייה נשמרת כפי שהוזנה (אחרי strip).
        """
        # מסננים מקרים שבהם ה-note מכיל תווים שמפריעים לרגקס
        assume("\n" not in note_text)
        assume("\t" not in note_text)
        assume("*" not in note_text)

        cmd = f"דחה {user_id} {note_text}"
        result = _match_approval_command(cmd)
        if result is not None:
            action, parsed_id, parsed_note = result
            if action == "reject" and parsed_id == user_id and parsed_note is not None:
                # ההערה אמורה להתחיל עם אותו תוכן (אחרי נרמול רווחים)
                import re
                normalized_input = re.sub(r'\s+', ' ', note_text).strip()
                assert parsed_note == normalized_input, (
                    f"הערה השתנתה: '{normalized_input}' -> '{parsed_note}'"
                )

    @pytest.mark.unit
    @given(random_text=text(min_size=0, max_size=500))
    @h_settings(max_examples=200, deadline=None)
    def test_never_crashes_on_arbitrary_input(self, random_text: str):
        """
        אינווריאנט: הפונקציה לעולם לא זורקת exception — תמיד None או tuple חוקי.
        """
        try:
            result = _match_approval_command(random_text)
        except Exception as e:
            pytest.fail(f"הפונקציה זרקה exception על קלט: '{random_text[:100]}...': {e}")

        if result is not None:
            assert isinstance(result, tuple) and len(result) == 3
            action, user_id, note = result
            assert action in ("approve", "reject")
            assert isinstance(user_id, int) and user_id > 0
            assert note is None or (isinstance(note, str) and len(note) > 0)
