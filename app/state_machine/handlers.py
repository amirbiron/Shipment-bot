"""
State Handlers - Process messages based on current state
"""
from typing import Tuple, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.state_machine.states import SenderState, CourierState
from app.state_machine.manager import StateManager
from app.db.models.user import User


class MessageResponse:
    """Response to be sent to user"""

    def __init__(self, text: str, keyboard: Optional[list] = None):
        self.text = text
        self.keyboard = keyboard


class SenderStateHandler:
    """Handles sender conversation states"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.state_manager = StateManager(db)

    async def handle_message(
        self,
        user_id: int,
        platform: str,
        message: str
    ) -> Tuple[MessageResponse, str]:
        """
        Process incoming message and return response with new state
        """
        current_state = await self.state_manager.get_current_state(user_id, platform)
        context = await self.state_manager.get_context(user_id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(message, context, user_id)

        if new_state != current_state:
            # Try to transition to new state
            success = await self.state_manager.transition_to(
                user_id, platform, new_state, context_update
            )
            if not success:
                # Transition failed - force it (skip validation)
                print(f"Forcing transition: {current_state} -> {new_state}")
                await self.state_manager.force_state(
                    user_id, platform, new_state,
                    {**context, **context_update} if context_update else context
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

            # Confirmation
            SenderState.DELIVERY_CONFIRM.value: self._handle_confirm,
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== Initial & Registration ====================

    async def _handle_initial(self, message: str, context: dict, user_id: int):
        """Handle initial state"""
        response = MessageResponse(
            "שלום! ברוכים הבאים לבוט המשלוחים.\n"
            "אנא הזינו את שמכם להרשמה:",
        )
        return response, SenderState.REGISTER_COLLECT_NAME.value, {}

    async def _handle_new(self, message: str, context: dict, user_id: int):
        """Handle new user"""
        response = MessageResponse(
            "שלום! בואו נתחיל בהרשמה.\n"
            "מה השם שלך?",
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

        response = MessageResponse(
            f"שלום {name}! ההרשמה הושלמה בהצלחה.\n\n"
            "מה תרצו לעשות?\n"
            "1. יצירת משלוח חדש\n"
            "2. צפייה במשלוחים שלי",
            keyboard=[["משלוח חדש", "המשלוחים שלי"]]
        )
        return response, SenderState.MENU.value, {"name": name}

    # ==================== Main Menu ====================

    async def _handle_menu(self, message: str, context: dict, user_id: int):
        """Handle main menu"""
        if "משלוח חדש" in message or message == "1":
            response = MessageResponse(
                "בואו ניצור משלוח חדש!\n\n"
                "📍 *כתובת איסוף*\n"
                "מה העיר?"
            )
            return response, SenderState.PICKUP_CITY.value, {}

        elif "משלוחים" in message or message == "2":
            response = MessageResponse(
                "המשלוחים שלך:\n(אין משלוחים עדיין)\n\n"
                "חזרה לתפריט:",
                keyboard=[["משלוח חדש", "המשלוחים שלי"]]
            )
            return response, SenderState.MENU.value, {}

        response = MessageResponse(
            "לא הבנתי. אנא בחרו אפשרות:\n"
            "1. משלוח חדש\n"
            "2. המשלוחים שלי",
            keyboard=[["משלוח חדש", "המשלוחים שלי"]]
        )
        return response, SenderState.MENU.value, {}

    # ==================== Pickup Address Wizard ====================

    async def _handle_pickup_city(self, message: str, context: dict, user_id: int):
        """Collect pickup city"""
        city = message.strip()

        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזינו שם עיר תקין:")
            return response, SenderState.PICKUP_CITY.value, {}

        response = MessageResponse(
            f"עיר: {city} ✓\n\n"
            "מה שם הרחוב?"
        )
        return response, SenderState.PICKUP_STREET.value, {"pickup_city": city}

    async def _handle_pickup_street(self, message: str, context: dict, user_id: int):
        """Collect pickup street"""
        street = message.strip()

        if len(street) < 2:
            response = MessageResponse("שם הרחוב קצר מדי. אנא הזינו שם רחוב תקין:")
            return response, SenderState.PICKUP_STREET.value, {}

        city = context.get("pickup_city", "")
        response = MessageResponse(
            f"עיר: {city} ✓\n"
            f"רחוב: {street} ✓\n\n"
            "מה מספר הבית?"
        )
        return response, SenderState.PICKUP_NUMBER.value, {"pickup_street": street}

    async def _handle_pickup_number(self, message: str, context: dict, user_id: int):
        """Collect pickup house number"""
        number = message.strip()

        # Check if contains a digit
        if not any(char.isdigit() for char in number):
            response = MessageResponse("מספר הבית חייב להכיל ספרה. אנא הזינו מספר תקין:")
            return response, SenderState.PICKUP_NUMBER.value, {}

        city = context.get("pickup_city", "")
        street = context.get("pickup_street", "")

        response = MessageResponse(
            f"עיר: {city} ✓\n"
            f"רחוב: {street} ✓\n"
            f"מספר: {number} ✓\n\n"
            "קומה ודירה? (או הקלידו *דלג* אם לא רלוונטי)",
            keyboard=[["דלג"]]
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

        response = MessageResponse(
            f"📍 כתובת איסוף נשמרה:\n"
            f"{full_address}\n\n"
            "עכשיו נזין את כתובת היעד.\n"
            "🎯 *כתובת יעד*\n"
            "מה העיר?"
        )
        return response, SenderState.DROPOFF_CITY.value, {
            "pickup_apartment": apartment,
            "pickup_address": full_address
        }

    # ==================== Dropoff Address Wizard ====================

    async def _handle_dropoff_city(self, message: str, context: dict, user_id: int):
        """Collect dropoff city"""
        city = message.strip()

        if len(city) < 2:
            response = MessageResponse("שם העיר קצר מדי. אנא הזינו שם עיר תקין:")
            return response, SenderState.DROPOFF_CITY.value, {}

        response = MessageResponse(
            f"עיר: {city} ✓\n\n"
            "מה שם הרחוב?"
        )
        return response, SenderState.DROPOFF_STREET.value, {"dropoff_city": city}

    async def _handle_dropoff_street(self, message: str, context: dict, user_id: int):
        """Collect dropoff street"""
        street = message.strip()

        if len(street) < 2:
            response = MessageResponse("שם הרחוב קצר מדי. אנא הזינו שם רחוב תקין:")
            return response, SenderState.DROPOFF_STREET.value, {}

        city = context.get("dropoff_city", "")
        response = MessageResponse(
            f"עיר: {city} ✓\n"
            f"רחוב: {street} ✓\n\n"
            "מה מספר הבית?"
        )
        return response, SenderState.DROPOFF_NUMBER.value, {"dropoff_street": street}

    async def _handle_dropoff_number(self, message: str, context: dict, user_id: int):
        """Collect dropoff house number"""
        number = message.strip()

        # Check if contains a digit
        if not any(char.isdigit() for char in number):
            response = MessageResponse("מספר הבית חייב להכיל ספרה. אנא הזינו מספר תקין:")
            return response, SenderState.DROPOFF_NUMBER.value, {}

        city = context.get("dropoff_city", "")
        street = context.get("dropoff_street", "")

        response = MessageResponse(
            f"עיר: {city} ✓\n"
            f"רחוב: {street} ✓\n"
            f"מספר: {number} ✓\n\n"
            "קומה ודירה? (או הקלידו *דלג* אם לא רלוונטי)",
            keyboard=[["דלג"]]
        )
        return response, SenderState.DROPOFF_APARTMENT.value, {"dropoff_number": number}

    async def _handle_dropoff_apartment(self, message: str, context: dict, user_id: int):
        """Collect dropoff apartment/floor (optional) and show summary"""
        msg = message.strip()

        city = context.get("dropoff_city", "")
        street = context.get("dropoff_street", "")
        number = context.get("dropoff_number", "")
        pickup = context.get("pickup_address", "לא צוין")

        # Build full address
        if msg.lower() == "דלג" or msg == "-" or msg == "0":
            full_dropoff = f"{street} {number}, {city}"
            apartment = ""
        else:
            full_dropoff = f"{street} {number}, {city} (קומה/דירה: {msg})"
            apartment = msg

        response = MessageResponse(
            f"📋 *סיכום המשלוח:*\n\n"
            f"📍 איסוף: {pickup}\n"
            f"🎯 יעד: {full_dropoff}\n\n"
            "לאשר את המשלוח?",
            keyboard=[["אישור ושליחה", "ביטול"]]
        )
        return response, SenderState.DELIVERY_CONFIRM.value, {
            "dropoff_apartment": apartment,
            "dropoff_address": full_dropoff
        }

    # ==================== Confirmation ====================

    async def _handle_confirm(self, message: str, context: dict, user_id: int):
        """Handle delivery confirmation"""
        if "אישור" in message or "כן" in message.lower():
            pickup = context.get("pickup_address", "לא צוין")
            dropoff = context.get("dropoff_address", "לא צוין")

            response = MessageResponse(
                "המשלוח נוצר בהצלחה! 🎉\n\n"
                f"📍 מ: {pickup}\n"
                f"🎯 אל: {dropoff}\n\n"
                "השליחים יקבלו התראה בקרוב.\n"
                "מה תרצו לעשות עכשיו?",
                keyboard=[["משלוח חדש", "המשלוחים שלי"]]
            )
            return response, SenderState.MENU.value, {}

        if "ביטול" in message or "לא" in message.lower():
            response = MessageResponse(
                "המשלוח בוטל.\n\n"
                "מה תרצו לעשות?",
                keyboard=[["משלוח חדש", "המשלוחים שלי"]]
            )
            return response, SenderState.MENU.value, {}

        # Invalid response
        response = MessageResponse(
            "אנא בחרו אפשרות:\n"
            "1. אישור ושליחה\n"
            "2. ביטול",
            keyboard=[["אישור ושליחה", "ביטול"]]
        )
        return response, SenderState.DELIVERY_CONFIRM.value, {}

    # ==================== Unknown State ====================

    async def _handle_unknown(self, message: str, context: dict, user_id: int):
        """Handle unknown state"""
        response = MessageResponse(
            "משהו השתבש. חוזרים לתפריט הראשי.",
            keyboard=[["משלוח חדש", "המשלוחים שלי"]]
        )
        return response, SenderState.MENU.value, {}
