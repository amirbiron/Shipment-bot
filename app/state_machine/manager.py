"""
State Manager - Handles state transitions and context management
"""
from typing import Optional, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.models.conversation_session import ConversationSession
from app.state_machine.states import (
    SenderState,
    CourierState,
    DispatcherState,
    StationOwnerState,
    SENDER_TRANSITIONS,
    COURIER_TRANSITIONS,
    DISPATCHER_TRANSITIONS,
    STATION_OWNER_TRANSITIONS,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


class StateManager:
    """Manages conversation state transitions"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_session(
        self,
        user_id: int,
        platform: str
    ) -> ConversationSession:
        """Get existing session or create new one"""
        result = await self.db.execute(
            select(ConversationSession).where(
                ConversationSession.user_id == user_id,
                ConversationSession.platform == platform
            )
        )
        session = result.scalar_one_or_none()

        if not session:
            session = ConversationSession(
                user_id=user_id,
                platform=platform,
                current_state=SenderState.INITIAL.value
            )
            self.db.add(session)
            await self.db.commit()
            await self.db.refresh(session)

        return session

    async def get_current_state(self, user_id: int, platform: str) -> str:
        """Get current state for a user"""
        session = await self.get_or_create_session(user_id, platform)
        return session.current_state

    async def transition_to(
        self,
        user_id: int,
        platform: str,
        new_state: str,
        context_update: Optional[dict] = None
    ) -> bool:
        """
        Transition to a new state if valid.
        Returns True if transition was successful.
        """
        session = await self.get_or_create_session(user_id, platform)
        current_state = session.current_state

        # Validate transition
        if not self._is_valid_transition(current_state, new_state):
            logger.warning(
                "Invalid state transition attempted",
                extra_data={
                    "user_id": user_id,
                    "platform": platform,
                    "current_state": current_state,
                    "target_state": new_state
                }
            )
            return False

        # Update state
        session.current_state = new_state

        # Update context if provided - create NEW dict to trigger SQLAlchemy change detection
        if context_update:
            current_context = dict(session.context_data or {})  # Create copy!
            current_context.update(context_update)
            session.context_data = current_context

        await self.db.commit()
        return True

    async def force_state(
        self,
        user_id: int,
        platform: str,
        new_state: str,
        context: Optional[dict] = None
    ) -> None:
        """Force state change without validation (for admin/reset)"""
        session = await self.get_or_create_session(user_id, platform)
        session.current_state = new_state
        if context is not None:
            session.context_data = context
        await self.db.commit()

    async def get_context(self, user_id: int, platform: str) -> dict:
        """Get context data for current session"""
        session = await self.get_or_create_session(user_id, platform)
        return session.context_data or {}

    async def update_context(
        self,
        user_id: int,
        platform: str,
        key: str,
        value: Any
    ) -> None:
        """Update a single context key"""
        session = await self.get_or_create_session(user_id, platform)
        context = dict(session.context_data or {})  # Create copy!
        context[key] = value
        session.context_data = context
        await self.db.commit()

    async def clear_context(self, user_id: int, platform: str) -> None:
        """Clear all context data"""
        session = await self.get_or_create_session(user_id, platform)
        session.context_data = {}
        await self.db.commit()

    def _is_valid_transition(self, current: str, target: str) -> bool:
        """Check if transition from current to target state is valid"""
        # רשימת כל סוגי ה-states והמעברים שלהם
        state_maps = [
            (SenderState, SENDER_TRANSITIONS),
            (CourierState, COURIER_TRANSITIONS),
            (DispatcherState, DISPATCHER_TRANSITIONS),
            (StationOwnerState, STATION_OWNER_TRANSITIONS),
        ]

        for state_enum, transitions in state_maps:
            try:
                current_state = state_enum(current)
                target_state = state_enum(target)
                if current_state in transitions:
                    return target_state in transitions[current_state]
            except ValueError:
                continue

        return False
