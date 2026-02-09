import pytest
from fastapi import BackgroundTasks

from app.api.webhooks.telegram import (
    TelegramUpdate,
    _is_in_multi_step_flow,
    _parse_inbound_event,
)
from app.db.models.user import User, UserRole
from app.state_machine.states import CourierState


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
    def test_is_in_multi_step_flow_courier_registration(self):
        courier = User(phone_number="tg:unit1", platform="telegram", role=UserRole.COURIER)
        assert _is_in_multi_step_flow(courier, CourierState.REGISTER_COLLECT_NAME.value) is True

    @pytest.mark.unit
    def test_is_in_multi_step_flow_dispatcher_prefix(self):
        sender = User(phone_number="tg:unit2", platform="telegram", role=UserRole.SENDER)
        assert _is_in_multi_step_flow(sender, "DISPATCHER.ADD_SHIPMENT_PICKUP_CITY") is True

    @pytest.mark.unit
    def test_is_in_multi_step_flow_station_prefix(self):
        owner = User(phone_number="tg:unit3", platform="telegram", role=UserRole.STATION_OWNER)
        assert _is_in_multi_step_flow(owner, "STATION.ADD_BLACKLIST_REASON") is True

    @pytest.mark.unit
    def test_is_in_multi_step_flow_default_false(self):
        sender = User(phone_number="tg:unit4", platform="telegram", role=UserRole.SENDER)
        assert _is_in_multi_step_flow(sender, None) is False
