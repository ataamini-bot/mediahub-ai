import json
import os
import time
import urllib.request
from collections.abc import Callable

import redis
from sqlalchemy import text

from app.db.worker_session import worker_engine
from app.services.admin_notifications import (
    notify_event,
)
from app.workers.celery_app import celery_app


SERVICE_LABELS = {
    "backend": "Backend",
    "worker": "Worker",
    "postgres": "PostgreSQL",
    "redis": "Redis",
}


FAILURE_LEVELS = {
    "backend": "error",
    "worker": "error",
    "postgres": "critical",
    "redis": "critical",
}


def _get_positive_int(
    name: str,
    default: int,
) -> int:

    raw_value = os.getenv(
        name,
        str(default),
    ).strip()

    try:
        value = int(raw_value)
    except ValueError:
        value = default

    return max(
        1,
        value,
    )


def _check_backend() -> bool:

    url = os.getenv(
        "HEALTH_MONITOR_BACKEND_URL",
        "http://backend:8000/health",
    ).strip()

    try:

        with urllib.request.urlopen(
            url,
            timeout=5,
        ) as response:

            if response.status != 200:
                return False

            payload = json.loads(
                response.read().decode(
                    "utf-8"
                )
            )

        return (
            payload.get("status")
            == "healthy"
        )

    except Exception as exc:

        print(
            "Health check failed: "
            "service=backend "
            f"error={type(exc).__name__}"
        )

        return False


def _check_postgres() -> bool:

    try:

        with worker_engine.connect() as connection:

            connection.execute(
                text("SELECT 1")
            )

        return True

    except Exception as exc:

        print(
            "Health check failed: "
            "service=postgres "
            f"error={type(exc).__name__}"
        )

        return False


def _check_redis() -> bool:

    try:

        client = redis.Redis.from_url(
            os.getenv(
                "REDIS_URL",
                "redis://redis:6379/0",
            ),
            socket_connect_timeout=3,
            socket_timeout=3,
        )

        return bool(
            client.ping()
        )

    except Exception as exc:

        print(
            "Health check failed: "
            "service=redis "
            f"error={type(exc).__name__}"
        )

        return False


def _check_worker() -> bool:

    try:

        responses = celery_app.control.ping(
            timeout=5,
        )

        return bool(
            responses
        )

    except Exception as exc:

        print(
            "Health check failed: "
            "service=worker "
            f"error={type(exc).__name__}"
        )

        return False


def _notify_failure(
    service_name: str,
) -> None:

    label = SERVICE_LABELS[
        service_name
    ]

    notify_event(
        "monitoring",
        level=FAILURE_LEVELS[
            service_name
        ],
        title=(
            f"اختلال در سرویس {label}"
        ),
        message=(
            f"Health Monitor نتوانست "
            f"سلامت سرویس {label} را "
            "تأیید کند."
        ),
        service="Health Monitor",
        component=label,
        action=(
            "وضعیت سرویس و Logهای مربوط "
            "بررسی شود."
        ),
        # The state machine already prevents duplicates.
        dedup_seconds=0,
    )


def _notify_recovery(
    service_name: str,
) -> None:

    label = SERVICE_LABELS[
        service_name
    ]

    notify_event(
        "monitoring",
        level="recovery",
        title=(
            f"سرویس {label} بازیابی شد"
        ),
        message=(
            f"Health Monitor دوباره سلامت "
            f"سرویس {label} را تأیید کرد."
        ),
        service="Health Monitor",
        component=label,
        action=(
            "اقدام دیگری لازم نیست. "
            "سرویس به وضعیت عادی بازگشته است."
        ),
        dedup_seconds=0,
    )


def _update_state(
    *,
    service_name: str,
    healthy: bool,
    states: dict[str, bool],
) -> None:

    previous = states.get(
        service_name
    )

    # First observation:
    # healthy services are recorded silently.
    # already-unhealthy services alert immediately.

    if previous is None:

        states[
            service_name
        ] = healthy

        if not healthy:
            _notify_failure(
                service_name
            )

        return

    if previous == healthy:
        return

    states[
        service_name
    ] = healthy

    if healthy:
        _notify_recovery(
            service_name
        )
    else:
        _notify_failure(
            service_name
        )


