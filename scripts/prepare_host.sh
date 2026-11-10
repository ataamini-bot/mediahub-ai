#!/usr/bin/env bash
# Called only by install/bootstrap, never during a normal update.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { printf 'Run with sudo.\n'; exit 1; }
source /etc/os-release
[[ "$ID" == ubuntu && "$VERSION_ID" =~ ^(22\.04|24\.04|26\.04)$ ]] || {
  printf 'Supported: Ubuntu Server 22.04 / 24.04 / 26.04 LTS.\n'; exit 1;
}
export DEBIAN_FRONTEND=noninteractive
[[ "$(dpkg --print-architecture)" == amd64 ]] || { printf 'V1 release images currently target amd64.\n'; exit 1; }
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git python3 python3-dotenv
if ! command -v docker >/dev/null 2>&1; then
  # Do not remove an existing container runtime or its data automatically.
  for package in docker.io docker-compose podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      printf 'Existing runtime package %s: migrate it before installing Docker CE.\n' "$package"; exit 1
    fi
  done
  install -m 0755 -d /etc/apt/keyrings
  curl --proto '=https' --tlsv1.2 -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod 644 /etc/apt/keyrings/docker.asc
  cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version >/dev/null || {
  printf 'Docker is installed but the Compose plugin is missing. Install docker-compose-plugin.\n'; exit 1;
}
systemctl enable --now docker
docker info >/dev/null
