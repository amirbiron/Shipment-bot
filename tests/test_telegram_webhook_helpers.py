import pytest
from fastapi import BackgroundTasks

from app.api.webhooks.telegram import (
    TelegramUpdate,
    _queue_response_send,
    _SENDER_BUTTON_ROUTES,
    _get_station_for_owner_or_downgrade,
    _handle_sender_join_as_courier,
    _is_in_multi_step_flow,
    _parse_inbound_event,
    _store_inline_button_mapping,
    _resolve_inline_button_mapping,
    _truncate_utf8,
    telegram_webhook,
)
from app.db.models.user import User, UserRole
from app.db.models.station import Station
from app.state_machine.manager import StateManager
from app.state_machine.handlers import MessageResponse
from app.state_machine.states import CourierState, SenderState


class TestTelegramWebhookHelpers:
    @pytest.mark.unit
    def test_parse_inbound_event_callback_without_from_user_skips_but_answers(self):
        background_tasks = BackgroundTasks()
        update = TelegramUpdate(
            update_id=1,
            callback_query={
                "id": "cb-no-from",
                "data": "תפריט",
                # intentionally missing "from"
                "message": None,
            },
        )

        event = _parse_inbound_event(update, background_tasks)
        assert event is None
        assert len(background_tasks.tasks) == 1
        assert background_tasks.tasks[0].func.__name__ == "answer_callback_query"

    @pytest.mark.unit
    def test_parse_inbound_event_private_chat_prefers_chat_id_over_from_id(self):
        background_tasks = BackgroundTasks()
        update = TelegramUpdate(
            update_id=2,
            message={
                "message_id": 10,
                "chat": {"id": 12345, "type": "private"},
                "text": "תפריט",
                "date": 1700000000,
                "from": {"id": 999, "first_name": "Smoke"},
            },
        )
        event = _parse_inbound_event(update, background_tasks)
        assert event is not None
        assert event.send_chat_id == "12345"
        assert event.telegram_user_id == "12345"

    @pytest.mark.unit
    def test_parse_inbound_event_photo_uses_largest_size(self):
        background_tasks = BackgroundTasks()
        update = TelegramUpdate(
            update_id=3,
            message={
                "message_id": 11,
                "chat": {"id": 12345, "type": "private"},
                "text": "",
                "date": 1700000000,
                "from": {"id": 12345, "first_name": "Photo"},
                "photo": [
                    {
                        "file_id": "small",
                        "file_unique_id": "u1",
                        "width": 10,
                        "height": 10,
                    },
                    {
                        "file_id": "large",
                        "file_unique_id": "u2",
                        "width": 100,
                        "height": 100,
                    },
                ],
            },
        )
        event = _parse_inbound_event(update, background_tasks)
        assert event is not None
        assert event.photo_file_id == "large"

    @pytest.mark.unit
    def test_is_in_multi_step_flow_courier_registration(self):
        courier = User(
            phone_number="tg:unit1", platform="telegram", role=UserRole.COURIER
        )
        assert (
            _is_in_multi_step_flow(courier, CourierState.REGISTER_COLLECT_NAME.value)
            is True
        )

    @pytest.mark.unit
    def test_is_in_multi_step_flow_sender_prefix_not_menu(self):
        sender = User(
            phone_number="tg:unit_sender", platform="telegram", role=UserRole.SENDER
        )
        assert _is_in_multi_step_flow(sender, SenderState.PICKUP_CITY.value) is True
        assert _is_in_multi_step_flow(sender, SenderState.MENU.value) is False

    @pytest.mark.unit
    def test_is_in_multi_step_flow_dispatcher_prefix(self):
        sender = User(
            phone_number="tg:unit2", platform="telegram", role=UserRole.SENDER
        )
        assert (
            _is_in_multi_step_flow(sender, "DISPATCHER.ADD_SHIPMENT_PICKUP_CITY")
            is True
        )

    @pytest.mark.unit
    def test_is_in_multi_step_flow_station_prefix(self):
        owner = User(
            phone_number="tg:unit3", platform="telegram", role=UserRole.STATION_OWNER
        )
        assert _is_in_multi_step_flow(owner, "STATION.ADD_BLACKLIST_REASON") is True

    @pytest.mark.unit
    def test_is_in_multi_step_flow_default_false(self):
        sender = User(
            phone_number="tg:unit4", platform="telegram", role=UserRole.SENDER
        )
        assert _is_in_multi_step_flow(sender, None) is False

    @pytest.mark.unit
    def test_queue_response_send_adds_background_task(self):
        background_tasks = BackgroundTasks()
        response = MessageResponse("hello", keyboard=[["a"]], inline=True)
        _queue_response_send(background_tasks, "123", response)
        assert len(background_tasks.tasks) == 1
        task = background_tasks.tasks[0]
        assert task.func.__name__ == "send_telegram_message"
        assert task.args[:2] == ("123", "hello")

    @pytest.mark.unit
    def test_sender_button_routes_order_prefers_specific_keyword(self):
        text = "הצטרפות למנוי כשליח"
        first_match = None
        for keyword, _handler in _SENDER_BUTTON_ROUTES:
            if keyword in text:
                first_match = keyword
                break
        assert first_match == "הצטרפות למנוי"

    @pytest.mark.unit
    def test_truncate_utf8_never_exceeds_max_bytes(self):
        # מחרוזת עם עברית + אימוג'י (utf-8 multi byte)
        s = "🚚 הצטרפות למנוי וקבלת משלוחים"
        out = _truncate_utf8(s, 64)
        assert isinstance(out, str)
        assert len(out.encode("utf-8")) <= 64

    @pytest.mark.asyncio
    async def test_inline_button_mapping_store_and_resolve(self, monkeypatch):
        class _FakeRedis:
            def __init__(self):
                self._store = {}

            async def setex(self, key, ttl, value):
                self._store[key] = value

            async def get(self, key):
                return self._store.get(key)

        fake = _FakeRedis()

        async def _fake_get_redis():
            return fake

        monkeypatch.setattr("app.core.redis_client.get_redis", _fake_get_redis)

        chat_id = "123"
        cb = "btn:unit"
        text = "כפתור ארוך מאוד מאוד מאוד כדי לבדוק מיפוי"
        ok = await _store_inline_button_mapping(chat_id, cb, text)
        assert ok is True

        resolved = await _resolve_inline_button_mapping(chat_id, cb)
        assert resolved == text

    @pytest.mark.asyncio
    async def test_webhook_btn_callback_missing_mapping_sends_expired_message_and_returns(
        self, db_session, monkeypatch
    ):
        # Redis מחזיר None (מיפוי פג תוקף / לא קיים)
        class _FakeRedis:
            async def get(self, key):
                return None

        async def _fake_get_redis():
            return _FakeRedis()

        monkeypatch.setattr("app.core.redis_client.get_redis", _fake_get_redis)

        update = TelegramUpdate(
            update_id=100,
            callback_query={
                "id": "cb-expired",
                "data": "btn:expired",
                "from": {"id": 123, "first_name": "Unit"},
                "message": {
                    "message_id": 1,
                    "chat": {"id": 123, "type": "private"},
                    "date": 1700000000,
                    "text": "",
                },
            },
        )
        background_tasks = BackgroundTasks()

        result = await telegram_webhook(
            update=update,
            background_tasks=background_tasks,
            db=db_session,
            _=None,
        )

        assert result["ok"] is True
        assert result["expired_inline_button"] is True
        # אמור להישלח גם answer_callback_query וגם הודעת "פג תוקף"
        funcs = [t.func.__name__ for t in background_tasks.tasks]
        assert "answer_callback_query" in funcs
        assert "send_telegram_message" in funcs

    @pytest.mark.asyncio
    async def test_get_station_for_owner_or_downgrade_downgrades_when_missing(
        self, db_session, user_factory
    ):
        owner = await user_factory(
            phone_number="+972501199001",
            name="Owner No Station",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="99001",
        )
        station = await _get_station_for_owner_or_downgrade(owner, db_session)
        assert station is None
        await db_session.refresh(owner)
        assert owner.role == UserRole.SENDER

    @pytest.mark.asyncio
    async def test_get_station_for_owner_or_downgrade_returns_station_when_exists(
        self, db_session, user_factory
    ):
        owner = await user_factory(
            phone_number="+972501199002",
            name="Owner Has Station",
            role=UserRole.STATION_OWNER,
            platform="telegram",
            telegram_chat_id="99002",
        )
        station_obj = Station(name="תחנת Unit", owner_id=owner.id)
        db_session.add(station_obj)
        await db_session.commit()

        station = await _get_station_for_owner_or_downgrade(owner, db_session)
        assert station is not None
        assert station.owner_id == owner.id
        await db_session.refresh(owner)
        assert owner.role == UserRole.STATION_OWNER

    @pytest.mark.asyncio
    async def test_handle_sender_join_as_courier_changes_role_and_state(
        self, db_session, user_factory
    ):
        sender = await user_factory(
            phone_number="+972501199003",
            name="Join Courier",
            role=UserRole.SENDER,
            platform="telegram",
            telegram_chat_id="99003",
        )
        state_manager = StateManager(db_session)
        response, new_state = await _handle_sender_join_as_courier(
            sender, db_session, state_manager, "הצטרפות למנוי", None
        )
        assert response.text
        assert new_state == CourierState.REGISTER_COLLECT_NAME.value
        await db_session.refresh(sender)
        assert sender.role == UserRole.COURIER