def _run_check(
    *,
    service_name: str,
    check: Callable[[], bool],
    states: dict[str, bool],
) -> bool:

    healthy = check()

    print(
        "Health status: "
        f"service={service_name} "
        f"healthy={healthy}"
    )

    _update_state(
        service_name=service_name,
        healthy=healthy,
        states=states,
    )

    return healthy


def _run_worker_check(
    *,
    states: dict[str, bool],
) -> bool:

    worker_healthy = _check_worker()

    if worker_healthy:

        print(
            "Health status: "
            "service=worker "
            "healthy=True"
        )

        _update_state(
            service_name="worker",
            healthy=True,
            states=states,
        )

        return True

    # Worker uses Redis as its Celery broker.
    #
    # Redis may have gone down in the few seconds between
    # the Redis health check and the Celery ping. Re-check
    # Redis before declaring an independent Worker failure.

    redis_healthy = _check_redis()

    print(
        "Dependency recheck: "
        "service=redis "
        f"healthy={redis_healthy}"
    )

    if not redis_healthy:

        _update_state(
            service_name="redis",
            healthy=False,
            states=states,
        )

        print(
            "Health state update suppressed: "
            "service=worker "
            "reason=redis_unhealthy_after_recheck"
        )

        return False

    print(
        "Health status: "
        "service=worker "
        "healthy=False"
    )

    _update_state(
        service_name="worker",
        healthy=False,
        states=states,
    )

    return False


def run_monitor() -> None:

    interval_seconds = (
        _get_positive_int(
            "HEALTH_MONITOR_INTERVAL_SECONDS",
            30,
        )
    )

    startup_grace_seconds = (
        _get_positive_int(
            "HEALTH_MONITOR_STARTUP_GRACE_SECONDS",
            15,
        )
    )

    worker_redis_recovery_grace_seconds = (
        _get_positive_int(
            "HEALTH_MONITOR_WORKER_REDIS_RECOVERY_GRACE_SECONDS",
            90,
        )
    )

    states: dict[
        str,
        bool
    ] = {}

    worker_grace_until = 0.0

    print(
        "MediaHub Health Monitor starting: "
        f"grace={startup_grace_seconds}s "
        f"interval={interval_seconds}s"
    )

    time.sleep(
        startup_grace_seconds
    )

    while True:

        _run_check(
            service_name="backend",
            check=_check_backend,
            states=states,
        )

        _run_check(
            service_name="postgres",
            check=_check_postgres,
            states=states,
        )

        previous_redis_state = (
            states.get(
                "redis"
            )
        )

        redis_healthy = _run_check(
            service_name="redis",
            check=_check_redis,
            states=states,
        )

        if not redis_healthy:

            # Celery uses Redis as its broker.
            # A failed Worker ping while Redis is unavailable
            # is a dependency failure, not an independent
            # Worker incident.

            print(
                "Health check skipped: "
                "service=worker "
                "reason=redis_unhealthy"
            )

        else:

            if previous_redis_state is False:

                worker_grace_until = (
                    time.monotonic()
                    + worker_redis_recovery_grace_seconds
                )

                print(
                    "Worker recovery grace started: "
                    f"seconds="
                    f"{worker_redis_recovery_grace_seconds}"
                )

            remaining_grace = (
                worker_grace_until
                - time.monotonic()
            )

            if remaining_grace > 0:

                worker_healthy = (
                    _check_worker()
                )

                print(
                    "Worker recovery probe: "
                    f"healthy={worker_healthy} "
                    f"remaining={remaining_grace:.1f}s"
                )

                if worker_healthy:

                    worker_grace_until = 0.0

                    print(
                        "Worker recovery grace completed: "
                        "reason=worker_healthy"
                    )

                    _update_state(
                        service_name="worker",
                        healthy=True,
                        states=states,
                    )

                else:

                    # Do not turn a dependency-recovery delay
                    # into an independent Worker incident.
                    # If Worker is still unavailable after the
                    # grace deadline, the normal state machine
                    # will report it.

                    print(
                        "Health state update suppressed: "
                        "service=worker "
                        "reason=redis_recovery_grace"
                    )

            else:

                _run_worker_check(
                    states=states,
                )

        time.sleep(
            interval_seconds
        )


if __name__ == "__main__":

    try:
        run_monitor()

    except KeyboardInterrupt:
        print(
            "MediaHub Health Monitor stopped"
        )
