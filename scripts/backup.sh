#!/usr/bin/env bash
set -Eeuo pipefail
cd "${MEDIAHUB_DIR:-/opt/mediahub-ai}"
command="${1:-create}"
case "$command" in
  create|list) docker compose run --rm --no-deps -T backup "$command" ;;
  verify)
    [[ "${2:-}" =~ ^[0-9a-f]{32}$ ]] || { printf 'Provide a valid backup id.\n'; exit 1; }
    docker compose run --rm --no-deps -T backup verify "$2"
    ;;
  *) printf 'Usage: bash scripts/backup.sh [create|list|verify <id>]\n'; exit 1 ;;
esac
