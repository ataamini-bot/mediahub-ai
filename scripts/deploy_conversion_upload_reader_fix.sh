#!/usr/bin/env bash
set -Eeuo pipefail

# Bot-only release for reading uploaded audio/video files through the local
# Telegram Bot API. No database migration or backend/worker restart is needed.
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="387e3d64d076f2bbe0686b6a84fed6499e04facd"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_conversion_upload_reader_fix.sh <full-target-commit>\n'
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
    . ':!bot/**' ':!scripts/**' ':!docs/**'
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
old_image="$(docker inspect --format='{{.Image}}' mediahub-bot)"
rollback_tag="mediahub-ai-bot:rollback-conversion-upload-reader-$release_id"
docker image tag "$old_image" "$rollback_tag"
printf 'ROLLBACK_BOT=%s\n' "$rollback_tag"

source_touched=0
bot_touched=0
rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    if [ "$source_touched" -eq 1 ]; then
      git reset --keep "$expected_base" >/dev/null 2>&1 || true
    fi
    docker image tag "$old_image" mediahub-ai-bot:latest || true
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
docker compose build bot
docker compose run --rm --no-deps -T bot python -m compileall -q app
docker compose run --rm --no-deps -T bot python - <<'PY'
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.handlers.conversion import _download_telegram_file


async def main():
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "telegram-api" / "voice.ogg"
        source.parent.mkdir()
        source.write_bytes(b"audio-smoke")
        destination = root / "downloads" / "incoming" / "upload.ogg"
        bot = SimpleNamespace(
            token="123:token",
            session=SimpleNamespace(
                api=SimpleNamespace(
                    is_local=True,
                    wrap_local_file=SimpleNamespace(to_local=lambda value: value),
                )
            ),
            get_file=AsyncMock(return_value=SimpleNamespace(file_path=str(source))),
            download_file=AsyncMock(side_effect=AssertionError("local copy was not used")),
        )
        source_kind = await _download_telegram_file(bot, "file-id", destination)
        assert source_kind == "local-path"
        assert destination.read_bytes() == b"audio-smoke"


asyncio.run(main())
print("CONVERSION_UPLOAD_READER=OK")
PY

require_no_active_jobs
docker compose stop bot >/dev/null
require_no_active_jobs
bot_touched=1
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate bot
sleep 10

bot_status="$(docker inspect --format='{{.State.Status}}' mediahub-bot)"
bot_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-bot)"
telegram_status="$(docker inspect --format='{{.State.Status}}' mediahub-telegram-api)"
worker_status="$(docker inspect --format='{{.State.Status}}' mediahub-worker)"
monitor_status="$(docker inspect --format='{{.State.Status}}' mediahub-monitor)"
printf 'BOT_STATUS=%s RESTARTS=%s\nTELEGRAM_API_STATUS=%s\nWORKER_STATUS=%s\nMONITOR_STATUS=%s\n' \
  "$bot_status" "$bot_restarts" "$telegram_status" "$worker_status" "$monitor_status"
[ "$bot_status" = "running" ] && [ "$bot_restarts" = "0" ]
[ "$telegram_status" = "running" ] && [ "$worker_status" = "running" ] && [ "$monitor_status" = "running" ]
docker compose exec -T bot test -d /var/lib/telegram-bot-api

if docker compose logs --since="$started_at" --tail=200 bot |
   grep -Eq 'Traceback \(most recent call last\)|TelegramConflictError|TelegramUnauthorizedError'; then
  printf 'BOT_LOG_CHECK=FAILED\n'
  exit 1
fi

curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null
[ "$(git rev-parse HEAD)" = "$target_commit" ]
[ -z "$(git status --porcelain)" ]

printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\n' "$(git rev-parse HEAD)"
trap - EXIT
