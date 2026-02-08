"""
Station Owner State Handler - פאנל ניהול תחנה [שלב 3.3]

בעל תחנה מנהל:
- סדרנים (הוספה/הסרה לפי מספר טלפון)
- ארנק תחנה (10% עמלה מכל משלוח)
- דוח גבייה (ה-28 לחודש)
- רשימה שחורה (נהגים שלא שילמו חודשיים רצופים)
"""
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

    async def handle_message(
        self,
        user: User,
        message: str,
        photo_file_id: str = None
    ) -> Tuple[MessageResponse, str]:
        """עיבוד הודעה נכנסת מבעל תחנה"""
        platform = self.platform or user.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(user, message, context)

        if new_state != current_state:
            await self.state_manager.force_state(
                user.id, platform, new_state,
                {**context, **context_update} if context_update else context
            )
        elif context_update:
            for key, value in context_update.items():
                await self.state_manager.update_context(user.id, platform, key, value)

        return response, new_state

    def _get_handler(self, state: str):
        """ניתוב ל-handler המתאים"""
        handlers = {
            StationOwnerState.MENU.value: self._handle_menu,

            # ניהול סדרנים
            StationOwnerState.MANAGE_DISPATCHERS.value: self._handle_manage_dispatchers,
            StationOwnerState.ADD_DISPATCHER_PHONE.value: self._handle_add_dispatcher,
            StationOwnerState.REMOVE_DISPATCHER_SELECT.value: self._handle_remove_dispatcher,

            # ארנק תחנה
            StationOwnerState.VIEW_WALLET.value: self._handle_view_wallet,

            # דוח גבייה
            StationOwnerState.COLLECTION_REPORT.value: self._handle_collection_report,

            # רשימה שחורה
            StationOwnerState.VIEW_BLACKLIST.value: self._handle_view_blacklist,
            StationOwnerState.ADD_BLACKLIST_PHONE.value: self._handle_add_blacklist_phone,
            StationOwnerState.ADD_BLACKLIST_REASON.value: self._handle_add_blacklist_reason,
            StationOwnerState.REMOVE_BLACKLIST_SELECT.value: self._handle_remove_blacklist,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== תפריט ראשי ====================

    async def _show_menu(self, user: User, context: dict):
        """הצגת תפריט ראשי ללא ניתוב לפי תוכן הודעה"""
        station = await self.station_service.get_station(self.station_id)
        station_name = station.name if station else "תחנה"

        wallet = await self.station_service.get_station_wallet(self.station_id)
        balance = wallet.balance if wallet else 0.0

        response = MessageResponse(
            f"🏢 <b>פאנל ניהול - {escape(station_name)}</b>\n\n"
            f"💰 יתרת ארנק: {balance:.2f} ₪\n\n"
            "בחר פעולה:",
            keyboard=[
                ["👥 ניהול סדרנים", "💰 ארנק תחנה"],
                ["📊 דוח גבייה", "🚫 רשימה שחורה"],
            ],
            inline=True
        )
        return response, StationOwnerState.MENU.value, {}

    async def _handle_menu(self, user: User, message: str, context: dict):
        """תפריט ראשי של בעל תחנה"""
        msg = message.strip()

        if "סדרנים" in msg or "ניהול" in msg:
            return await self._show_manage_dispatchers(user, context)

        if "ארנק" in msg or "כספים" in msg:
            return await self._show_wallet(user, context)

        if "גבייה" in msg or "דוח" in msg:
            return await self._show_collection_report(user, context)

        if "רשימה שחורה" in msg or "חסימה" in msg or "שחורה" in msg:
            return await self._show_blacklist(user, context)

        return await self._show_menu(user, context)

    # ==================== ניהול סדרנים ====================

    async def _show_manage_dispatchers(self, user: User, context: dict):
        """הצגת מסך ניהול סדרנים ללא ניתוב לפי תוכן הודעה"""
        dispatchers = await self.station_service.get_dispatchers(self.station_id)

        text = "👥 <b>ניהול סדרנים</b>\n\n"
        if dispatchers:
            for i, d in enumerate(dispatchers, 1):
                result = await self.db.execute(
                    select(User).where(User.id == d.user_id)
                )
                dispatcher_user = result.scalar_one_or_none()
                name = dispatcher_user.name if dispatcher_user else "לא ידוע"
                text += f"{i}. {escape(name)}\n"
        else:
            text += "אין סדרנים רשומים עדיין.\n"

        text += "\nבחר פעולה:"

        response = MessageResponse(
            text,
            keyboard=[
                ["➕ הוספת סדרן", "➖ הסרת סדרן"],
                ["🔙 חזרה לתפריט"],
            ]
        )
        return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

    async def _handle_manage_dispatchers(
        self, user: User, message: str, context: dict
    ):
        """תפריט ניהול סדרנים"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "הוספת" in message or "הוספה" in message:
            response = MessageResponse(
                "👥 <b>הוספת סדרן</b>\n\n"
                "הזן את מספר הטלפון של הסדרן:"
            )
            return response, StationOwnerState.ADD_DISPATCHER_PHONE.value, {}

        if "הסרה" in message or "הסר" in message:
            return await self._show_dispatcher_list_for_removal(user, context)

        return await self._show_manage_dispatchers(user, context)

    async def _handle_add_dispatcher(
        self, user: User, message: str, context: dict
    ):
        """הוספת סדרן לפי מספר טלפון"""
        if "חזרה" in message:
            return await self._show_manage_dispatchers(user, context)

        phone = message.strip()
        success, msg = await self.station_service.add_dispatcher(
            self.station_id, phone
        )

        response = MessageResponse(
            msg,
            keyboard=[
                ["➕ הוספת סדרן", "➖ הסרת סדרן"],
                ["🔙 חזרה לתפריט"],
            ]
        )
        return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

    async def _show_dispatcher_list_for_removal(
        self, user: User, context: dict
    ):
        """הצגת רשימת סדרנים להסרה"""
        dispatchers = await self.station_service.get_dispatchers(self.station_id)

        if not dispatchers:
            response = MessageResponse(
                "אין סדרנים להסרה.",
                keyboard=[["🔙 חזרה לתפריט"]]
            )
            return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

        text = "➖ <b>הסרת סדרן</b>\n\nבחר סדרן להסרה:\n\n"
        keyboard_items = []
        dispatcher_map = {}

        for i, d in enumerate(dispatchers, 1):
            result = await self.db.execute(
                select(User).where(User.id == d.user_id)
            )
            dispatcher_user = result.scalar_one_or_none()
            name = dispatcher_user.name if dispatcher_user else "לא ידוע"
            text += f"{i}. {escape(name)}\n"
            keyboard_items.append([f"הסר {i}"])
            dispatcher_map[str(i)] = d.user_id

        keyboard_items.append(["🔙 חזרה"])

        response = MessageResponse(text, keyboard=keyboard_items)
        return response, StationOwnerState.REMOVE_DISPATCHER_SELECT.value, {
            "dispatcher_map": dispatcher_map
        }

    async def _handle_remove_dispatcher(
        self, user: User, message: str, context: dict
    ):
        """הסרת סדרן לפי בחירה מרשימה"""
        if "חזרה" in message:
            return await self._show_manage_dispatchers(user, context)

        import re
        numbers = re.findall(r'\d+', message)
        dispatcher_map = context.get("dispatcher_map", {})

        if numbers and numbers[0] in dispatcher_map:
            dispatcher_user_id = dispatcher_map[numbers[0]]
            success, msg = await self.station_service.remove_dispatcher(
                self.station_id, dispatcher_user_id
            )
            response = MessageResponse(
                msg,
                keyboard=[
                    ["➕ הוספת סדרן", "➖ הסרת סדרן"],
                    ["🔙 חזרה לתפריט"],
                ]
            )
            return response, StationOwnerState.MANAGE_DISPATCHERS.value, {}

        response = MessageResponse(
            "בחירה לא תקינה. אנא בחר מספר מהרשימה.",
            keyboard=[["🔙 חזרה"]]
        )
        return response, StationOwnerState.REMOVE_DISPATCHER_SELECT.value, {}

    # ==================== ארנק תחנה ====================

    async def _show_wallet(self, user: User, context: dict):
        """הצגת ארנק תחנה ללא ניתוב לפי תוכן הודעה"""
        wallet = await self.station_service.get_station_wallet(self.station_id)
        ledger = await self.station_service.get_station_ledger(self.station_id)

        text = (
            "💰 <b>ארנק תחנה</b>\n\n"
            f"💵 יתרה: <b>{wallet.balance:.2f} ₪</b>\n"
            f"📊 שיעור עמלה: {wallet.commission_rate * 100:.0f}%\n\n"
        )

        if ledger:
            text += "<b>תנועות אחרונות:</b>\n"
            for entry in ledger[:5]:
                sign = "+" if entry.amount > 0 else ""
                text += f"  {sign}{entry.amount:.2f} ₪ | {escape(entry.description or '')}\n"
        else:
            text += "אין תנועות עדיין.\n"

        response = MessageResponse(
            text,
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, StationOwnerState.VIEW_WALLET.value, {}

    async def _handle_view_wallet(self, user: User, message: str, context: dict):
        """צפייה בארנק התחנה - 10% עמלה מכל משלוח"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        return await self._show_wallet(user, context)

    # ==================== דוח גבייה ====================

    async def _show_collection_report(self, user: User, context: dict):
        """הצגת דוח גבייה ללא ניתוב לפי תוכן הודעה"""
        report = await self.station_service.get_collection_report(self.station_id)

        text = "📊 <b>דוח גבייה</b>\n\n"
        text += "מחזור חיוב: ה-28 לחודש עד ה-28 בחודש הבא\n\n"

        if report:
            text += "<b>נהגים עם חוב:</b>\n"
            total = 0.0
            for item in report:
                name = item["driver_name"]
                debt = item["total_debt"]
                text += f"  👤 {escape(name)}: {debt:.2f} ₪\n"
                total += debt
            text += f"\n<b>סה\"כ חוב: {total:.2f} ₪</b>"
        else:
            text += "אין חובות פתוחים. 🎉"

        response = MessageResponse(
            text,
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, StationOwnerState.COLLECTION_REPORT.value, {}

    async def _handle_collection_report(
        self, user: User, message: str, context: dict
    ):
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
                name = blocked_user.name if blocked_user else "לא ידוע"
                reason = entry.reason or "אי תשלום"
                text += f"{i}. {escape(name)} - {escape(reason)}\n"
        else:
            text += "הרשימה ריקה. 👍"

        response = MessageResponse(
            text,
            keyboard=[
                ["➕ הוספת נהג לרשימה", "➖ הסרת נהג מהרשימה"],
                ["🔙 חזרה לתפריט"],
            ]
        )
        return response, StationOwnerState.VIEW_BLACKLIST.value, {}

    async def _handle_view_blacklist(
        self, user: User, message: str, context: dict
    ):
        """צפייה ברשימה השחורה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        if "הוספת" in message or "הוספה" in message or "חסום" in message:
            response = MessageResponse(
                "🚫 <b>הוספה לרשימה שחורה</b>\n\n"
                "הזן את מספר הטלפון של הנהג:"
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
            response = MessageResponse(
                "מספר טלפון לא תקין. אנא הזן מספר תקין:"
            )
            return response, StationOwnerState.ADD_BLACKLIST_PHONE.value, {}

        response = MessageResponse(
            f"טלפון: {PhoneNumberValidator.mask(phone)} ✓\n\n"
            "📝 סיבת החסימה:"
        )
        return response, StationOwnerState.ADD_BLACKLIST_REASON.value, {
            "blacklist_phone": phone
        }

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
            ]
        )
        return response, StationOwnerState.VIEW_BLACKLIST.value, {}

    async def _show_blacklist_for_removal(
        self, user: User, context: dict
    ):
        """הצגת רשימה שחורה להסרה"""
        blacklist = await self.station_service.get_blacklist(self.station_id)

        if not blacklist:
            response = MessageResponse(
                "הרשימה השחורה ריקה, אין מי להסיר.",
                keyboard=[["🔙 חזרה"]]
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
            name = blocked_user.name if blocked_user else "לא ידוע"
            text += f"{i}. {escape(name)}\n"
            keyboard_items.append([f"הסר {i}"])
            blacklist_map[str(i)] = entry.courier_id

        keyboard_items.append(["🔙 חזרה"])

        response = MessageResponse(text, keyboard=keyboard_items)
        return response, StationOwnerState.REMOVE_BLACKLIST_SELECT.value, {
            "blacklist_map": blacklist_map
        }

    async def _handle_remove_blacklist(
        self, user: User, message: str, context: dict
    ):
        """הסרת נהג מרשימה שחורה"""
        if "חזרה" in message:
            return await self._show_blacklist(user, context)

        import re
        numbers = re.findall(r'\d+', message)
        blacklist_map = context.get("blacklist_map", {})

        if numbers and numbers[0] in blacklist_map:
            courier_id = blacklist_map[numbers[0]]
            success, msg = await self.station_service.remove_from_blacklist(
                self.station_id, courier_id
            )
            response = MessageResponse(
                msg,
                keyboard=[
                    ["➕ הוספת נהג לרשימה", "➖ הסרת נהג מהרשימה"],
                    ["🔙 חזרה לתפריט"],
                ]
            )
            return response, StationOwnerState.VIEW_BLACKLIST.value, {}

        response = MessageResponse(
            "בחירה לא תקינה. אנא בחר מספר מהרשימה.",
            keyboard=[["🔙 חזרה"]]
        )
        return response, StationOwnerState.REMOVE_BLACKLIST_SELECT.value, {}

    # ==================== Unknown ====================

    async def _handle_unknown(self, user: User, message: str, context: dict):
        """ניתוב ברירת מחדל - חזרה לתפריט"""
        return await self._handle_menu(user, "תפריט", context)
