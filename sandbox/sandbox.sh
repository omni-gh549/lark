#!/usr/bin/env bash
# Manage Lark's sandbox container. Run as root on the server.
#   sandbox.sh up      build the image, create network, volume and container, print env for Lark
#   sandbox.sh reset   wipe /home/lark and rebuild the container from the image (keeps the token)
#   sandbox.sh nuke    remove container, volume and network
#   sandbox.sh install-reset   let Lark's Settings page trigger `reset` (done by `up` too)
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

# Limits come from the environment, else from the env file (so a reset keeps them), else these defaults.
cfg() { # NAME default
  local v="${!1:-}"
  [ -n "$v" ] || v=$(grep -s "^$1=" "$ENV_FILE" | cut -d= -f2- || true)
  echo "${v:-$2}"
}
MEM=$(cfg SANDBOX_MEM 1g)
SWAP=$(cfg SANDBOX_SWAP "$MEM")   # total memory + swap; equal to MEM means no swap
CPUS=$(cfg SANDBOX_CPUS 1)
PIDS=$(cfg SANDBOX_PIDS 256)

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
    --shm-size 256m --memory "$MEM" --memory-swap "$SWAP" --cpus "$CPUS" --pids-limit "$PIDS" \
    --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --cap-add FOWNER --cap-add SETUID --cap-add SETGID --cap-add KILL \
    --security-opt no-new-privileges \
    -e SANDBOX_TOKEN="$tok" -v "$VOL":/home/lark "$IMG" >/dev/null
}

data_dir() {
  local d
  d=$(grep -s '^LARK_DATA=' "$ENV_FILE" | cut -d= -f2- || true)
  echo "${d:-/var/lib/lark}"
}

# Lark runs unprivileged and can't touch Docker. Instead it drops a request file in its data directory and
# a root-owned systemd path unit runs `sandbox.sh reset` when the file appears.
install_reset() {
  local req here
  req="$(data_dir)/sandbox-reset-request"
  here=$(readlink -f "$0")
  cat > /etc/systemd/system/lark-sandbox-reset.service <<UNIT
[Unit]
Description=Reset the Lark sandbox

[Service]
Type=oneshot
ExecStart=$here reset
ExecStopPost=/bin/rm -f $req
UNIT
  cat > /etc/systemd/system/lark-sandbox-reset.path <<UNIT
[Unit]
Description=Watch for Lark sandbox reset requests

[Path]
PathExists=$req

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now lark-sandbox-reset.path >/dev/null
  set_env LARK_SANDBOX_RESET_FILE "$req"
}

case "${1:-}" in
  up)
    command -v docker >/dev/null || { echo "Docker is required." >&2; exit 1; }
    docker build -t "$IMG" . 
    tok=$(token)
    create "$tok"
    set_env LARK_SANDBOX_URL "http://127.0.0.1:$PORT"
    set_env LARK_SANDBOX_TOKEN "$tok"
    if command -v systemctl >/dev/null; then install_reset; fi
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
  install-reset) install_reset; echo "Installed. Restart Lark to pick it up: systemctl restart lark" ;;
  status) docker ps -a --filter "name=$NAME" ;;
  *) sed -n '2,9p' "$0"; exit 1 ;;
esac
