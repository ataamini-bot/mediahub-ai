from app.workers.celery_app import celery_app


@celery_app.task(name="mediahub.health")
def health_task() -> str:
    return "MediaHub worker is healthy"
