"""Reserve one pool for short tasks/recognition while downloads are busy."""
import os
import signal
import subprocess
import sys
import time


def commands(download_processes):
    base = [sys.executable, "-m", "celery", "-A", "app.workers.celery_app", "worker",
            "--loglevel=INFO", "--prefetch-multiplier=1"]
    return [base + ["--queues=downloads", f"--concurrency={download_processes}", "--hostname=downloads@%h"],
            base + ["--queues=celery", "--concurrency=1", "--hostname=tools@%h"]]


def main():
    from app.services.source_capacity import configuration
    count = configuration()["downloads.worker_processes"]
    children = []
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for command in commands(count):
            children.append(subprocess.Popen(command, start_new_session=True))
        while not stopping and all(child.poll() is None for child in children):
            time.sleep(0.5)
    finally:
        stop()
        deadline = time.monotonic() + 45
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    return 0 if stopping and all(child.returncode == 0 for child in children) else 1


if __name__ == "__main__":
    raise SystemExit(main())
