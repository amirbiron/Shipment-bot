from datetime import datetime, timedelta

import pytest

from app.core.config import settings
from app.db.models.outbox_message import MessagePlatform, MessageStatus, OutboxMessage
from app.domain.services.outbox_service import OutboxService, _calculate_backoff_seconds


def test_calculate_backoff_seconds_matches_previous_semantics() -> None:
    # Previous logic was: base_seconds * (2 ** retry_count)
    base = 30
    max_backoff = 3600

    assert _calculate_backoff_seconds(0, base_seconds=base, max_backoff_seconds=max_backoff) == 30
    assert _calculate_backoff_seconds(1, base_seconds=base, max_backoff_seconds=max_backoff) == 60
    assert _calculate_backoff_seconds(6, base_seconds=base, max_backoff_seconds=max_backoff) == 1920


def test_calculate_backoff_seconds_is_capped() -> None:
    base = 30
    max_backoff = 3600

    # 30 * 2**7 = 3840 -> capped to 3600
    assert _calculate_backoff_seconds(7, base_seconds=base, max_backoff_seconds=max_backoff) == 3600
    assert _calculate_backoff_seconds(10_000, base_seconds=base, max_backoff_seconds=max_backoff) == 3600


@pytest.mark.asyncio
async def test_mark_as_failed_sets_next_retry_at_with_cap(db_session) -> None:
    # Set an intentionally huge retry_count to ensure we don't compute 2**retry_count.
    msg = OutboxMessage(
        platform=MessagePlatform.WHATSAPP,
        recipient_id="test",
        message_type="test",
        message_content={"hello": "world"},
        status=MessageStatus.PENDING,
        retry_count=10_000,
        max_retries=20_000,
    )
    db_session.add(msg)
    await db_session.commit()
    await db_session.refresh(msg)

    svc = OutboxService(db_session)
    before = datetime.utcnow()
    await svc.mark_as_failed(msg.id, "boom")
    after = datetime.utcnow()

    await db_session.refresh(msg)
    assert msg.status == MessageStatus.PENDING
    assert msg.next_retry_at is not None

    max_backoff = settings.OUTBOX_MAX_BACKOFF_SECONDS
    lower = before + timedelta(seconds=max_backoff) - timedelta(seconds=2)
    upper = after + timedelta(seconds=max_backoff) + timedelta(seconds=2)
    assert lower <= msg.next_retry_at <= upper

