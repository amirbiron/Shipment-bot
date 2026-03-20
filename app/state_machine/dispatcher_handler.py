"""
Dispatcher State Handler - תפריט סדרן היברידי [שלב 3.2 + סשן 9]

סדרן הוא משתמש עם הרשאות ניהול ברמת תחנה.
יכול להיות שליח (COURIER), נהג (DRIVER) או כל תפקיד אחר שנוסף כסדרן דרך הפאנל.
תפריט סדרן ייעודי עם:
- הוספת משלוח (טופס הזנת פרטים)
- משלוחים פעילים של התחנה
- היסטוריית משלוחים
- הוספת חיוב ידני
- פרסום נסיעה למערכת ההפצה (סשן 9)
- צפייה בנסיעות פעילות (סשן 9)
"""

import re
from typing import Tuple
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession

from app.state_machine.states import DispatcherState
from app.state_machine.manager import StateManager
from app.state_machine.handlers import MessageResponse
from app.db.models.user import User
from app.db.models.delivery import DeliveryStatus
from app.db.models.dispatcher_ride import DispatcherRideStatus
from app.domain.services.station_service import StationService
from app.domain.services.delivery_service import DeliveryService
from app.core.logging import get_logger
from app.core.validation import TextSanitizer

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
        "dropoff_apartment",
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

    # סשן 9: מפתחות קונטקסט של פרסום נסיעה — מנוקים בחזרה ל-MENU
    _POST_RIDE_CONTEXT_KEYS = {
        "ride_origin",
        "ride_destination",
        "ride_seats",
        "ride_price",
    }

    def _is_multi_step_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימה רב-שלבית.

        בודק באופן דינמי — כל state של סדרן שאינו MENU או VIEW נחשב לזרימה רב-שלבית.
        כך flows חדשים (כמו ISSUE_REFUND) ייכללו אוטומטית ללא עדכון ידני.
        """
        if not state.startswith("DISPATCHER."):
            return False
        # MENU ו-VIEW הם states "סופיים" — לא חלק מזרימה רב-שלבית
        suffix = state[len("DISPATCHER."):]
        if suffix == "MENU" or suffix.startswith("VIEW_"):
            return False
        return True

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
                self._SHIPMENT_CONTEXT_KEYS
                | self._MANUAL_CHARGE_CONTEXT_KEYS
                | self._POST_RIDE_CONTEXT_KEYS
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
            DispatcherState.ADD_SHIPMENT_DROPOFF_APARTMENT.value: self._handle_add_shipment_dropoff_apartment,
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
            # סשן 9: פרסום נסיעה
            DispatcherState.POST_RIDE_ORIGIN.value: self._handle_post_ride_origin,
            DispatcherState.POST_RIDE_DESTINATION.value: self._handle_post_ride_destination,
            DispatcherState.POST_RIDE_SEATS.value: self._handle_post_ride_seats,
            DispatcherState.POST_RIDE_PRICE.value: self._handle_post_ride_price,
            DispatcherState.POST_RIDE_CONFIRM.value: self._handle_post_ride_confirm,
            # סשן 9: צפייה בנסיעות
            DispatcherState.VIEW_POSTED_RIDES.value: self._handle_view_posted_rides,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== תפריט סדרן ====================

    async def _show_menu(self, user: User, context: dict):
        """הצגת תפריט סדרן ללא ניתוב לפי תוכן הודעה"""
        station = await self.station_service.get_station(self.station_id)
        station_name = station.name if station else "תחנה"

        from app.core.message_design import dispatcher_menu_card

        response = MessageResponse(
            dispatcher_menu_card(station_name=station_name),
            keyboard=[
                ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                ["🚗 פרסום נסיעה", "🛣 נסיעות פעילות"],
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

        if "פרסום נסיעה" in msg or "🚗" in msg:
            response = MessageResponse(
                "🚗 <b>פרסום נסיעה חדשה</b>\n\n"
                "📍 <b>עיר מוצא:</b>\n"
                "מאיפה יוצאים?"
            )
            return response, DispatcherState.POST_RIDE_ORIGIN.value, {}

        if "נסיעות פעילות" in msg or "🛣" in msg:
            return await self._show_posted_rides(user, context)

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

        response = MessageResponse(
            f"עיר: {escape(context.get('dropoff_city', ''))} ✓\n"
            f"רחוב: {escape(context.get('dropoff_street', ''))} {escape(number)} ✓\n\n"
            "מספר דירה/יחידה? (או 'דלג' אם אין)"
        )
        return (
            response,
            DispatcherState.ADD_SHIPMENT_DROPOFF_APARTMENT.value,
            {"dropoff_number": number},
        )

    async def _handle_add_shipment_dropoff_apartment(
        self, user: User, message: str, context: dict
    ):
        """דירה/יחידה ביעד"""
        apartment = message.strip()
        city = context.get("dropoff_city", "")
        street = context.get("dropoff_street", "")
        number = context.get("dropoff_number", "")

        if apartment and apartment not in ("דלג", "-", "0"):
            dropoff_address = f"{street} {number} דירה {apartment}, {city}"
            ctx_update = {"dropoff_apartment": apartment, "dropoff_address": dropoff_address}
        else:
            dropoff_address = f"{street} {number}, {city}"
            ctx_update = {"dropoff_apartment": None, "dropoff_address": dropoff_address}

        response = MessageResponse(
            f"🎯 כתובת יעד: {escape(dropoff_address)} ✓\n\n"
            "📝 <b>תיאור המשלוח:</b>\n"
            "מה נשלח? (תיאור קצר)"
        )
        return (
            response,
            DispatcherState.ADD_SHIPMENT_DESCRIPTION.value,
            ctx_update,
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

        from app.core.message_design import shipment_summary_card

        summary = shipment_summary_card(
            pickup=pickup,
            dropoff=dropoff,
            description=description,
            fee=f"{fee:.2f}",
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
                    ["🚗 פרסום נסיעה", "🛣 נסיעות פעילות"],
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
                    ["🚗 פרסום נסיעה", "🛣 נסיעות פעילות"],
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
            from app.core.message_design import card_header

            response = MessageResponse(
                card_header("📦", "משלוחים פעילים") + "\n\nאין משלוחים פעילים כרגע.",
                keyboard=[["🔙 חזרה לתפריט סדרן"]],
            )
            return response, DispatcherState.VIEW_ACTIVE_SHIPMENTS.value, {}

        status_map = {
            DeliveryStatus.OPEN: "🟡 פתוח",
            DeliveryStatus.CAPTURED: "🟠 נתפס",
            DeliveryStatus.IN_PROGRESS: "🔵 בדרך",
        }

        from app.core.message_design import active_deliveries_card, delivery_list_item

        items = []
        for d in deliveries[:10]:
            status_text = status_map.get(d.status, d.status.value)
            items.append(delivery_list_item(
                delivery_id=d.id,
                status_text=status_text,
                pickup=d.pickup_address,
                dropoff=d.dropoff_address,
                fee=d.fee,
            ))

        text = active_deliveries_card("\n\n".join(items))

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
            from app.core.message_design import card_header

            response = MessageResponse(
                card_header("📋", "היסטוריית משלוחים") + "\n\nאין משלוחים בהיסטוריה עדיין.",
                keyboard=[["🔙 חזרה לתפריט סדרן"]],
            )
            return response, DispatcherState.VIEW_SHIPMENT_HISTORY.value, {}

        status_map = {
            DeliveryStatus.DELIVERED: "✅ הושלם",
            DeliveryStatus.CANCELLED: "❌ בוטל",
        }

        from app.core.message_design import active_deliveries_card, delivery_list_item

        items = []
        for d in deliveries[:10]:
            status_text = status_map.get(d.status, d.status.value)
            items.append(delivery_list_item(
                delivery_id=d.id,
                status_text=status_text,
                pickup=d.pickup_address,
                dropoff=d.dropoff_address,
                fee=d.fee,
            ))

        text = active_deliveries_card(
            "\n\n".join(items),
            title="היסטוריית משלוחים",
            emoji="📋",
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
                    ["🚗 פרסום נסיעה", "🛣 נסיעות פעילות"],
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
                    ["🚗 פרסום נסיעה", "🛣 נסיעות פעילות"],
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

    # ==================== פרסום נסיעה (סשן 9) ====================

    async def _handle_post_ride_origin(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """עיר מוצא לנסיעה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        city = message.strip()
        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזן שם עיר תקין:")
            return response, DispatcherState.POST_RIDE_ORIGIN.value, {}

        # ולידציית בטיחות
        is_safe, _pattern = TextSanitizer.check_for_injection(city)
        if not is_safe:
            response = MessageResponse("קלט לא תקין. אנא הזן שם עיר:")
            return response, DispatcherState.POST_RIDE_ORIGIN.value, {}

        # ניסיון זיהוי קיצור עיר דרך CityAbbreviationService
        from app.domain.services.city_abbreviation_service import (
            CityAbbreviationService,
        )

        resolved = CityAbbreviationService.resolve_or_raw(city)

        response = MessageResponse(
            f"📍 מוצא: {escape(resolved)} ✓\n\n"
            "🎯 <b>עיר יעד:</b>\n"
            "לאן נוסעים?"
        )
        return (
            response,
            DispatcherState.POST_RIDE_DESTINATION.value,
            {"ride_origin": resolved},
        )

    async def _handle_post_ride_destination(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """עיר יעד לנסיעה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        city = message.strip()
        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזן שם עיר תקין:")
            return response, DispatcherState.POST_RIDE_DESTINATION.value, {}

        is_safe, _pattern = TextSanitizer.check_for_injection(city)
        if not is_safe:
            response = MessageResponse("קלט לא תקין. אנא הזן שם עיר:")
            return response, DispatcherState.POST_RIDE_DESTINATION.value, {}

        from app.domain.services.city_abbreviation_service import (
            CityAbbreviationService,
        )

        resolved = CityAbbreviationService.resolve_or_raw(city)

        response = MessageResponse(
            f"📍 מוצא: {escape(context.get('ride_origin', ''))} ✓\n"
            f"🎯 יעד: {escape(resolved)} ✓\n\n"
            "👥 <b>מספר מקומות פנויים:</b>"
        )
        return (
            response,
            DispatcherState.POST_RIDE_SEATS.value,
            {"ride_destination": resolved},
        )

    async def _handle_post_ride_seats(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """מספר מקומות פנויים"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        numbers = re.findall(r"\d+", message.strip())
        if not numbers:
            response = MessageResponse("אנא הזן מספר תקין (מספר מקומות).")
            return response, DispatcherState.POST_RIDE_SEATS.value, {}

        seats = int(numbers[0])
        if seats <= 0 or seats > 50:
            response = MessageResponse("מספר מקומות חייב להיות בין 1 ל-50.")
            return response, DispatcherState.POST_RIDE_SEATS.value, {}

        response = MessageResponse(
            f"👥 מקומות: {seats} ✓\n\n"
            "💰 <b>מחיר הנסיעה:</b>\n"
            "כמה עולה? (מספר בלבד, בשקלים)"
        )
        return (
            response,
            DispatcherState.POST_RIDE_PRICE.value,
            {"ride_seats": seats},
        )

    async def _handle_post_ride_price(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """מחיר הנסיעה"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        numbers = re.findall(r"\d+\.?\d*", message.strip())
        if not numbers:
            response = MessageResponse("אנא הזן סכום תקין (מספר בלבד).")
            return response, DispatcherState.POST_RIDE_PRICE.value, {}

        price = float(numbers[0])
        if price <= 0:
            response = MessageResponse("הסכום חייב להיות חיובי.")
            return response, DispatcherState.POST_RIDE_PRICE.value, {}

        origin = context.get("ride_origin", "לא צוין")
        destination = context.get("ride_destination", "לא צוין")
        seats = context.get("ride_seats", 0)

        summary = (
            "🚗 <b>סיכום הנסיעה:</b>\n\n"
            f"📍 מוצא: {escape(origin)}\n"
            f"🎯 יעד: {escape(destination)}\n"
            f"👥 מקומות: {seats}\n"
            f"💰 מחיר: {price:.0f} ₪\n\n"
            "לאשר ולפרסם?"
        )

        response = MessageResponse(
            summary, keyboard=[["✅ אישור ופרסום", "❌ ביטול"]]
        )
        return (
            response,
            DispatcherState.POST_RIDE_CONFIRM.value,
            {"ride_price": price},
        )

    async def _handle_post_ride_confirm(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """אישור או ביטול פרסום נסיעה"""
        if "אישור" in message or "✅" in message:
            origin = context.get("ride_origin", "")
            destination = context.get("ride_destination", "")
            seats = context.get("ride_seats", 1)
            price = context.get("ride_price", 0)

            # יצירת הנסיעה ב-DB + הפצה לקבוצות
            ride = await self.station_service.create_dispatcher_ride(
                station_id=self.station_id,
                dispatcher_id=user.id,
                origin_city=origin,
                destination_city=destination,
                seats=int(seats),
                price=float(price),
            )

            # הפצה לקבוצות רלוונטיות
            sent_count, total_groups = await self._broadcast_ride(
                user, origin, destination, int(seats), float(price)
            )

            broadcast_text = ""
            if total_groups > 0:
                broadcast_text = f"\n📡 פורסם ב-{sent_count}/{total_groups} קבוצות."
            else:
                broadcast_text = "\n📡 לא נמצאו קבוצות רלוונטיות לפרסום."

            response = MessageResponse(
                "הנסיעה פורסמה בהצלחה! 🎉\n\n"
                f"📍 מ: {escape(origin)}\n"
                f"🎯 אל: {escape(destination)}\n"
                f"👥 מקומות: {seats}\n"
                f"💰 מחיר: {price:.0f} ₪"
                f"{broadcast_text}",
                keyboard=[
                    ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                    ["🚗 פרסום נסיעה", "🛣 נסיעות פעילות"],
                    ["📋 היסטוריית משלוחים", "💳 חיוב ידני"],
                    ["🔙 חזרה לתפריט ראשי"],
                ],
            )
            return response, DispatcherState.MENU.value, {}

        if "ביטול" in message or "❌" in message:
            response = MessageResponse(
                "הפרסום בוטל.\n\n" "חזרה לתפריט סדרן.",
                keyboard=[
                    ["➕ הוספת משלוח", "📦 משלוחים פעילים"],
                    ["🚗 פרסום נסיעה", "🛣 נסיעות פעילות"],
                    ["📋 היסטוריית משלוחים", "💳 חיוב ידני"],
                    ["🔙 חזרה לתפריט ראשי"],
                ],
            )
            return response, DispatcherState.MENU.value, {}

        response = MessageResponse(
            "אנא בחר:\n" "1. ✅ אישור ופרסום\n" "2. ❌ ביטול",
            keyboard=[["✅ אישור ופרסום", "❌ ביטול"]],
        )
        return response, DispatcherState.POST_RIDE_CONFIRM.value, {}

    async def _broadcast_ride(
        self,
        user: User,
        origin: str,
        destination: str,
        seats: int,
        price: float,
    ) -> tuple[int, int]:
        """הפצת נסיעה לקבוצות רלוונטיות דרך RidePostingService"""
        try:
            from app.domain.services.ride_posting_service import (
                ParsedRidePosting,
                RidePostingService,
            )

            posting = ParsedRidePosting(
                origin=origin,
                destination=destination,
                seats=seats,
                price=price,
            )
            ride_service = RidePostingService(self.db)
            _success, _msg, sent_count, total_groups = await ride_service.post_ride(
                user, posting
            )
            return sent_count, total_groups
        except Exception as e:
            logger.error(
                "כשלון בהפצת נסיעה לקבוצות",
                extra_data={"user_id": user.id, "error": str(e)},
                exc_info=True,
            )
            return 0, 0

    # ==================== צפייה בנסיעות (סשן 9) ====================

    async def _show_posted_rides(
        self, user: User, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """הצגת נסיעות פעילות שפרסם הסדרן"""
        rides = await self.station_service.get_station_active_rides(self.station_id)

        if not rides:
            from app.core.message_design import card_header

            response = MessageResponse(
                card_header("🛣", "נסיעות פעילות") + "\n\nאין נסיעות פעילות כרגע.",
                keyboard=[["🔙 חזרה לתפריט סדרן"]],
            )
            return response, DispatcherState.VIEW_POSTED_RIDES.value, {}

        status_map = {
            DispatcherRideStatus.OPEN.value: "🟢 פתוחה",
            DispatcherRideStatus.TAKEN.value: "🟠 נתפסה",
        }

        from app.core.message_design import active_deliveries_card

        items = []
        for ride in rides[:10]:
            status_text = status_map.get(ride.status, ride.status)
            items.append(
                f"#{ride.id} | {status_text}\n"
                f"├ 📍 {escape(ride.origin_city)} → {escape(ride.destination_city)}\n"
                f"└ 👥 {ride.seats} מק' | 💰 {ride.price:.0f} ₪"
            )

        text = active_deliveries_card(
            "\n\n".join(items),
            title="נסיעות פעילות",
            emoji="🛣",
        )

        response = MessageResponse(text, keyboard=[["🔙 חזרה לתפריט סדרן"]])
        return response, DispatcherState.VIEW_POSTED_RIDES.value, {}

    async def _handle_view_posted_rides(
        self, user: User, message: str, context: dict
    ) -> Tuple[MessageResponse, str, dict]:
        """צפייה בנסיעות שפרסם הסדרן"""
        if "חזרה" in message:
            return await self._show_menu(user, context)

        return await self._show_posted_rides(user, context)

    # ==================== Unknown ====================

    async def _handle_unknown(self, user: User, message: str, context: dict):
        """ניתוב ברירת מחדל - הצגת תפריט סדרן ללא ניתוב מילות מפתח (guard)"""
        logger.warning(
            "סדרן במצב לא מוכר, מחזיר לתפריט",
            extra_data={"user_id": user.id, "message_length": len(message)},
        )
        return await self._show_menu(user, context)
