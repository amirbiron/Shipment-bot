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
            await self.state_manager.transition_to(
                user_id, platform, new_state, context_update
            )

        return response, new_state

    def _get_handler(self, state: str):
        """Get handler function for state"""
        handlers = {
            SenderState.INITIAL.value: self._handle_initial,
            SenderState.NEW.value: self._handle_new,
            SenderState.REGISTER_COLLECT_NAME.value: self._handle_collect_name,
            SenderState.MENU.value: self._handle_menu,
            SenderState.DELIVERY_COLLECT_PICKUP.value: self._handle_collect_pickup,
            SenderState.DELIVERY_COLLECT_DROPOFF_MODE.value: self._handle_dropoff_mode,
            SenderState.DELIVERY_COLLECT_DROPOFF_ADDRESS.value: self._handle_collect_dropoff,
            SenderState.DELIVERY_CONFIRM.value: self._handle_confirm,
        }
        return handlers.get(state, self._handle_unknown)

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

    async def _handle_menu(self, message: str, context: dict, user_id: int):
        """Handle main menu"""
        if "משלוח חדש" in message or message == "1":
            response = MessageResponse(
                "בואו ניצור משלוח חדש!\n"
                "אנא הזינו את כתובת האיסוף:"
            )
            return response, SenderState.DELIVERY_COLLECT_PICKUP.value, {}

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

    async def _handle_collect_pickup(self, message: str, context: dict, user_id: int):
        """Collect pickup address"""
        address = message.strip()
        if len(address) < 5:
            response = MessageResponse("הכתובת קצרה מדי. אנא הזינו כתובת מלאה:")
            return response, SenderState.DELIVERY_COLLECT_PICKUP.value, {}

        response = MessageResponse(
            "כתובת האיסוף נשמרה.\n\n"
            "כיצד תרצו להזין את כתובת היעד?\n"
            "1. הקלדה ידנית\n"
            "2. שליחת מיקום",
            keyboard=[["הקלדה ידנית", "שליחת מיקום"]]
        )
        return response, SenderState.DELIVERY_COLLECT_DROPOFF_MODE.value, {"pickup_address": address}

    async def _handle_dropoff_mode(self, message: str, context: dict, user_id: int):
        """Handle dropoff mode selection"""
        response = MessageResponse("אנא הזינו את כתובת היעד:")
        return response, SenderState.DELIVERY_COLLECT_DROPOFF_ADDRESS.value, {}

    async def _handle_collect_dropoff(self, message: str, context: dict, user_id: int):
        """Collect dropoff address"""
        address = message.strip()
        if len(address) < 5:
            response = MessageResponse("הכתובת קצרה מדי. אנא הזינו כתובת מלאה:")
            return response, SenderState.DELIVERY_COLLECT_DROPOFF_ADDRESS.value, {}

        pickup = context.get("pickup_address", "לא צוין")
        response = MessageResponse(
            f"פרטי המשלוח:\n"
            f"📍 איסוף: {pickup}\n"
            f"🎯 יעד: {address}\n\n"
            "לאשר את המשלוח?",
            keyboard=[["אישור ושליחה", "ביטול"]]
        )
        return response, SenderState.DELIVERY_CONFIRM.value, {"dropoff_address": address}

    async def _handle_confirm(self, message: str, context: dict, user_id: int):
        """Handle delivery confirmation"""
        if "אישור" in message:
            response = MessageResponse(
                "המשלוח נוצר בהצלחה! 🎉\n"
                "השליחים יקבלו התראה בקרוב.\n\n"
                "מה תרצו לעשות עכשיו?",
                keyboard=[["משלוח חדש", "המשלוחים שלי"]]
            )
            return response, SenderState.MENU.value, {}

        response = MessageResponse(
            "המשלוח בוטל.\n\n"
            "מה תרצו לעשות?",
            keyboard=[["משלוח חדש", "המשלוחים שלי"]]
        )
        return response, SenderState.MENU.value, {}

    async def _handle_unknown(self, message: str, context: dict, user_id: int):
        """Handle unknown state"""
        response = MessageResponse(
            "משהו השתבש. חוזרים לתפריט הראשי.",
            keyboard=[["משלוח חדש", "המשלוחים שלי"]]
        )
        return response, SenderState.MENU.value, {}
