"""
State Handlers - Process messages based on current state
"""

from typing import Tuple, Optional
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select

from app.state_machine.states import SenderState, CourierState
from app.state_machine.manager import StateManager
from app.db.models.user import User
from app.core.logging import get_logger

logger = get_logger(__name__)


class MessageResponse:
    """Response to be sent to user — כפתורים תמיד inline"""

    def __init__(
        self,
        text: str,
        keyboard: Optional[list] = None,
    ):
        self.text = text
        self.keyboard = keyboard


class SenderStateHandler:
    """Handles sender conversation states"""

    # טקסטים של כפתורי תפריט — לחיצה עליהם בזמן זרימת משלוח מבטלת את הזרימה
    _MENU_BUTTON_TEXTS = {
        "📦 המשלוחים שלי",
        "➕ משלוח חדש",
        "🚚 הצטרפות למנוי וקבלת משלוחים",
        "🚗 הצטרפות כנהג",
        "📦 העלאת משלוח מהיר",
        "🏪 הצטרפות כתחנה",
        "📞 פנייה לניהול",
    }

    # מפתחות קונטקסט של טיוטת משלוח — מנוקים בחזרה ל-MENU
    _DELIVERY_CONTEXT_KEYS = {
        "pickup_city",
        "pickup_street",
        "pickup_number",
        "pickup_apartment",
        "pickup_address",
        "dropoff_city",
        "dropoff_street",
        "dropoff_number",
        "dropoff_apartment",
        "dropoff_address",
        "delivery_location",
        "same_city",
        "urgency",
        "delivery_time",
        "min_price",
        "customer_price",
        "description",
    }

    def __init__(self, db: AsyncSession):
        self.db = db
        self.state_manager = StateManager(db)

    def _is_delivery_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת יצירת משלוח"""
        return state.startswith("SENDER.DELIVERY.")

    async def handle_message(
        self, user_id: int, platform: str, message: str
    ) -> Tuple[MessageResponse, str]:
        """
        Process incoming message and return response with new state
        """
        current_state = await self.state_manager.get_current_state(user_id, platform)
        context = await self.state_manager.get_context(user_id, platform)

        # ביטול זרימת משלוח בלחיצה על כפתור תפריט
        if (
            self._is_delivery_flow_state(current_state)
            and message.strip() in self._MENU_BUTTON_TEXTS
        ):
            logger.info(
                "ביטול זרימת משלוח — המשתמש לחץ כפתור תפריט",
                extra_data={"user_id": user_id, "state": current_state},
            )
            handler = self._get_handler(SenderState.MENU.value)
            response, new_state, context_update = await handler("תפריט", context, user_id)
            # ניקוי context של משלוח + מיזוג context_update מה-handler
            clean_context = {
                k: v for k, v in context.items() if k not in self._DELIVERY_CONTEXT_KEYS
            }
            if context_update:
                for k, v in context_update.items():
                    if k not in self._DELIVERY_CONTEXT_KEYS:
                        clean_context[k] = v
            await self.state_manager.force_state(
                user_id, platform, new_state, clean_context
            )
            return response, new_state

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(message, context, user_id)

        # ניקוי קונטקסט טיוטת משלוח בחזרה ל-MENU מזרימת משלוח
        if new_state == SenderState.MENU.value and self._is_delivery_flow_state(
            current_state
        ):
            clean_context = {
                k: v for k, v in context.items() if k not in self._DELIVERY_CONTEXT_KEYS
            }
            if context_update:
                for k, v in context_update.items():
                    if k not in self._DELIVERY_CONTEXT_KEYS:
                        clean_context[k] = v
            await self.state_manager.force_state(
                user_id, platform, new_state, clean_context
            )
            return response, new_state

        if new_state != current_state:
            # Try to transition to new state
            success = await self.state_manager.transition_to(
                user_id, platform, new_state, context_update
            )
            if not success:
                # Transition failed - force it (skip validation)
                logger.info(
                    "Forcing state transition",
                    extra_data={
                        "user_id": user_id,
                        "platform": platform,
                        "current_state": current_state,
                        "new_state": new_state,
                    },
                )
                await self.state_manager.force_state(
                    user_id,
                    platform,
                    new_state,
                    {**context, **context_update} if context_update else context,
                )
        elif context_update:
            # State didn't change but we have context to save
            for key, value in context_update.items():
                await self.state_manager.update_context(user_id, platform, key, value)

        return response, new_state

    def _get_handler(self, state: str):
        """Get handler function for state"""
        handlers = {
            # Initial & Registration
            SenderState.INITIAL.value: self._handle_initial,
            SenderState.NEW.value: self._handle_new,
            SenderState.REGISTER_COLLECT_NAME.value: self._handle_collect_name,
            SenderState.REGISTER_COLLECT_PHONE.value: self._handle_collect_phone,
            SenderState.MENU.value: self._handle_menu,
            # Pickup address wizard
            SenderState.PICKUP_CITY.value: self._handle_pickup_city,
            SenderState.PICKUP_STREET.value: self._handle_pickup_street,
            SenderState.PICKUP_NUMBER.value: self._handle_pickup_number,
            SenderState.PICKUP_APARTMENT.value: self._handle_pickup_apartment,
            # Dropoff address wizard
            SenderState.DROPOFF_CITY.value: self._handle_dropoff_city,
            SenderState.DROPOFF_STREET.value: self._handle_dropoff_street,
            SenderState.DROPOFF_NUMBER.value: self._handle_dropoff_number,
            SenderState.DROPOFF_APARTMENT.value: self._handle_dropoff_apartment,
            # Delivery details
            SenderState.DELIVERY_LOCATION.value: self._handle_delivery_location,
            SenderState.DELIVERY_URGENCY.value: self._handle_delivery_urgency,
            SenderState.DELIVERY_TIME.value: self._handle_delivery_time,
            SenderState.DELIVERY_PRICE.value: self._handle_delivery_price,
            SenderState.DELIVERY_DESCRIPTION.value: self._handle_delivery_description,
            # Confirmation
            SenderState.DELIVERY_CONFIRM.value: self._handle_confirm,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== Initial & Registration ====================

    async def _handle_initial(self, message: str, context: dict, user_id: int):
        """Handle initial state — בדיקת רישום קיים לפני התחלת רישום מחדש"""
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user and user.name:
            # משתמש רשום — מנתב לתפריט במקום לרישום מחדש
            logger.info(
                "שולח רשום ניגש ל-INITIAL — מנתב לתפריט",
                extra_data={"user_id": user_id},
            )
            response = MessageResponse(
                f"שלום {escape(user.name)}!\n\n"
                "מה תרצו לעשות?\n"
                "1. יצירת משלוח חדש\n"
                "2. צפייה במשלוחים שלי",
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            )
            return response, SenderState.MENU.value, {}

        response = MessageResponse(
            "שלום! ברוכים הבאים לבוט המשלוחים.\n" "אנא הזינו את שמכם להרשמה:",
        )
        return response, SenderState.REGISTER_COLLECT_NAME.value, {}

    async def _handle_new(self, message: str, context: dict, user_id: int):
        """Handle new user"""
        response = MessageResponse(
            "שלום! בואו נתחיל בהרשמה.\n" "מה השם שלך?",
        )
        return response, SenderState.REGISTER_COLLECT_NAME.value, {}

    async def _handle_collect_name(self, message: str, context: dict, user_id: int):
        """Collect user name and save to User table"""
        name = message.strip()
        if len(name) < 2:
            response = MessageResponse("השם קצר מדי. אנא הזינו שם תקין:")
            return response, SenderState.REGISTER_COLLECT_NAME.value, {}

        # Save name to User table
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            user.name = name
            await self.db.commit()

        # אם למשתמש כבר יש מספר טלפון תקין — מדלג לתפריט
        if user and user.phone_number and not user.phone_number.startswith("tg:"):
            safe_name = escape(name)
            response = MessageResponse(
                f"שלום {safe_name}! ההרשמה הושלמה בהצלחה.\n\n"
                "מה תרצו לעשות?\n"
                "1. יצירת משלוח חדש\n"
                "2. צפייה במשלוחים שלי",
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            )
            return response, SenderState.MENU.value, {"name": name}

        safe_name = escape(name)
        response = MessageResponse(
            f"תודה {safe_name}!\n\n"
            "📱 אנא הזינו את מספר הטלפון שלכם:"
        )
        return response, SenderState.REGISTER_COLLECT_PHONE.value, {"name": name}

    async def _handle_collect_phone(self, message: str, context: dict, user_id: int):
        """איסוף מספר טלפון והשלמת רישום"""
        from app.core.validation import PhoneNumberValidator

        phone = message.strip()
        if not PhoneNumberValidator.validate(phone):
            response = MessageResponse(
                "מספר הטלפון לא תקין. אנא הזינו מספר טלפון ישראלי תקין\n"
                "(לדוגמה: 0501234567):"
            )
            return response, SenderState.REGISTER_COLLECT_PHONE.value, {}

        normalized = PhoneNumberValidator.normalize(phone)

        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            logger.error(
                "משתמש לא נמצא בעת רישום טלפון",
                extra_data={"user_id": user_id},
            )
            response = MessageResponse(
                "אירעה שגיאה, נסה שוב מאוחר יותר"
            )
            return response, SenderState.INITIAL.value, {}

        user.phone_number = normalized
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            logger.warning(
                "ניסיון רישום עם מספר טלפון קיים",
                extra_data={
                    "user_id": user_id,
                    "phone": PhoneNumberValidator.mask(normalized),
                },
            )
            response = MessageResponse(
                "מספר הטלפון הזה כבר רשום במערכת.\n"
                "אנא הזינו מספר טלפון אחר:"
            )
            return response, SenderState.REGISTER_COLLECT_PHONE.value, {}

        name = context.get("name", "")
        safe_name = escape(name) if name else ""
        response = MessageResponse(
            f"שלום {safe_name}! ההרשמה הושלמה בהצלחה.\n\n"
            "מה תרצו לעשות?\n"
            "1. יצירת משלוח חדש\n"
            "2. צפייה במשלוחים שלי",
            keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
        )
        logger.info(
            "רישום שולח הושלם",
            extra_data={
                "user_id": user_id,
                "phone": PhoneNumberValidator.mask(normalized),
            },
        )
        return response, SenderState.MENU.value, {"name": name}

    # ==================== Main Menu ====================

    async def _handle_menu(self, message: str, context: dict, user_id: int):
        """Handle main menu"""
        msg = message.strip()
        # הצגת תפריט (למשל לאחר /start או חזרה)
        if msg in {"תפריט", "/start"}:
            response = MessageResponse(
                "מה תרצו לעשות?\n\n"
                "📦 צפייה במשלוחים שלי\n"
                "➕ יצירת משלוח חדש\n"
                "🚚 הצטרפות כנהג/שליח\n"
                "🏪 הצטרפות כתחנה",
                keyboard=[
                    ["📦 המשלוחים שלי", "➕ משלוח חדש"],
                    ["🚚 הצטרפות למנוי וקבלת משלוחים"],
                    ["🚗 הצטרפות כנהג"],
                    ["📦 העלאת משלוח מהיר"],
                    ["🏪 הצטרפות כתחנה"],
                    ["📞 פנייה לניהול"],
                ],
            )
            return response, SenderState.MENU.value, {}

        if "משלוח חדש" in message or "➕" in message or message == "1":
            response = MessageResponse(
                "בואו ניצור משלוח חדש!\n\n" "📍 <b>כתובת איסוף</b>\n" "מה העיר?"
            )
            return response, SenderState.PICKUP_CITY.value, {}

        elif "משלוחים" in message or "📦" in message or message == "2":
            response = MessageResponse(
                "המשלוחים שלך:\n(אין משלוחים עדיין)\n\n" "חזרה לתפריט:",
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            )
            return response, SenderState.MENU.value, {}

        response = MessageResponse(
            "לא הבנתי. אנא בחרו אפשרות מהתפריט:",
            keyboard=[
                ["📦 המשלוחים שלי", "➕ משלוח חדש"],
                ["🚚 הצטרפות למנוי וקבלת משלוחים"],
                ["🚗 הצטרפות כנהג"],
                ["📦 העלאת משלוח מהיר"],
                ["🏪 הצטרפות כתחנה"],
                ["📞 פנייה לניהול"],
            ],
        )
        return response, SenderState.MENU.value, {}

    # ==================== Pickup Address Wizard ====================

    async def _handle_pickup_city(self, message: str, context: dict, user_id: int):
        """Collect pickup city"""
        city = message.strip()

        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזינו שם עיר תקין:")
            return response, SenderState.PICKUP_CITY.value, {}

        safe_city = escape(city)
        response = MessageResponse(f"עיר: {safe_city} ✓\n\n" "מה שם הרחוב?")
        return response, SenderState.PICKUP_STREET.value, {"pickup_city": city}

    async def _handle_pickup_street(self, message: str, context: dict, user_id: int):
        """Collect pickup street"""
        street = message.strip()

        if len(street) < 2:
            response = MessageResponse("שם הרחוב קצר מדי. אנא הזינו שם רחוב תקין:")
            return response, SenderState.PICKUP_STREET.value, {}

        city = context.get("pickup_city", "")
        safe_city = escape(city)
        safe_street = escape(street)
        response = MessageResponse(
            f"עיר: {safe_city} ✓\n" f"רחוב: {safe_street} ✓\n\n" "מה מספר הבית?"
        )
        return response, SenderState.PICKUP_NUMBER.value, {"pickup_street": street}

    async def _handle_pickup_number(self, message: str, context: dict, user_id: int):
        """Collect pickup house number"""
        number = message.strip()

        # Check if contains a digit
        if not any(char.isdigit() for char in number):
            response = MessageResponse(
                "מספר הבית חייב להכיל ספרה. אנא הזינו מספר תקין:"
            )
            return response, SenderState.PICKUP_NUMBER.value, {}

        city = context.get("pickup_city", "")
        street = context.get("pickup_street", "")
        safe_city = escape(city)
        safe_street = escape(street)
        safe_number = escape(number)

        response = MessageResponse(
            f"עיר: {safe_city} ✓\n"
            f"רחוב: {safe_street} ✓\n"
            f"מספר: {safe_number} ✓\n\n"
            "קומה ודירה? (או לחצו <b>דלג</b> אם לא רלוונטי)",
            keyboard=[["דלג"]],
        )
        return response, SenderState.PICKUP_APARTMENT.value, {"pickup_number": number}

    async def _handle_pickup_apartment(self, message: str, context: dict, user_id: int):
        """Collect pickup apartment/floor (optional)"""
        msg = message.strip()

        city = context.get("pickup_city", "")
        street = context.get("pickup_street", "")
        number = context.get("pickup_number", "")

        # Build full address
        if msg.lower() == "דלג" or msg == "-" or msg == "0":
            full_address = f"{street} {number}, {city}"
            apartment = ""
        else:
            full_address = f"{street} {number}, {city} (קומה/דירה: {msg})"
            apartment = msg

        safe_full_address = escape(full_address)
        # לאחר כתובת איסוף - שואלים על סוג המשלוח (בתוך/מחוץ לעיר)
        response = MessageResponse(
            f"📍 כתובת איסוף נשמרה:\n"
            f"{safe_full_address}\n\n"
            "לאן תרצו להעביר את המשלוח?",
            keyboard=[["🏙️ בתוך העיר", "🚗 מחוץ לעיר"]],
        )
        return (
            response,
            SenderState.DELIVERY_LOCATION.value,
            {"pickup_apartment": apartment, "pickup_address": full_address},
        )

    # ==================== Dropoff Address Wizard ====================

    async def _handle_dropoff_city(self, message: str, context: dict, user_id: int):
        """Collect dropoff city"""
        city = message.strip()

        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזינו שם עיר תקין:")
            return response, SenderState.DROPOFF_CITY.value, {}

        safe_city = escape(city)
        response = MessageResponse(f"עיר: {safe_city} ✓\n\n" "מה שם הרחוב?")
        return response, SenderState.DROPOFF_STREET.value, {"dropoff_city": city}

    async def _handle_dropoff_street(self, message: str, context: dict, user_id: int):
        """Collect dropoff street"""
        street = message.strip()

        if len(street) < 2:
            response = MessageResponse("שם הרחוב קצר מדי. אנא הזינו שם רחוב תקין:")
            return response, SenderState.DROPOFF_STREET.value, {}

        city = context.get("dropoff_city", "")
        safe_city = escape(city)
        safe_street = escape(street)
        response = MessageResponse(
            f"עיר: {safe_city} ✓\n" f"רחוב: {safe_street} ✓\n\n" "מה מספר הבית?"
        )
        return response, SenderState.DROPOFF_NUMBER.value, {"dropoff_street": street}

    async def _handle_dropoff_number(self, message: str, context: dict, user_id: int):
        """Collect dropoff house number"""
        number = message.strip()

        # Check if contains a digit
        if not any(char.isdigit() for char in number):
            response = MessageResponse(
                "מספר הבית חייב להכיל ספרה. אנא הזינו מספר תקין:"
            )
            return response, SenderState.DROPOFF_NUMBER.value, {}

        city = context.get("dropoff_city", "")
        street = context.get("dropoff_street", "")
        safe_city = escape(city)
        safe_street = escape(street)
        safe_number = escape(number)

        response = MessageResponse(
            f"עיר: {safe_city} ✓\n"
            f"רחוב: {safe_street} ✓\n"
            f"מספר: {safe_number} ✓\n\n"
            "קומה ודירה? (או לחצו <b>דלג</b> אם לא רלוונטי)",
            keyboard=[["דלג"]],
        )
        return response, SenderState.DROPOFF_APARTMENT.value, {"dropoff_number": number}

    async def _handle_dropoff_apartment(
        self, message: str, context: dict, user_id: int
    ):
        """Collect dropoff apartment/floor (optional) and ask about urgency"""
        msg = message.strip()

        street = context.get("dropoff_street", "")
        number = context.get("dropoff_number", "")
        pickup_city = context.get("pickup_city", "")
        delivery_location = context.get("delivery_location", "")

        # אכיפה: משלוח "בתוך העיר" — עיר היעד חייבת להיות עיר האיסוף
        if delivery_location == "within_city":
            city = pickup_city
        else:
            city = context.get("dropoff_city", "")

        # Build full address
        if msg.lower() == "דלג" or msg == "-" or msg == "0":
            full_dropoff = f"{street} {number}, {city}"
            apartment = ""
        else:
            full_dropoff = f"{street} {number}, {city} (קומה/דירה: {msg})"
            apartment = msg

        # same_city נגזר מ-delivery_location — לא מהשוואת מחרוזות
        same_city = delivery_location == "within_city" or (
            pickup_city.strip().lower() == city.strip().lower()
        )

        # לאחר כתובת יעד - עוברים לשאלת הדחיפות
        safe_full_dropoff = escape(full_dropoff)
        response = MessageResponse(
            f"🎯 כתובת יעד נשמרה:\n{safe_full_dropoff}\n\n" "האם המשלוח דחוף?",
            keyboard=[["🚀 מיידי", "☕ בנחת"]],
        )
        return (
            response,
            SenderState.DELIVERY_URGENCY.value,
            {
                "dropoff_apartment": apartment,
                "dropoff_address": full_dropoff,
                "same_city": same_city,
            },
        )

    # ==================== Delivery Details ====================

    async def _handle_delivery_location(
        self, message: str, context: dict, user_id: int
    ):
        """Handle delivery location selection (within/outside city)"""
        msg = message.strip()

        # לוג לדיבוג - מה בדיוק התקבל מהמשתמש
        logger.debug(
            "Handling delivery location input",
            extra_data={
                "user_id": user_id,
                "raw_input": repr(msg),
                "input_length": len(msg),
            },
        )

        if "בתוך" in msg or "🏙️" in msg or msg == "1":
            location_type = "within_city"
            location_text = "בתוך העיר"
        elif "מחוץ" in msg or "🚗" in msg or msg == "2":
            location_type = "outside_city"
            location_text = "מחוץ לעיר"
        else:
            # לוג כשהתנאי לא מתקיים - לעזור בדיבוג
            logger.warning(
                "Delivery location input did not match expected patterns",
                extra_data={"user_id": user_id, "raw_input": repr(msg)},
            )
            response = MessageResponse(
                "אנא בחרו אפשרות:\n" "1. בתוך העיר\n" "2. מחוץ לעיר",
                keyboard=[["🏙️ בתוך העיר", "🚗 מחוץ לעיר"]],
            )
            return response, SenderState.DELIVERY_LOCATION.value, {}

        # אם משלוח בתוך העיר - משתמשים בעיר האיסוף גם ליעד ומדלגים לשאלת הרחוב
        if location_type == "within_city":
            pickup_city = context.get("pickup_city", "")
            safe_city = escape(pickup_city)
            response = MessageResponse(
                f"סוג משלוח: {location_text} ✓\n\n"
                "עכשיו נזין את כתובת היעד.\n"
                "🎯 <b>כתובת יעד</b>\n"
                f"עיר: {safe_city} ✓\n\n"
                "מה שם הרחוב?"
            )
            return (
                response,
                SenderState.DROPOFF_STREET.value,
                {
                    "delivery_location": location_type,
                    "dropoff_city": pickup_city,  # עיר היעד = עיר האיסוף
                },
            )

        # משלוח מחוץ לעיר - שואלים על עיר היעד
        response = MessageResponse(
            f"סוג משלוח: {location_text} ✓\n\n"
            "עכשיו נזין את כתובת היעד.\n"
            "🎯 <b>כתובת יעד</b>\n"
            "מה העיר?"
        )
        return (
            response,
            SenderState.DROPOFF_CITY.value,
            {"delivery_location": location_type},
        )

    async def _handle_delivery_urgency(self, message: str, context: dict, user_id: int):
        """Handle urgency selection (immediate/later)"""
        msg = message.strip()

        if "מיידי" in msg or "🚀" in msg or msg == "1":
            # Immediate - skip time and price questions, go directly to description
            response = MessageResponse(
                "⚡ משלוח מיידי!\n\n"
                "📝 <b>תיאור המשלוח:</b>\n"
                "מה אתם שולחים? (תיאור קצר של הפריט)"
            )
            return (
                response,
                SenderState.DELIVERY_DESCRIPTION.value,
                {"urgency": "immediate", "delivery_time": "מיידי"},
            )

        elif "בנחת" in msg or "☕" in msg or msg == "2":
            # Later - ask for time
            response = MessageResponse(
                "☕ משלוח בנחת\n\n"
                "⏰ באיזו שעה תרצו שהמשלוח יתבצע?\n"
                "(נא להזין בפורמט HH:MM, לדוגמה: 14:30)"
            )
            return response, SenderState.DELIVERY_TIME.value, {"urgency": "later"}

        response = MessageResponse(
            "אנא בחרו אפשרות:\n"
            "1. 🚀 מיידי - המשלוח יתבצע בהקדם\n"
            "2. ☕ בנחת - תבחרו שעה מועדפת",
            keyboard=[["🚀 מיידי", "☕ בנחת"]],
        )
        return response, SenderState.DELIVERY_URGENCY.value, {}

    async def _handle_delivery_time(self, message: str, context: dict, user_id: int):
        """Handle delivery time input (HH:MM format) - only for 'later' urgency"""
        import re

        msg = message.strip()

        # Validate time format HH:MM
        time_pattern = re.compile(r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$")
        if not time_pattern.match(msg):
            response = MessageResponse(
                "❌ פורמט שעה לא תקין.\n\n"
                "אנא הזינו שעה בפורמט HH:MM\n"
                "לדוגמה: 09:00, 14:30, 18:45"
            )
            return response, SenderState.DELIVERY_TIME.value, {}

        # Calculate minimum price based on location
        location_type = context.get("delivery_location", "within_city")
        if location_type == "within_city":
            min_price = 25
        else:
            min_price = 45

        response = MessageResponse(
            f"⏰ שעת משלוח: {escape(msg)} ✓\n\n"
            f"💰 <b>הצעת מחיר:</b>\n"
            f"מה המחיר שתרצו לשלם?\n"
            f"(מינימום להזמנה זו: {min_price} ₪)"
        )
        return (
            response,
            SenderState.DELIVERY_PRICE.value,
            {"delivery_time": msg, "min_price": min_price},
        )

    async def _handle_delivery_price(self, message: str, context: dict, user_id: int):
        """Handle customer price input - only for 'later' urgency"""
        msg = message.strip()

        # Extract number from message
        import re

        numbers = re.findall(r"\d+", msg)
        if not numbers:
            min_price = context.get("min_price", 25)
            response = MessageResponse(
                f"❌ אנא הזינו סכום תקין (מספר בלבד).\n" f"מינימום: {min_price} ₪"
            )
            return response, SenderState.DELIVERY_PRICE.value, {}

        price = int(numbers[0])
        min_price = context.get("min_price", 25)

        if price < min_price:
            response = MessageResponse(
                f"❌ המחיר נמוך מהמינימום.\n"
                f"מינימום להזמנה זו: {min_price} ₪\n\n"
                "אנא הזינו סכום גבוה יותר:"
            )
            return response, SenderState.DELIVERY_PRICE.value, {}

        response = MessageResponse(
            f"💰 מחיר: {price} ₪ ✓\n\n"
            "📝 <b>תיאור המשלוח:</b>\n"
            "מה אתם שולחים? (תיאור קצר של הפריט)"
        )
        return (
            response,
            SenderState.DELIVERY_DESCRIPTION.value,
            {"customer_price": price},
        )

    async def _handle_delivery_description(
        self, message: str, context: dict, user_id: int
    ):
        """Handle shipment description and show final summary"""
        description = message.strip()

        if len(description) < 2:
            response = MessageResponse(
                "❌ התיאור קצר מדי. אנא תארו את המשלוח (לפחות 2 תווים):"
            )
            return response, SenderState.DELIVERY_DESCRIPTION.value, {}

        # Build summary
        pickup = context.get("pickup_address", "לא צוין")
        dropoff = context.get("dropoff_address", "לא צוין")
        location_type = context.get("delivery_location", "within_city")
        location_text = "בתוך העיר" if location_type == "within_city" else "מחוץ לעיר"
        urgency = context.get("urgency", "immediate")
        delivery_time = context.get("delivery_time", "מיידי")
        customer_price = context.get("customer_price", "לא הוגדר")

        safe_pickup = escape(pickup)
        safe_dropoff = escape(dropoff)
        safe_description = escape(description)
        safe_delivery_time = escape(str(delivery_time))
        summary = (
            f"📋 <b>סיכום המשלוח:</b>\n\n"
            f"📍 איסוף: {safe_pickup}\n"
            f"🎯 יעד: {safe_dropoff}\n"
            f"🗺️ סוג: {location_text}\n"
            f"⏰ זמן: {safe_delivery_time}\n"
        )

        if urgency == "later" and customer_price != "לא הוגדר":
            summary += f"💰 מחיר מוצע: {escape(str(customer_price))} ₪\n"

        summary += f"📦 תיאור: {safe_description}\n\n"
        summary += "לאשר את המשלוח?"

        response = MessageResponse(
            summary, keyboard=[["✅ אישור ושליחה", "❌ ביטול"]]
        )
        return (
            response,
            SenderState.DELIVERY_CONFIRM.value,
            {"description": description},
        )

    # ==================== Confirmation ====================

    async def _handle_confirm(self, message: str, context: dict, user_id: int):
        """Handle delivery confirmation"""
        if "אישור" in message or "✅" in message or "כן" in message.lower():
            pickup = context.get("pickup_address", "לא צוין")
            dropoff = context.get("dropoff_address", "לא צוין")
            description = context.get("description", "")
            urgency = context.get("urgency", "immediate")
            delivery_time = context.get("delivery_time", "מיידי")
            customer_price = context.get("customer_price")

            safe_pickup = escape(pickup)
            safe_dropoff = escape(dropoff)
            safe_delivery_time = escape(str(delivery_time))
            safe_description = escape(description) if description else ""
            success_msg = (
                "המשלוח נוצר בהצלחה! 🎉\n\n"
                f"📍 מ: {safe_pickup}\n"
                f"🎯 אל: {safe_dropoff}\n"
                f"⏰ זמן: {safe_delivery_time}\n"
            )
            if description:
                success_msg += f"📦 תיאור: {safe_description}\n"
            if customer_price:
                success_msg += f"💰 מחיר: {escape(str(customer_price))} ₪\n"

            success_msg += "\nהשליחים יקבלו התראה בקרוב.\n" "מה תרצו לעשות עכשיו?"

            response = MessageResponse(
                success_msg,
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            )
            return response, SenderState.MENU.value, {}

        if "ביטול" in message or "❌" in message or "לא" in message.lower():
            response = MessageResponse(
                "המשלוח בוטל.\n\n" "מה תרצו לעשות?",
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            )
            return response, SenderState.MENU.value, {}

        # Invalid response
        response = MessageResponse(
            "אנא בחרו אפשרות:\n" "1. ✅ אישור ושליחה\n" "2. ❌ ביטול",
            keyboard=[["✅ אישור ושליחה", "❌ ביטול"]],
        )
        return response, SenderState.DELIVERY_CONFIRM.value, {}

    # ==================== Unknown State ====================

    async def _handle_unknown(self, message: str, context: dict, user_id: int):
        """Handle unknown state"""
        response = MessageResponse(
            "משהו השתבש. חוזרים לתפריט הראשי.",
            keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
        )
        return response, SenderState.MENU.value, {}


class CourierStateHandler:
    """Handles courier conversation states - Full registration and operational flow"""

    TERMS_TEXT = """
