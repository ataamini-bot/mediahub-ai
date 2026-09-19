#!/usr/bin/env bash
set -Eeuo pipefail

# Safe release for:
# - Local Telegram API upload reading for audio/video/document conversions;
# - conversion callbacks that must not reattach aiogram routers;
# - administrator-controlled main-menu order and 1/2/3 button row width.
#
# This release adds only two reversible application-setting rows. It rebuilds
# Backend and Bot, and recreates Telegram Bot API so its explicit shared
# storage directory is used for large incoming media files.
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="fafd1e91a174a2fafc3e5245b1acd07556450efc"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"
previous_migration="f9c0d1e2f3a4"
target_migration="fa0b1c2d3e4f"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_conversion_reader_and_home_layout.sh <full-target-commit>\n'
  exit 1
fi
if [ "$(git branch --show-current)" != "$expected_branch" ] ||
   [ "$(git rev-parse HEAD)" != "$expected_base" ] ||
   [ -n "$(git status --porcelain)" ]; then
  printf 'DEPLOYMENT=ABORTED_UNEXPECTED_BRANCH_HEAD_OR_LOCAL_CHANGES\n'
  exit 1
fi

git fetch origin "$expected_branch"
if [ "$(git rev-parse FETCH_HEAD)" != "$target_commit" ]; then
  printf 'DEPLOYMENT=ABORTED_REMOTE_HEAD_CHANGED\n'
  exit 1
fi
git merge-base --is-ancestor "$expected_base" "$target_commit"

unexpected_files="$(
  git diff --name-only "$expected_base" "$target_commit" -- \
    . ':!backend/**' ':!bot/**' ':!docker-compose.yml' ':!scripts/**' ':!docs/**'
)"
if [ -n "$unexpected_files" ]; then
  printf 'DEPLOYMENT=ABORTED_UNEXPECTED_FILES\n%s\n' "$unexpected_files"
  exit 1
fi
if ! curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null; then
  printf 'DEPLOYMENT=ABORTED_BACKEND_PREFLIGHT_FAILED\n'
  exit 1
fi

active_jobs() {
  docker compose exec -T postgres sh -lc '
    psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
      --no-psqlrc --tuples-only --no-align --command="
        SELECT count(*) FROM download_jobs
        WHERE status IN ('\''PENDING'\'', '\''PROCESSING'\'');
      "
  ' | tr -d '[:space:]'
}

require_no_active_jobs() {
  local jobs
  jobs="$(active_jobs)"
  printf 'ACTIVE_DOWNLOAD_JOBS=%s\n' "$jobs"
  [[ "$jobs" =~ ^[0-9]+$ ]] && [ "$jobs" -eq 0 ]
}

require_no_active_jobs

release_id="$(date -u +%Y%m%dT%H%M%SZ)"
old_backend_image="$(docker inspect --format='{{.Image}}' mediahub-backend)"
old_bot_image="$(docker inspect --format='{{.Image}}' mediahub-bot)"
rollback_backend="mediahub-ai-backend:rollback-conversion-layout-$release_id"
rollback_bot="mediahub-ai-bot:rollback-conversion-layout-$release_id"
docker image tag "$old_backend_image" "$rollback_backend"
docker image tag "$old_bot_image" "$rollback_bot"
printf 'ROLLBACK_BACKEND=%s\nROLLBACK_BOT=%s\n' "$rollback_backend" "$rollback_bot"

source_touched=0
backend_touched=0
bot_touched=0
telegram_api_touched=0
migration_applied=0
probe_created=0
probe_path="/var/lib/telegram-bot-api/.mediahub-smoke/conversion-layout-$release_id.bin"

cleanup_probe() {
  if [ "$probe_created" -eq 1 ]; then
    docker compose exec -T telegram-api sh -lc "rm -f -- '$probe_path'" >/dev/null 2>&1 || true
    probe_created=0
  fi
}

