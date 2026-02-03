"""
State Handlers - Process messages based on current state
"""
from typing import Tuple, Optional
from html import escape
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.state_machine.states import SenderState, CourierState
from app.state_machine.manager import StateManager
from app.db.models.user import User
from app.core.logging import get_logger

logger = get_logger(__name__)


class MessageResponse:
    """Response to be sent to user"""

    def __init__(self, text: str, keyboard: Optional[list] = None, inline: bool = False):
        self.text = text
        self.keyboard = keyboard
        self.inline = inline


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
                logger.info(
                    "Forcing state transition",
                    extra_data={
                        "user_id": user_id,
                        "platform": platform,
                        "current_state": current_state,
                        "new_state": new_state
                    }
                )
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

        safe_name = escape(name)
        response = MessageResponse(
            f"שלום {safe_name}! ההרשמה הושלמה בהצלחה.\n\n"
            "מה תרצו לעשות?\n"
            "1. יצירת משלוח חדש\n"
            "2. צפייה במשלוחים שלי",
            keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            inline=True
        )
        return response, SenderState.MENU.value, {"name": name}

    # ==================== Main Menu ====================

    async def _handle_menu(self, message: str, context: dict, user_id: int):
        """Handle main menu"""
        msg = message.strip()
        # הצגת תפריט (למשל לאחר /start או חזרה)
        if msg in {"תפריט", "/start"}:
            response = MessageResponse(
                "מה תרצו לעשות?\n"
                "1. יצירת משלוח חדש\n"
                "2. צפייה במשלוחים שלי",
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
                inline=True
            )
            return response, SenderState.MENU.value, {}

        if "משלוח חדש" in message or "➕" in message or message == "1":
            response = MessageResponse(
                "בואו ניצור משלוח חדש!\n\n"
                "📍 <b>כתובת איסוף</b>\n"
                "מה העיר?"
            )
            return response, SenderState.PICKUP_CITY.value, {}

        elif "משלוחים" in message or "📦" in message or message == "2":
            response = MessageResponse(
                "המשלוחים שלך:\n(אין משלוחים עדיין)\n\n"
                "חזרה לתפריט:",
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
                inline=True
            )
            return response, SenderState.MENU.value, {}

        response = MessageResponse(
            "לא הבנתי. אנא בחרו אפשרות:\n"
            "1. משלוח חדש\n"
            "2. המשלוחים שלי",
            keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            inline=True
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
        response = MessageResponse(
            f"עיר: {safe_city} ✓\n\n"
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
        safe_city = escape(city)
        safe_street = escape(street)
        response = MessageResponse(
            f"עיר: {safe_city} ✓\n"
            f"רחוב: {safe_street} ✓\n\n"
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
        safe_city = escape(city)
        safe_street = escape(street)
        safe_number = escape(number)

        response = MessageResponse(
            f"עיר: {safe_city} ✓\n"
            f"רחוב: {safe_street} ✓\n"
            f"מספר: {safe_number} ✓\n\n"
            "קומה ודירה? (או לחצו <b>דלג</b> אם לא רלוונטי)",
            keyboard=[["דלג"]],
            inline=True
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
            inline=True
        )
        return response, SenderState.DELIVERY_LOCATION.value, {
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

        safe_city = escape(city)
        response = MessageResponse(
            f"עיר: {safe_city} ✓\n\n"
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
        safe_city = escape(city)
        safe_street = escape(street)
        response = MessageResponse(
            f"עיר: {safe_city} ✓\n"
            f"רחוב: {safe_street} ✓\n\n"
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
        safe_city = escape(city)
        safe_street = escape(street)
        safe_number = escape(number)

        response = MessageResponse(
            f"עיר: {safe_city} ✓\n"
            f"רחוב: {safe_street} ✓\n"
            f"מספר: {safe_number} ✓\n\n"
            "קומה ודירה? (או לחצו <b>דלג</b> אם לא רלוונטי)",
            keyboard=[["דלג"]],
            inline=True
        )
        return response, SenderState.DROPOFF_APARTMENT.value, {"dropoff_number": number}

    async def _handle_dropoff_apartment(self, message: str, context: dict, user_id: int):
        """Collect dropoff apartment/floor (optional) and ask about urgency"""
        msg = message.strip()

        city = context.get("dropoff_city", "")
        street = context.get("dropoff_street", "")
        number = context.get("dropoff_number", "")
        pickup_city = context.get("pickup_city", "")

        # Build full address
        if msg.lower() == "דלג" or msg == "-" or msg == "0":
            full_dropoff = f"{street} {number}, {city}"
            apartment = ""
        else:
            full_dropoff = f"{street} {number}, {city} (קומה/דירה: {msg})"
            apartment = msg

        # Check if same city or different city
        same_city = pickup_city.strip().lower() == city.strip().lower()

        # לאחר כתובת יעד - עוברים לשאלת הדחיפות
        safe_full_dropoff = escape(full_dropoff)
        response = MessageResponse(
            f"🎯 כתובת יעד נשמרה:\n{safe_full_dropoff}\n\n"
            "האם המשלוח דחוף?",
            keyboard=[["🚀 מיידי", "☕ בנחת"]],
            inline=True
        )
        return response, SenderState.DELIVERY_URGENCY.value, {
            "dropoff_apartment": apartment,
            "dropoff_address": full_dropoff,
            "same_city": same_city
        }

    # ==================== Delivery Details ====================

    async def _handle_delivery_location(self, message: str, context: dict, user_id: int):
        """Handle delivery location selection (within/outside city)"""
        msg = message.strip()

        # לוג לדיבוג - מה בדיוק התקבל מהמשתמש
        logger.debug(
            "Handling delivery location input",
            extra_data={"user_id": user_id, "raw_input": repr(msg), "input_length": len(msg)}
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
                extra_data={"user_id": user_id, "raw_input": repr(msg)}
            )
            response = MessageResponse(
                "אנא בחרו אפשרות:\n"
                "1. בתוך העיר\n"
                "2. מחוץ לעיר",
                keyboard=[["🏙️ בתוך העיר", "🚗 מחוץ לעיר"]],
                inline=True
            )
            return response, SenderState.DELIVERY_LOCATION.value, {}

        # לאחר בחירת סוג משלוח - עוברים לכתובת יעד
        response = MessageResponse(
            f"סוג משלוח: {location_text} ✓\n\n"
            "עכשיו נזין את כתובת היעד.\n"
            "🎯 <b>כתובת יעד</b>\n"
            "מה העיר?"
        )
        return response, SenderState.DROPOFF_CITY.value, {"delivery_location": location_type}

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
            return response, SenderState.DELIVERY_DESCRIPTION.value, {
                "urgency": "immediate",
                "delivery_time": "מיידי"
            }

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
            inline=True
        )
        return response, SenderState.DELIVERY_URGENCY.value, {}

    async def _handle_delivery_time(self, message: str, context: dict, user_id: int):
        """Handle delivery time input (HH:MM format) - only for 'later' urgency"""
        import re
        msg = message.strip()

        # Validate time format HH:MM
        time_pattern = re.compile(r'^([01]?[0-9]|2[0-3]):([0-5][0-9])$')
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
        return response, SenderState.DELIVERY_PRICE.value, {"delivery_time": msg, "min_price": min_price}

    async def _handle_delivery_price(self, message: str, context: dict, user_id: int):
        """Handle customer price input - only for 'later' urgency"""
        msg = message.strip()

        # Extract number from message
        import re
        numbers = re.findall(r'\d+', msg)
        if not numbers:
            min_price = context.get("min_price", 25)
            response = MessageResponse(
                f"❌ אנא הזינו סכום תקין (מספר בלבד).\n"
                f"מינימום: {min_price} ₪"
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
        return response, SenderState.DELIVERY_DESCRIPTION.value, {"customer_price": price}

    async def _handle_delivery_description(self, message: str, context: dict, user_id: int):
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
            summary += f"💰 מחיר מוצע: {customer_price} ₪\n"

        summary += f"📦 תיאור: {safe_description}\n\n"
        summary += "לאשר את המשלוח?"

        response = MessageResponse(
            summary,
            keyboard=[["✅ אישור ושליחה", "❌ ביטול"]],
            inline=True
        )
        return response, SenderState.DELIVERY_CONFIRM.value, {"description": description}

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
                success_msg += f"💰 מחיר: {customer_price} ₪\n"

            success_msg += (
                "\nהשליחים יקבלו התראה בקרוב.\n"
                "מה תרצו לעשות עכשיו?"
            )

            response = MessageResponse(
                success_msg,
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
                inline=True
            )
            return response, SenderState.MENU.value, {}

        if "ביטול" in message or "❌" in message or "לא" in message.lower():
            response = MessageResponse(
                "המשלוח בוטל.\n\n"
                "מה תרצו לעשות?",
                keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
                inline=True
            )
            return response, SenderState.MENU.value, {}

        # Invalid response
        response = MessageResponse(
            "אנא בחרו אפשרות:\n"
            "1. ✅ אישור ושליחה\n"
            "2. ❌ ביטול",
            keyboard=[["✅ אישור ושליחה", "❌ ביטול"]],
            inline=True
        )
        return response, SenderState.DELIVERY_CONFIRM.value, {}

    # ==================== Unknown State ====================

    async def _handle_unknown(self, message: str, context: dict, user_id: int):
        """Handle unknown state"""
        response = MessageResponse(
            "משהו השתבש. חוזרים לתפריט הראשי.",
            keyboard=[["📦 המשלוחים שלי"], ["➕ משלוח חדש"]],
            inline=True
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

    async def handle_message(
        self,
        user: User,
        message: str,
        photo_file_id: str = None
    ) -> Tuple[MessageResponse, str]:
        """Process incoming message for courier and return response with new state"""
        platform = self.platform or user.platform
        current_state = await self.state_manager.get_current_state(user.id, platform)
        context = await self.state_manager.get_context(user.id, platform)

        handler = self._get_handler(current_state)
        response, new_state, context_update = await handler(user, message, context, photo_file_id)

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
        """Get handler function for state"""
        handlers = {
            CourierState.INITIAL.value: self._handle_initial,
            CourierState.NEW.value: self._handle_initial,
            CourierState.REGISTER_COLLECT_NAME.value: self._handle_collect_name,
            CourierState.REGISTER_COLLECT_DOCUMENT.value: self._handle_collect_document,
            CourierState.REGISTER_COLLECT_AREA.value: self._handle_collect_area,
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
        }
        return handlers.get(state, self._handle_unknown)

    # ==================== Registration Flow [1.2] ====================

    async def _handle_initial(self, user: User, message: str, context: dict, photo_file_id: str):
        """Welcome message and start registration"""
        response = MessageResponse(
            "ברוכים הבאים למערכת משלוח בצ'יק! 🚚\n\n"
            "כדי להתחיל לקחת משלוחים, עלינו להכיר אותך.\n\n"
            "<b>שלב א' - שם מלא:</b>\n"
            "אנא הזן את שמך המלא כפי שמופיע בתעודת הזהות."
        )
        return response, CourierState.REGISTER_COLLECT_NAME.value, {}

    async def _handle_collect_name(self, user: User, message: str, context: dict, photo_file_id: str):
        """Collect full name - Step a"""
        name = message.strip()
        if len(name) < 2:
            response = MessageResponse("השם שהוזן קצר מדי. אנא הזן את שמך המלא (לפחות 2 תווים).")
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

    async def _handle_collect_document(self, user: User, message: str, context: dict, photo_file_id: str):
        """Collect ID document - Step b"""
        if not photo_file_id:
            response = MessageResponse(
                "לא התקבלה תמונה. אנא שלח תמונה של תעודת זהות או רישיון נהיגה."
            )
            return response, CourierState.REGISTER_COLLECT_DOCUMENT.value, {}

        response = MessageResponse(
            "המסמך התקבל בהצלחה!\n\n"
            "<b>שלב ג' - התמחות גיאוגרפית:</b>\n"
            "באיזו עיר או אזור אתה מתמקד בעיקר?\n\n"
            "לדוגמה: בני ברק, ירושלים, אזור המרכז, גוש דן"
        )
        return response, CourierState.REGISTER_COLLECT_AREA.value, {"document_file_id": photo_file_id}

    async def _handle_collect_area(self, user: User, message: str, context: dict, photo_file_id: str):
        """Collect service area - Step c"""
        area = message.strip()
        if len(area) < 2:
            response = MessageResponse("אנא הזן אזור תקין (לפחות 2 תווים).")
            return response, CourierState.REGISTER_COLLECT_AREA.value, {}

        user.service_area = area
        await self.db.commit()

        response = MessageResponse(
            self.TERMS_TEXT,
            keyboard=[["קראתי ואני מאשר ✅"]]
        )
        return response, CourierState.REGISTER_TERMS.value, {}

    async def _handle_terms(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle terms acceptance [1.3]"""
        from datetime import datetime
        from app.db.models.user import ApprovalStatus, UserRole

        if "מאשר" not in message and "אישור" not in message:
            response = MessageResponse(
                "כדי להמשיך, עליך ללחוץ על הכפתור 'קראתי ואני מאשר'.",
                keyboard=[["קראתי ואני מאשר ✅"]]
            )
            return response, CourierState.REGISTER_TERMS.value, {}

        # Update user status
        user.terms_accepted_at = datetime.utcnow()
        user.role = UserRole.COURIER
        user.approval_status = ApprovalStatus.PENDING

        # Save document URL from context
        if context.get("document_file_id"):
            user.id_document_url = context["document_file_id"]

        await self.db.commit()

        response = MessageResponse(
            "<b>הרישום הושלם בהצלחה!</b>\n\n"
            "פרטיך הועברו לבדיקת הנהלה.\n"
            "תקבל הודעה ברגע שחשבונך יאושר.\n\n"
            "⏳ בדרך כלל האישור מתבצע תוך 24 שעות."
        )
        return response, CourierState.PENDING_APPROVAL.value, {}

    async def _handle_pending_approval(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle messages while pending approval [1.4]"""
        from app.db.models.user import ApprovalStatus

        await self.db.refresh(user)

        # בדיקת סטטוס חסימה/דחייה קודם - למניעת עקיפת החסימה דרך הרשמה מחדש
        if user.approval_status == ApprovalStatus.BLOCKED:
            response = MessageResponse(
                "❌ חשבונך נחסם. לפרטים נוספים, פנה להנהלה.\n\n"
                "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
            )
            return response, CourierState.PENDING_APPROVAL.value, {}

        if user.approval_status == ApprovalStatus.REJECTED:
            response = MessageResponse(
                "לצערנו, בקשתך להצטרף כשליח נדחתה. לפרטים נוספים, פנה להנהלה.\n\n"
                "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
            )
            return response, CourierState.PENDING_APPROVAL.value, {}

        # בדיקה: אם המשתמש לא סיים את הרישום - מחזירים אותו להתחלה
        # (רק אם הוא לא חסום/נדחה)
        if user.terms_accepted_at is None:
            logger.info(
                "User in pending_approval but didn't complete registration, restarting",
                extra_data={"user_id": user.id}
            )
            return await self._handle_initial(user, message, context, photo_file_id)

        if user.approval_status == ApprovalStatus.APPROVED:
            return await self._handle_menu(user, message, context, photo_file_id)

        response = MessageResponse(
            "⏳ בקשתך עדיין בבדיקה. תקבל הודעה ברגע שחשבונך יאושר.\n\n"
            "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
        )
        return response, CourierState.PENDING_APPROVAL.value, {}

    # ==================== Main Menu [4] ====================

    async def _handle_menu(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle main menu display and navigation"""
        from app.db.models.user import ApprovalStatus

        if user.approval_status != ApprovalStatus.APPROVED:
            return await self._handle_pending_approval(user, message, context, photo_file_id)

        # Navigation by button text
        if "ארנק" in message or "יתרה" in message:
            return await self._handle_view_wallet(user, message, context, photo_file_id)
        if "אזור" in message or "הגדרות" in message:
            return await self._handle_change_area(user, message, context, photo_file_id)
        if "היסטוריה" in message or "עבודות" in message:
            return await self._handle_view_history(user, message, context, photo_file_id)
        if "תמיכה" in message or "עזרה" in message:
            return await self._handle_support(user, message, context, photo_file_id)
        if "הפקדה" in message or "טעינה" in message:
            return await self._handle_deposit_request(user, message, context, photo_file_id)
        if "משלוח פעיל" in message or "משלוח נוכחי" in message:
            return await self._handle_view_active(user, message, context, photo_file_id)

        # Default menu display
        response = MessageResponse(
            f"📋 <b>תפריט שליח</b>\n\n"
            f"שלום {user.name}! 👋\n\n"
            f"💰 <b>מצב הארנק:</b> 0.00 ₪\n"
            f"📍 <b>האזור שלך:</b> {user.service_area or 'לא הוגדר'}\n\n"
            "בחר פעולה:",
            keyboard=[
                ["💰 מצב הארנק", "📍 הגדרות אזור"],
                ["📦 היסטוריית עבודות", "📦 משלוח פעיל"],
                ["💳 הפקדה", "❓ תמיכה"],
            ]
        )
        return response, CourierState.MENU.value, {}

    # ==================== Wallet Module [3] ====================

    async def _handle_view_wallet(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle wallet view [3.1]"""
        from app.core.config import settings

        response = MessageResponse(
            "💰 <b>פרטי הארנק</b>\n\n"
            "🟢 סטטוס: פעיל\n\n"
            "💵 יתרה נוכחית: <b>0.00 ₪</b>\n"
            f"📊 מסגרת אשראי: {settings.DEFAULT_CREDIT_LIMIT:.2f} ₪\n"
            f"🎯 נותר עד לחסימה: {-settings.DEFAULT_CREDIT_LIMIT:.2f} ₪\n\n"
            "לטעינת הארנק, לחץ על 'הפקדה'.",
            keyboard=[["💳 הפקדה"], ["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.VIEW_WALLET.value, {}

    async def _handle_deposit_request(self, user: User, message: str, context: dict, photo_file_id: str):
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
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.DEPOSIT_UPLOAD.value, {}

    async def _handle_deposit_upload(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle deposit screenshot upload"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        if not photo_file_id:
            response = MessageResponse(
                "📸 אנא שלח צילום מסך של אישור ההעברה, או לחץ 'חזרה לתפריט'.",
                keyboard=[["🔙 חזרה לתפריט"]]
            )
            return response, CourierState.DEPOSIT_UPLOAD.value, {}

        response = MessageResponse(
            "<b>בקשת ההפקדה התקבלה!</b>\n\n"
            "הבקשה הועברה למנהל לאישור.\n"
            "היתרה תתעדכן לאחר אישור ההפקדה.\n\n"
            "⏳ זמן טיפול: עד 24 שעות.",
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.MENU.value, {"deposit_screenshot": photo_file_id}

    # ==================== Settings ====================

    async def _handle_change_area(self, user: User, message: str, context: dict, photo_file_id: str):
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
                    keyboard=[["🔙 חזרה לתפריט"]]
                )
                return response, CourierState.MENU.value, {"changing_area": False}

        response = MessageResponse(
            f"📍 <b>הגדרות אזור</b>\n\n"
            f"האזור הנוכחי שלך: <b>{user.service_area or 'לא הוגדר'}</b>\n\n"
            "לשינוי האזור, הקלד את האזור החדש.",
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.CHANGE_AREA.value, {"changing_area": True}

    async def _handle_view_history(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle work history view"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "📦 <b>היסטוריית עבודות</b>\n\n"
            "אין משלוחים בהיסטוריה עדיין.\n"
            "התחל לקחת משלוחים כדי לראות את ההיסטוריה שלך!",
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.VIEW_HISTORY.value, {}

    async def _handle_view_active(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle viewing active delivery"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "📦 אין לך משלוח פעיל כרגע.\nתפוס משלוח חדש מהקבוצה!",
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.MENU.value, {}

    async def _handle_support(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle support requests"""
        if "חזרה" in message or "תפריט" in message:
            return await self._handle_menu(user, "תפריט", context, None)

        response = MessageResponse(
            "❓ <b>תמיכה</b>\n\n"
            "לתמיכה טכנית או שאלות:\n\n"
            "📧 שלח הודעה למנהל - פשוט כתוב את ההודעה כאן והיא תועבר.\n\n"
            "📞 מוקד: 050-1234567\n"
            "שעות פעילות: א'-ה' 08:00-20:00",
            keyboard=[["🔙 חזרה לתפריט"]]
        )
        return response, CourierState.SUPPORT.value, {}

    async def _handle_unknown(self, user: User, message: str, context: dict, photo_file_id: str):
        """Handle unknown state - restart registration or show appropriate screen"""
        from app.db.models.user import ApprovalStatus

        # אם השליח מאושר - מציגים תפריט
        if user.approval_status == ApprovalStatus.APPROVED:
            return await self._handle_menu(user, message, context, photo_file_id)

        # אם השליח נחסם או נדחה - מציגים הודעה מתאימה ולא מאפשרים רישום מחדש
        if user.approval_status == ApprovalStatus.BLOCKED:
            response = MessageResponse(
                "❌ חשבונך נחסם. לפרטים נוספים, פנה להנהלה.\n\n"
                "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
            )
            return response, CourierState.PENDING_APPROVAL.value, {}

        if user.approval_status == ApprovalStatus.REJECTED:
            response = MessageResponse(
                "לצערנו, בקשתך להצטרף כשליח נדחתה. לפרטים נוספים, פנה להנהלה.\n\n"
                "💡 לחזרה לתפריט הראשי (כשולח חבילות) לחצו על #"
            )
            return response, CourierState.PENDING_APPROVAL.value, {}

        # אם השליח סיים את הרישום (יש לו תאריך אישור תקנון) - הוא ממתין לאישור
        if user.terms_accepted_at is not None:
            return await self._handle_pending_approval(user, message, context, photo_file_id)

        # אחרת - המשתמש לא סיים את הרישום, מתחילים מחדש
        logger.info(
            "Courier in unknown state without completing registration, restarting",
            extra_data={"user_id": user.id}
        )
        return await self._handle_initial(user, message, context, photo_file_id)