📜 <b>תקנון שליחים - הצהרת קבלן עצמאי</b>

בלחיצה על "קראתי ואני מאשר" אני מאשר/ת כי:

1. אני קבלן/ית עצמאי/ת ולא עובד/ת של המערכת.
2. אני אחראי/ת באופן מלא על ביצוע המשלוחים.
3. אני מתחייב/ת לשמור על סודיות פרטי הלקוחות.
4. אני מודע/ת לכך שעמלות יקוזזו מיתרתי בגין כל משלוח.
5. אני מתחייב/ת לבצע את המשלוחים בזמן סביר ובצורה מקצועית.
"""

    def __init__(self, db: AsyncSession, platform: str = "telegram"):
        self.db = db
        self.platform = platform
        self.state_manager = StateManager(db)

    def _blocked_or_rejected_response(
        self, user: User
    ) -> tuple[MessageResponse, str, dict] | None:
        """מחזיר תשובה לשליח חסום/נדחה, או None אם הסטטוס אחר."""
        from app.db.models.user import ApprovalStatus
        from app.core.validation import TextSanitizer

        if user.approval_status == ApprovalStatus.BLOCKED:
            response = MessageResponse(
                "❌ חשבונך נחסם. לפרטים נוספים, פנה להנהלה.\n\n"
                "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
            )
            return response, CourierState.PENDING_APPROVAL.value, {}

        if user.approval_status == ApprovalStatus.REJECTED:
            note_line = TextSanitizer.format_note_line(
                user.rejection_note,
                platform=self.platform,
            )
            response = MessageResponse(
                f"לצערנו, בקשתך להצטרף כשליח נדחתה.{note_line}\n"
                "לפרטים נוספים, פנה להנהלה.\n\n"
                "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
            )
            return response, CourierState.PENDING_APPROVAL.value, {}

        return None

    # מפתחות קונטקסט של רישום KYC — מנוקים בחזרה ל-MENU
    _KYC_CONTEXT_KEYS = {
        "document_file_id",
        "selfie_file_id",
        "vehicle_category",
        "vehicle_photo_file_id",
        "changing_area",
    }

    def _is_registration_flow_state(self, state: str) -> bool:
        """בודק אם המצב שייך לזרימת רישום שליח"""
        return (
            state.startswith("COURIER.REGISTER.")
            or state == CourierState.PENDING_APPROVAL.value
        )

    async def handle_message(
        self, user: User, message: str, photo_file_id: str = None
    ) -> Tuple[MessageResponse, str]:
        """Process incoming message for courier and return response with new state"""
        platform = self.platform or user.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(
            user, message, context, photo_file_id
        )

        # ניקוי קונטקסט KYC בחזרה ל-MENU מזרימת רישום
        if new_state == CourierState.MENU.value and self._is_registration_flow_state(
            current_state
        ):
            clean_context = {
                k: v for k, v in context.items() if k not in self._KYC_CONTEXT_KEYS
            }
            if context_update:
                for k, v in context_update.items():
                    if k not in self._KYC_CONTEXT_KEYS:
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
                    "כפיית מעבר מצב בשליח",
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
        """Get handler function for state"""
        handlers = {
            CourierState.INITIAL.value: self._handle_initial,
            CourierState.NEW.value: self._handle_initial,
            CourierState.REGISTER_COLLECT_NAME.value: self._handle_collect_name,
            CourierState.REGISTER_COLLECT_DOCUMENT.value: self._handle_collect_document,
            CourierState.REGISTER_COLLECT_SELFIE.value: self._handle_collect_selfie,
            CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value: self._handle_collect_vehicle_category,
            CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value: self._handle_collect_vehicle_photo,
            CourierState.REGISTER_TERMS.value: self._handle_terms,
            CourierState.PENDING_APPROVAL.value: self._handle_pending_approval,
            CourierState.MENU.value: self._handle_menu,
            CourierState.VIEW_WALLET.value: self._handle_view_wallet,
            CourierState.DEPOSIT_REQUEST.value: self._handle_deposit_request,
            CourierState.DEPOSIT_UPLOAD.value: self._handle_deposit_upload,
            CourierState.CHANGE_AREA.value: self._handle_change_area,
            CourierState.VIEW_HISTORY.value: self._handle_view_history,
            CourierState.VIEW_ACTIVE.value: self._handle_view_active,
            CourierState.SUPPORT.value: self._handle_support,
            # מצבים המטופלים בעיקר דרך webhook קבוצתי (callback queries).
            # כאשר שליח שולח הודעה ישירה בעודו באחד מהמצבים הללו,
            # ה-handler מציג הודעת הכוונה ומחזיר לתפריט.
            CourierState.VIEW_AVAILABLE.value: self._handle_view_available,
            CourierState.CAPTURE_CONFIRM.value: self._handle_capture_confirm,
            CourierState.MARK_PICKED_UP.value: self._handle_mark_picked_up,
            CourierState.MARK_DELIVERED.value: self._handle_mark_delivered,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== Registration Flow [1.2] ====================

    async def _handle_initial(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Welcome message and start registration — בדיקת רישום קיים"""
        from app.db.models.user import ApprovalStatus

        await self.db.refresh(user)

        # שליח שסיים רישום ומאושר — מנתב לתפריט
        if user.terms_accepted_at is not None and user.approval_status == ApprovalStatus.APPROVED:
            logger.info(
                "שליח רשום ניגש ל-INITIAL — מנתב לתפריט",
                extra_data={"user_id": user.id},
            )
            return await self._handle_menu(user, "תפריט", context, photo_file_id)

        # שליח שסיים רישום וממתין לאישור (לא נדחה/נחסם) — מנתב להמתנה
        if user.terms_accepted_at is not None and user.approval_status == ApprovalStatus.PENDING:
            logger.info(
                "שליח רשום ניגש ל-INITIAL — מנתב להמתנת אישור",
                extra_data={"user_id": user.id},
            )
            return await self._handle_pending_approval(
                user, message, context, photo_file_id
            )

        # שליח חסום — אסור לאפשר הגשה מחדש
        if user.terms_accepted_at is not None and user.approval_status == ApprovalStatus.BLOCKED:
            logger.warning(
                "שליח חסום ניסה להירשם מחדש",
                extra_data={"user_id": user.id},
            )
            response = MessageResponse(
                "חשבונך חסום ולא ניתן להירשם מחדש.\n"
                "לבירורים, פנה לתמיכה."
            )
            return response, CourierState.INITIAL.value, {}

        # שליח שנדחה/חדש — מתחיל רישום (מאפשר הגשה חוזרת)
        response = MessageResponse(
            "ברוכים הבאים למערכת משלוח בצ'יק! 🚚\n\n"
            "כדי להתחיל לקחת משלוחים, עלינו להכיר אותך.\n\n"
            "<b>שלב א' - שם מלא:</b>\n"
            "אנא הזן את שמך המלא כפי שמופיע בתעודת הזהות."
        )
        return response, CourierState.REGISTER_COLLECT_NAME.value, {}

    async def _handle_collect_name(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Collect full name - Step a"""
        name = message.strip()
        if len(name) < 2:
            response = MessageResponse(
                "השם שהוזן קצר מדי. אנא הזן את שמך המלא (לפחות 2 תווים)."
            )
            return response, CourierState.REGISTER_COLLECT_NAME.value, {}

        if len(name) > 150:
            response = MessageResponse("השם שהוזן ארוך מדי. אנא הזן שם קצר יותר.")
            return response, CourierState.REGISTER_COLLECT_NAME.value, {}

        # Save name
        user.full_name = name
        user.name = name.split()[0] if name.split() else name
        await self.db.commit()

        response = MessageResponse(
            f"תודה {user.name}!\n\n"
            "<b>שלב ב' - תיעוד רשמי:</b>\n"
            "אנא צלם ושלח כעת תעודת זהות או רישיון נהיגה בתוקף.\n\n"
            "📸 שלח תמונה של המסמך (ודא שהפרטים קריאים)."
        )
        return response, CourierState.REGISTER_COLLECT_DOCUMENT.value, {}

    async def _handle_collect_document(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """איסוף תעודת זהות / רישיון נהיגה - שלב ב'"""
        if not photo_file_id:
            response = MessageResponse(
                "לא התקבלה תמונה. אנא שלח תמונה של תעודת זהות או רישיון נהיגה."
            )
            return response, CourierState.REGISTER_COLLECT_DOCUMENT.value, {}

        # שומרים את מזהה התמונה ישירות ב-DB (כמו סלפי ותמונת רכב)
        user.id_document_url = photo_file_id
        await self.db.commit()

        response = MessageResponse(
            "המסמך התקבל בהצלחה! ✓\n\n"
            "<b>שלב ג' - אימות חי:</b>\n"
            "אנא צלם ושלח סלפי שלך כעת (בזמן אמת).\n\n"
            "📸 שלח תמונת סלפי ברורה."
        )
        return (
            response,
            CourierState.REGISTER_COLLECT_SELFIE.value,
            {"document_file_id": photo_file_id},
        )

    async def _handle_collect_selfie(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """איסוף סלפי לאימות חי - שלב ג'"""
        if not photo_file_id:
            response = MessageResponse(
                "לא התקבלה תמונה. אנא שלח צילום סלפי שלך בזמן אמת."
            )
            return response, CourierState.REGISTER_COLLECT_SELFIE.value, {}

        # שומרים את מזהה הסלפי ומעבירים לבחירת קטגוריית רכב
        user.selfie_file_id = photo_file_id
        await self.db.commit()

        response = MessageResponse(
            "הסלפי התקבל בהצלחה! ✓\n\n"
            "<b>שלב ד' - קטגוריית רכב:</b>\n"
            "באיזה סוג רכב אתה עובד?",
            keyboard=[
                ["🚗 רכב 4 מקומות", "🚐 7 מקומות"],
                ["🛻 טנדר", "🏍️ אופנוע"],
            ],
        )
        return (
            response,
            CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value,
            {"selfie_file_id": photo_file_id},
        )

    async def _handle_collect_vehicle_category(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """בחירת קטגוריית רכב - שלב ד'"""
        msg = message.strip()

        # מיפוי בחירת המשתמש לקטגוריה
        category = None
        category_display = None
        if "4 מקומות" in msg or "🚗" in msg:
            category = "car_4"
            category_display = "רכב 4 מקומות"
        elif "7 מקומות" in msg or "🚐" in msg:
            category = "car_7"
            category_display = "7 מקומות"
        elif "טנדר" in msg or "🛻" in msg:
            category = "pickup_truck"
            category_display = "טנדר"
        elif "אופנוע" in msg or "🏍" in msg:
            category = "motorcycle"
            category_display = "אופנוע"

        if not category:
            response = MessageResponse(
                "אנא בחר אחת מהאפשרויות:\n"
                "1. 🚗 רכב 4 מקומות\n"
                "2. 🚐 7 מקומות\n"
                "3. 🛻 טנדר\n"
                "4. 🏍️ אופנוע",
                keyboard=[
                    ["🚗 רכב 4 מקומות", "🚐 7 מקומות"],
                    ["🛻 טנדר", "🏍️ אופנוע"],
                ],
            )
            return response, CourierState.REGISTER_COLLECT_VEHICLE_CATEGORY.value, {}

        user.vehicle_category = category
        await self.db.commit()

        response = MessageResponse(
            f"קטגוריית רכב: {category_display} ✓\n\n"
            "<b>שלב ה' - תיעוד רכב:</b>\n"
            "אנא צלם ושלח תמונה של הרכב שלך.\n\n"
            "📸 שלח תמונה ברורה של הרכב."
        )
        return (
            response,
            CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value,
            {"vehicle_category": category},
        )

    async def _handle_collect_vehicle_photo(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """איסוף תמונת רכב - שלב ה'"""
        if not photo_file_id:
            response = MessageResponse("לא התקבלה תמונה. אנא שלח תמונה של הרכב שלך.")
            return response, CourierState.REGISTER_COLLECT_VEHICLE_PHOTO.value, {}

        # שומרים את תמונת הרכב ומעבירים לאישור תקנון
        user.vehicle_photo_file_id = photo_file_id
        await self.db.commit()

        response = MessageResponse(
            "תמונת הרכב התקבלה בהצלחה! ✓\n\n" + self.TERMS_TEXT,
            keyboard=[["קראתי ואני מאשר ✅"]],
        )
        return (
            response,
            CourierState.REGISTER_TERMS.value,
            {"vehicle_photo_file_id": photo_file_id},
        )

    async def _handle_terms(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """אישור תקנון - שלב ו'"""
        from datetime import datetime
        from app.db.models.user import ApprovalStatus, UserRole

        # מניעת עיבוד כפול: אם המשתמש כבר אישר תקנון (race condition / webhook כפול)
        # מרפרשים מה-DB כדי לתפוס שינויים שבוצעו בבקשה מקבילית
        await self.db.refresh(user)
        if (
            user.terms_accepted_at is not None
            and user.approval_status == ApprovalStatus.PENDING
        ):
            logger.info(
                "תקנון כבר אושר, מעביר למצב המתנה לאישור",
                extra_data={"user_id": user.id},
            )
            return await self._handle_pending_approval(
                user, message, context, photo_file_id
            )

        if "מאשר" not in message and "אישור" not in message:
            response = MessageResponse(
                "כדי להמשיך, עליך ללחוץ על הכפתור 'קראתי ואני מאשר'.",
                keyboard=[["קראתי ואני מאשר ✅"]],
            )
            return response, CourierState.REGISTER_TERMS.value, {}

        # עדכון סטטוס המשתמש ושמירת כל הנתונים שנאספו במהלך ה-KYC
        user.terms_accepted_at = datetime.utcnow()
        user.role = UserRole.COURIER
        user.approval_status = ApprovalStatus.PENDING
        # ניקוי הערת דחייה ישנה מהגשה קודמת (אם קיימת)
        user.rejection_note = None

        # fallback לתקופת מעבר: שליחים שהתחילו KYC לפני הפריסה
        # עדיין מחזיקים מסמכים רק ב-context. נעתיק ל-DB אם השדה ריק.
        if not user.id_document_url and context.get("document_file_id"):
            user.id_document_url = context["document_file_id"]
        if not user.selfie_file_id and context.get("selfie_file_id"):
            user.selfie_file_id = context["selfie_file_id"]
        if not user.vehicle_category and context.get("vehicle_category"):
            user.vehicle_category = context["vehicle_category"]
        if not user.vehicle_photo_file_id and context.get("vehicle_photo_file_id"):
            user.vehicle_photo_file_id = context["vehicle_photo_file_id"]

        await self.db.commit()

        response = MessageResponse(
            "<b>הרישום הושלם בהצלחה!</b> 🎉\n\n"
            "פרטיך הועברו לבדיקת הנהלה.\n"
            "תקבל הודעה ברגע שחשבונך יאושר.\n\n"
            "⏳ בדרך כלל האישור מתבצע תוך 24 שעות."
        )
        return response, CourierState.PENDING_APPROVAL.value, {}

    async def _handle_pending_approval(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle messages while pending approval [1.4]"""
        from app.db.models.user import ApprovalStatus

        await self.db.refresh(user)

        # בדיקת סטטוס חסימה/דחייה קודם - למניעת עקיפת החסימה דרך הרשמה מחדש
        blocked_or_rejected = self._blocked_or_rejected_response(user)
        if blocked_or_rejected is not None:
            return blocked_or_rejected

        # בדיקה: אם המשתמש לא סיים את הרישום - מחזירים אותו להתחלה
        # (רק אם הוא לא חסום/נדחה)
        if user.terms_accepted_at is None:
            logger.info(
                "User in pending_approval but didn't complete registration, restarting",
                extra_data={"user_id": user.id},
            )
            return await self._handle_initial(user, message, context, photo_file_id)

        if user.approval_status == ApprovalStatus.APPROVED:
            return await self._handle_menu(user, "תפריט", context, photo_file_id)

        response = MessageResponse(
            "⏳ בקשתך עדיין בבדיקה. תקבל הודעה ברגע שחשבונך יאושר.\n\n"
            "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
        )
        return response, CourierState.PENDING_APPROVAL.value, {}

    # ==================== Main Menu [4] ====================

    async def _handle_menu(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle main menu display and navigation"""
        from app.db.models.user import ApprovalStatus

        if user.approval_status != ApprovalStatus.APPROVED:
            return await self._handle_pending_approval(
                user, message, context, photo_file_id
            )

        # Navigation by button text
        if "ארנק" in message or "יתרה" in message:
            return await self._handle_view_wallet(user, message, context, photo_file_id)
        if "אזור" in message or "הגדרות" in message:
            return await self._handle_change_area(user, message, context, photo_file_id)
        if "היסטוריה" in message or "עבודות" in message:
            return await self._handle_view_history(
                user, message, context, photo_file_id
            )
        if "תמיכה" in message or "עזרה" in message:
            return await self._handle_support(user, message, context, photo_file_id)
        if "הפקדה" in message or "טעינה" in message:
            return await self._handle_deposit_request(
                user, message, context, photo_file_id
            )
        if "משלוח פעיל" in message or "משלוח נוכחי" in message:
            return await self._handle_view_active(user, message, context, photo_file_id)

        # בדיקה אם הנהג הוא גם סדרן - הוספת כפתור תפריט סדרן [שלב 3.2]
        from app.domain.services.station_service import StationService

        station_service = StationService(self.db)
        is_dispatcher = await station_service.is_dispatcher(user.id)

        # בניית מקלדת בסיסית
        keyboard = [
            ["💰 מצב הארנק", "📍 הגדרות אזור"],
            ["📦 היסטוריית עבודות", "📦 משלוח פעיל"],
            ["💳 הפקדה", "❓ תמיכה"],
        ]

        # אם הנהג הוא סדרן - מוסיפים כפתור בולט לתפריט סדרן
        if is_dispatcher:
            keyboard.insert(0, ["🏪 תפריט סדרן"])

        # Default menu display
        safe_name = escape(user.full_name or user.name or "שליח")
        safe_area = escape(user.service_area) if user.service_area else "לא הוגדר"
        response = MessageResponse(
            f"📋 <b>תפריט שליח</b>\n\n"
            f"שלום {safe_name}! 👋\n\n"
            f"💰 <b>מצב הארנק:</b> 0.00 ₪\n"
            f"📍 <b>האזור שלך:</b> {safe_area}\n\n"
            "בחר פעולה:",
            keyboard=keyboard,
        )
        return response, CourierState.MENU.value, {}

    # ==================== Wallet Module [3] ====================

    async def _handle_view_wallet(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle wallet view [3.1]"""
        # כפתור חזרה לתפריט - מחזיר לתפריט הראשי
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        from app.core.config import settings

        response = MessageResponse(
            "💰 <b>פרטי הארנק</b>\n\n"
            "🟢 סטטוס: פעיל\n\n"
            "💵 יתרה נוכחית: <b>0.00 ₪</b>\n"
            f"📊 מסגרת אשראי: {settings.DEFAULT_CREDIT_LIMIT:.2f} ₪\n"
            f"🎯 נותר עד לחסימה: {-settings.DEFAULT_CREDIT_LIMIT:.2f} ₪\n\n"
            "לטעינת הארנק, לחץ על 'הפקדה'.",
            keyboard=[["💳 הפקדה"], ["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.VIEW_WALLET.value, {}

    async def _handle_deposit_request(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle deposit request [3.2]"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "💳 <b>טעינת ארנק</b>\n\n"
            "לטעינת הארנק, בצע העברה לאחד מהאמצעים הבאים:\n\n"
            "📱 <b>ביט:</b> 050-1234567\n"
            "📱 <b>פייבוקס:</b> 050-1234567\n"
            "🏦 <b>העברה בנקאית:</b>\n"
            "   בנק: לאומי (10)\n"
            "   סניף: 800\n"
            "   חשבון: 12345678\n\n"
            "לאחר ההעברה, שלח צילום מסך של אישור ההעברה.",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.DEPOSIT_UPLOAD.value, {}

    async def _handle_deposit_upload(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle deposit screenshot upload"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        if not photo_file_id:
            response = MessageResponse(
                "📸 אנא שלח צילום מסך של אישור ההעברה, או לחץ 'חזרה לתפריט'.",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, CourierState.DEPOSIT_UPLOAD.value, {}

        response = MessageResponse(
            "<b>בקשת ההפקדה התקבלה!</b>\n\n"
            "הבקשה הועברה למנהל לאישור.\n"
            "היתרה תתעדכן לאחר אישור ההפקדה.\n\n"
            "⏳ זמן טיפול: עד 24 שעות.",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.MENU.value, {"deposit_screenshot": photo_file_id}

    # ==================== Settings ====================

    async def _handle_change_area(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle area change"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        # Check if this is a new area being set
        if context.get("changing_area"):
            new_area = message.strip()
            if len(new_area) >= 2:
                user.service_area = new_area
                await self.db.commit()

                response = MessageResponse(
                    f"האזור עודכן בהצלחה!\n\nהאזור החדש: <b>{new_area}</b>",
                    keyboard=[["🔙 חזרה לתפריט"]],
                )
                return response, CourierState.MENU.value, {"changing_area": False}

        response = MessageResponse(
            f"📍 <b>הגדרות אזור</b>\n\n"
            f"האזור הנוכחי שלך: <b>{user.service_area or 'לא הוגדר'}</b>\n\n"
            "לשינוי האזור, הקלד את האזור החדש.",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.CHANGE_AREA.value, {"changing_area": True}

    async def _handle_view_history(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle work history view"""
        # הצגה ראשונית — נקרא ישירות מ-_handle_menu עם טקסט הכפתור
        if "היסטוריה" in message or "עבודות" in message:
            response = MessageResponse(
                "📦 <b>היסטוריית עבודות</b>\n\n"
                "אין משלוחים בהיסטוריה עדיין.\n"
                "התחל לקחת משלוחים כדי לראות את ההיסטוריה שלך!",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, CourierState.VIEW_HISTORY.value, {}

        # כל קלט אחר (כולל כפתורי תפריט) — חזרה לתפריט
        return await self._handle_menu(user, "תפריט", context, None)

    async def _handle_view_active(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle viewing active delivery"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "📦 אין לך משלוח פעיל כרגע.\nתפוס משלוח חדש מהקבוצה!",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.MENU.value, {}

    async def _handle_support(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle support requests — העברת הודעה להנהלה"""
        if "חזרה" in message or "תפריט" in message:
            # ניקוי דגל תמיכה כדי שהכניסה הבאה תציג הנחיות מחדש
            menu_response, menu_state, menu_ctx = await self._handle_menu(
                user, "תפריט", context, None
            )
            menu_ctx["support_prompt_shown"] = None
            return menu_response, menu_state, menu_ctx

        # כניסה ראשונה מהתפריט — הצגת הנחיות
        # משתמשים ב-context flag כדי להבדיל בין כניסה ראשונה לבין הודעת תמיכה
        # (בדיקת substring כמו "תמיכה" in message תתפוס גם הודעות תמיכה אמיתיות)
        if not context.get("support_prompt_shown"):
            response = MessageResponse(
                "❓ <b>תמיכה</b>\n\n"
                "📧 שלח הודעה למנהל - פשוט כתוב את ההודעה כאן והיא תועבר.\n\n"
                "📞 מוקד: 050-1234567\n"
                "שעות פעילות: א'-ה' 08:00-20:00",
                keyboard=[["🔙 חזרה לתפריט"]],
            )
            return response, CourierState.SUPPORT.value, {"support_prompt_shown": True}

        # העברת ההודעה להנהלה
        from app.domain.services.admin_notification_service import (
            AdminNotificationService,
        )
        from app.core.validation import PhoneNumberValidator

        user_name = user.full_name or user.name or "לא צוין"
        phone_display = (
            PhoneNumberValidator.mask(user.phone_number)
            if user.phone_number
            else f"Telegram ID: {user.telegram_id or user.id}"
        )
        forward_text = (
            f"📨 פנייה מ-{user_name}\n"
            f"({phone_display})\n\n"
            f"{message}"
        )

        sent = await AdminNotificationService.forward_support_message(
            forward_text, user.id, prefer_telegram=True
        )

        if sent:
            confirm_text = "✅ ההודעה נשלחה להנהלה. נחזור אליכם בהקדם!"
        else:
            confirm_text = (
                "⚠️ לא הצלחנו להעביר את ההודעה כרגע.\n"
                "אנא נסו שוב מאוחר יותר."
            )

        response = MessageResponse(
            confirm_text, keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.MENU.value, {"support_prompt_shown": None}

    # ==================== מצבי משלוח (webhook קבוצתי) ====================
    # מצבים אלה מופעלים בעיקר דרך callback queries בקבוצת טלגרם.
    # כאשר שליח שולח הודעה ישירה בעודו באחד מהמצבים הללו,
    # ה-handlers מציגים הנחיה מתאימה.

    async def _handle_view_available(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """צפייה במשלוחים זמינים - מטופל בעיקר דרך הקבוצה"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "📋 <b>משלוחים זמינים</b>\n\n"
            "משלוחים זמינים מוצגים בקבוצת הטלגרם.\n"
            "לחץ על 'תפוס' בקבוצה כדי לתפוס משלוח.",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.MENU.value, {}

    async def _handle_capture_confirm(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """אישור תפיסת משלוח - מטופל בעיקר דרך callback query"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "📦 <b>תפיסת משלוח</b>\n\n"
            "כדי לתפוס משלוח, לחץ על כפתור 'תפוס' בהודעת המשלוח בקבוצה.",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.MENU.value, {}

    async def _handle_mark_picked_up(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """סימון איסוף משלוח - מטופל בעיקר דרך callback query"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "📦 <b>סימון איסוף</b>\n\n"
            "כדי לסמן שאספת את המשלוח, השתמש בכפתורים בהודעת המשלוח.",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.MENU.value, {}

    async def _handle_mark_delivered(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """סימון מסירת משלוח - מטופל בעיקר דרך callback query"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "📦 <b>סימון מסירה</b>\n\n"
            "כדי לסמן שמסרת את המשלוח, השתמש בכפתורים בהודעת המשלוח.",
            keyboard=[["🔙 חזרה לתפריט"]],
        )
        return response, CourierState.MENU.value, {}

    async def _handle_unknown(
        self, user: User, message: str, context: dict, photo_file_id: str
    ):
        """Handle unknown state - restart registration or show appropriate screen"""
        from app.db.models.user import ApprovalStatus

        # אם השליח מאושר - מציגים תפריט
        if user.approval_status == ApprovalStatus.APPROVED:
            return await self._handle_menu(user, message, context, photo_file_id)

        # אם השליח נחסם או נדחה - מציגים הודעה מתאימה ולא מאפשרים רישום מחדש
        blocked_or_rejected = self._blocked_or_rejected_response(user)
        if blocked_or_rejected is not None:
            return blocked_or_rejected

        # אם השליח סיים את הרישום (יש לו תאריך אישור תקנון) - הוא ממתין לאישור
        if user.terms_accepted_at is not None:
            return await self._handle_pending_approval(
                user, message, context, photo_file_id
            )

        # אחרת - המשתמש לא סיים את הרישום, מתחילים מחדש
        logger.info(
            "Courier in unknown state without completing registration, restarting",
            extra_data={"user_id": user.id},
        )
        return await self._handle_initial(user, message, context, photo_file_id)
