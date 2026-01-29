"""
Celery Application Configuration
"""
from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "shipment_bot",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"]
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jerusalem",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "process-outbox-every-10-seconds": {
        "task": "app.workers.tasks.process_outbox_messages",
        "schedule": 10.0,
    },
    "cleanup-old-messages-daily": {
        "task": "app.workers.tasks.cleanup_old_messages",
        "schedule": 86400.0,  # 24 hours
    },
}
