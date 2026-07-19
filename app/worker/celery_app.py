from __future__ import annotations

from celery import Celery
from celery.signals import worker_init, worker_shutdown

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "video_downloader",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,

    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,

    task_time_limit=settings.max_download_duration_seconds,
    task_soft_time_limit=max(60, settings.max_download_duration_seconds - 60),

    task_routes={
        "app.worker.tasks.process_download_request": {"queue": "downloads"},
    },

    beat_schedule={
        "cleanup-stale-downloads": {
            "task": "app.worker.tasks.cleanup_stale_downloads",
            "schedule": 3600.0,  # hourly
        },
    },
)


@worker_init.connect
def on_worker_init(**kwargs) -> None:
    from app.core.logging import setup_logging
    setup_logging(get_settings().log_dir)


@worker_shutdown.connect
def on_worker_shutdown(**kwargs) -> None:
    from app.worker.telegram_sender import close_bot_session
    close_bot_session()
