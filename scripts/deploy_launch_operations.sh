#!/usr/bin/env bash
# Install from the running c8a94d6 release, including an already-pulled checkout.
set -Eeuo pipefail
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"
base=c8a94d6dd84b145f1666091dcef5bf31bba4fd3e
prepared=f6de11031c88b9848f5ffcb806eb686fb99e1793
branch=feature/admin-foundation
target="${1:-}"
[[ "$target" =~ ^[0-9a-f]{40}$ ]] || { printf 'Provide the full release commit.\n'; exit 1; }
original_head="$(git rev-parse HEAD)"
[[ "$(git branch --show-current)" == "$branch" ]] || {
  printf 'DEPLOYMENT=ABORTED_UNEXPECTED_BRANCH\n'; exit 1;
}
[[ -z "$(git status --porcelain)" ]] || {
  printf 'DEPLOYMENT=ABORTED_LOCAL_CHANGES\n'; exit 1;
}
[[ "$original_head" == "$base" || "$original_head" == "$prepared" || "$original_head" == "$target" ]] || {
  printf 'DEPLOYMENT=ABORTED_UNEXPECTED_HEAD HEAD=%s\n' "$original_head"; exit 1;
}
printf 'SOURCE_HEAD=%s TARGET_HEAD=%s\n' "$original_head" "$target"
[[ -f .env ]] || { printf 'DEPLOYMENT=ABORTED_MISSING_ENV\n'; exit 1; }
git fetch origin "$branch"
[[ "$(git rev-parse FETCH_HEAD)" == "$target" ]] || { printf 'DEPLOYMENT=ABORTED_REMOTE_CHANGED\n'; exit 1; }
git merge-base --is-ancestor "$base" "$target"
git merge-base --is-ancestor "$original_head" "$target"
curl --max-time 10 --retry 2 -fsS http://127.0.0.1:8000/health >/dev/null

active_jobs() {
  docker compose exec -T postgres sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM download_jobs WHERE status IN ('\''PENDING'\'', '\''PROCESSING'\'', '\''PAUSED'\'');"' | tr -d '[:space:]'
}
require_idle() {
  local count
  count="$(active_jobs)"
  printf 'ACTIVE_OR_PAUSED_DOWNLOAD_JOBS=%s\n' "$count"
  [[ "$count" =~ ^[0-9]+$ && "$count" -eq 0 ]]
}
require_idle
services=(backend bot worker monitor)
if [[ "$(docker inspect --format '{{.State.Running}}' mediahub-backup 2>/dev/null || true)" == true ]]; then
  services+=(backup)
fi
declare -A old_images image_names
release_time="$(date -u +%Y%m%dT%H%M%SZ)"
# Config.Image may contain an immutable sha256 ID. Restore the actual Compose
# image tags, not that container field, before recreating any service.
compose_images="$(docker compose config --format json | python3 -c '
import json, sys
config = json.load(sys.stdin)
for service in sys.argv[1:]:
    image = config["services"][service].get("image") or (config["name"] + "-" + service)
    if image.startswith("sha256:") or "@" in image or any(c.isspace() for c in image):
        raise SystemExit("DEPLOYMENT=ABORTED_NON_TAGGABLE_COMPOSE_IMAGE")
    print(service + " " + image)
' "${services[@]}")"
while read -r service image_name; do
  image_names[$service]="$image_name"
done <<< "$compose_images"
for service in "${services[@]}"; do
  old_images[$service]="$(docker inspect --format '{{.Image}}' "mediahub-$service")"
  docker image tag "${old_images[$service]}" "mediahub-ai-$service:rollback-$release_time"
done
changed=0
stopped=0
bot_stopped=0
rollback() {
  local code=$?
  trap - EXIT
  if [[ "$code" -ne 0 && "$changed" -eq 1 ]]; then
    if [[ "$stopped" -eq 1 ]]; then
      docker compose stop backup >/dev/null 2>&1 || true
    fi
    git reset --keep "$original_head" || true
    for service in "${services[@]}"; do
      docker image tag "${old_images[$service]}" "${image_names[$service]}" || true
    done
    if [[ "$stopped" -eq 1 ]]; then
      docker compose up -d --no-deps --no-build --pull never --force-recreate "${services[@]}" || true
    elif [[ "$bot_stopped" -eq 1 ]]; then
      docker compose up -d --no-deps --no-build --pull never bot || true
    fi
    printf 'ROLLBACK=ATTEMPTED DATABASE=NOT_DOWNGRADED\nDEPLOYMENT=FAILED\n'
  fi
  exit "$code"
}
trap rollback EXIT
git merge --ff-only "$target"
changed=1
mkdir -p backups secrets
chmod 700 backups secrets
chmod 600 .env
docker compose build backend bot worker monitor backup
docker compose run --rm --no-deps -T backend python -m compileall -q app
docker compose run --rm --no-deps -T bot python -m compileall -q app
require_idle
bot_stopped=1
docker compose stop bot
require_idle
stopped=1
docker compose stop worker backend monitor backup

# This runs before any schema change. The same snapshot is restored into a
# disposable database and every table's row count is compared.
backup_result="$(docker compose run --rm --no-deps -T backup create)"
backup_id="$(printf '%s' "$backup_result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')"
[[ "$backup_id" =~ ^[0-9a-f]{32}$ ]]
printf 'PRE_MIGRATION_BACKUP=%s\n' "$backup_id"
docker compose run --rm --no-deps -T backup verify "$backup_id"
printf 'BACKUP_RESTORE_DRILL=OK\n'

docker compose run --rm --no-deps -T backend alembic upgrade head
docker compose run --rm --no-deps -T backend python - <<'PY'
import asyncio
from sqlalchemy import text
from app.db.session import engine
async def check():
    async with engine.connect() as conn:
        assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "fb1c2d3e4f5a"
        await conn.execute(text("SELECT quota_reset_at, conversion_quota_reset_at FROM users LIMIT 0"))
        await conn.execute(text("SELECT request_id FROM customer_actions LIMIT 0"))
        assert await conn.scalar(text("SELECT count(*) FROM application_settings WHERE category='backups'")) >= 5
    await engine.dispose()
asyncio.run(check())
print("LAUNCH_SCHEMA=OK")
PY
docker compose up -d --no-deps --force-recreate backend worker backup monitor
healthy=0
for attempt in $(seq 1 45); do
  if curl --max-time 3 -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done
[[ "$healthy" -eq 1 ]]
docker compose up -d --no-deps --force-recreate bot
sleep 5
for service in backend bot worker monitor backup; do
  status="$(docker inspect --format '{{.State.Status}}' "mediahub-$service")"
  restarts="$(docker inspect --format '{{.RestartCount}}' "mediahub-$service")"
  printf '%s_STATUS=%s RESTARTS=%s\n' "$service" "$status" "$restarts"
  [[ "$status" == running && "$restarts" -eq 0 ]]
done
docker compose exec -T backup /opt/backup-venv/bin/python -m app.backup.engine health
for service in "${services[@]}"; do
  docker image tag "$(docker inspect --format '{{.Image}}' "mediahub-$service")" "mediahub-ai-$service:release-${target:0:12}"
done
printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\nPILOT_STATUS=AWAITING_LIVE_TESTS\n' "$(git rev-parse HEAD)"
