"""Runs the real app on :8001 against the fake provider on :8791, for browser tests."""
import os
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
from lark import providers  # noqa: E402
from lark.main import app  # noqa: E402

for p in providers.PROVIDERS.values():
    p["base"] = "http://127.0.0.1:8791"
threading.Thread(target=uvicorn.Server(uvicorn.Config(mock_upstream.app, port=8791, log_level="error")).run, daemon=True).start()
time.sleep(0.5)
uvicorn.run(app, port=8001, log_level="warning")
