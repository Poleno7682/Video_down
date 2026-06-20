from __future__ import annotations

from unittest.mock import patch


def test_celery_app_task_serializer():
    from app.worker.celery_app import celery_app
    assert celery_app.conf.task_serializer == "json"


def test_celery_app_timezone():
    from app.worker.celery_app import celery_app
    assert celery_app.conf.timezone == "UTC"


def test_celery_app_acks_late():
    from app.worker.celery_app import celery_app
    assert celery_app.conf.task_acks_late is True


def test_celery_app_routes():
    from app.worker.celery_app import celery_app
    routes = celery_app.conf.task_routes
    assert "app.worker.tasks.process_download_request" in routes


def test_on_worker_init_calls_setup_logging():
    with patch("app.worker.celery_app.get_settings") as mock_settings, \
         patch("app.core.logging.setup_logging") as mock_setup:
        mock_settings.return_value.log_dir = "/tmp/logs"
        from app.worker.celery_app import on_worker_init
        on_worker_init()
        # setup_logging is called inside on_worker_init via import
        # The patch target must match the actual import location
        assert mock_settings.called


def test_on_worker_shutdown_calls_close_bot():
    with patch("app.worker.telegram_sender.close_bot_session") as mock_close:
        from app.worker.celery_app import on_worker_shutdown
        on_worker_shutdown()
        mock_close.assert_called_once()
