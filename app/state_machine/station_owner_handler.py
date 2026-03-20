"""
Station Owner State Handler - פאנל ניהול תחנה [שלב 3.3]

בעל תחנה מנהל:
- סדרנים (הוספה/הסרה לפי מספר טלפון)
- ארנק תחנה (10% עמלה מכל משלוח)
- דוח גבייה (ה-28 לחודש)
- רשימה שחורה (נהגים שלא שילמו חודשיים רצופים)
"""

from decimal import Decimal
from typing import Tuple
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.state_machine.states import StationOwnerState
from app.state_machine.manager import StateManager
from app.state_machine.handlers import MessageResponse
from app.db.models.user import User
from app.domain.services.station_service import StationService
from app.core.logging import get_logger
from app.core.validation import PhoneNumberValidator

logger = get_logger(__name__)


class StationOwnerStateHandler:
    """Handler לפאנל ניהול תחנה"""

    def __init__(self, db: AsyncSession, station_id: int, platform: str = "telegram"):
        self.db = db
        self.station_id = station_id
        self.platform = platform
        self.state_manager = StateManager(db)
        self.station_service = StationService(db)

    # מפתחות קונטקסט של ניהול סדרנים ורשימה שחורה — מנוקים בחזרה ל-MENU
    _MANAGEMENT_CONTEXT_KEYS = {
        # ניהול בעלים
        "owner_map",
        "remove_owner_id",
        "remove_owner_name",
        # ניהול סדרנים
        "dispatcher_map",
        "remove_dispatcher_id",
        "remove_dispatcher_name",
        # רשימה שחורה
        "blacklist_phone",
        "blacklist_map",
        "remove_blacklist_courier_id",
        "remove_blacklist_name",
        # הגדרות קבוצות
        "public_group_id",
        "private_group_id",
        # הגדרות תחנה מורחבות
        "edit_hours_day",
    }

    def _is_multi_step_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימה רב-שלבית (לא MENU)"""
        return state.startswith("STATION.") and state != StationOwnerState.MENU.value

    async def handle_message(
        self, user: User, message: str, photo_file_id: str = None
    ) -> Tuple[MessageResponse, str]:
        """עיבוד הודעה נכנסת מבעל תחנה"""
        platform = self.platform or user.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(user, message, context)

        # ניקוי קונטקסט ניהולי בחזרה ל-MENU מזרימה רב-שלבית
        if (
            new_state == StationOwnerState.MENU.value
            and self._is_multi_step_flow_state(current_state)
        ):
            clean_context = {
                k: v
                for k, v in context.items()
                if k not in self._MANAGEMENT_CONTEXT_KEYS
            }
            if context_update:
                for k, v in context_update.items():
                    if k not in self._MANAGEMENT_CONTEXT_KEYS:
                        clean_context[k] = v
            await self.state_manager.force_state(
                user.id, platform, new_state, clean_context
            )
            return response, new_state

        if new_state != current_state:
            # ניסיון מעבר מצב עם ולידציה
            success = await self.state_manager.transition_to(
                user.id, platform, new_state, context_update
            )
            if not success:
                # המעבר נכשל - כפיית מעבר (דילוג על ולידציה)
                logger.info(
                    "כפיית מעבר מצב בבעל תחנה",
                    extra_data={
                        "user_id": user.id,
                        "platform": platform,
                        "current_state": current_state,
                        "new_state": new_state,
                    },
                )
                await self.state_manager.force_state(
                    user.id,
                    platform,
                    new_state,
                    {**context, **context_update} if context_update else context,
                )
        elif context_update:
            for key, value in context_update.items():
                await self.state_manager.update_context(user.id, platform, key, value)

        return response, new_state

    def _get_handler(self, state: str):
        """ניתוב ל-handler המתאים"""
        handlers = {
            StationOwnerState.MENU.value: self._handle_menu,
            # ניהול בעלים
            StationOwnerState.MANAGE_OWNERS.value: self._handle_manage_owners,
            StationOwnerState.ADD_OWNER_PHONE.value: self._handle_add_owner,
            StationOwnerState.REMOVE_OWNER_SELECT.value: self._handle_remove_owner_select,
            StationOwnerState.CONFIRM_REMOVE_OWNER.value: self._handle_confirm_remove_owner,
            # ניהול סדרנים
            StationOwnerState.MANAGE_DISPATCHERS.value: self._handle_manage_dispatchers,
            StationOwnerState.ADD_DISPATCHER_PHONE.value: self._handle_add_dispatcher,
            StationOwnerState.REMOVE_DISPATCHER_SELECT.value: self._handle_remove_dispatcher_select,
            StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value: self._handle_confirm_remove_dispatcher,
            # ארנק תחנה
            StationOwnerState.VIEW_WALLET.value: self._handle_view_wallet,
            StationOwnerState.SET_COMMISSION_RATE.value: self._handle_set_commission_rate,
            # דוח גבייה
            StationOwnerState.COLLECTION_REPORT.value: self._handle_collection_report,
            # רשימה שחורה
            StationOwnerState.VIEW_BLACKLIST.value: self._handle_view_blacklist,
            StationOwnerState.ADD_BLACKLIST_PHONE.value: self._handle_add_blacklist_phone,
            StationOwnerState.ADD_BLACKLIST_REASON.value: self._handle_add_blacklist_reason,
            StationOwnerState.REMOVE_BLACKLIST_SELECT.value: self._handle_remove_blacklist_select,
            StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value: self._handle_confirm_remove_blacklist,
            # שלב 4: הגדרות קבוצות
            StationOwnerState.GROUP_SETTINGS.value: self._handle_group_settings,
            StationOwnerState.SET_PUBLIC_GROUP.value: self._handle_set_public_group,
            StationOwnerState.SET_PRIVATE_GROUP.value: self._handle_set_private_group,
            # סעיף 8: הגדרות תחנה מורחבות
            StationOwnerState.STATION_SETTINGS.value: self._handle_station_settings,
            StationOwnerState.EDIT_STATION_NAME.value: self._handle_edit_name,
            StationOwnerState.EDIT_STATION_DESCRIPTION.value: self._handle_edit_description,
            StationOwnerState.EDIT_OPERATING_HOURS.value: self._handle_edit_operating_hours,
            StationOwnerState.EDIT_SERVICE_AREAS.value: self._handle_edit_service_areas,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== תפריט ראשי ====================

    async def _show_menu(self, user: User, context: dict):
        """הצגת תפריט ראשי ללא ניתוב לפי תוכן הודעה"""
        station = await self.station_service.get_station(self.station_id)
        station_name = station.name if station else "תחנה"

        wallet = await self.station_service.get_station_wallet(self.station_id)
        balance = wallet.balance if wallet else 0.0

        from app.core.message_design import station_panel_card

        response = MessageResponse(
            station_panel_card(
                station_name=station_name,
                balance=f"{balance:.2f}",
            ),
            keyboard=[
                ["👤 ניהול בעלים", "👥 ניהול סדרנים"],
                ["💰 ארנק תחנה", "📊 דוח גבייה"],
                ["🚫 רשימה שחורה", "⚙️ הגדרות קבוצות"],
                ["🏪 הגדרות תחנה"],
            ],
        )
        return response, StationOwnerState.MENU.value, {}

    async def _handle_menu(self, user: User, message: str, context: dict):
        """תפריט ראשי של בעל תחנה"""
        msg = message.strip()

        if "ניהול בעלים" in msg or "בעלים" in msg:
            return await self._show_manage_owners(user, context)

        if "סדרנים" in msg or "ניהול סדרנים" in msg:
            return await self._show_manage_dispatchers(user, context)

        if "ארנק" in msg or "כספים" in msg:
            return await self._show_wallet(user, context)

        if "גבייה" in msg or "דוח" in msg:
            return await self._show_collection_report(user, context)

        if "רשימה שחורה" in msg or "חסימה" in msg or "שחורה" in msg:
            return await self._show_blacklist(user, context)

        if "הגדרות קבוצות" in msg or "קבוצות" in msg:
            return await self._show_group_settings(user, context)

        if "הגדרות תחנה" in msg:
            return await self._show_station_settings(user, context)

        return await self._show_menu(user, context)

    # ==================== ניהול בעלים ====================

    async def _show_manage_owners(self, user: User, context: dict):
        """הצגת מסך ניהול בעלים"""
        owners = await self.station_service.get_owners(self.station_id)

        text = "👤 <b>ניהול בעלים</b>\n\n"
        if owners:
            for i, o in enumerate(owners, 1):
                result = await self.db.execute(select(User).where(User.id == o.user_id))
                owner_user = result.scalar_one_or_none()
                name = (
                    (owner_user.full_name or owner_user.name or "לא ידוע")
                    if owner_user
                    else "לא ידוע"
                )
                is_self = " (אתה)" if o.user_id == user.id else ""
                text += f"{i}. {escape(name)}{is_self}\n"
        else:
            text += "אין בעלים רשומים.\n"

        text += "\nבחר פעולה:"

        response = MessageResponse(
            text,
            keyboard=[
                ["➕ הוספת בעלים", "➖ הסרת בעלים"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.MANAGE_OWNERS.value, {}

    async def _handle_manage_owners(self, user: User, message: str, context: dict):
        """תפריט ניהול בעלים"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "הוספת" in message or "הוספה" in message:
            response = MessageResponse(
                "👤 <b>הוספת בעלים</b>\n\n" "הזן את מספר הטלפון של הבעלים החדש:"
            )
            return response, StationOwnerState.ADD_OWNER_PHONE.value, {}

        if "הסרה" in message or "הסר" in message:
            return await self._show_owner_list_for_removal(user, context)

        return await self._show_manage_owners(user, context)

    async def _handle_add_owner(self, user: User, message: str, context: dict):
        """הוספת בעלים לפי מספר טלפון"""
        if "חזרה" in message:
            return await self._show_manage_owners(user, context)

        phone = message.strip()
        success, msg = await self.station_service.add_owner(self.station_id, phone)

        response = MessageResponse(
            msg,
            keyboard=[
                ["➕ הוספת בעלים", "➖ הסרת בעלים"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.MANAGE_OWNERS.value, {}

    async def _show_owner_list_for_removal(self, user: User, context: dict):
        """הצגת רשימת בעלים להסרה"""
        owners = await self.station_service.get_owners(self.station_id)

        if len(owners) <= 1:
            response = MessageResponse(
                "לא ניתן להסיר בעלים — חייב להישאר לפחות בעלים אחד בתחנה.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, StationOwnerState.MANAGE_OWNERS.value, {}

        text = "➖ <b>הסרת בעלים</b>\n\nבחר בעלים להסרה:\n\n"
        keyboard_items = []
        owner_map = {}

        for i, o in enumerate(owners, 1):
            result = await self.db.execute(select(User).where(User.id == o.user_id))
            owner_user = result.scalar_one_or_none()
            name = (
                (owner_user.full_name or owner_user.name or "לא ידוע")
                if owner_user
                else "לא ידוע"
            )
            is_self = " (אתה)" if o.user_id == user.id else ""
            text += f"{i}. {escape(name)}{is_self}\n"
            keyboard_items.append([f"הסר {i}"])
            owner_map[str(i)] = o.user_id

        keyboard_items.append(["🔙 חזרה"])

        response = MessageResponse(text, keyboard=keyboard_items)
        return (
            response,
            StationOwnerState.REMOVE_OWNER_SELECT.value,
            {"owner_map": owner_map},
        )

    async def _handle_remove_owner_select(
        self, user: User, message: str, context: dict
    ):
        """בחירת בעלים להסרה — מעביר לשלב אישור"""
        if "חזרה" in message:
            return await self._show_manage_owners(user, context)

        import re

        numbers = re.findall(r"\d+", message)
        owner_map = context.get("owner_map", {})

        if numbers and numbers[0] in owner_map:
            owner_user_id = owner_map[numbers[0]]
            # שליפת שם הבעלים להצגה בהודעת האישור
            result = await self.db.execute(select(User).where(User.id == owner_user_id))
            owner_user = result.scalar_one_or_none()
            name = (
                (owner_user.full_name or owner_user.name or "לא ידוע")
                if owner_user
                else "לא ידוע"
            )

            response = MessageResponse(
                f"⚠️ <b>אישור הסרת בעלים</b>\n\n"
                f"האם אתה בטוח שברצונך להסיר את <b>{escape(name)}</b> מרשימת הבעלים?",
                keyboard=[["✅ כן, הסר", "❌ ביטול"]],
            )
            return (
                response,
                StationOwnerState.CONFIRM_REMOVE_OWNER.value,
                {
                    "remove_owner_id": owner_user_id,
                    "remove_owner_name": name,
                },
            )

        # בחירה לא תקינה — מציגים מחדש את רשימת הבעלים עם הכפתורים
        return await self._show_owner_list_for_removal(user, context)

    async def _handle_confirm_remove_owner(
        self, user: User, message: str, context: dict
    ):
        """אישור הסרת בעלים"""
        if "ביטול" in message or "❌" in message:
            return await self._show_manage_owners(user, context)

        if "כן" in message or "✅" in message or "הסר" in message:
            owner_user_id = context.get("remove_owner_id")
            if not owner_user_id:
                return await self._show_manage_owners(user, context)

            success, msg = await self.station_service.remove_owner(
                self.station_id, owner_user_id
            )
            response = MessageResponse(
                msg,
                keyboard=[
                    ["➕ הוספת בעלים", "➖ הסרת בעלים"],
                    ["🔙 חזרה לתפריט"],
                ],
            )
            return response, StationOwnerState.MANAGE_OWNERS.value, {}

        response = MessageResponse(
            "אנא בחר:\n✅ כן, הסר\n❌ ביטול",
            keyboard=[["✅ כן, הסר", "❌ ביטול"]],
        )
        return response, StationOwnerState.CONFIRM_REMOVE_OWNER.value, {}

    # ==================== ניהול סדרנים ====================

    async def _show_manage_dispatchers(self, user: User, context: dict):
        """הצגת מסך ניהול סדרנים ללא ניתוב לפי תוכן הודעה"""
        dispatchers = await self.station_service.get_dispatchers(self.station_id)

        text = "👥 <b>ניהול סדרנים</b>\n\n"
        if dispatchers:
            for i, d in enumerate(dispatchers, 1):
                result = await self.db.execute(select(User).where(User.id == d.user_id))
                dispatcher_user = result.scalar_one_or_none()
                name = (
                    (dispatcher_user.full_name or dispatcher_user.name or "לא ידוע")
                    if dispatcher_user
                    else "לא ידוע"
                )
                text += f"{i}. {escape(name)}\n"
        else:
            text += "אין סדרנים רשומים עדיין.\n"

        text += "\nבחר פעולה:"

        response = MessageResponse(
            text,
            keyboard=[
                ["➕ הוספת סדרן", "➖ הסרת סדרן"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

    async def _handle_manage_dispatchers(self, user: User, message: str, context: dict):
        """תפריט ניהול סדרנים"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "הוספת" in message or "הוספה" in message:
            response = MessageResponse(
                "👥 <b>הוספת סדרן</b>\n\n" "הזן את מספר הטלפון של הסדרן:"
            )
            return response, StationOwnerState.ADD_DISPATCHER_PHONE.value, {}

        if "הסרה" in message or "הסר" in message:
            return await self._show_dispatcher_list_for_removal(user, context)

        return await self._show_manage_dispatchers(user, context)

    async def _handle_add_dispatcher(self, user: User, message: str, context: dict):
        """הוספת סדרן לפי מספר טלפון"""
        if "חזרה" in message:
            return await self._show_manage_dispatchers(user, context)

        phone = message.strip()
        success, msg = await self.station_service.add_dispatcher(self.station_id, phone)

        response = MessageResponse(
            msg,
            keyboard=[
                ["➕ הוספת סדרן", "➖ הסרת סדרן"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

    async def _show_dispatcher_list_for_removal(self, user: User, context: dict):
        """הצגת רשימת סדרנים להסרה"""
        dispatchers = await self.station_service.get_dispatchers(self.station_id)

        if not dispatchers:
            response = MessageResponse(
                "אין סדרנים להסרה.", keyboard=[["🔙 חזרה לתפריט"]]
            )
            return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

        text = "➖ <b>הסרת סדרן</b>\n\nבחר סדרן להסרה:\n\n"
        keyboard_items = []
        dispatcher_map = {}

        for i, d in enumerate(dispatchers, 1):
            result = await self.db.execute(select(User).where(User.id == d.user_id))
            dispatcher_user = result.scalar_one_or_none()
            name = (
                (dispatcher_user.full_name or dispatcher_user.name or "לא ידוע")
                if dispatcher_user
                else "לא ידוע"
            )
            text += f"{i}. {escape(name)}\n"
            keyboard_items.append([f"הסר {i}"])
            dispatcher_map[str(i)] = d.user_id

        keyboard_items.append(["🔙 חזרה"])

        response = MessageResponse(text, keyboard=keyboard_items)
        return (
            response,
            StationOwnerState.REMOVE_DISPATCHER_SELECT.value,
            {"dispatcher_map": dispatcher_map},
        )

    async def _handle_remove_dispatcher_select(
        self, user: User, message: str, context: dict
    ):
        """בחירת סדרן להסרה — מעביר לשלב אישור"""
        if "חזרה" in message:
            return await self._show_manage_dispatchers(user, context)

        import re

        numbers = re.findall(r"\d+", message)
        dispatcher_map = context.get("dispatcher_map", {})

        if numbers and numbers[0] in dispatcher_map:
            dispatcher_user_id = dispatcher_map[numbers[0]]
            # שליפת שם הסדרן להצגה בהודעת האישור
            result = await self.db.execute(
                select(User).where(User.id == dispatcher_user_id)
            )
            dispatcher_user = result.scalar_one_or_none()
            name = (
                (dispatcher_user.full_name or dispatcher_user.name or "לא ידוע")
                if dispatcher_user
                else "לא ידוע"
            )

            response = MessageResponse(
                f"⚠️ <b>אישור הסרת סדרן</b>\n\n"
                f"האם אתה בטוח שברצונך להסיר את <b>{escape(name)}</b> מרשימת הסדרנים?",
                keyboard=[["✅ כן, הסר", "❌ ביטול"]],
            )
            return (
                response,
                StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value,
                {
                    "remove_dispatcher_id": dispatcher_user_id,
                    "remove_dispatcher_name": name,
                },
            )

        response = MessageResponse(
            "בחירה לא תקינה. אנא בחר מספר מהרשימה.", keyboard=[["🔙 חזרה"]]
        )
        return response, StationOwnerState.REMOVE_DISPATCHER_SELECT.value, {}

    async def _handle_confirm_remove_dispatcher(
        self, user: User, message: str, context: dict
    ):
        """אישור הסרת סדרן"""
        if "ביטול" in message or "❌" in message:
            return await self._show_manage_dispatchers(user, context)

        if "כן" in message or "✅" in message or "הסר" in message:
            dispatcher_user_id = context.get("remove_dispatcher_id")
            if not dispatcher_user_id:
                return await self._show_manage_dispatchers(user, context)

            success, msg = await self.station_service.remove_dispatcher(
                self.station_id, dispatcher_user_id
            )
            response = MessageResponse(
                msg,
                keyboard=[
                    ["➕ הוספת סדרן", "➖ הסרת סדרן"],
                    ["🔙 חזרה לתפריט"],
                ],
            )
            return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

        response = MessageResponse(
            "אנא בחר:\n✅ כן, הסר\n❌ ביטול",
            keyboard=[["✅ כן, הסר", "❌ ביטול"]],
        )
        return response, StationOwnerState.CONFIRM_REMOVE_DISPATCHER.value, {}

    # ==================== ארנק תחנה ====================

    async def _show_wallet(self, user: User, context: dict):
        """הצגת ארנק תחנה ללא ניתוב לפי תוכן הודעה"""
        wallet = await self.station_service.get_station_wallet(self.station_id)
        ledger = await self.station_service.get_station_ledger(self.station_id)

        from app.core.message_design import station_wallet_card

        ledger_lines = None
        if ledger:
            ledger_lines = []
            for entry in ledger[:5]:
                sign = "+" if entry.amount > 0 else ""
                ledger_lines.append(
                    f"{sign}{entry.amount:.2f} ₪ | {escape(entry.description or '')}"
                )

        text = station_wallet_card(
            balance=f"{wallet.balance:.2f}",
            commission_rate=f"{wallet.commission_rate * 100:.0f}",
            ledger_lines=ledger_lines,
        )

        response = MessageResponse(
            text,
            keyboard=[
                ["📊 שינוי אחוז עמלה"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.VIEW_WALLET.value, {}

    async def _handle_view_wallet(self, user: User, message: str, context: dict):
        """צפייה בארנק התחנה — עם אפשרות לשנות אחוז עמלה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "עמלה" in message or "אחוז" in message:
            return await self._show_set_commission_rate(user, context)

        return await self._show_wallet(user, context)

    def _commission_rate_keyboard(self) -> list[list[str]]:
        """כפתורי בחירת אחוז עמלה — נגזרים מקבועי השירות"""
        lo = StationService.COMMISSION_MIN_PCT
        hi = StationService.COMMISSION_MAX_PCT + 1
        mid = lo + (hi - lo) // 2
        return [
            [f"{pct}%" for pct in range(lo, mid)],
            [f"{pct}%" for pct in range(mid, hi)],
            ["🔙 חזרה"],
        ]

    async def _show_set_commission_rate(self, user: User, context: dict):
        """הצגת מסך בחירת אחוז עמלה"""
        wallet = await self.station_service.get_station_wallet(self.station_id)
        current_pct = int(wallet.commission_rate * 100)
        lo = StationService.COMMISSION_MIN_PCT
        hi = StationService.COMMISSION_MAX_PCT

        text = (
            "📊 <b>שינוי אחוז עמלה</b>\n\n"
            f"אחוז עמלה נוכחי: <b>{current_pct}%</b>\n\n"
            f"בחר אחוז עמלה חדש ({lo}%–{hi}%):"
        )

        keyboard = self._commission_rate_keyboard()
        response = MessageResponse(text, keyboard=keyboard)
        return response, StationOwnerState.SET_COMMISSION_RATE.value, {}

    async def _handle_set_commission_rate(
        self, user: User, message: str, context: dict
    ):
        """עדכון אחוז עמלה — מקבל מספר מ-6 עד 12"""
        if "חזרה" in message:
            return await self._show_wallet(user, context)

        lo = StationService.COMMISSION_MIN_PCT
        hi = StationService.COMMISSION_MAX_PCT

        import re

        numbers = re.findall(r"\d+", message)
        if not numbers:
            response = MessageResponse(
                f"אנא בחר אחוז עמלה מהכפתורים או הזן מספר בין {lo} ל-{hi}.",
                keyboard=self._commission_rate_keyboard(),
            )
            return response, StationOwnerState.SET_COMMISSION_RATE.value, {}

        pct = int(numbers[0])
        # המרה ל-Decimal דרך מחרוזת — מונע הפתעות עיגול
        new_rate = Decimal(pct) / Decimal("100")

        success, msg = await self.station_service.update_commission_rate(
            self.station_id,
            float(new_rate),
            actor_user_id=user.id,
        )

        if success:
            logger.info(
                "אחוז עמלה עודכן דרך הבוט",
                extra_data={
                    "station_id": self.station_id,
                    "user_id": user.id,
                    "new_rate_percent": pct,
                },
            )
            # מציג את הארנק המעודכן
            return await self._show_wallet(user, context)

        # שגיאת ולידציה — מציגים שוב את מסך הבחירה
        response = MessageResponse(
            f"{msg}\n\nבחר אחוז עמלה בין {lo}% ל-{hi}%:",
            keyboard=self._commission_rate_keyboard(),
        )
        return response, StationOwnerState.SET_COMMISSION_RATE.value, {}

    # ==================== דוח גבייה ====================

    async def _show_collection_report(self, user: User, context: dict):
        """הצגת דוח גבייה ללא ניתוב לפי תוכן הודעה"""
        report = await self.station_service.get_collection_report(self.station_id)

        text = "📊 <b>דוח גבייה</b>\n\n"
        text += "מחזור חיוב: ה-28 לחודש עד ה-28 בחודש הבא\n\n"

        if report:
            text += "<b>נהגים עם חוב:</b>\n"
            total = Decimal("0")
            for item in report:
                name = item["driver_name"]
                debt = item["total_debt"]
                text += f"  👤 {escape(name)}: {debt:.2f} ₪\n"
                total += debt
            text += f'\n<b>סה"כ חוב: {total:.2f} ₪</b>'
        else:
            text += "אין חובות פתוחים. 🎉"

        response = MessageResponse(text, keyboard=[["🔙 חזרה לתפריט"]])
        return response, StationOwnerState.COLLECTION_REPORT.value, {}

    async def _handle_collection_report(self, user: User, message: str, context: dict):
        """דוח גבייה - ה-28 לכל חודש"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        return await self._show_collection_report(user, context)

    # ==================== רשימה שחורה ====================

    async def _show_blacklist(self, user: User, context: dict):
        """הצגת רשימה שחורה ללא ניתוב לפי תוכן הודעה"""
        blacklist = await self.station_service.get_blacklist(self.station_id)

        text = "🚫 <b>רשימה שחורה</b>\n\n"
        text += "נהגים שלא שילמו חודשיים רצופים נחסמים מהתחנה בלבד.\n\n"

        if blacklist:
            for i, entry in enumerate(blacklist, 1):
                result = await self.db.execute(
                    select(User).where(User.id == entry.courier_id)
                )
                blocked_user = result.scalar_one_or_none()
                name = (
                    (blocked_user.full_name or blocked_user.name or "לא ידוע")
                    if blocked_user
                    else "לא ידוע"
                )
                reason = entry.reason or "אי תשלום"
                text += f"{i}. {escape(name)} - {escape(reason)}\n"
        else:
            text += "הרשימה ריקה. 👍"

        response = MessageResponse(
            text,
            keyboard=[
                ["➕ הוספת נהג לרשימה", "➖ הסרת נהג מהרשימה"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.VIEW_BLACKLIST.value, {}

    async def _handle_view_blacklist(self, user: User, message: str, context: dict):
        """צפייה ברשימה השחורה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "הוספת" in message or "הוספה" in message or "חסום" in message:
            response = MessageResponse(
                "🚫 <b>הוספה לרשימה שחורה</b>\n\n" "הזן את מספר הטלפון של הנהג:"
            )
            return response, StationOwnerState.ADD_BLACKLIST_PHONE.value, {}

        if "הסרה" in message or "הסר" in message or "שחרר" in message:
            return await self._show_blacklist_for_removal(user, context)

        return await self._show_blacklist(user, context)

    async def _handle_add_blacklist_phone(
        self, user: User, message: str, context: dict
    ):
        """הוספת נהג לרשימה שחורה - שלב מספר טלפון"""
        if "חזרה" in message:
            return await self._show_blacklist(user, context)

        phone = message.strip()
        if not PhoneNumberValidator.validate(phone):
            response = MessageResponse("מספר טלפון לא תקין. אנא הזן מספר תקין:")
            return response, StationOwnerState.ADD_BLACKLIST_PHONE.value, {}

        response = MessageResponse(
            f"טלפון: {PhoneNumberValidator.mask(phone)} ✓\n\n" "📝 סיבת החסימה:"
        )
        return (
            response,
            StationOwnerState.ADD_BLACKLIST_REASON.value,
            {"blacklist_phone": phone},
        )

    async def _handle_add_blacklist_reason(
        self, user: User, message: str, context: dict
    ):
        """הוספת נהג לרשימה שחורה - שלב סיבה"""
        if "חזרה" in message:
            return await self._show_blacklist(user, context)

        reason = message.strip()
        phone = context.get("blacklist_phone", "")

        success, msg = await self.station_service.add_to_blacklist(
            self.station_id, phone, reason
        )

        response = MessageResponse(
            msg,
            keyboard=[
                ["➕ הוספת נהג לרשימה", "➖ הסרת נהג מהרשימה"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.VIEW_BLACKLIST.value, {}

    async def _show_blacklist_for_removal(self, user: User, context: dict):
        """הצגת רשימה שחורה להסרה"""
        blacklist = await self.station_service.get_blacklist(self.station_id)

        if not blacklist:
            response = MessageResponse(
                "הרשימה השחורה ריקה, אין מי להסיר.", keyboard=[["🔙 חזרה"]]
            )
            return response, StationOwnerState.VIEW_BLACKLIST.value, {}

        text = "➖ <b>הסרה מרשימה שחורה</b>\n\nבחר נהג להסרה:\n\n"
        keyboard_items = []
        blacklist_map = {}

        for i, entry in enumerate(blacklist, 1):
            result = await self.db.execute(
                select(User).where(User.id == entry.courier_id)
            )
            blocked_user = result.scalar_one_or_none()
            name = (
                (blocked_user.full_name or blocked_user.name or "לא ידוע")
                if blocked_user
                else "לא ידוע"
            )
            text += f"{i}. {escape(name)}\n"
            keyboard_items.append([f"הסר {i}"])
            blacklist_map[str(i)] = entry.courier_id

        keyboard_items.append(["🔙 חזרה"])

        response = MessageResponse(text, keyboard=keyboard_items)
        return (
            response,
            StationOwnerState.REMOVE_BLACKLIST_SELECT.value,
            {"blacklist_map": blacklist_map},
        )

    async def _handle_remove_blacklist_select(
        self, user: User, message: str, context: dict
    ):
        """בחירת נהג להסרה מרשימה שחורה — מעביר לשלב אישור"""
        if "חזרה" in message:
            return await self._show_blacklist(user, context)

        import re

        numbers = re.findall(r"\d+", message)
        blacklist_map = context.get("blacklist_map", {})

        if numbers and numbers[0] in blacklist_map:
            courier_id = blacklist_map[numbers[0]]
            # שליפת שם הנהג להצגה בהודעת האישור
            result = await self.db.execute(select(User).where(User.id == courier_id))
            blocked_user = result.scalar_one_or_none()
            name = (
                (blocked_user.full_name or blocked_user.name or "לא ידוע")
                if blocked_user
                else "לא ידוע"
            )

            response = MessageResponse(
                f"⚠️ <b>אישור הסרה מרשימה שחורה</b>\n\n"
                f"האם אתה בטוח שברצונך להסיר את <b>{escape(name)}</b> מהרשימה השחורה?",
                keyboard=[["✅ כן, הסר", "❌ ביטול"]],
            )
            return (
                response,
                StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value,
                {
                    "remove_blacklist_courier_id": courier_id,
                    "remove_blacklist_name": name,
                },
            )

        response = MessageResponse(
            "בחירה לא תקינה. אנא בחר מספר מהרשימה.", keyboard=[["🔙 חזרה"]]
        )
        return response, StationOwnerState.REMOVE_BLACKLIST_SELECT.value, {}

    async def _handle_confirm_remove_blacklist(
        self, user: User, message: str, context: dict
    ):
        """אישור הסרת נהג מרשימה שחורה"""
        if "ביטול" in message or "❌" in message:
            return await self._show_blacklist(user, context)

        if "כן" in message or "✅" in message or "הסר" in message:
            courier_id = context.get("remove_blacklist_courier_id")
            if not courier_id:
                return await self._show_blacklist(user, context)

            success, msg = await self.station_service.remove_from_blacklist(
                self.station_id, courier_id
            )
            response = MessageResponse(
                msg,
                keyboard=[
                    ["➕ הוספת נהג לרשימה", "➖ הסרת נהג מהרשימה"],
                    ["🔙 חזרה לתפריט"],
                ],
            )
            return response, StationOwnerState.VIEW_BLACKLIST.value, {}

        response = MessageResponse(
            "אנא בחר:\n✅ כן, הסר\n❌ ביטול",
            keyboard=[["✅ כן, הסר", "❌ ביטול"]],
        )
        return response, StationOwnerState.CONFIRM_REMOVE_BLACKLIST.value, {}

    # ==================== שלב 4: הגדרות קבוצות ====================

    async def _show_group_settings(self, user: User, context: dict):
        """הצגת הגדרות קבוצות תחנה"""
        from app.db.models.station import Station

        result = await self.db.execute(
            select(Station).where(Station.id == self.station_id)
        )
        station = result.scalar_one_or_none()

        public_id = station.public_group_chat_id if station else None
        private_id = station.private_group_chat_id if station else None

        text = "⚙️ <b>הגדרות קבוצות</b>\n\n"
        text += f"📢 קבוצה ציבורית (שידור): {escape(public_id or 'לא מוגדרת')}\n"
        text += (
            f"🔒 קבוצה פרטית (כרטיסים סגורים): {escape(private_id or 'לא מוגדרת')}\n\n"
        )
        text += "בחר פעולה:"

        response = MessageResponse(
            text,
            keyboard=[
                ["📢 הגדרת קבוצה ציבורית", "🔒 הגדרת קבוצה פרטית"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.GROUP_SETTINGS.value, {}

    async def _handle_group_settings(self, user: User, message: str, context: dict):
        """תפריט הגדרות קבוצות"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "ציבורית" in message:
            response = MessageResponse(
                "📢 <b>הגדרת קבוצה ציבורית</b>\n\n"
                "הזן את מזהה הקבוצה (Chat ID).\n\n"
                "💡 כדי לקבל את מזהה הקבוצה:\n"
                "1. הוסף את הבוט לקבוצה\n"
                "2. שלח הודעה בקבוצה\n"
                "3. המזהה יופיע בלוגים של הבוט\n\n"
                "הזן מזהה (לדוגמה: -1001234567890):"
            )
            return response, StationOwnerState.SET_PUBLIC_GROUP.value, {}

        if "פרטית" in message:
            response = MessageResponse(
                "🔒 <b>הגדרת קבוצה פרטית</b>\n\n"
                "הזן את מזהה הקבוצה הפרטית (Chat ID).\n"
                "כרטיסים סגורים ישלחו לקבוצה זו.\n\n"
                "הזן מזהה (לדוגמה: -1001234567890):"
            )
            return response, StationOwnerState.SET_PRIVATE_GROUP.value, {}

        return await self._show_group_settings(user, context)

    async def _handle_set_public_group(self, user: User, message: str, context: dict):
        """קבלת מזהה קבוצה ציבורית"""
        if "חזרה" in message:
            return await self._show_group_settings(user, context)

        chat_id = message.strip()
        # ולידציה בסיסית — מזהה קבוצה הוא מספר (שלילי בטלגרם) או מחרוזת
        if not chat_id:
            response = MessageResponse("מזהה קבוצה ריק. אנא הזן מזהה תקין:")
            return response, StationOwnerState.SET_PUBLIC_GROUP.value, {}

        # זיהוי פלטפורמה — מזהי טלגרם מתחילים ב-"-" או ספרות, WhatsApp מכיל "@g.us"
        platform = "whatsapp" if chat_id.endswith("@g.us") else "telegram"

        success, msg = await self.station_service.update_station_groups(
            self.station_id,
            public_group_chat_id=chat_id,
            public_group_platform=platform,
        )

        response = MessageResponse(
            msg,
            keyboard=[
                ["📢 הגדרת קבוצה ציבורית", "🔒 הגדרת קבוצה פרטית"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.GROUP_SETTINGS.value, {}

    async def _handle_set_private_group(self, user: User, message: str, context: dict):
        """קבלת מזהה קבוצה פרטית"""
        if "חזרה" in message:
            return await self._show_group_settings(user, context)

        chat_id = message.strip()
        if not chat_id:
            response = MessageResponse("מזהה קבוצה ריק. אנא הזן מזהה תקין:")
            return response, StationOwnerState.SET_PRIVATE_GROUP.value, {}

        platform = "whatsapp" if chat_id.endswith("@g.us") else "telegram"

        success, msg = await self.station_service.update_station_groups(
            self.station_id,
            private_group_chat_id=chat_id,
            private_group_platform=platform,
        )

        response = MessageResponse(
            msg,
            keyboard=[
                ["📢 הגדרת קבוצה ציבורית", "🔒 הגדרת קבוצה פרטית"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.GROUP_SETTINGS.value, {}

    # ==================== סעיף 8: הגדרות תחנה מורחבות ====================

    # שמות ימים בעברית לתצוגה
    _DAYS_HE = {
        "sunday": "ראשון",
        "monday": "שני",
        "tuesday": "שלישי",
        "wednesday": "רביעי",
        "thursday": "חמישי",
        "friday": "שישי",
        "saturday": "שבת",
    }
    _DAYS_ORDER = [
        "sunday",
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
    ]

    async def _show_station_settings(self, user: User, context: dict):
        """הצגת הגדרות תחנה מורחבות"""
        settings = await self.station_service.get_station_settings(self.station_id)

        name = settings.get("name", "תחנה")
        desc = settings.get("description") or "לא הוגדר"

        # שעות פעילות
        hours = settings.get("operating_hours")
        hours_text = ""
        if hours:
            for day_key in self._DAYS_ORDER:
                day_he = self._DAYS_HE[day_key]
                schedule = hours.get(day_key)
                if schedule:
                    hours_text += (
                        f"  {day_he}: {schedule['open']}-{schedule['close']}\n"
                    )
                else:
                    hours_text += f"  {day_he}: סגור\n"
        else:
            hours_text = "  לא הוגדרו\n"

        # אזורי שירות
        areas = settings.get("service_areas")
        areas_text = ", ".join(areas) if areas else "לא הוגדרו"

        text = (
            f"🏪 <b>הגדרות תחנה — {escape(name)}</b>\n\n"
            f"📝 תיאור: {escape(desc)}\n\n"
            f"🕐 <b>שעות פעילות:</b>\n{hours_text}\n"
            f"📍 <b>אזורי שירות:</b> {escape(areas_text)}\n\n"
            "בחר מה לערוך:"
        )

        response = MessageResponse(
            text,
            keyboard=[
                ["✏️ שם תחנה", "📝 תיאור"],
                ["🕐 שעות פעילות", "📍 אזורי שירות"],
                ["🔙 חזרה לתפריט"],
            ],
        )
        return response, StationOwnerState.STATION_SETTINGS.value, {}

    async def _handle_station_settings(self, user: User, message: str, context: dict):
        """תפריט הגדרות תחנה מורחבות"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "שם" in message:
            station = await self.station_service.get_station(self.station_id)
            current_name = station.name if station else "תחנה"
            response = MessageResponse(
                f"✏️ <b>עריכת שם תחנה</b>\n\n"
                f"שם נוכחי: <b>{escape(current_name)}</b>\n\n"
                "הזן שם חדש לתחנה:"
            )
            return response, StationOwnerState.EDIT_STATION_NAME.value, {}

        if "תיאור" in message:
            station = await self.station_service.get_station(self.station_id)
            current_desc = station.description if station else None
            response = MessageResponse(
                f"📝 <b>עריכת תיאור תחנה</b>\n\n"
                f"תיאור נוכחי: {escape(current_desc or 'לא הוגדר')}\n\n"
                "הזן תיאור חדש (עד 500 תווים):\n"
                "לביטול התיאור — שלח 'מחק'"
            )
            return response, StationOwnerState.EDIT_STATION_DESCRIPTION.value, {}

        if "שעות" in message:
            return await self._show_edit_operating_hours(user, context)

        if "אזור" in message or "שירות" in message:
            station = await self.station_service.get_station(self.station_id)
            current_areas = station.service_areas if station else None
            areas_text = ", ".join(current_areas) if current_areas else "לא הוגדרו"
            response = MessageResponse(
                f"📍 <b>עריכת אזורי שירות</b>\n\n"
                f"אזורים נוכחיים: {escape(areas_text)}\n\n"
                "הזן רשימת אזורי שירות, מופרדים בפסיקים.\n"
                "לדוגמה: תל אביב, רמת גן, גבעתיים\n\n"
                "לביטול — שלח 'מחק'"
            )
            return response, StationOwnerState.EDIT_SERVICE_AREAS.value, {}

        return await self._show_station_settings(user, context)

    async def _handle_edit_name(self, user: User, message: str, context: dict):
        """עריכת שם התחנה"""
        if "חזרה" in message:
            return await self._show_station_settings(user, context)

        name = message.strip()
        success, msg = await self.station_service.update_station_settings(
            station_id=self.station_id,
            name=name,
        )

        if success:
            return await self._show_station_settings(user, context)

        response = MessageResponse(f"{msg}\n\nהזן שם תקין:", keyboard=[["🔙 חזרה"]])
        return response, StationOwnerState.EDIT_STATION_NAME.value, {}

    async def _handle_edit_description(self, user: User, message: str, context: dict):
        """עריכת תיאור התחנה"""
        if "חזרה" in message:
            return await self._show_station_settings(user, context)

        text = message.strip()

        if text == "מחק":
            success, msg = await self.station_service.update_station_settings(
                station_id=self.station_id,
                description=None,
            )
            if not success:
                logger.error(
                    "כשלון במחיקת תיאור תחנה",
                    extra_data={
                        "station_id": self.station_id,
                        "error": msg,
                    },
                )
                response = MessageResponse(
                    f"{msg}\n\nנסה שוב או לחץ חזרה:",
                    keyboard=[["🔙 חזרה"]],
                )
                return response, StationOwnerState.EDIT_STATION_DESCRIPTION.value, {}
            return await self._show_station_settings(user, context)

        success, msg = await self.station_service.update_station_settings(
            station_id=self.station_id,
            description=text,
        )

        if success:
            return await self._show_station_settings(user, context)

        response = MessageResponse(f"{msg}\n\nהזן תיאור תקין:", keyboard=[["🔙 חזרה"]])
        return response, StationOwnerState.EDIT_STATION_DESCRIPTION.value, {}

    async def _show_edit_operating_hours(self, user: User, context: dict):
        """הצגת מסך עריכת שעות פעילות"""
        station = await self.station_service.get_station(self.station_id)
        hours = station.operating_hours if station else None

        text = "🕐 <b>עריכת שעות פעילות</b>\n\n"
        if hours:
            for day_key in self._DAYS_ORDER:
                day_he = self._DAYS_HE[day_key]
                schedule = hours.get(day_key)
                if schedule:
                    text += f"  {day_he}: {schedule['open']}-{schedule['close']}\n"
                else:
                    text += f"  {day_he}: סגור\n"
        else:
            text += "לא הוגדרו שעות פעילות.\n"

        text += (
            "\nשלח שעות בפורמט:\n"
            "<code>יום HH:MM-HH:MM</code>\n"
            "לדוגמה: <code>ראשון 08:00-20:00</code>\n"
            "ליום סגור: <code>שבת סגור</code>\n\n"
            "לאיפוס כל השעות — שלח 'מחק'"
        )

        response = MessageResponse(
            text,
            keyboard=[["🔙 חזרה"]],
        )
        return response, StationOwnerState.EDIT_OPERATING_HOURS.value, {}

    async def _handle_edit_operating_hours(
        self, user: User, message: str, context: dict
    ):
        """עריכת שעות פעילות — קבלת יום ושעות"""
        if "חזרה" in message:
            return await self._show_station_settings(user, context)

        text = message.strip()

        if text == "מחק":
            success, msg = await self.station_service.update_station_settings(
                station_id=self.station_id,
                operating_hours=None,
            )
            if not success:
                logger.error(
                    "כשלון במחיקת שעות פעילות",
                    extra_data={
                        "station_id": self.station_id,
                        "error": msg,
                    },
                )
                response = MessageResponse(
                    f"{msg}\n\nנסה שוב או לחץ חזרה:",
                    keyboard=[["🔙 חזרה"]],
                )
                return response, StationOwnerState.EDIT_OPERATING_HOURS.value, {}
            return await self._show_station_settings(user, context)

        # ניתוח "יום HH:MM-HH:MM" או "יום סגור"
        # מיפוי ימים בעברית לאנגלית
        he_to_en = {v: k for k, v in self._DAYS_HE.items()}

        import re

        match = re.match(r"^(\S+)\s+(.+)$", text)
        if not match:
            response = MessageResponse(
                "פורמט לא תקין.\n"
                "שלח: <code>יום HH:MM-HH:MM</code>\n"
                "לדוגמה: <code>ראשון 08:00-20:00</code>",
                keyboard=[["🔙 חזרה"]],
            )
            return response, StationOwnerState.EDIT_OPERATING_HOURS.value, {}

        day_he = match.group(1)
        time_part = match.group(2).strip()

        day_en = he_to_en.get(day_he)
        if not day_en:
            response = MessageResponse(
                f"יום לא מוכר: {escape(day_he)}\n"
                "ימים תקינים: ראשון, שני, שלישי, רביעי, חמישי, שישי, שבת",
                keyboard=[["🔙 חזרה"]],
            )
            return response, StationOwnerState.EDIT_OPERATING_HOURS.value, {}

        # קבלת שעות קיימות ועדכון
        station = await self.station_service.get_station(self.station_id)
        current_hours = (
            dict(station.operating_hours) if station and station.operating_hours else {}
        )

        if time_part == "סגור":
            current_hours[day_en] = None
        else:
            time_match = re.match(r"^(\d{2}:\d{2})-(\d{2}:\d{2})$", time_part)
            if not time_match:
                response = MessageResponse(
                    "פורמט שעות לא תקין.\n"
                    "שלח: <code>HH:MM-HH:MM</code> (לדוגמה: 08:00-20:00)\n"
                    "או <code>סגור</code>",
                    keyboard=[["🔙 חזרה"]],
                )
                return response, StationOwnerState.EDIT_OPERATING_HOURS.value, {}

            open_time = time_match.group(1)
            close_time = time_match.group(2)
            current_hours[day_en] = {"open": open_time, "close": close_time}

        success, msg = await self.station_service.update_station_settings(
            station_id=self.station_id,
            operating_hours=current_hours,
        )

        if success:
            # מציג חזרה את מסך שעות הפעילות כדי לאפשר עריכת ימים נוספים
            return await self._show_edit_operating_hours(user, context)

        response = MessageResponse(
            msg,
            keyboard=[["🔙 חזרה"]],
        )
        return response, StationOwnerState.EDIT_OPERATING_HOURS.value, {}

    async def _handle_edit_service_areas(self, user: User, message: str, context: dict):
        """עריכת אזורי שירות"""
        if "חזרה" in message:
            return await self._show_station_settings(user, context)

        text = message.strip()

        if text == "מחק":
            success, msg = await self.station_service.update_station_settings(
                station_id=self.station_id,
                service_areas=None,
            )
            if not success:
                logger.error(
                    "כשלון במחיקת אזורי שירות",
                    extra_data={
                        "station_id": self.station_id,
                        "error": msg,
                    },
                )
                response = MessageResponse(
                    f"{msg}\n\nנסה שוב או לחץ חזרה:",
                    keyboard=[["🔙 חזרה"]],
                )
                return response, StationOwnerState.EDIT_SERVICE_AREAS.value, {}
            return await self._show_station_settings(user, context)

        # פיצול לפי פסיקים
        areas = [a.strip() for a in text.split(",") if a.strip()]

        if not areas:
            response = MessageResponse(
                "רשימה ריקה. הזן אזורי שירות מופרדים בפסיקים:",
                keyboard=[["🔙 חזרה"]],
            )
            return response, StationOwnerState.EDIT_SERVICE_AREAS.value, {}

        success, msg = await self.station_service.update_station_settings(
            station_id=self.station_id,
            service_areas=areas,
        )

        if success:
            return await self._show_station_settings(user, context)

        response = MessageResponse(
            f"{msg}\n\nהזן רשימת אזורים תקינה:",
            keyboard=[["🔙 חזרה"]],
        )
        return response, StationOwnerState.EDIT_SERVICE_AREAS.value, {}

    # ==================== Unknown ====================

    async def _handle_unknown(self, user: User, message: str, context: dict):
        """ניתוב ברירת מחדל - הצגת תפריט ללא ניתוב מילות מפתח (guard)"""
        logger.warning(
            "בעל תחנה במצב לא מוכר, מחזיר לתפריט",
            extra_data={"user_id": user.id, "message_length": len(message)},
        )
        return await self._show_menu(user, context)
