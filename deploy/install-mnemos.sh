#!/bin/sh
# Installs Mnemos (https://github.com/Riley-Coyote/mnemos-mcp) for Lark's optional Mnemos memory backend.
# Run as root on the server. It makes a separate virtualenv (default /opt/mnemos) so Lark's own environment stays as it is,
# and installs it without the local-embeddings extra: that pulls in PyTorch (hundreds of MB of disk and RAM), which a small
# server does not need. Recall then matches words only. Pass --with-embeddings to add it on a machine with room.
#
#   sh deploy/install-mnemos.sh [--with-embeddings]
#
# Afterwards, open Settings -> Memory, choose Mnemos and (if it is not on the default path) enter /opt/mnemos/bin/mnemos.
# Or put LARK_MNEMOS_COMMAND=/opt/mnemos/bin/mnemos in /etc/lark/lark.env and restart Lark.
set -eu
DEST="${MNEMOS_DEST:-/opt/mnemos}"
EXTRA=""
[ "${1:-}" = "--with-embeddings" ] && EXTRA="[embeddings]"
python3 -m venv "$DEST"
"$DEST/bin/pip" install --quiet --upgrade pip
"$DEST/bin/pip" install --quiet "mnemos-continuity$EXTRA"
[ -n "$EXTRA" ] && "$DEST/bin/mnemos" embeddings download
"$DEST/bin/mnemos" --help >/dev/null
echo "Mnemos installed: $DEST/bin/mnemos"
