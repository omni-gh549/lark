#!/bin/sh
# Installs Mnemos (https://github.com/Riley-Coyote/mnemos-mcp) for Lark's optional Mnemos memory backend.
# Run as root on the server. It makes a separate virtualenv (default /opt/mnemos) so Lark's own environment stays as it is,
# and, by default, adds meaning-based recall with a small local model (MiniLM) on the CPU-only build of PyTorch: about 1.4 GB
# of disk and roughly 300 to 500 MB of RAM while Mnemos is running (it stops when Lark is idle). Pass --no-embeddings to skip it
# and match words only.
#
#   sh deploy/install-mnemos.sh [--no-embeddings]
#
# Afterwards, open Settings -> Memory, choose Mnemos and (if it is not on the default path) enter /opt/mnemos/bin/mnemos.
# Or put LARK_MNEMOS_COMMAND=/opt/mnemos/bin/mnemos in /etc/lark/lark.env and restart Lark.
set -eu
DEST="${MNEMOS_DEST:-/opt/mnemos}"
python3 -m venv "$DEST"
"$DEST/bin/pip" install --quiet --upgrade pip
"$DEST/bin/pip" install --quiet mnemos-continuity
if [ "${1:-}" != "--no-embeddings" ]; then
  "$DEST/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
  "$DEST/bin/pip" install --quiet "sentence-transformers>=2.2.0"
  HF_HOME="$DEST/hf" "$DEST/bin/mnemos" embeddings download   # Lark finds the model here (LARK_MNEMOS_MODELS to move it)
  chmod -R a+rX "$DEST"
fi
"$DEST/bin/mnemos" --help >/dev/null
echo "Mnemos installed: $DEST/bin/mnemos"
