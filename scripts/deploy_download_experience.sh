#!/usr/bin/env bash
set -Eeuo pipefail

# Safe release for the download experience:
# - progress/file captions without a visible Job ID;
# - redownload and details controls on delivered files;
# - high-quality cover and description actions on the quality menu;
# - FPS and description metadata returned by the Backend.
#
# This release has no database migration and restarts only Backend and Bot.
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="18a57ea04b379f2e04e9b06c5ec173b87472faad"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_download_experience.sh <full-target-commit>\n'
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
    . ':!backend/**' ':!bot/**' ':!scripts/**' ':!docs/**'
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
rollback_backend="mediahub-ai-backend:rollback-download-experience-$release_id"
rollback_bot="mediahub-ai-bot:rollback-download-experience-$release_id"
docker image tag "$old_backend_image" "$rollback_backend"
docker image tag "$old_bot_image" "$rollback_bot"
printf 'ROLLBACK_BACKEND=%s\nROLLBACK_BOT=%s\n' "$rollback_backend" "$rollback_bot"

source_touched=0
backend_touched=0
bot_touched=0

rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    if [ "$source_touched" -eq 1 ]; then
      git reset --keep "$expected_base" >/dev/null 2>&1 || true
    fi
    docker image tag "$old_backend_image" mediahub-ai-backend:latest || true
    docker image tag "$old_bot_image" mediahub-ai-bot:latest || true
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
docker compose build backend bot
docker compose run --rm --no-deps -T backend python -m compileall -q app
docker compose run --rm --no-deps -T bot python -m compileall -q app

docker compose run --rm --no-deps -T backend python - <<'PY'
from app.schemas.download import MediaFormat, MediaInfoResponse
from app.services.download import _best_thumbnail_url, _normalize_format

thumbnail = _best_thumbnail_url({
    "thumbnail": "https://cdn.example.test/default.jpg",
    "thumbnails": [
        {"url": "https://cdn.example.test/small.jpg", "width": 320, "height": 180},
        {"url": "https://cdn.example.test/original.jpg", "width": 1920, "height": 1080},
    ],
})
assert thumbnail == "https://cdn.example.test/original.jpg"
normalized = _normalize_format({
    "format_id": "1080p", "width": 1920, "height": 1080,
    "fps": 30, "vcodec": "avc1", "acodec": "mp4a", "ext": "mp4",
})
assert normalized and normalized["fps"] == 30.0
assert MediaFormat.model_validate(normalized).fps == 30.0
assert MediaInfoResponse(source_url="https://example.test", description="caption").description == "caption"
print("DOWNLOAD_EXPERIENCE_BACKEND_CODE=OK")
PY

docker compose run --rm --no-deps -T bot python - <<'PY'
from app.keyboards.download import build_completed_download_keyboard
from app.keyboards.quality import build_quality_keyboard
from app.main import _completed_file_caption, build_progress_text
from app.middleware.interface import ui_language

token = ui_language.set("en")
try:
    progress = build_progress_text(
        428,
        "1080p",
        {
            "progress": 90,
            "downloaded_bytes": 34 * 1024 * 1024,
            "total_bytes": 38 * 1024 * 1024,
            "speed": int(1.5 * 1024 * 1024),
            "eta": 3,
        },
        media_info={"formats": [{"has_video": True, "resolution": "1920x1080", "fps": 30}]},
    )
    caption = _completed_file_caption(
        "MediaHub-428.mp4",
        "33 MB",
        {"quality": "1080p", "source_url": "https://www.instagram.com/reel/example/"},
        {"duration": 42, "formats": [{"has_video": True, "resolution": "1920x1080", "fps": 30}]},
    )
    delivered = build_completed_download_keyboard(428)
    quality = build_quality_keyboard(
        [(720, None)], "smoke", audio_token="smoke", cover_token="smoke",
        description_token="smoke", language="en",
    )
finally:
    ui_language.reset(token)

delivered_buttons = [button for row in delivered.inline_keyboard for button in row]
quality_callbacks = {button.callback_data for row in quality.inline_keyboard for button in row}
assert "Job ID" not in progress and "428" not in progress
assert "Job ID" not in caption
assert {button.callback_data for button in delivered_buttons} == {"download_again:428", "download_details:428"}
assert {button.text for button in delivered_buttons} == {"🔄 Download again", "📋 Details"}
assert {"audio:open:smoke", "media:cover:smoke", "media:description:smoke"} <= quality_callbacks
print("DOWNLOAD_EXPERIENCE_BOT_CODE=OK")
PY

# No active job is interrupted.  Worker and monitor need no restart for this
# metadata/UI-only release.
require_no_active_jobs
bot_touched=1
docker compose stop bot >/dev/null
require_no_active_jobs

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

bot_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate bot
sleep 10

backend_status="$(docker inspect --format='{{.State.Status}}' mediahub-backend)"
backend_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-backend)"
bot_status="$(docker inspect --format='{{.State.Status}}' mediahub-bot)"
bot_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-bot)"
telegram_status="$(docker inspect --format='{{.State.Status}}' mediahub-telegram-api)"
worker_status="$(docker inspect --format='{{.State.Status}}' mediahub-worker)"
monitor_status="$(docker inspect --format='{{.State.Status}}' mediahub-monitor)"
printf 'BACKEND_STATUS=%s RESTARTS=%s\n' "$backend_status" "$backend_restarts"
printf 'BOT_STATUS=%s RESTARTS=%s\n' "$bot_status" "$bot_restarts"
printf 'TELEGRAM_API_STATUS=%s\nWORKER_STATUS=%s\nMONITOR_STATUS=%s\n' \
  "$telegram_status" "$worker_status" "$monitor_status"
[ "$backend_status" = "running" ] && [ "$backend_restarts" = "0" ]
[ "$bot_status" = "running" ] && [ "$bot_restarts" = "0" ]
[ "$telegram_status" = "running" ] && [ "$worker_status" = "running" ] && [ "$monitor_status" = "running" ]

if docker compose logs --since="$backend_started_at" --tail=300 backend bot |
   grep -Eq 'Traceback \(most recent call last\)|Application startup failed|TelegramConflictError|TelegramUnauthorizedError'; then
  printf 'SERVICE_LOG_CHECK=FAILED\n'
  exit 1
fi
curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null
[ "$(git rev-parse HEAD)" = "$target_commit" ]
[ -z "$(git status --porcelain)" ]

printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\n' "$(git rev-parse HEAD)"
trap - EXIT
