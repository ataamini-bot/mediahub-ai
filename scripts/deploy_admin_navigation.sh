#!/usr/bin/env bash
# Bot-only update from the verified Threads / English-plan deployment.
set -Eeuo pipefail

cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"
expected_base=4d765f960473c864c3aa965a41f842279ba78fae
expected_branch=feature/admin-foundation
target_commit="${1:-}"
if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_admin_navigation.sh <full-target-commit>\n'
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
if [ -n "$(git diff --name-only "$expected_base" "$target_commit" -- . ':!bot/**' ':!docs/**' ':!scripts/**')" ]; then
  printf 'DEPLOYMENT=ABORTED_NOT_A_BOT_ONLY_UPDATE\n'
  exit 1
fi

umask 077
mkdir -p backups/manual
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="backups/manual/admin-navigation-${stamp}.log"
backup_file="backups/manual/admin-navigation-${stamp}.dump"
exec > >(tee -a "$log_file") 2>&1

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
curl --max-time 10 -fsS http://127.0.0.1:8000/health
bot_id="$(docker compose ps -q bot)"
[ -n "$bot_id" ]
old_image="$(docker inspect --format '{{.Image}}' "$bot_id")"
image_tag="$(docker inspect --format '{{.Config.Image}}' "$bot_id")"
rollback_tag="mediahub-ai-bot:rollback-admin-navigation-${stamp}"
docker image tag "$old_image" "$rollback_tag"
printf '\nROLLBACK_BOT=%s\n' "$rollback_tag"

docker compose exec -T postgres sh -lc '
  exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
    --format=custom --no-owner --no-privileges
' > "${backup_file}.part"
docker compose exec -T postgres pg_restore --list < "${backup_file}.part" >/dev/null
mv "${backup_file}.part" "$backup_file"
printf 'DATABASE_BACKUP=%s\n' "$backup_file"

bot_touched=0
rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    docker image tag "$old_image" "$image_tag" || true
    if [ "$bot_touched" -eq 1 ]; then
      if docker compose up -d --no-deps --force-recreate bot; then
        printf 'BOT_RUNTIME_ROLLBACK=STARTED\n'
      else
        printf 'BOT_RUNTIME_ROLLBACK=FAILED\n'
      fi
    fi
    printf 'DEPLOYMENT=FAILED SOURCE_HEAD=%s LOG=%s\n' "$(git rev-parse HEAD)" "$log_file"
  fi
  exit "$status"
}
trap rollback_on_error EXIT

git merge --ff-only "$target_commit"
docker compose build bot
docker compose run --rm --no-deps -T bot python - <<'PY'
import asyncio
from app.main import dp
from app.services.backend import get_bot_configuration

async def verify():
    routers = [router.name for router in dp.sub_routers]
    assert routers.index("admin-experience") < routers.index("home")
    for language in ("fa", "en"):
        configuration = await get_bot_configuration(language)
        assert isinstance(configuration.get("buttons"), dict)
        assert configuration["buttons"].get("buy")
    print("BOT_CONFIGURATION_API=OK")

asyncio.run(verify())
PY

require_no_active_jobs
bot_touched=1
docker compose stop -t 30 bot
require_no_active_jobs
started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate bot
sleep 10
bot_id="$(docker compose ps -q bot)"
[ -n "$bot_id" ]
status="$(docker inspect --format '{{.State.Status}}' "$bot_id")"
restarts="$(docker inspect --format '{{.RestartCount}}' "$bot_id")"
printf 'BOT_STATUS=%s RESTARTS=%s\n' "$status" "$restarts"
[ "$status" = running ] && [ "$restarts" = 0 ]
docker compose logs --since="$started_at" --tail=200 bot > "${log_file}.bot"
if grep -Eq 'Traceback \(most recent call last\)|TelegramConflictError|TelegramUnauthorizedError' "${log_file}.bot"; then
  printf 'BOT_LOG_CHECK=FAILED\n'
  exit 1
fi
curl --max-time 10 -fsS http://127.0.0.1:8000/health
printf '\nFINAL_HEAD=%s\nVERIFY_LOG=%s\nDEPLOYMENT=OK\n' "$(git rev-parse HEAD)" "$log_file"
trap - EXIT
