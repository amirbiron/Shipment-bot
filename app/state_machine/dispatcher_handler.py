"""
Dispatcher State Handler - תפריט סדרן היברידי [שלב 3.2]

סדרן הוא משתמש עם הרשאות ניהול ברמת תחנה.
יכול להיות שליח (COURIER) או כל תפקיד אחר שנוסף כסדרן דרך הפאנל.
תפריט סדרן ייעודי עם:
- הוספת משלוח (טופס הזנת פרטים)
- משלוחים פעילים של התחנה
- היסטוריית משלוחים
- הוספת חיוב ידני
"""

from typing import Tuple
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession

from app.state_machine.states import DispatcherState
from app.state_machine.manager import StateManager
from app.state_machine.handlers import MessageResponse
from app.db.models.user import User
from app.db.models.delivery import DeliveryStatus
from app.domain.services.station_service import StationService
from app.domain.services.delivery_service import DeliveryService
from app.core.logging import get_logger

logger = get_logger(__name__)


class DispatcherStateHandler:
    """
    Handler לתפריט סדרן - חלק ה'סדרן' בתפריט ההיברידי.

    הערה: תפריט הנהג הרגיל מטופל ע"י CourierStateHandler.
    handler זה מטפל רק בפעולות הייחודיות לסדרן.
    """

    def __init__(self, db: AsyncSession, station_id: int, platform: str = "telegram"):
        self.db = db
        self.station_id = station_id
        self.platform = platform
        self.state_manager = StateManager(db)
        self.station_service = StationService(db)
        self.delivery_service = DeliveryService(db)

    # מפתחות קונטקסט של הוספת משלוח — מנוקים בחזרה ל-MENU
    _SHIPMENT_CONTEXT_KEYS = {
        "pickup_city",
        "pickup_street",
        "pickup_number",
        "pickup_address",
        "dropoff_city",
        "dropoff_street",
        "dropoff_number",
        "dropoff_address",
        "description",
        "fee",
    }

    # מפתחות קונטקסט של חיוב ידני — מנוקים בחזרה ל-MENU
    _MANUAL_CHARGE_CONTEXT_KEYS = {
        "charge_driver_name",
        "charge_amount",
        "charge_description",
    }

    def _is_add_shipment_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת הוספת משלוח"""
        return state.startswith("DISPATCHER.ADD_SHIPMENT.")

    def _is_manual_charge_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת חיוב ידני"""
        return state.startswith("DISPATCHER.MANUAL_CHARGE.")

    def _is_multi_step_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימה רב-שלבית"""
        return self._is_add_shipment_flow_state(
            state
        ) or self._is_manual_charge_flow_state(state)

    async def handle_message(
        self, user: User, message: str, photo_file_id: str = None
    ) -> Tuple[MessageResponse, str]:
        """עיבוד הודעה נכנסת מסדרן"""
        platform = self.platform or user.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(user, message, context)

        # ניקוי קונטקסט זרימת משלוח/חיוב בחזרה ל-MENU
        if new_state == DispatcherState.MENU.value and self._is_multi_step_flow_state(
            current_state
        ):
            keys_to_clean = (
                self._SHIPMENT_CONTEXT_KEYS | self._MANUAL_CHARGE_CONTEXT_KEYS
            )
            clean_context = {k: v for k, v in context.items() if k not in keys_to_clean}
            if context_update:
                for k, v in context_update.items():
                    if k not in keys_to_clean:
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
                    "כפיית מעבר מצב בסדרן",
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
            DispatcherState.MENU.value: self._handle_menu,
            # הוספת משלוח
            DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value: self._handle_add_shipment_pickup_city,
            DispatcherState.ADD_SHIPMENT_PICKUP_STREET.value: self._handle_add_shipment_pickup_street,
            DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER.value: self._handle_add_shipment_pickup_number,
            DispatcherState.ADD_SHIPMENT_DROPOFF_CITY.value: self._handle_add_shipment_dropoff_city,
            DispatcherState.ADD_SHIPMENT_DROPOFF_STREET.value: self._handle_add_shipment_dropoff_street,
            DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER.value: self._handle_add_shipment_dropoff_number,
            DispatcherState.ADD_SHIPMENT_DESCRIPTION.value: self._handle_add_shipment_description,
            DispatcherState.ADD_SHIPMENT_FEE.value: self._handle_add_shipment_fee,
            DispatcherState.ADD_SHIPMENT_CONFIRM.value: self._handle_add_shipment_confirm,
            # צפייה במשלוחים
            DispatcherState.VIEW_ACTIVE_SHIPMENTS.value: self._handle_view_active,
            DispatcherState.VIEW_SHIPMENT_HISTORY.value: self._handle_view_history,
            # חיוב ידני
            DispatcherState.MANUAL_CHARGE_DRIVER_NAME.value: self._handle_manual_charge_name,
            DispatcherState.MANUAL_CHARGE_AMOUNT.value: self._handle_manual_charge_amount,
            DispatcherState.MANUAL_CHARGE_DESCRIPTION.value: self._handle_manual_charge_description,
            DispatcherState.MANUAL_CHARGE_CONFIRM.value: self._handle_manual_charge_confirm,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== תפריט סדרן ====================

    async def _show_menu(self, user: User, context: dict):
        """הצגת תפריט סדרן ללא ניתוב לפי תוכן הודעה"""
        station = await self.station_service.get_station(self.station_id)
        station_name = station.name if station else "תחנה"

        response = MessageResponse(
            f"🏪 <b>תפריט סדרן - {escape(station_name)}</b>\n\n" "בחר פעולה:",
            keyboard=[
                ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                ["📋 היסטוריית משלוחים", "💳 חיוב ידני"],
                ["🔙 חזרה לתפריט ראשי"],
            ],
        )
        return response, DispatcherState.MENU.value, {}

    async def _handle_menu(self, user: User, message: str, context: dict):
        """תפריט סדרן ראשי"""
        msg = message.strip()

        if "הוספת משלוח" in msg or "משלוח חדש" in msg or "➕" in msg:
            response = MessageResponse(
                "📦 <b>הוספת משלוח חדש</b>\n\n" "📍 <b>כתובת איסוף</b>\n" "מה העיר?"
            )
            return response, DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value, {}

        if "משלוחים פעילים" in msg or "פעילים" in msg:
            return await self._show_active(user, context)

        if "היסטוריה" in msg or "היסטוריית" in msg:
            return await self._show_history(user, context)

        if "חיוב ידני" in msg or "חיוב" in msg:
            response = MessageResponse(
                "💳 <b>הוספת חיוב ידני</b>\n\n" "הזן את שם הנהג:"
            )
            return response, DispatcherState.MANUAL_CHARGE_DRIVER_NAME.value, {}

        return await self._show_menu(user, context)

    # ==================== הוספת משלוח ====================

    async def _handle_add_shipment_pickup_city(
        self, user: User, message: str, context: dict
    ):
        """עיר איסוף"""
        city = message.strip()
        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזן שם עיר תקין:")
            return response, DispatcherState.ADD_SHIPMENT_PICKUP_CITY.value, {}

        response = MessageResponse(f"עיר: {escape(city)} ✓\n\n" "מה שם הרחוב?")
        return (
            response,
            DispatcherState.ADD_SHIPMENT_PICKUP_STREET.value,
            {"pickup_city": city},
        )

    async def _handle_add_shipment_pickup_street(
        self, user: User, message: str, context: dict
    ):
        """רחוב איסוף"""
        street = message.strip()
        if len(street) < 2:
            response = MessageResponse("שם הרחוב קצר מדי. אנא הזן שם רחוב תקין:")
            return response, DispatcherState.ADD_SHIPMENT_PICKUP_STREET.value, {}

        response = MessageResponse(
            f"עיר: {escape(context.get('pickup_city', ''))} ✓\n"
            f"רחוב: {escape(street)} ✓\n\n"
            "מה מספר הבית?"
        )
        return (
            response,
            DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER.value,
            {"pickup_street": street},
        )

    async def _handle_add_shipment_pickup_number(
        self, user: User, message: str, context: dict
    ):
        """מספר בית איסוף"""
        number = message.strip()
        if not any(char.isdigit() for char in number):
            response = MessageResponse("מספר הבית חייב להכיל ספרה. אנא הזן מספר תקין:")
            return response, DispatcherState.ADD_SHIPMENT_PICKUP_NUMBER.value, {}

        city = context.get("pickup_city", "")
        street = context.get("pickup_street", "")
        pickup_address = f"{street} {number}, {city}"

        response = MessageResponse(
            f"📍 כתובת איסוף: {escape(pickup_address)} ✓\n\n"
            "🎯 <b>כתובת יעד</b>\n"
            "מה העיר?"
        )
        return (
            response,
            DispatcherState.ADD_SHIPMENT_DROPOFF_CITY.value,
            {
                "pickup_number": number,
                "pickup_address": pickup_address,
            },
        )

    async def _handle_add_shipment_dropoff_city(
        self, user: User, message: str, context: dict
    ):
        """עיר יעד"""
        city = message.strip()
        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזן שם עיר תקין:")
            return response, DispatcherState.ADD_SHIPMENT_DROPOFF_CITY.value, {}

        response = MessageResponse(f"עיר: {escape(city)} ✓\n\n" "מה שם הרחוב?")
        return (
            response,
            DispatcherState.ADD_SHIPMENT_DROPOFF_STREET.value,
            {"dropoff_city": city},
        )

    async def _handle_add_shipment_dropoff_street(
        self, user: User, message: str, context: dict
    ):
        """רחוב יעד"""
        street = message.strip()
        if len(street) < 2:
            response = MessageResponse("שם הרחוב קצר מדי. אנא הזן שם רחוב תקין:")
            return response, DispatcherState.ADD_SHIPMENT_DROPOFF_STREET.value, {}

        response = MessageResponse(
            f"עיר: {escape(context.get('dropoff_city', ''))} ✓\n"
            f"רחוב: {escape(street)} ✓\n\n"
            "מה מספר הבית?"
        )
        return (
            response,
            DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER.value,
            {"dropoff_street": street},
        )

    async def _handle_add_shipment_dropoff_number(
        self, user: User, message: str, context: dict
    ):
        """מספר בית יעד"""
        number = message.strip()
        if not any(char.isdigit() for char in number):
            response = MessageResponse("מספר הבית חייב להכיל ספרה. אנא הזן מספר תקין:")
            return response, DispatcherState.ADD_SHIPMENT_DROPOFF_NUMBER.value, {}

        city = context.get("dropoff_city", "")
        street = context.get("dropoff_street", "")
        dropoff_address = f"{street} {number}, {city}"

        response = MessageResponse(
            f"🎯 כתובת יעד: {escape(dropoff_address)} ✓\n\n"
            "📝 <b>תיאור המשלוח:</b>\n"
            "מה נשלח? (תיאור קצר)"
        )
        return (
            response,
            DispatcherState.ADD_SHIPMENT_DESCRIPTION.value,
            {
                "dropoff_number": number,
                "dropoff_address": dropoff_address,
            },
        )

    async def _handle_add_shipment_description(
        self, user: User, message: str, context: dict
    ):
        """תיאור המשלוח"""
        description = message.strip()
        if len(description) < 2:
            response = MessageResponse(
                "התיאור קצר מדי. אנא תאר את המשלוח (לפחות 2 תווים):"
            )
            return response, DispatcherState.ADD_SHIPMENT_DESCRIPTION.value, {}

        response = MessageResponse(
            f"📦 תיאור: {escape(description)} ✓\n\n"
            "💰 <b>מחיר המשלוח:</b>\n"
            "כמה עולה המשלוח? (מספר בלבד, בשקלים)"
        )
        return (
            response,
            DispatcherState.ADD_SHIPMENT_FEE.value,
            {"description": description},
        )

    async def _handle_add_shipment_fee(self, user: User, message: str, context: dict):
        """מחיר המשלוח"""
        import re

        numbers = re.findall(r"\d+\.?\d*", message.strip())
        if not numbers:
            response = MessageResponse("אנא הזן סכום תקין (מספר בלבד).")
            return response, DispatcherState.ADD_SHIPMENT_FEE.value, {}

        fee = float(numbers[0])
        if fee <= 0:
            response = MessageResponse("הסכום חייב להיות חיובי.")
            return response, DispatcherState.ADD_SHIPMENT_FEE.value, {}

        # סיכום לפני אישור
        pickup = context.get("pickup_address", "לא צוין")
        dropoff = context.get("dropoff_address", "לא צוין")
        description = context.get("description", "")

        summary = (
            "📋 <b>סיכום המשלוח:</b>\n\n"
            f"📍 איסוף: {escape(pickup)}\n"
            f"🎯 יעד: {escape(dropoff)}\n"
            f"📦 תיאור: {escape(description)}\n"
            f"💰 מחיר: {fee:.2f} ₪\n\n"
            "לאשר את המשלוח?"
        )

        response = MessageResponse(
            summary, keyboard=[["✅ אישור ושליחה", "❌ ביטול"]]
        )
        return response, DispatcherState.ADD_SHIPMENT_CONFIRM.value, {"fee": fee}

    async def _handle_add_shipment_confirm(
        self, user: User, message: str, context: dict
    ):
        """אישור או ביטול משלוח"""
        if "אישור" in message or "✅" in message:
            pickup = context.get("pickup_address", "")
            dropoff = context.get("dropoff_address", "")
            description = context.get("description", "")
            fee = context.get("fee", 10.0)

            # יצירת המשלוח דרך DeliveryService - כולל שידור לנהגים ושיוך לתחנה
            # תיאור המשלוח ("מה נשלח?") נשמר ב-dropoff_notes (תוכן השליחה)
            delivery = await self.delivery_service.create_delivery(
                sender_id=user.id,
                pickup_address=pickup,
                dropoff_address=dropoff,
                dropoff_notes=description,
                fee=float(fee),
                station_id=self.station_id,
            )

            response = MessageResponse(
                "המשלוח נוצר בהצלחה! 🎉\n\n"
                f"📍 מ: {escape(pickup)}\n"
                f"🎯 אל: {escape(dropoff)}\n"
                f"💰 מחיר: {fee:.2f} ₪\n\n"
                "המשלוח ישודר לנהגים.",
                keyboard=[
                    ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                    ["📋 היסטוריית משלוחים", "💳 חיוב ידני"],
                    ["🔙 חזרה לתפריט ראשי"],
                ],
            )
            return response, DispatcherState.MENU.value, {}

        if "ביטול" in message or "❌" in message:
            response = MessageResponse(
                "המשלוח בוטל.\n\n" "חזרה לתפריט סדרן.",
                keyboard=[
                    ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                    ["📋 היסטוריית משלוחים", "💳 חיוב ידני"],
                    ["🔙 חזרה לתפריט ראשי"],
                ],
            )
            return response, DispatcherState.MENU.value, {}

        response = MessageResponse(
            "אנא בחר:\n" "1. ✅ אישור ושליחה\n" "2. ❌ ביטול",
            keyboard=[["✅ אישור ושליחה", "❌ ביטול"]],
        )
        return response, DispatcherState.ADD_SHIPMENT_CONFIRM.value, {}

    # ==================== צפייה במשלוחים ====================

    async def _show_active(self, user: User, context: dict):
        """הצגת משלוחים פעילים ללא ניתוב לפי תוכן הודעה"""
        deliveries = await self.station_service.get_station_active_deliveries(
            self.station_id
        )

        if not deliveries:
            response = MessageResponse(
                "📦 <b>משלוחים פעילים</b>\n\n" "אין משלוחים פעילים כרגע.",
                keyboard=[["🔙 חזרה לתפריט סדרן"]],
            )
            return response, DispatcherState.VIEW_ACTIVE_SHIPMENTS.value, {}

        status_map = {
            DeliveryStatus.OPEN: "🟡 פתוח",
            DeliveryStatus.CAPTURED: "🟠 נתפס",
            DeliveryStatus.IN_PROGRESS: "🔵 בדרך",
        }

        text = "📦 <b>משלוחים פעילים</b>\n\n"
        for d in deliveries[:10]:
            status_text = status_map.get(d.status, d.status.value)
            text += (
                f"#{d.id} | {status_text}\n"
                f"  📍 {escape(d.pickup_address[:30])}\n"
                f"  🎯 {escape(d.dropoff_address[:30])}\n"
                f"  💰 {d.fee:.0f} ₪\n\n"
            )

        response = MessageResponse(text, keyboard=[["🔙 חזרה לתפריט סדרן"]])
        return response, DispatcherState.VIEW_ACTIVE_SHIPMENTS.value, {}

    async def _handle_view_active(self, user: User, message: str, context: dict):
        """משלוחים פעילים של התחנה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        return await self._show_active(user, context)

    async def _show_history(self, user: User, context: dict):
        """הצגת היסטוריית משלוחים ללא ניתוב לפי תוכן הודעה"""
        deliveries = await self.station_service.get_station_delivery_history(
            self.station_id
        )

        if not deliveries:
            response = MessageResponse(
                "📋 <b>היסטוריית משלוחים</b>\n\n" "אין משלוחים בהיסטוריה עדיין.",
                keyboard=[["🔙 חזרה לתפריט סדרן"]],
            )
            return response, DispatcherState.VIEW_SHIPMENT_HISTORY.value, {}

        status_map = {
            DeliveryStatus.DELIVERED: "✅ הושלם",
            DeliveryStatus.CANCELLED: "❌ בוטל",
        }

        text = "📋 <b>היסטוריית משלוחים</b>\n\n"
        for d in deliveries[:10]:
            status_text = status_map.get(d.status, d.status.value)
            text += (
                f"#{d.id} | {status_text}\n"
                f"  📍 {escape(d.pickup_address[:30])}\n"
                f"  🎯 {escape(d.dropoff_address[:30])}\n"
                f"  💰 {d.fee:.0f} ₪\n\n"
            )

        response = MessageResponse(text, keyboard=[["🔙 חזרה לתפריט סדרן"]])
        return response, DispatcherState.VIEW_SHIPMENT_HISTORY.value, {}

    async def _handle_view_history(self, user: User, message: str, context: dict):
        """היסטוריית משלוחים של התחנה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        return await self._show_history(user, context)

    # ==================== חיוב ידני ====================

    async def _handle_manual_charge_name(self, user: User, message: str, context: dict):
        """שם הנהג לחיוב ידני"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        name = message.strip()
        if len(name) < 2:
            response = MessageResponse("שם הנהג קצר מדי. אנא הזן שם תקין:")
            return response, DispatcherState.MANUAL_CHARGE_DRIVER_NAME.value, {}

        response = MessageResponse(
            f"נהג: {escape(name)} ✓\n\n" "💰 כמה לחייב? (סכום בשקלים)"
        )
        return (
            response,
            DispatcherState.MANUAL_CHARGE_AMOUNT.value,
            {"charge_driver_name": name},
        )

    async def _handle_manual_charge_amount(
        self, user: User, message: str, context: dict
    ):
        """סכום החיוב הידני"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        import re

        numbers = re.findall(r"\d+\.?\d*", message.strip())
        if not numbers:
            response = MessageResponse("אנא הזן סכום תקין (מספר בלבד).")
            return response, DispatcherState.MANUAL_CHARGE_AMOUNT.value, {}

        amount = float(numbers[0])
        if amount <= 0:
            response = MessageResponse("הסכום חייב להיות חיובי.")
            return response, DispatcherState.MANUAL_CHARGE_AMOUNT.value, {}

        response = MessageResponse(
            f"סכום: {amount:.0f} ₪ ✓\n\n" "📝 תיאור (פרטי המשלוח):"
        )
        return (
            response,
            DispatcherState.MANUAL_CHARGE_DESCRIPTION.value,
            {"charge_amount": amount},
        )

    async def _handle_manual_charge_description(
        self, user: User, message: str, context: dict
    ):
        """תיאור החיוב הידני"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        description = message.strip()
        driver_name = context.get("charge_driver_name", "")
        amount = context.get("charge_amount", 0)

        summary = (
            "💳 <b>סיכום חיוב ידני:</b>\n\n"
            f"👤 נהג: {escape(driver_name)}\n"
            f"💰 סכום: {amount:.0f} ₪\n"
            f"📝 תיאור: {escape(description)}\n\n"
            "לאשר את החיוב?"
        )

        response = MessageResponse(
            summary, keyboard=[["✅ אישור", "❌ ביטול"]]
        )
        return (
            response,
            DispatcherState.MANUAL_CHARGE_CONFIRM.value,
            {"charge_description": description},
        )

    async def _handle_manual_charge_confirm(
        self, user: User, message: str, context: dict
    ):
        """אישור חיוב ידני"""
        if "אישור" in message or "✅" in message:
            driver_name = context.get("charge_driver_name", "")
            amount = context.get("charge_amount", 0)
            description = context.get("charge_description", "")

            await self.station_service.create_manual_charge(
                station_id=self.station_id,
                dispatcher_id=user.id,
                driver_name=driver_name,
                amount=amount,
                description=description,
            )

            response = MessageResponse(
                "החיוב הידני נרשם בהצלחה! ✓\n\n"
                f"👤 נהג: {escape(driver_name)}\n"
                f"💰 סכום: {amount:.0f} ₪\n",
                keyboard=[
                    ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                    ["📋 היסטוריית משלוחים", "💳 חיוב ידני"],
                    ["🔙 חזרה לתפריט ראשי"],
                ],
            )
            return response, DispatcherState.MENU.value, {}

        if "ביטול" in message or "❌" in message:
            response = MessageResponse(
                "החיוב בוטל.\n\n" "חזרה לתפריט סדרן.",
                keyboard=[
                    ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                    ["📋 היסטוריית משלוחים", "💳 חיוב ידני"],
                    ["🔙 חזרה לתפריט ראשי"],
                ],
            )
            return response, DispatcherState.MENU.value, {}

        response = MessageResponse(
            "אנא בחר:\n" "1. ✅ אישור\n" "2. ❌ ביטול",
            keyboard=[["✅ אישור", "❌ ביטול"]],
        )
        return response, DispatcherState.MANUAL_CHARGE_CONFIRM.value, {}

    # ==================== Unknown ====================

    async def _handle_unknown(self, user: User, message: str, context: dict):
        """ניתוב ברירת מחדל - הצגת תפריט סדרן ללא ניתוב מילות מפתח (guard)"""
        logger.warning(
            "סדרן במצב לא מוכר, מחזיר לתפריט",
            extra_data={"user_id": user.id, "message_length": len(message)},
        )
        return await self._show_menu(user, context)
