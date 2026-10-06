#!/usr/bin/env bash
# Stops the sandbox container reaching the host, other containers or private networks. Internet stays open.
#   sandbox-firewall.sh apply      add the rules now (idempotent)
#   sandbox-firewall.sh install    apply, and re-apply at boot via a systemd unit
#   sandbox-firewall.sh remove
set -euo pipefail

SUBNET=${SANDBOX_SUBNET:-172.30.0.0/24}
CHAIN=LARK-SANDBOX

apply() {
  iptables -N $CHAIN 2>/dev/null || iptables -F $CHAIN
  # Reply traffic for connections the sandbox opened is handled by Docker's own rules; we only restrict new ones.
  iptables -A $CHAIN -d 10.0.0.0/8 -j DROP
  iptables -A $CHAIN -d 172.16.0.0/12 -j DROP
  iptables -A $CHAIN -d 192.168.0.0/16 -j DROP
  iptables -A $CHAIN -d 169.254.0.0/16 -j DROP   # cloud metadata
  iptables -A $CHAIN -d 100.64.0.0/10 -j DROP
  iptables -A $CHAIN -j RETURN
  # Forwarded traffic from the sandbox (to the internet or other containers)
  iptables -C DOCKER-USER -s "$SUBNET" -j $CHAIN 2>/dev/null || iptables -I DOCKER-USER -s "$SUBNET" -j $CHAIN
  # Traffic from the sandbox to the host itself (its own ports, sshd, databases, Lark)
  iptables -C INPUT -s "$SUBNET" -m conntrack --ctstate NEW -j DROP 2>/dev/null || iptables -I INPUT -s "$SUBNET" -m conntrack --ctstate NEW -j DROP
}

remove() {
  iptables -D DOCKER-USER -s "$SUBNET" -j $CHAIN 2>/dev/null || true
  iptables -D INPUT -s "$SUBNET" -m conntrack --ctstate NEW -j DROP 2>/dev/null || true
  iptables -F $CHAIN 2>/dev/null || true
  iptables -X $CHAIN 2>/dev/null || true
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