rollback_on_error() {
  local status=$?
  trap - EXIT
  cleanup_probe
  if [ "$status" -ne 0 ]; then
    # The migration solely inserts the two layout setting rows, so its exact
    # downgrade is safe before returning the source and containers to base.
    if [ "$migration_applied" -eq 1 ]; then
      docker compose run --rm --no-deps -T backend \
        alembic downgrade "$previous_migration" >/dev/null 2>&1 || true
    fi
    if [ "$source_touched" -eq 1 ]; then
      git reset --keep "$expected_base" >/dev/null 2>&1 || true
    fi
    docker image tag "$old_backend_image" mediahub-ai-backend:latest || true
    docker image tag "$old_bot_image" mediahub-ai-bot:latest || true
    if [ "$telegram_api_touched" -eq 1 ]; then
      docker compose up -d --no-deps --force-recreate telegram-api || true
    fi
    if [ "$backend_touched" -eq 1 ]; then
      docker compose up -d --no-deps --force-recreate backend || true
    fi
    if [ "$bot_touched" -eq 1 ]; then
      docker compose up -d --no-deps --force-recreate bot || true
    fi
    printf 'ROLLBACK=ATTEMPTED\nDEPLOYMENT=FAILED SOURCE_HEAD=%s\n' "$(git rev-parse HEAD)"
  fi
  exit "$status"
}
trap rollback_on_error EXIT

source_touched=1
git merge --ff-only "$target_commit"

# Assert the two production guarantees before the source is built.
grep -Fq -- '--dir=/var/lib/telegram-bot-api' docker-compose.yml
grep -Fq 'telegram_api_data:/var/lib/telegram-bot-api:ro' docker-compose.yml
grep -Fq 'configure_download_runtime' bot/app/handlers/conversion.py
if grep -Fq 'from app.main import' bot/app/handlers/conversion.py; then
  printf 'DEPLOYMENT=ABORTED_CONVERSION_ENTRYPOINT_REIMPORT\n'
  exit 1
fi
printf 'CONVERSION_ROUTER_AND_STORAGE_SOURCE=OK\n'

docker compose build backend bot
docker compose run --rm --no-deps -T backend python -m compileall -q app alembic
docker compose run --rm --no-deps -T bot python -m compileall -q app
docker compose run --rm --no-deps -T backend python - <<'PY'
from app.services.managed_settings import (
    HOME_MENU_BUTTON_KEYS,
    validate_managed_setting,
)

layout = {"columns": 3, "order": list(reversed(HOME_MENU_BUTTON_KEYS))}
assert validate_managed_setting("bot.home_layout.en", layout) == layout
print("HOME_LAYOUT_SCHEMA_CODE=OK")
PY
docker compose run --rm --no-deps -T bot python - <<'PY'
from app import main  # noqa: F401 - registers conversion delivery runtime
from app.handlers.conversion import _download_runtime_function
from app.keyboards.payment import build_home_reply_keyboard

assert callable(_download_runtime_function("wait_for_download"))
assert callable(_download_runtime_function("send_downloaded_file"))
keyboard = build_home_reply_keyboard(
    "en",
    include_admin=True,
    configuration={
        "language": "en",
        "buttons": {},
        "home_layout": {
            "columns": 3,
            "order": [
                "support", "buy", "subscription", "convert",
                "language", "faq", "tutorial", "admin",
            ],
        },
        "custom_buttons": [],
    },
)
assert [len(row) for row in keyboard.keyboard] == [3, 3, 2]
print("CONVERSION_CALLBACK_AND_HOME_LAYOUT_CODE=OK")
PY

# Stop only Bot after a second empty-queue check. Worker and monitor continue
# to run; no active download/conversion is interrupted.
require_no_active_jobs
bot_touched=1
docker compose stop bot >/dev/null
require_no_active_jobs

docker compose run --rm --no-deps -T backend alembic upgrade head
migration_applied=1
docker compose run --rm --no-deps -T backend alembic current | grep -q "$target_migration"
printf 'MIGRATION=%s\n' "$target_migration"

telegram_api_touched=1
telegram_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate telegram-api
telegram_ready=0
for _ in $(seq 1 40); do
  if docker compose run --rm --no-deps -T bot python - <<'PY' >/dev/null 2>&1
import os
import httpx

token = os.environ["TELEGRAM_BOT_TOKEN"]
response = httpx.get(f"http://telegram-api:8081/bot{token}/getMe", timeout=5)
response.raise_for_status()
assert response.json().get("ok") is True
PY
  then
    telegram_ready=1
    break
  fi
  sleep 2
done
[ "$telegram_ready" -eq 1 ]
docker compose exec -T telegram-api sh -lc \
  "tr '\\000' ' ' < /proc/1/cmdline | grep -F -- '--dir=/var/lib/telegram-bot-api' >/dev/null"
