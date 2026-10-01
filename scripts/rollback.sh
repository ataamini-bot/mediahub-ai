#!/usr/bin/env bash
set -Eeuo pipefail
exec python3 "${MEDIAHUB_DIR:-/opt/mediahub-ai}/scripts/mediahub.py" rollback
