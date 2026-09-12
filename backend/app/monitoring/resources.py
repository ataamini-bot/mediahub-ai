"""Non-blocking resource probes used by the independent monitor."""
import json
import os
import shutil
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import redis
from sqlalchemy import func, select

from app.models.download_job import DownloadJob, DownloadJobStatus
from app.db.worker_session import WorkerSessionLocal

_cpu_previous = None


def redis_client():
    return redis.Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"),
        socket_timeout=3, socket_connect_timeout=3)


def bot_age():
    with redis_client() as client:
        raw = client.get("mediahub:bot:poll_heartbeat")
    return max(0, time.time() - float(raw)) if raw else None


def telegram_api_ok():
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return False
    base = os.getenv("TELEGRAM_BOT_API", "http://telegram-api:8081").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/bot{token}/getMe", timeout=5) as response:
            return bool(json.load(response).get("ok"))
    except Exception:
        # Never log the request URL: it contains the bot token.
        return False


def queue_depth():
    # Kombu Redis priority queues use a separator followed by the priority.
    with redis_client() as client:
        keys = ["celery"] + [f"celery\x06\x16{priority}" for priority in range(1, 10)]
        with client.pipeline() as pipe:
            for key in keys:
                pipe.llen(key)
            return sum(pipe.execute())


def disk_usage():
    stat = shutil.disk_usage(os.getenv("HEALTH_MONITOR_DISK_PATH", "/app/downloads"))
    return round(stat.used * 100 / stat.total, 2)


def ram_usage():
    data = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        name, value = line.split(":", 1)
        data[name] = int(value.strip().split()[0])
    return round(100 * (1 - data["MemAvailable"] / data["MemTotal"]), 2)


def cpu_usage():
    global _cpu_previous
    raw = Path("/proc/stat").read_text().splitlines()[0].split()[1:]
    # guest time is already counted in user/nice time.
    values = [int(x) for x in raw[:8]]
    current = (sum(values), values[3] + values[4])
    previous, _cpu_previous = _cpu_previous, current
    if previous is None or current[0] <= previous[0]:
        return None
    return round(max(0, min(100, 100 * (1 - (current[1]-previous[1])/(current[0]-previous[0])))), 2)


def download_error_rate(window_minutes):
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    with WorkerSessionLocal() as session:
        failed, total = session.execute(select(
            func.count(DownloadJob.id).filter(DownloadJob.status == DownloadJobStatus.FAILED),
            func.count(DownloadJob.id),
        ).where(DownloadJob.updated_at >= since,
            DownloadJob.status.in_((DownloadJobStatus.FAILED, DownloadJobStatus.COMPLETED)))).one()
    return {"percent": round(100 * failed / total, 2) if total else 0, "samples": total}


def collect(thresholds, *, redis_healthy, postgres_healthy):
    probes = {
        "telegram_api": (telegram_api_ok, lambda value: bool(value)),
        "disk": (disk_usage, lambda value: value < thresholds["disk_percent"]),
        "ram": (ram_usage, lambda value: value < thresholds["ram_percent"]),
        "cpu": (cpu_usage, lambda value: value is None or value < thresholds["cpu_percent"]),
    }
    if redis_healthy:
        probes.update({
            "bot": (bot_age, lambda value: value is not None and value <= thresholds["bot_heartbeat_seconds"]),
            "celery_queue": (queue_depth, lambda value: value < thresholds["celery_queue_size"]),
        })
    if postgres_healthy:
        probes["download_errors"] = (
            lambda: download_error_rate(thresholds["download_error_window_minutes"]),
            lambda value: value["samples"] < 5 or value["percent"] < thresholds["download_error_percent"],
        )
    result = {}
    for name, (probe, evaluate) in probes.items():
        try:
            value = probe()
            result[name] = {"healthy": evaluate(value), "value": value}
        except Exception as exc:
            result[name] = {"healthy": False, "value": None, "error": type(exc).__name__}
    return result


def save_snapshot(states, metrics, thresholds):
    with redis_client() as client:
        client.set("mediahub:monitor:snapshot", json.dumps({
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "services": states, "metrics": metrics, "thresholds": thresholds,
        }), ex=max(120, thresholds["interval_seconds"] * 4))
