#!/usr/bin/env bash
set -Eeuo pipefail

# Release procedure for the completion phase. It drains work, creates a
# PostgreSQL archive, applies the migration, then restarts all four changed
# services. It never downgrades a database after a successful migration.
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="4e4df8a20c38624a3e08d973e119f8318a744342"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_completion_phase.sh <full-target-commit>\n'
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
backup_file="$backup_dir/mediahub-before-completion-$release_id.dump"
mkdir -p "$backup_dir"
chmod 700 "$backup_dir"
old_backend_image="$(docker inspect --format='{{.Image}}' mediahub-backend)"
old_bot_image="$(docker inspect --format='{{.Image}}' mediahub-bot)"
old_worker_image="$(docker inspect --format='{{.Image}}' mediahub-worker)"
old_monitor_image="$(docker inspect --format='{{.Image}}' mediahub-monitor)"
rollback_backend="mediahub-ai-backend:rollback-completion-$release_id"
rollback_bot="mediahub-ai-bot:rollback-completion-$release_id"
rollback_worker="mediahub-ai-worker:rollback-completion-$release_id"
rollback_monitor="mediahub-ai-monitor:rollback-completion-$release_id"
docker image tag "$old_backend_image" "$rollback_backend"
docker image tag "$old_bot_image" "$rollback_bot"
docker image tag "$old_worker_image" "$rollback_worker"
docker image tag "$old_monitor_image" "$rollback_monitor"
printf 'ROLLBACK_BACKEND=%s\nROLLBACK_BOT=%s\nROLLBACK_WORKER=%s\nROLLBACK_MONITOR=%s\n' \
  "$rollback_backend" "$rollback_bot" "$rollback_worker" "$rollback_monitor"

require_no_active_jobs

# Custom format is restorable and is written without putting credentials in
# the process arguments or deployment logs.
docker compose exec -T postgres sh -lc \
  'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom' > "$backup_file"
chmod 600 "$backup_file"
[ -s "$backup_file" ]
docker compose exec -T postgres sh -lc 'pg_restore --list' < "$backup_file" >/dev/null
printf 'DATABASE_BACKUP=%s\n' "$backup_file"

migration_started=0
migration_succeeded=0
rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    if [ "$migration_started" -eq 0 ]; then
      # The preflight requires a clean tree, and --keep preserves any
      # unexpected local work instead of silently deleting it.
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
from app.models.payment import Payment
from app.models.bot_experience import SupportTicket, SupportTicketEvent
from app.models.subscription import SubscriptionStatus
from app.services.operations import MONITOR_DEFAULTS

assert hasattr(Payment, "txid") and hasattr(Payment, "txid_normalized")
assert hasattr(SupportTicket, "reopened_count")
assert SupportTicketEvent.__tablename__ == "support_ticket_events"
assert SubscriptionStatus.SCHEDULED.value == "scheduled"
assert set(MONITOR_DEFAULTS) >= {"cpu_percent", "ram_percent", "disk_percent", "download_error_percent"}
print("COMPLETION_SCHEMA_CODE=OK")
PY

docker compose stop bot worker monitor backend >/dev/null
require_no_active_jobs
migration_started=1
docker compose run --rm --no-deps -T backend alembic upgrade head
docker compose run --rm --no-deps -T backend alembic current | grep -q e6f7a8b9c0d1
migration_succeeded=1
printf 'MIGRATION=e6f7a8b9c0d1\n'

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
from app.utils.text_pages import text_pages
from app.keyboards.admin_statistics import STATISTICS_PERIODS

assert [key for key, _label in STATISTICS_PERIODS] == ["1yr", "6mo", "3mo", "1mo", "7d", "today"]
assert len(text_pages("x" * 7000)) > 1
print("BOT_COMPLETION_CODE=OK")
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
