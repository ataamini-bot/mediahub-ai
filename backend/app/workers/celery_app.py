from celery import Celery

from app.core.config import settings


celery_app = Celery(
    "mediahub",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    broker_transport_options={
        "queue_order_strategy": "priority",
        "priority_steps": list(range(10)),
    },
)

celery_app.conf.imports = (
    "app.workers.tasks.health",
    "app.workers.tasks.download",
)


# ============================================================
# Worker lifecycle notifications
# ============================================================

from celery.signals import (
    worker_ready,
    worker_shutdown,
)

from app.services.system_monitor import (
    notify_worker_ready,
    notify_worker_stopping,
)


@worker_ready.connect
def on_worker_ready(
    sender=None,
    **kwargs,
):
    notify_worker_ready()


@worker_shutdown.connect
def on_worker_shutdown(
    sender=None,
    **kwargs,
):
    notify_worker_stopping()
