#!/usr/bin/env bash
# Download this bootstrap from the SAME pinned tag/commit passed below.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n'; exit 1; }
[[ -t 0 ]] || exec </dev/tty
version="${1:-}"
[[ "$version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$ || "$version" =~ ^[a-f0-9]{40}$ ]] || {
  printf 'Usage: sudo bash install.sh <v1.0.0 or verified full candidate SHA>\n'; exit 1;
}
destination="${MEDIAHUB_DIR:-/opt/mediahub-ai}"
[[ ! -e "$destination" ]] || { printf 'Destination exists. Use its scripts/install.sh or sudo mediahub.\n'; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates git
git clone --no-checkout https://github.com/ataamini-bot/mediahub-ai.git "$destination"
git -C "$destination" checkout --detach "$version"
action="${2:-menu}"
[[ "$action" == menu || "$action" == install || "$action" == import ]] || exit 1
exec bash "$destination/scripts/install.sh" "$action"
