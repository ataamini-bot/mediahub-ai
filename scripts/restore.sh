#!/usr/bin/env bash
set -Eeuo pipefail
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"
backup_id="${1:-}"
[[ "$backup_id" =~ ^[0-9a-f]{32}$ && -t 0 ]] || { printf 'Usage (interactive terminal): bash scripts/restore.sh <backup-id>\n'; exit 1; }
database="$(docker compose exec -T postgres printenv POSTGRES_DB | tr -d '\r\n')"
printf 'Restore replaces database %s with snapshot %s.\n' "$database" "$backup_id"
printf 'Applications will stop. Transient queues/FSM will be cleared; unfinished restored jobs will be cancelled.\n'
printf 'An emergency backup and the previous database are retained. Type VERIFY to continue: '
read -r first
[[ "$first" == VERIFY ]] || exit 1
docker compose run --rm --no-deps -T backup verify "$backup_id"
printf 'Isolated restore passed. Type RESTORE:%s to replace the live database: ' "$database"
read -r second
[[ "$second" == "RESTORE:$database" ]] || exit 1
# Refuse to clear a shared or remote Redis instance.
docker compose run --rm --no-deps -T backend python - <<'PY'
from urllib.parse import urlparse
from app.core.config import settings
url = urlparse(settings.redis_url)
assert url.hostname == "redis" and url.path in ("", "/", "/0"), "Restore requires the dedicated Compose Redis DB 0"
PY
active="$(docker compose exec -T postgres sh -c 'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM download_jobs WHERE status IN ('\''PENDING'\'', '\''PROCESSING'\'', '\''PAUSED'\'');"' | tr -d '[:space:]')"
[[ "$active" == 0 ]] || { printf 'Finish/cancel active or paused downloads before recovery.\n'; exit 1; }
docker compose stop bot worker backend monitor backup
trap 'printf "RESTORE=INTERRUPTED Keep applications stopped and inspect the last error.\n"' ERR
docker compose run --rm --no-deps -T backup restore "$backup_id" --confirm "$second"
docker compose run --rm --no-deps -T backend alembic upgrade head
docker compose run --rm --no-deps -T backend python - <<'PY'
import asyncio
from sqlalchemy import update
from app.db.session import AsyncSessionLocal, engine
from app.models.download_job import DownloadJob, DownloadJobStatus
from app.services.audit import AuditService
async def reconcile():
    async with AsyncSessionLocal() as db:
        result = await db.execute(update(DownloadJob).where(DownloadJob.status.in_([
            DownloadJobStatus.PENDING, DownloadJobStatus.PROCESSING, DownloadJobStatus.PAUSED
        ])).values(status=DownloadJobStatus.CANCELLED))
        AuditService(db).record(action="system.database_restored", target_type="database",
            details={"unfinished_jobs_cancelled": result.rowcount})
        await db.commit()
    await engine.dispose()
asyncio.run(reconcile())
PY
docker compose exec -T redis redis-cli FLUSHDB
docker compose up -d --no-deps backend worker backup monitor
healthy=0
for attempt in $(seq 1 45); do
  if curl --max-time 3 -fsS http://127.0.0.1:8000/health >/dev/null 2>&1; then healthy=1; break; fi
  sleep 2
done
[[ "$healthy" == 1 ]]
docker compose up -d --no-deps bot
printf 'RESTORE=OK\nRun: bash scripts/check_launch.sh\n'
