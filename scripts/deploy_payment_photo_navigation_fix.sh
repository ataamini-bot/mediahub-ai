#!/usr/bin/env bash
set -Eeuo pipefail

cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="26c3406b9541ba7904da17c1999dfa867dedf2d2"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_payment_photo_navigation_fix.sh <full-target-commit>\n'
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
if [ -n "$(
  git diff --name-only "$expected_base" "$target_commit" -- \
    . ':!bot/**' ':!docs/**' ':!scripts/**'
)" ]; then
  printf 'DEPLOYMENT=ABORTED_NOT_A_BOT_ONLY_UPDATE\n'
  exit 1
fi

curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null

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
old_image="$(docker inspect --format='{{.Image}}' mediahub-bot)"
rollback_tag="mediahub-ai-bot:rollback-payment-photo-navigation-$(date -u +%Y%m%dT%H%M%SZ)"
docker image tag "$old_image" "$rollback_tag"
printf 'ROLLBACK_BOT=%s\n' "$rollback_tag"

bot_touched=0
rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    docker image tag "$old_image" mediahub-ai-bot:latest || true
    if [ "$bot_touched" -eq 1 ]; then
      docker compose up -d --no-deps --force-recreate bot || true
    fi
    printf 'DEPLOYMENT=FAILED SOURCE_HEAD=%s\n' "$(git rev-parse HEAD)"
  fi
  exit "$status"
}
trap rollback_on_error EXIT

git merge --ff-only "$target_commit"
docker compose build bot
docker compose run --rm --no-deps -T bot python - <<'PY'
import asyncio

from app.handlers.payments import _replace_payment_message


class PhotoMessage:
    text = None

    def __init__(self):
        self.calls = []

    async def answer(self, *_args, **_kwargs):
        self.calls.append("answer")

    async def delete(self):
        self.calls.append("delete")

    async def edit_text(self, *_args, **_kwargs):
        raise AssertionError("A photo message must not use edit_text")


message = PhotoMessage()
asyncio.run(
    _replace_payment_message(
        message,
        "Choose a plan",
        parse_mode="HTML",
    )
)
assert message.calls == ["answer", "delete"]
print("PAYMENT_PHOTO_NAVIGATION=OK")
PY

require_no_active_jobs
bot_touched=1
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate bot
sleep 10

status="$(docker inspect --format='{{.State.Status}}' mediahub-bot)"
restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-bot)"
printf 'BOT_STATUS=%s RESTARTS=%s\n' "$status" "$restarts"
[ "$status" = "running" ] && [ "$restarts" = "0" ]

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
