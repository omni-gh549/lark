#!/usr/bin/env bash
# Stops the sandbox container reaching the host, other containers or private networks. Internet stays open.
#   sandbox-firewall.sh apply      add the rules now (idempotent)
#   sandbox-firewall.sh install    apply, and re-apply at boot via a systemd unit
#   sandbox-firewall.sh remove
set -euo pipefail

SUBNET=${SANDBOX_SUBNET:-172.30.0.0/24}
CHAIN=LARK-SANDBOX
ENV_FILE=${LARK_ENV_FILE:-/etc/lark/lark.env}
PROXY_PORT=$(grep -s '^SANDBOX_PROXY_PORT=' "$ENV_FILE" | cut -d= -f2- || true)
PROXY_PORT=${PROXY_PORT:-8898}
[[ "$PROXY_PORT" =~ ^[0-9]+$ ]] || PROXY_PORT=8898

allow_host_proxy() {
  # Lark's selector proxy listens on this port on the docker bridge. It must stay
  # above the DROP below: apply() can run again after a reboot or a sandbox rebuild.
  while iptables -w 5 -D INPUT -s "$SUBNET" -p tcp --dport "$PROXY_PORT" -j ACCEPT 2>/dev/null; do :; done
  iptables -w 5 -I INPUT 1 -s "$SUBNET" -p tcp --dport "$PROXY_PORT" -j ACCEPT
}

apply() {
  iptables -w 5 -N $CHAIN 2>/dev/null || iptables -w 5 -F $CHAIN
  # Reply traffic for connections the sandbox opened is handled by Docker's own rules; we only restrict new ones.
  iptables -w 5 -A $CHAIN -d 10.0.0.0/8 -j DROP
  iptables -w 5 -A $CHAIN -d 172.16.0.0/12 -j DROP
  iptables -w 5 -A $CHAIN -d 192.168.0.0/16 -j DROP
  iptables -w 5 -A $CHAIN -d 169.254.0.0/16 -j DROP   # cloud metadata
  iptables -w 5 -A $CHAIN -d 100.64.0.0/10 -j DROP
  iptables -w 5 -A $CHAIN -j RETURN
  # Forwarded traffic from the sandbox (to the internet or other containers)
  iptables -w 5 -C DOCKER-USER -s "$SUBNET" -j $CHAIN 2>/dev/null || iptables -w 5 -I DOCKER-USER -s "$SUBNET" -j $CHAIN
  # Traffic from the sandbox to the host itself (its own ports, sshd, databases, Lark)
  iptables -w 5 -C INPUT -s "$SUBNET" -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -w 5 -I INPUT -s "$SUBNET" -m conntrack --ctstate NEW -j DROP
  allow_host_proxy
}

remove() {
  while iptables -w 5 -D INPUT -s "$SUBNET" -p tcp --dport "$PROXY_PORT" -j ACCEPT 2>/dev/null; do :; done
  iptables -w 5 -D DOCKER-USER -s "$SUBNET" -j $CHAIN 2>/dev/null || true
  iptables -w 5 -D INPUT -s "$SUBNET" -m conntrack --ctstate NEW -j DROP 2>/dev/null || true
  iptables -w 5 -F $CHAIN 2>/dev/null || true
  iptables -w 5 -X $CHAIN 2>/dev/null || true
}

case "${1:-}" in
  apply) apply ;;
  remove) remove ;;
  install)
    apply
    here=$(readlink -f "$0")
    cat > /etc/systemd/system/lark-sandbox-firewall.service <<UNIT
[Unit]
Description=Firewall rules for the Lark sandbox
After=docker.service network-online.target
Wants=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$here apply
ExecStop=$here remove

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable lark-sandbox-firewall.service
    echo "Installed."
    ;;
  *) sed -n '2,5p' "$0"; exit 1 ;;
esac
