#!/usr/bin/env bash
# Install from the running c8a94d6 release, including an already-pulled checkout.
set -Eeuo pipefail
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"
base=c8a94d6dd84b145f1666091dcef5bf31bba4fd3e
prepared=f6de11031c88b9848f5ffcb806eb686fb99e1793
launched=7d9dd72b85bd74086c6b80ef6c1300a9f86832ae
channel_languages=f52fb5be34a46ecc4c6d5a3a66fd0b4f86d6c928
private_payments=506c5989a138785a16a55cc08fdb9588c836b7ca
broadcasts=0322acc644817859c737abbd165421218a488ab8
internal_credit=88ffc9ac64b378d0bf0869967629c0c6314e486b
media_outputs=2b5be6e8b3173d548974ae3ac404560e67a36375
coupons=a0800c62a3a7dc4f817f7a27a91178c6f37ef692
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
[[ "$original_head" == "$base" || "$original_head" == "$prepared" || "$original_head" == "$launched" || "$original_head" == "$channel_languages" || "$original_head" == "$private_payments" || "$original_head" == "$broadcasts" || "$original_head" == "$internal_credit" || "$original_head" == "$media_outputs" || "$original_head" == "$coupons" || "$original_head" == "$target" ]] || {
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
  [[ "$(docker inspect --format '{{.State.Running}}' "mediahub-$service")" == true ]] || {
    printf 'DEPLOYMENT=ABORTED_SERVICE_NOT_RUNNING SERVICE=%s\n' "$service"; exit 1;
  }
  old_images[$service]="$(docker inspect --format '{{.Image}}' "mediahub-$service")"
  docker image tag "${old_images[$service]}" "mediahub-ai-$service:rollback-$release_time"
done
changed=0
stopped=0
bot_stopped=0
rollback() {
  local code=$?
  local recovery_ok=1
  trap - EXIT
  if [[ "$code" -ne 0 && "$changed" -eq 1 ]]; then
    # Keep the original services running on build/compile failures. Once cutover
    # has started, stop all new writers before restoring the old image tags.
    if [[ "$stopped" -eq 1 ]]; then
      docker compose stop backend bot worker monitor backup || recovery_ok=0
    fi
    git reset --keep "$original_head" || recovery_ok=0
    for service in "${services[@]}"; do
      docker image tag "${old_images[$service]}" "${image_names[$service]}" || recovery_ok=0
    done
    if [[ "$stopped" -eq 1 && "$recovery_ok" -eq 1 ]]; then
      docker compose up -d --no-deps --no-build --pull never --force-recreate "${services[@]}" || recovery_ok=0
    elif [[ "$bot_stopped" -eq 1 && "$stopped" -eq 0 ]]; then
      # Resume the existing bot container without recreating it before cutover.
      docker compose start bot || recovery_ok=0
    fi
    if [[ "$recovery_ok" -eq 1 ]]; then
      printf 'ROLLBACK=ATTEMPTED DATABASE=NOT_DOWNGRADED\n'
    else
      printf 'ROLLBACK=INCOMPLETE_MANUAL_RECOVERY_REQUIRED DATABASE=NOT_DOWNGRADED\n'
    fi
    printf 'DEPLOYMENT=FAILED\n'
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
        assert await conn.scalar(text("SELECT version_num FROM alembic_version")) == "a06b7c8d9e0f"
        await conn.execute(text("SELECT id, source_url, files_removed_at FROM download_jobs LIMIT 0"))
        await conn.execute(text("SELECT code, currency, version FROM coupons LIMIT 0"))
        await conn.execute(text("SELECT status, snapshot FROM coupon_uses LIMIT 0"))
        await conn.execute(text("SELECT payload_hash FROM coupon_actions LIMIT 0"))
        await conn.execute(text("SELECT discount_snapshot FROM payments LIMIT 0"))
        await conn.execute(text("SELECT balance, version FROM credit_accounts LIMIT 0"))
        await conn.execute(text("SELECT delta, balance_after FROM credit_entries LIMIT 0"))
        assert await conn.scalar(text("SELECT count(*) FROM pg_trigger WHERE tgname='credit_entries_append_only' AND tgenabled='O'")) == 1
        await conn.execute(text("SELECT status, confirmation_token FROM broadcasts LIMIT 0"))
        await conn.execute(text("SELECT claim_token, lease_until FROM broadcast_recipients LIMIT 0"))
        await conn.execute(text("SELECT language FROM required_channels LIMIT 0"))
        await conn.execute(text("SELECT quota_reset_at, conversion_quota_reset_at FROM users LIMIT 0"))
        await conn.execute(text("SELECT request_id FROM customer_actions LIMIT 0"))
        assert await conn.scalar(text("SELECT count(*) FROM application_settings WHERE category='backups'")) >= 5
    await engine.dispose()
asyncio.run(check())
print("LAUNCH_SCHEMA=OK")
PY
docker compose up -d --no-deps --no-build --pull never --force-recreate backend worker backup monitor
healthy=0
for attempt in $(seq 1 45); do
  if curl --max-time 3 -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then
    healthy=1
    break
  fi
  sleep 2
done
[[ "$healthy" -eq 1 ]]
docker compose up -d --no-deps --no-build --pull never --force-recreate bot
sleep 5
for service in backend bot worker monitor backup; do
  status="$(docker inspect --format '{{.State.Status}}' "mediahub-$service")"
  restarts="$(docker inspect --format '{{.RestartCount}}' "mediahub-$service")"
  printf '%s_STATUS=%s RESTARTS=%s\n' "$service" "$status" "$restarts"
  [[ "$status" == running && "$restarts" -eq 0 ]]
done
docker compose exec -T backup /opt/backup-venv/bin/python -m app.backup.engine health
for service in backend bot worker monitor backup; do
  docker image tag "$(docker inspect --format '{{.Image}}' "mediahub-$service")" "mediahub-ai-$service:release-${target:0:12}"
done
printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\nPILOT_STATUS=AWAITING_LIVE_TESTS\n' "$(git rev-parse HEAD)"
