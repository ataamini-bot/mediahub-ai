#!/usr/bin/env bash
cd /opt/mediahub-ai || exit 1

(
  set -u
  expected_branch="feature/admin-foundation"
  expected_commit="${1:-}"
  expected_migration="8c3d4e5f6a71"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  backup="backups/manual/before-bot-experience-${stamp}.dump"
  verify_log="backups/manual/bot-experience-${stamp}.log"
  migration_container="mediahub-migration-${stamp}"
  rollback_backend="mediahub-ai-backend:rollback-bot-experience-${stamp}"
  rollback_worker="mediahub-ai-worker:rollback-bot-experience-${stamp}"
  rollback_monitor="mediahub-ai-monitor:rollback-bot-experience-${stamp}"
  rollback_bot="mediahub-ai-bot:rollback-bot-experience-${stamp}"

  if ! [[ "$expected_commit" =~ ^[0-9a-f]{40}$ ]]; then
    printf 'Usage: %s <expected-40-character-commit-sha>\n' "$0"
    exit 2
  fi

  restore_tags() {
    restore_ok=1
    docker image tag "$rollback_backend" mediahub-ai-backend:latest || restore_ok=0
    docker image tag "$rollback_worker" mediahub-ai-worker:latest || restore_ok=0
    docker image tag "$rollback_monitor" mediahub-ai-monitor:latest || restore_ok=0
    docker image tag "$rollback_bot" mediahub-ai-bot:latest || restore_ok=0
    [ "$restore_ok" -eq 1 ]
  }

  rollback_runtime() {
    printf 'RUNTIME_ROLLBACK=STARTING\n'
    restore_tags || printf 'ROLLBACK_IMAGE_TAGS=RESTORE_FAILED\n'
    docker compose up -d --no-deps --force-recreate backend || return 1
    for attempt in $(seq 1 30); do
      curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break
      sleep 2
    done
    docker compose up -d --no-deps --force-recreate worker monitor bot || return 1
    printf 'RUNTIME_ROLLBACK=OLD_SERVICES_RESTARTED\n'
  }

  active_jobs() {
    docker compose exec -T postgres sh -lc '
      psql --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
        --no-psqlrc --tuples-only --no-align --command="
          SELECT count(*) FROM download_jobs
          WHERE status IN ('\''PENDING'\'', '\''PROCESSING'\'');
        "
    ' 2>/dev/null | tr -d '[:space:]'
  }

  branch="$(git branch --show-current 2>/dev/null || true)"
  git_state="$(git status --short 2>/dev/null || true)"
  printf 'BRANCH=%s\n' "${branch:-UNKNOWN}"
  if [ "$branch" != "$expected_branch" ] || [ -n "$git_state" ]; then
    printf 'DEPLOYMENT=ABORTED_BRANCH_OR_GIT_STATE\n%s\n' "$git_state"
    exit 1
  fi

  if ! initial_health="$(curl -fsS http://127.0.0.1:8000/health)"; then
    printf 'DEPLOYMENT=ABORTED_INITIAL_HEALTH_FAILED\n'
    exit 1
  fi
  printf 'INITIAL_HEALTH=%s\n' "$initial_health"

  database_before="$(docker compose exec -T backend alembic current 2>&1)"
  printf '%s\n' "$database_before"
  if ! printf '%s\n' "$database_before" | grep -Eq \
    '5d1a9c7e2f40|7a2c9e1f4b60|8c3d4e5f6a71'
  then
    printf 'DEPLOYMENT=ABORTED_UNEXPECTED_DATABASE_REVISION\n'
    exit 1
  fi

  jobs="$(active_jobs)"
  printf 'ACTIVE_DOWNLOAD_JOBS=%s\n' "${jobs:-UNKNOWN}"
  if ! [[ "$jobs" =~ ^[0-9]+$ ]] || [ "$jobs" -ne 0 ]; then
    printf 'DEPLOYMENT=ABORTED_ACTIVE_OR_UNKNOWN_DOWNLOADS\n'
    exit 1
  fi

  old_backend="$(docker inspect --format='{{.Image}}' mediahub-backend)" || exit 1
  old_worker="$(docker inspect --format='{{.Image}}' mediahub-worker)" || exit 1
  old_monitor="$(docker inspect --format='{{.Image}}' mediahub-monitor)" || exit 1
  old_bot="$(docker inspect --format='{{.Image}}' mediahub-bot)" || exit 1
  docker image tag "$old_backend" "$rollback_backend" || exit 1
  docker image tag "$old_worker" "$rollback_worker" || exit 1
  docker image tag "$old_monitor" "$rollback_monitor" || exit 1
  docker image tag "$old_bot" "$rollback_bot" || exit 1
  printf 'ROLLBACK_IMAGES=READY\n'

  mkdir -p backups/manual || exit 1
  umask 077
  if ! docker compose exec -T postgres sh -lc '
    exec pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" \
      --format=custom --no-owner --no-privileges
  ' > "$backup"
  then
    printf 'DEPLOYMENT=ABORTED_DATABASE_BACKUP_FAILED\n'
    exit 1
  fi
  chmod 600 "$backup"
  if [ ! -s "$backup" ] ||
     ! docker compose exec -T postgres pg_restore --list \
       < "$backup" >/dev/null 2>&1
  then
    printf 'DEPLOYMENT=ABORTED_DATABASE_BACKUP_INVALID\n'
    exit 1
  fi
  backup_sha="$(sha256sum "$backup" | cut -d' ' -f1)"
  printf 'DATABASE_BACKUP=%s\n' "$backup"
  printf 'DATABASE_BACKUP_SHA256=%s\n' "$backup_sha"

  git fetch origin "$expected_branch" || exit 1
  remote_head="$(git rev-parse "origin/${expected_branch}" 2>/dev/null || true)"
  printf 'REMOTE_HEAD=%s\n' "${remote_head:-UNKNOWN}"
  if [ "$remote_head" != "$expected_commit" ]; then
    printf 'DEPLOYMENT=ABORTED_UNEXPECTED_REMOTE_HEAD\n'
    exit 1
  fi

  git pull --ff-only origin "$expected_branch" || exit 1
  current_head="$(git rev-parse HEAD 2>/dev/null || true)"
  printf 'HEAD=%s\n' "${current_head:-UNKNOWN}"
  if [ "$current_head" != "$expected_commit" ] ||
     [ -n "$(git status --short 2>/dev/null || true)" ]
  then
    printf 'DEPLOYMENT=ABORTED_UNEXPECTED_HEAD_OR_DIRTY_TREE\n'
    exit 1
  fi

  if ! docker compose build backend bot worker monitor; then
    restore_tags || true
    printf 'DEPLOYMENT=ABORTED_BUILD_FAILED\n'
    exit 1
  fi

  candidate_heads="$(docker compose run --rm --no-deps -T backend alembic heads 2>&1)"
  printf '%s\n' "$candidate_heads"
  if ! printf '%s\n' "$candidate_heads" | grep -q "$expected_migration"; then
    restore_tags || true
    printf 'DEPLOYMENT=ABORTED_CANDIDATE_HEAD_INVALID\n'
    exit 1
  fi

  if ! docker compose stop -t 30 bot; then
    restore_tags || true
    printf 'DEPLOYMENT=ABORTED_BOT_STOP_FAILED\n'
    exit 1
  fi
  jobs="$(active_jobs)"
  printf 'ACTIVE_DOWNLOAD_JOBS_AFTER_BOT_STOP=%s\n' "${jobs:-UNKNOWN}"
  if ! [[ "$jobs" =~ ^[0-9]+$ ]] || [ "$jobs" -ne 0 ]; then
    docker compose start bot >/dev/null 2>&1 || true
    restore_tags || true
    printf 'DEPLOYMENT=ABORTED_ACTIVE_OR_UNKNOWN_DOWNLOADS\n'
    exit 1
  fi

  if ! docker compose stop -t 120 worker monitor backend; then
    rollback_runtime || true
    printf 'DEPLOYMENT=ABORTED_SERVICE_STOP_FAILED\n'
    exit 1
  fi

  printf 'MIGRATION=STARTING TIMEOUT_SECONDS=600\n'
  migration_status=0
  timeout --signal=TERM --kill-after=30s 600s \
    docker compose run --name "$migration_container" --rm --no-deps -T \
      backend alembic upgrade head || migration_status=$?
  printf 'MIGRATION_COMMAND_STATUS=%s\n' "$migration_status"
  if [ "$(docker inspect --format='{{.State.Status}}' "$migration_container" 2>/dev/null || true)" = "running" ]; then
    docker stop --time 30 "$migration_container" >/dev/null 2>&1 || true
  fi

  database_after="$(
    timeout --signal=TERM --kill-after=15s 120s \
      docker compose run --rm --no-deps -T backend alembic current 2>&1
  )"
  printf '%s\n' "$database_after"
  if ! printf '%s\n' "$database_after" | grep -q "$expected_migration"; then
    rollback_runtime || true
    printf 'DEPLOYMENT=ABORTED_DATABASE_NOT_AT_EXPECTED_HEAD\n'
    exit 1
  fi
  [ "$migration_status" -eq 124 ] && printf 'MIGRATION=COMMITTED_BEFORE_TIMEOUT\n'

  if ! docker compose up -d --no-deps --force-recreate backend; then
    rollback_runtime || true
    printf 'DEPLOYMENT=ABORTED_BACKEND_RECREATE_FAILED\n'
    exit 1
  fi

  backend_ready=0
  for attempt in $(seq 1 40); do
    if curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
      backend_ready=1
      break
    fi
    sleep 2
  done
  if [ "$backend_ready" -ne 1 ]; then
    docker compose logs --tail=160 backend
    rollback_runtime || true
    printf 'DEPLOYMENT=ABORTED_NEW_BACKEND_UNHEALTHY\n'
    exit 1
  fi
  printf 'BACKEND_HEALTH=OK\n'

  if ! docker compose run --rm --no-deps -T bot python -c '
