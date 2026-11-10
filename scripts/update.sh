#!/usr/bin/env bash
set -Eeuo pipefail
repo="${MEDIAHUB_DIR:-/opt/mediahub-ai}"
exec python3 "$repo/scripts/mediahub.py" update "$@"
