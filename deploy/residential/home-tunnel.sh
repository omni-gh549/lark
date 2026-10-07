#!/bin/bash
# Keep a reverse SSH tunnel from this home machine to the Lark server.
# The remote side can reach the egress proxy only on 127.0.0.1:8899.
set -u
KEY="$HOME/.ssh/lark-egress-tunnel"
while true; do
  ssh -N \
    -i "$KEY" \
    -o BatchMode=yes \
    -o IdentitiesOnly=yes \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -o StrictHostKeyChecking=yes \
    -R 127.0.0.1:8899:HOME_TAILSCALE_OR_LAN_IP:8899 \
    root@YOUR_SERVER_IP || true
  sleep 15
done
