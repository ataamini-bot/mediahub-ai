#!/usr/bin/env bash
# Read-only aggregate report. No Telegram IDs, URLs, tokens or receipts printed.
set -Eeuo pipefail
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"
printf 'HEAD=%s\n' "$(git rev-parse HEAD)"
docker compose ps
curl --max-time 5 -fsS http://127.0.0.1:8000/health >/dev/null
printf 'BACKEND_HTTP=OK\n'
docker compose exec -T backend python -m app.services.youtube_health
docker compose exec -T backup /opt/backup-venv/bin/python -m app.backup.engine health
printf 'BACKUP_PROCESS=OK\n'
docker compose exec -T backend python - <<'PY'
import asyncio
import json
from sqlalchemy import text
from app.db.session import engine
from app.monitoring.resources import backup_status
async def report():
    async with engine.connect() as conn:
        rows = await conn.execute(text("""SELECT status::text, count(*) FROM download_jobs
            WHERE created_at >= now() - interval '24 hours' GROUP BY status"""))
        print("DOWNLOADS_24H=" + json.dumps(dict(rows.all())))
        pending = await conn.scalar(text("SELECT count(*) FROM payments WHERE status::text='PENDING'"))
        print(f"PAYMENTS_PENDING={pending}")
        print("MIGRATION=" + str(await conn.scalar(text("SELECT version_num FROM alembic_version"))))
        cleanup = await conn.scalar(text("""SELECT count(*) FROM download_jobs WHERE files_removed_at IS NULL
            AND (status::text IN ('FAILED','CANCELLED','EXPIRED') OR
            (status::text='COMPLETED' AND (delivered_at IS NOT NULL OR coalesce(completed_at,created_at) < now()-interval '24 hours')))"""))
        print(f"MEDIA_FILES_PENDING_CLEANUP={cleanup}")
    await engine.dispose()
asyncio.run(report())
status = backup_status()
print("BACKUP_STATUS=" + json.dumps(status))
assert status["healthy"], "Backup needs attention"
print("SERVER_CHECK=OK LIVE_ACCEPTANCE=NOT_CHECKED")
PY
