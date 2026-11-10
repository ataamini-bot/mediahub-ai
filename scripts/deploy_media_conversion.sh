#!/usr/bin/env bash
set -Eeuo pipefail

# Safe release for audio extraction and media conversion. It is anchored to
# the completion release deployed before this feature.
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="de438b927b4999f3d6cf0d6fc2c20565c0f6934e"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_media_conversion.sh <full-target-commit>\n'
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

release_id="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="${MEDIAHUB_BACKUP_DIR:-backups/manual}"
backup_file="$backup_dir/mediahub-before-media-conversion-$release_id.dump"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
old_backend_image="$(docker inspect --format='{{.Image}}' mediahub-backend)"
old_bot_image="$(docker inspect --format='{{.Image}}' mediahub-bot)"
old_worker_image="$(docker inspect --format='{{.Image}}' mediahub-worker)"
old_monitor_image="$(docker inspect --format='{{.Image}}' mediahub-monitor)"
rollback_backend="mediahub-ai-backend:rollback-media-conversion-$release_id"
rollback_bot="mediahub-ai-bot:rollback-media-conversion-$release_id"
rollback_worker="mediahub-ai-worker:rollback-media-conversion-$release_id"
rollback_monitor="mediahub-ai-monitor:rollback-media-conversion-$release_id"
docker image tag "$old_backend_image" "$rollback_backend"
docker image tag "$old_bot_image" "$rollback_bot"
docker image tag "$old_worker_image" "$rollback_worker"
docker image tag "$old_monitor_image" "$rollback_monitor"
printf 'ROLLBACK_BACKEND=%s\nROLLBACK_BOT=%s\nROLLBACK_WORKER=%s\nROLLBACK_MONITOR=%s\n' \
  "$rollback_backend" "$rollback_bot" "$rollback_worker" "$rollback_monitor"

require_no_active_jobs
docker compose exec -T postgres sh -lc \
  'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom' > "$backup_file"
chmod 600 "$backup_file"
[ -s "$backup_file" ]
docker compose exec -T postgres sh -lc 'pg_restore --list' < "$backup_file" >/dev/null
printf 'DATABASE_BACKUP=%s\n' "$backup_file"

migration_started=0
rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    if [ "$migration_started" -eq 0 ]; then
      git reset --keep "$expected_base" >/dev/null 2>&1 || true
      docker image tag "$old_backend_image" mediahub-ai-backend:latest || true
      docker image tag "$old_bot_image" mediahub-ai-bot:latest || true
      docker image tag "$old_worker_image" mediahub-ai-worker:latest || true
      docker image tag "$old_monitor_image" mediahub-ai-monitor:latest || true
      docker compose up -d --no-deps --force-recreate backend worker monitor bot || true
      printf 'PRE_MIGRATION_ROLLBACK=OK\n'
    else
      printf 'FORWARD_RECOVERY_REQUIRED=1\nDATABASE_BACKUP=%s\n' "$backup_file"
      docker compose stop bot worker monitor backend >/dev/null 2>&1 || true
    fi
    printf 'DEPLOYMENT=FAILED SOURCE_HEAD=%s\n' "$(git rev-parse HEAD)"
  fi
  exit "$status"
}
trap rollback_on_error EXIT

git merge --ff-only "$target_commit"
docker compose build backend bot worker monitor
docker compose run --rm --no-deps -T backend python -m compileall -q app alembic
docker compose run --rm --no-deps -T bot python -m compileall -q app
docker compose run --rm --no-deps -T backend python - <<'PY'
from app.models.download_job import DownloadJob
from app.models.bot_experience import HomeButton
from app.services.media_formats import AUDIO_OUTPUT_FORMATS, VIDEO_OUTPUT_FORMATS
from app.services.managed_settings import BUTTON_STYLES

assert hasattr(DownloadJob, "output_format")
assert set(AUDIO_OUTPUT_FORMATS) == {"mp3", "m4a", "wav", "aac", "flac", "ogg", "opus"}
assert set(VIDEO_OUTPUT_FORMATS) == {"mp4", "mkv", "avi", "mov", "webm"}
assert BUTTON_STYLES == {"default", "primary", "success", "danger"}
assert HomeButton.__table__.c.action_type is not None
print("MEDIA_CONVERSION_SCHEMA_CODE=OK")
PY

docker compose stop bot worker monitor backend >/dev/null
require_no_active_jobs
migration_started=1
docker compose run --rm --no-deps -T backend alembic upgrade head
docker compose run --rm --no-deps -T backend alembic current | grep -q f9c0d1e2f3a4
printf 'MIGRATION=f9c0d1e2f3a4\n'

docker compose up -d --no-deps --force-recreate backend
for _ in $(seq 1 40); do
  if curl --max-time 5 -fsS http://127.0.0.1:8000/health >/dev/null; then
    break
  fi
  sleep 2
done
curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null
docker compose up -d --no-deps --force-recreate worker monitor
docker compose run --rm --no-deps -T bot python - <<'PY'
from app.keyboards.conversion import build_conversion_format_keyboard
from app.keyboards.quality import build_quality_keyboard
from app.runtime_config import fallback_configuration
from app.utils.media_conversion import AUDIO_FORMATS, VIDEO_FORMATS

assert len(AUDIO_FORMATS) == 7 and len(VIDEO_FORMATS) == 5
assert any(
    button.callback_data == "convert:format:smoke:mp3"
    for row in build_conversion_format_keyboard("smoke", has_audio=True, has_video=False).inline_keyboard
    for button in row
)
assert any(
    button.callback_data == "audio:open:smoke"
    for row in build_quality_keyboard([(360, None)], "smoke", audio_token="smoke").inline_keyboard
    for button in row
)
assert fallback_configuration("en")["buttons"]["convert"] == "🔄 Convert media"
print("MEDIA_CONVERSION_CODE=OK")
PY
docker compose up -d --no-deps --force-recreate bot
sleep 10

backend_status="$(docker inspect --format='{{.State.Status}}' mediahub-backend)"
backend_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-backend)"
bot_status="$(docker inspect --format='{{.State.Status}}' mediahub-bot)"
bot_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-bot)"
worker_status="$(docker inspect --format='{{.State.Status}}' mediahub-worker)"
monitor_status="$(docker inspect --format='{{.State.Status}}' mediahub-monitor)"
printf 'BACKEND_STATUS=%s RESTARTS=%s\n' "$backend_status" "$backend_restarts"
printf 'BOT_STATUS=%s RESTARTS=%s\n' "$bot_status" "$bot_restarts"
printf 'WORKER_STATUS=%s\nMONITOR_STATUS=%s\n' "$worker_status" "$monitor_status"
[ "$backend_status" = "running" ] && [ "$backend_restarts" = "0" ]
[ "$bot_status" = "running" ] && [ "$bot_restarts" = "0" ]
[ "$worker_status" = "running" ] && [ "$monitor_status" = "running" ]

if docker compose logs --tail=300 backend bot worker monitor |
   grep -Eq 'Traceback \(most recent call last\)|Application startup failed|TelegramConflictError|TelegramUnauthorizedError'; then
  printf 'SERVICE_LOG_CHECK=FAILED\n'
  exit 1
fi
curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null
[ "$(git rev-parse HEAD)" = "$target_commit" ]
[ -z "$(git status --porcelain)" ]

printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\n' "$(git rev-parse HEAD)"
trap - EXIT
