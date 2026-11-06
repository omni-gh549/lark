"""Runs the real app on :8002 against the scripted swarm model on :8796, to watch the swarm card in a browser. Needs no sandbox or API key."""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["LARK_DATA"] = tempfile.mkdtemp()
os.environ["LARK_NO_SUGGESTED"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn  # noqa: E402

import mock_swarm  # noqa: E402
from lark import providers, vault  # noqa: E402
from lark.main import app  # noqa: E402

mock_swarm.CFG.update(slow=float(os.environ.get("SWARM_SLOW", "1.2")), fan=int(os.environ.get("SWARM_FAN", "7")), squads=int(os.environ.get("SWARM_SQUADS", "4")))
for p in providers.PROVIDERS.values():
    p["base"] = "http://127.0.0.1:8796"
vault.set_key("openrouter", "sk-good-key-1234")
vault.update(models={"openrouter": "a/model"}, tiers={"cheap": "a/model", "balanced": "a/model", "smart": "a/model"}, swarm={"max_agents": 100, "concurrency": 12})
threading.Thread(target=uvicorn.Server(uvicorn.Config(mock_swarm.app, port=8796, log_level="error")).run, daemon=True).start()
time.sleep(0.5)
uvicorn.run(app, port=8002, log_level="warning")