printf 'TELEGRAM_API_UPLOAD_STORAGE=OK\n'

# Verify the exact shared mounted volume across Telegram API and Bot. This is
# the path used for large MP4/MKV files before any public Bot API fallback.
docker compose exec -T telegram-api sh -lc \
  "umask 022; mkdir -p /var/lib/telegram-bot-api/.mediahub-smoke; printf 'conversion-reader-smoke' > '$probe_path'"
probe_created=1
docker compose run --rm --no-deps -T \
  -e CONVERSION_VOLUME_PROBE_PATH="$probe_path" \
  bot python - <<'PY'
import asyncio
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.handlers.conversion import _download_telegram_file


async def main() -> None:
    source = Path(os.environ["CONVERSION_VOLUME_PROBE_PATH"])
    assert source.is_file(), source
    with TemporaryDirectory() as directory:
        destination = Path(directory) / "incoming" / "video.mp4"
        bot = SimpleNamespace(
            token="123:token",
            session=SimpleNamespace(
                api=SimpleNamespace(
                    is_local=True,
                    wrap_local_file=SimpleNamespace(to_local=lambda value: value),
                )
            ),
            get_file=AsyncMock(return_value=SimpleNamespace(
                file_path=str(source),
                file_size=source.stat().st_size,
            )),
        )
        source_kind = await _download_telegram_file(
            bot,
            "file-id",
            destination,
            expected_size=source.stat().st_size,
        )
        assert source_kind == "local-path"
        assert destination.read_bytes() == source.read_bytes()


asyncio.run(main())
print("CONVERSION_SHARED_VOLUME=OK")
PY
cleanup_probe

backend_touched=1
backend_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate backend
backend_ready=0
for _ in $(seq 1 40); do
  if curl --max-time 5 -fsS http://127.0.0.1:8000/health >/dev/null; then
    backend_ready=1
    break
  fi
  sleep 2
done
[ "$backend_ready" -eq 1 ]
printf 'BACKEND_LAYOUT_CONFIGURATION=OK\n'

bot_touched=1
bot_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate bot
sleep 10

backend_status="$(docker inspect --format='{{.State.Status}}' mediahub-backend)"
backend_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-backend)"
bot_status="$(docker inspect --format='{{.State.Status}}' mediahub-bot)"
bot_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-bot)"
telegram_status="$(docker inspect --format='{{.State.Status}}' mediahub-telegram-api)"
telegram_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-telegram-api)"
worker_status="$(docker inspect --format='{{.State.Status}}' mediahub-worker)"
monitor_status="$(docker inspect --format='{{.State.Status}}' mediahub-monitor)"
printf 'BACKEND_STATUS=%s RESTARTS=%s\n' "$backend_status" "$backend_restarts"
printf 'BOT_STATUS=%s RESTARTS=%s\n' "$bot_status" "$bot_restarts"
printf 'TELEGRAM_API_STATUS=%s RESTARTS=%s\n' "$telegram_status" "$telegram_restarts"
printf 'WORKER_STATUS=%s\nMONITOR_STATUS=%s\n' "$worker_status" "$monitor_status"
[ "$backend_status" = "running" ] && [ "$backend_restarts" = "0" ]
[ "$bot_status" = "running" ] && [ "$bot_restarts" = "0" ]
[ "$telegram_status" = "running" ] && [ "$telegram_restarts" = "0" ]
[ "$worker_status" = "running" ] && [ "$monitor_status" = "running" ]

if docker compose logs --since="$backend_started_at" --tail=300 backend |
   grep -Eq 'Traceback \(most recent call last\)|Application startup failed'; then
  printf 'BACKEND_LOG_CHECK=FAILED\n'
  exit 1
fi
if docker compose logs --since="$bot_started_at" --tail=300 bot telegram-api |
   grep -Eq 'Traceback \(most recent call last\)|TelegramConflictError|TelegramUnauthorizedError|Router is already attached'; then
  printf 'BOT_OR_TELEGRAM_API_LOG_CHECK=FAILED\n'
  exit 1
fi

curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null
[ "$(git rev-parse HEAD)" = "$target_commit" ]
[ -z "$(git status --porcelain)" ]

printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\n' "$(git rev-parse HEAD)"
trap - EXIT
