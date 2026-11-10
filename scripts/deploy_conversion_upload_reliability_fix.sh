#!/usr/bin/env bash
set -Eeuo pipefail

# Bot-only release for reliable audio/video upload staging.  It keeps the
# Local Bot API's large-file path, retries short shared-volume races, and uses
# Telegram's public endpoint only as a fallback for otherwise inaccessible
# Local API files.  No migration or worker/backend restart is required.
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="64b924c693cf339c0b061054df9a1cb5e9a3c021"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_conversion_upload_reliability_fix.sh <full-target-commit>\n'
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
    . ':!bot/**' ':!scripts/**' ':!docs/**' ':!docker-compose.yml'
)"
if [ -n "$unexpected_files" ]; then
  printf 'DEPLOYMENT=ABORTED_NOT_A_BOT_ONLY_UPDATE\n%s\n' "$unexpected_files"
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
rollback_tag="mediahub-ai-bot:rollback-conversion-upload-reliability-$release_id"
docker image tag "$old_image" "$rollback_tag"
printf 'ROLLBACK_BOT=%s\n' "$rollback_tag"

source_touched=0
bot_touched=0
probe_relative=".mediahub-smoke/conversion-upload-$release_id.ogg"
probe_path="/var/lib/telegram-bot-api/$probe_relative"
probe_created=0

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
grep -Fq 'TELEGRAM_BOT_API_DATA_DIR: /var/lib/telegram-bot-api' docker-compose.yml
docker compose build bot
docker compose run --rm --no-deps -T bot python -m compileall -q app

# Verify the exact named volume is readable by the Bot, not merely that the
# mount directory exists.  The temporary file is created by telegram-api and
# copied through the production upload reader by a separate Bot container.
docker compose exec -T telegram-api sh -lc \
  "umask 022; mkdir -p /var/lib/telegram-bot-api/.mediahub-smoke; printf 'conversion-volume-smoke' > '$probe_path'"
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
    expected_size = source.stat().st_size
    with TemporaryDirectory() as directory:
        destination = Path(directory) / "incoming" / "upload.ogg"
        bot = SimpleNamespace(
            token="123:token",
            session=SimpleNamespace(
                api=SimpleNamespace(
                    is_local=True,
                    wrap_local_file=SimpleNamespace(to_local=lambda value: value),
                )
            ),
            get_file=AsyncMock(return_value=SimpleNamespace(file_path=str(source))),
        )
        source_kind = await _download_telegram_file(
            bot,
            "file-id",
            destination,
            expected_size=expected_size,
        )
        assert source_kind == "local-path"
        assert destination.read_bytes() == source.read_bytes()


asyncio.run(main())
print("CONVERSION_SHARED_VOLUME=OK")
PY
cleanup_probe

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
