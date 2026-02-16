"""
Celery Application Configuration
"""
from celery import Celery
from celery.schedules import crontab

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
    "cleanup-old-webhook-events-daily": {
        "task": "app.workers.tasks.cleanup_old_webhook_events",
        "schedule": 86400.0,  # 24 hours
    },
    # שלב 5: חסימה אוטומטית — בדיקה יומית (idempotent) לנהגים שלא שילמו חודשיים רצופים
    "process-billing-cycle-blocking-daily": {
        "task": "app.workers.tasks.process_billing_cycle_blocking",
        "schedule": 86400.0,  # 24 שעות
    },
    # בדיקת התראות — סף ארנק ומשלוחים שלא נאספו (כל 5 דקות)
    "check-station-alerts-every-5-minutes": {
        "task": "app.workers.tasks.check_station_alerts",
        "schedule": 300.0,  # 5 דקות
    },
    # סעיף 7: הפקת דוחות חודשיים אוטומטית — ב-1 לכל חודש בשעה 03:00
    "generate-monthly-reports": {
        "task": "app.workers.tasks.generate_monthly_reports",
        "schedule": crontab(day_of_month="1", hour="3", minute="0"),
    },
    # בדיקת חיבור WhatsApp Gateway — כל 3 דקות
    # מזהה session מנותק (למשל אחרי OOM restart) ושולח התראה למנהלים דרך Telegram
    "check-whatsapp-connection-every-3-minutes": {
        "task": "app.workers.tasks.check_whatsapp_connection",
        "schedule": 180.0,  # 3 דקות
    },
}
