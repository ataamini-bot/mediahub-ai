#!/usr/bin/env bash
set -Eeuo pipefail

# Safe release for three related Bot fixes:
# - shared local Telegram API upload storage for file conversion;
# - Free weekly conversion quota / paid-plan output quota;
# - immediate, supported Telegram button colors.
# No database migration is required.
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="688f91144db2c0eed73d4a1fca3bc61dcd3c3af5"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_conversion_quota_and_button_style_fix.sh <full-target-commit>\n'
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
if git diff --name-only "$expected_base" "$target_commit" -- backend/alembic | grep -q .; then
  printf 'DEPLOYMENT=ABORTED_UNEXPECTED_MIGRATION\n'
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
old_telegram_api_image="$(docker inspect --format='{{.Image}}' mediahub-telegram-api)"
rollback_backend="mediahub-ai-backend:rollback-conversion-quota-$release_id"
rollback_bot="mediahub-ai-bot:rollback-conversion-quota-$release_id"
rollback_telegram_api="mediahub-ai-telegram-api:rollback-conversion-quota-$release_id"
docker image tag "$old_backend_image" "$rollback_backend"
docker image tag "$old_bot_image" "$rollback_bot"
docker image tag "$old_telegram_api_image" "$rollback_telegram_api"
printf 'ROLLBACK_BACKEND=%s\nROLLBACK_BOT=%s\nROLLBACK_TELEGRAM_API=%s\n' \
  "$rollback_backend" "$rollback_bot" "$rollback_telegram_api"

backend_touched=0
bot_touched=0
telegram_api_touched=0
source_touched=0
rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    if [ "$source_touched" -eq 1 ]; then
      git reset --keep "$expected_base" >/dev/null 2>&1 || true
    fi
    docker image tag "$old_backend_image" mediahub-ai-backend:latest || true
    docker image tag "$old_bot_image" mediahub-ai-bot:latest || true
    # After source rollback, docker-compose again refers to :latest.
    docker image tag "$old_telegram_api_image" aiogram/telegram-bot-api:latest || true
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

# The conversion reader needs this exact read-only mount when the Bot API is
# local. Validate the release source before building any image.
grep -Fq 'telegram_api_data:/var/lib/telegram-bot-api:ro' docker-compose.yml
printf 'CONVERSION_UPLOAD_VOLUME_SOURCE=OK\n'

docker compose build backend bot
docker compose pull telegram-api

docker compose run --rm --no-deps -T backend python -m compileall -q app
docker compose run --rm --no-deps -T bot python -m compileall -q app
docker compose run --rm --no-deps -T backend python - <<'PY'
import inspect

from app.services.download_access import (
    DownloadEntitlement,
    WeeklyConversionLimitReached,
)
from app.services.download import DownloadService

assert WeeklyConversionLimitReached.code == "weekly_conversion_limit_reached"
assert "media_type=normalized_media_type" in inspect.getsource(DownloadService.create_job)
snapshot = DownloadEntitlement(
    user_id=1,
    plan_id=1,
    plan_name="Free",
    plan_slug="free",
    daily_download_limit=3,
    max_file_size_mb=300,
    max_quality=720,
    max_concurrent_downloads=1,
    priority_processing=False,
    forced_join_required=False,
    download_limit_period="weekly",
).limits_snapshot()
assert snapshot["plan_slug"] == "free"
print("FREE_CONVERSION_QUOTA_CODE=OK")
PY
docker compose run --rm --no-deps -T bot sh -lc '
  test -d /var/lib/telegram-bot-api
  test -x "$(command -v ffprobe)"
'
docker compose run --rm --no-deps -T bot python - <<'PY'
from aiogram import __version__ as aiogram_version

from app.keyboards.payment import build_home_keyboard, build_home_reply_keyboard
from app.runtime_config import fallback_configuration
from app.services.backend import BackendAPIError
from app.main import download_error_text
from app.middleware.interface import ui_language

major, minor, *_rest = (int(part) for part in aiogram_version.split(".")[:2])
assert (major, minor) >= (3, 31), aiogram_version
configuration = fallback_configuration("en")
configuration["button_styles"]["buy"] = "danger"
reply = build_home_reply_keyboard("en", configuration=configuration)
inline = build_home_keyboard("en", configuration=configuration)
assert reply.model_dump(exclude_none=True)["keyboard"][0][0]["style"] == "danger"
assert inline.model_dump(exclude_none=True)["inline_keyboard"][0][0]["style"] == "danger"

error = BackendAPIError(
    status_code=429,
    detail={"code": "weekly_conversion_limit_reached"},
)
token = ui_language.set("en")
try:
    assert "one file conversion per week" in download_error_text(error)
finally:
    ui_language.reset(token)
print(f"AIOGRAM_VERSION={aiogram_version}")
print("BUTTON_STYLE_SERIALIZATION=OK")
print("CONVERSION_ERROR_UI=OK")
PY
printf 'CONVERSION_UPLOAD_VOLUME=OK\n'

require_no_active_jobs
bot_touched=1
docker compose stop bot >/dev/null
require_no_active_jobs

telegram_api_touched=1
docker compose up -d --no-deps --force-recreate telegram-api
telegram_ready=0
for _ in $(seq 1 40); do
  if docker compose run --rm --no-deps -T bot python - <<'PY' >/dev/null 2>&1
import os
import httpx

token = os.environ["TELEGRAM_BOT_TOKEN"]
response = httpx.get(
    f"http://telegram-api:8081/bot{token}/getMe",
    timeout=5,
)
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

telegram_api_version="$(
  docker compose exec -T telegram-api telegram-bot-api --version 2>&1 |
    tr '\n' ' ' |
    sed 's/[[:space:]]\+/ /g; s/[[:space:]]$//'
)"
printf 'TELEGRAM_API_VERSION=%s\n' "$telegram_api_version"

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
printf 'BACKEND_QUOTA_CODE=OK\n'

bot_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate bot
sleep 10
docker compose exec -T bot test -d /var/lib/telegram-bot-api

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
   grep -Eq 'Traceback \(most recent call last\)|TelegramConflictError|TelegramUnauthorizedError'; then
  printf 'BOT_OR_TELEGRAM_API_LOG_CHECK=FAILED\n'
  exit 1
fi

curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null
[ "$(git rev-parse HEAD)" = "$target_commit" ]
[ -z "$(git status --porcelain)" ]

printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\n' "$(git rev-parse HEAD)"
trap - EXIT
