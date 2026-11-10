#!/usr/bin/env bash
set -Eeuo pipefail

cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"

expected_base="15e322f6e42ebb053661199a6dba4a98d696e5c5"
expected_branch="feature/admin-foundation"
target_commit="${1:-}"

if ! [[ "$target_commit" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'USAGE: bash scripts/deploy_usdt_network_selection.sh <full-target-commit>\n'
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
    . ':!backend/**' ':!bot/**' ':!docs/**' ':!scripts/**'
)"
if [ -n "$unexpected_files" ]; then
  printf 'DEPLOYMENT=ABORTED_UNEXPECTED_FILES\n%s\n' "$unexpected_files"
  exit 1
fi
if git diff --name-only "$expected_base" "$target_commit" -- \
   backend/alembic | grep -q .; then
  printf 'DEPLOYMENT=ABORTED_UNEXPECTED_MIGRATION\n'
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
old_backend_image="$(docker inspect --format='{{.Image}}' mediahub-backend)"
old_bot_image="$(docker inspect --format='{{.Image}}' mediahub-bot)"
release_id="$(date -u +%Y%m%dT%H%M%SZ)"
rollback_backend="mediahub-ai-backend:rollback-usdt-network-selection-${release_id}"
rollback_bot="mediahub-ai-bot:rollback-usdt-network-selection-${release_id}"
docker image tag "$old_backend_image" "$rollback_backend"
docker image tag "$old_bot_image" "$rollback_bot"
printf 'ROLLBACK_BACKEND=%s\nROLLBACK_BOT=%s\n' \
  "$rollback_backend" "$rollback_bot"

backend_touched=0
bot_touched=0
rollback_on_error() {
  local status=$?
  trap - EXIT
  if [ "$status" -ne 0 ]; then
    docker image tag "$old_backend_image" mediahub-ai-backend:latest || true
    docker image tag "$old_bot_image" mediahub-ai-bot:latest || true
    if [ "$backend_touched" -eq 1 ]; then
      docker compose up -d --no-deps --force-recreate backend || true
    fi
    if [ "$bot_touched" -eq 1 ]; then
      docker compose up -d --no-deps --force-recreate bot || true
    fi
    printf 'DEPLOYMENT=FAILED SOURCE_HEAD=%s\n' "$(git rev-parse HEAD)"
  fi
  exit "$status"
}
trap rollback_on_error EXIT

git merge --ff-only "$target_commit"
docker compose build backend bot

docker compose run --rm --no-deps -T backend python - <<'PY'
from app.schemas.payment import PaymentConfigurationResponse
from app.services.payment_management import PaymentManagementService

assert "destinations" in PaymentConfigurationResponse.model_fields
assert hasattr(PaymentManagementService, "list_active_usdt_destinations")
print("BACKEND_USDT_SELECTION_CODE=OK")
PY

docker compose run --rm --no-deps -T bot python - <<'PY'
from app.keyboards.payment import build_usdt_destination_keyboard
from app.state.payment import PaymentStates

keyboard = build_usdt_destination_keyboard([
    {"id": 11, "network_name": "TRON", "network_code": "TRC20"},
    {"id": 12, "network_name": "Ethereum", "network_code": "ERC20"},
])
callbacks = [row[0].callback_data for row in keyboard.inline_keyboard[:2]]
assert callbacks == [
    "payment:usdt-destination:11",
    "payment:usdt-destination:12",
]
assert PaymentStates.selecting_usdt_destination.state
print("BOT_USDT_SELECTION_CODE=OK")
PY

require_no_active_jobs
bot_touched=1
docker compose stop bot
require_no_active_jobs

backend_touched=1
backend_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate backend
for _ in $(seq 1 30); do
  if curl --max-time 5 -fsS http://127.0.0.1:8000/health >/dev/null; then
    break
  fi
  sleep 2
done
curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null

docker compose run --rm --no-deps -T bot python - <<'PY'
import asyncio

from app.services.backend import get_payment_configuration

configuration = asyncio.run(
    get_payment_configuration(select_destination=True, language="en")
)
destinations = configuration.get("destinations") or []
assert configuration.get("destination") is None
assert destinations
assert all(item.get("type") == "usdt" for item in destinations)
assert all(item.get("id") and item.get("network_code") for item in destinations)
print(f"ACTIVE_USDT_DESTINATIONS={len(destinations)}")
PY

bot_started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
docker compose up -d --no-deps --force-recreate bot
sleep 10

backend_status="$(docker inspect --format='{{.State.Status}}' mediahub-backend)"
backend_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-backend)"
bot_status="$(docker inspect --format='{{.State.Status}}' mediahub-bot)"
bot_restarts="$(docker inspect --format='{{.RestartCount}}' mediahub-bot)"
printf 'BACKEND_STATUS=%s RESTARTS=%s\n' "$backend_status" "$backend_restarts"
printf 'BOT_STATUS=%s RESTARTS=%s\n' "$bot_status" "$bot_restarts"
[ "$backend_status" = "running" ] && [ "$backend_restarts" = "0" ]
[ "$bot_status" = "running" ] && [ "$bot_restarts" = "0" ]

if docker compose logs --since="$backend_started_at" --tail=200 backend |
   grep -Eq 'Traceback \(most recent call last\)|ERROR:.*Application startup failed'; then
  printf 'BACKEND_LOG_CHECK=FAILED\n'
  exit 1
fi
if docker compose logs --since="$bot_started_at" --tail=200 bot |
   grep -Eq 'Traceback \(most recent call last\)|TelegramConflictError|TelegramUnauthorizedError'; then
  printf 'BOT_LOG_CHECK=FAILED\n'
  exit 1
fi

curl --max-time 10 -fsS http://127.0.0.1:8000/health >/dev/null
[ "$(git rev-parse HEAD)" = "$target_commit" ]
[ -z "$(git status --porcelain)" ]

printf 'FINAL_HEAD=%s\nDEPLOYMENT=OK\n' "$(git rev-parse HEAD)"
trap - EXIT
