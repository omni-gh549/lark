"""Runs the real app on :8001 against the fake provider on :8791, for browser tests."""
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["LARK_DATA"] = tempfile.mkdtemp()
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

import mock_upstream  # noqa: E402
from lark import providers, search  # noqa: E402
from lark.main import app  # noqa: E402

for p in providers.PROVIDERS.values():
    p["base"] = "http://127.0.0.1:8791"
search.SEARCH_PROVIDERS["brave"]["base"] = "http://127.0.0.1:8791/brave"
os.environ.update(SANDBOX_TOKEN="e2e-token", SANDBOX_HOME=tempfile.mkdtemp(), SANDBOX_PORT="8795", SANDBOX_BIND="127.0.0.1", SANDBOX_CHROMIUM="/opt/pw-browsers/chromium",
                  LARK_SANDBOX_URL="http://127.0.0.1:8795", LARK_SANDBOX_TOKEN="e2e-token")
subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[2] / "sandbox" / "agent.py")])
threading.Thread(target=uvicorn.Server(uvicorn.Config(mock_upstream.app, port=8791, log_level="error")).run, daemon=True).start()
time.sleep(0.5)
uvicorn.run(app, port=8001, log_level="warning")
