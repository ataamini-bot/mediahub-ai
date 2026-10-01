#!/usr/bin/env bash
set -Eeuo pipefail
repo="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export MEDIAHUB_DIR="$repo"
bash "$repo/scripts/prepare_host.sh"
exec python3 "$repo/scripts/mediahub.py" "${1:-menu}"
