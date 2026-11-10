"""Run only on a disposable CI host; no Telegram requests or real credentials."""
import json
import os
import time
import urllib.request
from pathlib import Path

from mediahub import Console
from ops_common import read_env, run, write_env


def check_backend():
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=3) as response:
                if response.status == 200:
                    return
        except OSError:
            pass
        time.sleep(2)
    raise AssertionError("Fresh backend failed to start")


def main():
    assert os.getenv("GITHUB_ACTIONS") == "true", "Disposable GitHub runner only"
    root = Path(__file__).resolve().parents[1]
    assert not (root / ".env").exists(), "Never overwrite an existing installation"
    assert not run(["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=mediahub-ai", "-q"], capture=True)
    values = read_env(root / ".env.example")
    values.update(APP_ENV="test", DEBUG="false", COMPOSE_PROJECT_NAME="mediahub-ai",
        POSTGRES_PASSWORD="ci-password", DATABASE_URL="postgresql+asyncpg://mediahub:ci-password@postgres:5432/mediahub",
        TELEGRAM_API_ID="1", TELEGRAM_API_HASH="a" * 32, TELEGRAM_BOT_TOKEN="123456789:" + "a" * 35,
        TELEGRAM_SUPERADMIN_ID="123456789", SECRET_KEY="test-only", JWT_SECRET_KEY="test-only",
        BOT_BACKEND_API_KEY="a" * 64, DATA_ENCRYPTION_KEY="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=")
    write_env(root / ".env", values)
    for name in ("backups", "secrets"):
        (root / name).mkdir(mode=0o700, exist_ok=True)
    console = Console(root)
    try:
        console.prepare()
        console.compose("up", "-d", "--wait", "--wait-timeout", "120", "postgres", "redis")
        console.compose("run", "--rm", "--no-deps", "-T", "backend", "alembic", "upgrade", "head")
        console.local({"action": "bootstrap"})
        console.start_youtube_provider()
        console.compose("up", "-d", "--no-deps", "--no-build", "backend", "worker", "backup")
        check_backend()
        for service in ("backend", "worker"):
            console.compose("exec", "-T", service, "python", "-m", "app.services.youtube_health")
        info = console.backup(verify=True)
        extracted = json.loads(console.compose("run", "--rm", "--no-deps", "-T", "backup", "extract-config",
                                info["id"], "--output", "/backups/ci-extracted", capture=True))
        assert extracted["schema_version"] == json.loads((root / "release.json").read_text())["schema"]
        console.compose("stop", "backend", "worker", "backup")
        console.compose("run", "--rm", "--no-deps", "-T", "backup", "restore", info["id"], "--confirm", "RESTORE:mediahub")
        console.local({"action": "reconcile_restore"})
        console.compose("up", "-d", "--no-deps", "backend", "worker", "backup")
        check_backend()
        print("FRESH_DOCKER_CORE=OK PORTABLE_BACKUP_RESTORE=OK TELEGRAM_LIVE=NOT_TESTED")
    finally:
        console.compose("down", "--volumes")
        # The runner can own root-created files only via Docker cleanup commands;
        # no decrypted configuration is uploaded as an artifact.


if __name__ == "__main__":
    main()
