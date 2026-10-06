#!/usr/bin/env bash
# Manage Lark's sandbox container. Run as root on the server.
#   sandbox.sh up      build the image, create network, volume and container, print env for Lark
#   sandbox.sh reset   wipe /home/lark and rebuild the container from the image (keeps the token)
#   sandbox.sh nuke    remove container, volume and network
#   sandbox.sh status
# Needs Docker. The container has no access to the host or its other services (see sandbox-firewall.sh).
set -euo pipefail
cd "$(dirname "$0")"

NAME=lark-sandbox
NET=lark-sandbox
VOL=lark-sandbox-home
IMG=lark-sandbox:latest
SUBNET=172.30.0.0/24
PORT=8791
ENV_FILE=${LARK_ENV_FILE:-/etc/lark/lark.env}
MEM=${SANDBOX_MEM:-768m}
CPUS=${SANDBOX_CPUS:-1}
PIDS=${SANDBOX_PIDS:-256}

token() {
  local t
  t=$(grep -s '^LARK_SANDBOX_TOKEN=' "$ENV_FILE" | cut -d= -f2- || true)
  if [ -z "$t" ]; then t=$(openssl rand -hex 24); fi
  echo "$t"
}

set_env() { # key value
  touch "$ENV_FILE"; chmod 600 "$ENV_FILE"
  grep -v "^$1=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
  echo "$1=$2" >> "$ENV_FILE.tmp"
  cat "$ENV_FILE.tmp" > "$ENV_FILE"; rm -f "$ENV_FILE.tmp"
}

create() {
  local tok="$1"
  docker network inspect "$NET" >/dev/null 2>&1 || docker network create --subnet "$SUBNET" "$NET" >/dev/null
  docker volume inspect "$VOL" >/dev/null 2>&1 || docker volume create "$VOL" >/dev/null
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run -d --name "$NAME" --restart unless-stopped \
    --network "$NET" -p 127.0.0.1:$PORT:$PORT \
    --memory "$MEM" --memory-swap "$MEM" --cpus "$CPUS" --pids-limit "$PIDS" \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add KILL \
    --security-opt no-new-privileges \
    -e SANDBOX_TOKEN="$tok" -v "$VOL":/home/lark "$IMG" >/dev/null
}

case "${1:-}" in
  up)
    command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }
    docker build -t "$IMG" . 
    tok=$(token)
    create "$tok"
    set_env LARK_SANDBOX_URL "http://127.0.0.1:$PORT"
    set_env LARK_SANDBOX_TOKEN "$tok"
    echo "Sandbox is up. Restart Lark to pick up the settings:  systemctl restart lark"
    echo "Next, install the firewall rules:  ./sandbox-firewall.sh install"
    ;;
  reset)
    tok=$(token)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOL" >/dev/null 2>&1 || true
    create "$tok"
    echo "Sandbox reset."
    ;;
  nuke)
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker volume rm "$VOL" >/dev/null 2>&1 || true
    docker network rm "$NET" >/dev/null 2>&1 || true
    echo "Removed. Delete LARK_SANDBOX_* from $ENV_FILE to turn the tools off."
    ;;
  status) docker ps -a --filter "name=$NAME" ;;
  *) sed -n '2,7p' "$0"; exit 1 ;;
esac