import asyncio
from app.services.backend import get_bot_configuration
async def verify():
    for language in ("fa", "en"):
        result = await get_bot_configuration(language)
        assert result["language"] == language
        assert result["content"] and result["buttons"]
asyncio.run(verify())
print("BOT_CONFIGURATION_API=OK")
'; then
    rollback_runtime || true
    printf 'DEPLOYMENT=ABORTED_CONFIGURATION_SMOKE_FAILED\n'
    exit 1
  fi

  if ! docker compose up -d --no-deps --force-recreate worker monitor bot; then
    rollback_runtime || true
    printf 'DEPLOYMENT=ABORTED_DEPENDENT_SERVICES_FAILED\n'
    exit 1
  fi

  sleep 15
  deployment_ok=1
  for service in backend worker monitor bot; do
    status="$(docker inspect --format='{{.State.Status}}' "mediahub-${service}" 2>/dev/null || printf 'missing')"
    restarts="$(docker inspect --format='{{.RestartCount}}' "mediahub-${service}" 2>/dev/null || printf 'unknown')"
    printf '%-25s STATUS=%-12s RESTARTS=%s\n' "mediahub-${service}" "$status" "$restarts"
    [ "$status" = "running" ] && [ "$restarts" = "0" ] || deployment_ok=0
  done

  final_database="$(docker compose exec -T backend alembic current 2>&1)"
  printf '%s\n' "$final_database"
  printf '%s\n' "$final_database" | grep -q "$expected_migration" || deployment_ok=0
  if final_health="$(curl -fsS http://127.0.0.1:8000/health)"; then
    printf 'HEALTH_RESPONSE=%s\n' "$final_health"
  else
    deployment_ok=0
  fi

  docker compose logs --since="$started_at" --tail=500 \
    backend worker monitor bot > "$verify_log" 2>&1
  chmod 600 "$verify_log"
  fatal_count="$(grep -Eic 'Traceback|CRITICAL|FATAL' "$verify_log" 2>/dev/null || true)"
  printf 'FATAL_LOG_MATCHES=%s\n' "${fatal_count:-0}"
  printf 'VERIFY_LOG=%s\n' "$verify_log"
  [ "${fatal_count:-0}" = "0" ] || deployment_ok=0

  final_head="$(git rev-parse HEAD 2>/dev/null || true)"
  git_state="$(git status --short 2>/dev/null || true)"
  printf 'FINAL_HEAD=%s\n' "$final_head"
  printf 'GIT_STATUS=%s\n' "${git_state:-CLEAN}"
  [ "$final_head" = "$expected_commit" ] && [ -z "$git_state" ] || deployment_ok=0
  docker compose ps
  printf 'ROLLBACK_BACKEND=%s\n' "$rollback_backend"
  printf 'ROLLBACK_WORKER=%s\n' "$rollback_worker"
  printf 'ROLLBACK_MONITOR=%s\n' "$rollback_monitor"
  printf 'ROLLBACK_BOT=%s\n' "$rollback_bot"

  if [ "$deployment_ok" -eq 1 ]; then
    printf 'DEPLOYMENT=OK\n'
    exit 0
  fi
  rollback_runtime || true
  printf 'DEPLOYMENT=ROLLED_BACK_NEEDS_REVIEW\n'
  exit 1
)
