"""Tests for the Mnemos memory backend, against a mock `mnemos serve` (mock_mnemos.py). Run: python tests/test_mnemos.py"""
import json
import os
import sys
import tempfile
import threading
import time
import socket
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA = tempfile.mkdtemp()
os.environ["LARK_DATA"] = DATA
for _k in ("LARK_PASSWORD", "LARK_SECRET_KEY", "LARK_SANDBOX_URL", "LARK_SANDBOX_TOKEN", "LARK_MNEMOS_COMMAND", "LARK_MNEMOS_HOME"):
    os.environ.pop(_k, None)
os.environ["LARK_NO_SUGGESTED"] = "1"
os.environ["LARK_SECRET_PROBE"] = "x"  # must not reach the Mnemos child

import mock_upstream  # noqa: E402
from lark import providers, vault, memory, tools as _tools  # noqa: E402
from lark.main import app  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


UPSTREAM = free_port()
threading.Thread(target=uvicorn.Server(uvicorn.Config(mock_upstream.app, port=UPSTREAM, log_level="error")).run, daemon=True).start()
time.sleep(1)
for p in providers.PROVIDERS.values():
    p["base"] = f"http://127.0.0.1:{UPSTREAM}"

c = TestClient(app, base_url="http://localhost")
c.__enter__()
FAILED = []


def run(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILED.append(label)


def chat(text):
    with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": text}]}) as r:
        return [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")]


c.put("/api/keys/openrouter", json={"key": "sk-good-key-1234"})
c.put("/api/settings", json={"provider": "openrouter", "models": {"openrouter": "a/model"}})
c.post("/api/memory", json={"text": "The user's sister Maya lives in Lisbon.", "kind": "person", "importance": 4})

# ---- Mnemos as the memory backend ----
from lark import mnemos  # noqa: E402

MN_CMD = f'"{sys.executable}" "{Path(__file__).resolve().parent / "mock_mnemos.py"}"'
MN_HOME = Path(DATA) / "mnemos" / ".mnemos"
run("memory backend defaults to Lark's own", c.get("/api/settings").json()["memory"]["backend"] == "lark")
r = c.put("/api/settings", json={"memory_backend": "mnemos", "mnemos_command": "definitely-not-installed-xyz"})
run("switching to Mnemos fails clearly when it isn't installed", r.status_code == 400 and "not installed" in r.json()["error"]
    and c.get("/api/settings").json()["memory"]["backend"] == "lark" and c.get("/api/settings").json()["memory"]["mnemos_command"] == "")
run("bad backend rejected", c.put("/api/settings", json={"memory_backend": "nope"}).status_code == 422)
r = c.put("/api/settings", json={"memory_backend": "mnemos", "mnemos_command": MN_CMD + " serve"})
run("switch to Mnemos once it starts", r.status_code == 200 and r.json()["memory"]["backend"] == "mnemos")
ev = chat("tools")
tl = "".join(e.get("text", "") for e in ev)
run("Mnemos swaps the memory tools", tl == "mnemos_capture,mnemos_recall,mnemos_correct,mnemos_reflect,mnemos_handoff,mnemos_journal,mnemos_note,subagent")
run("Lark's own memory tools are gone while Mnemos is chosen", "remember" not in tl.split(",") and "search_conversations" not in tl)
r = [t for t in c.portal.call(_tools.available) if t.name == "mnemos_capture"][0].spec()
run("the model never sees signed_as or shared", "signed_as" not in json.dumps(r) and "shared" not in json.dumps(r))
CURRENT = "test/model-1"
mnemos.CURRENT_MODEL.set(CURRENT)
cap = [t for t in c.portal.call(_tools.available) if t.name == "mnemos_capture"][0]
out = c.portal.call(cap.run, {"content": "The user's dentist is Dr Okafor.", "shared": True})
calls = [json.loads(l) for l in (MN_HOME / "calls.jsonl").read_text().splitlines()]
last = [x for x in calls if x["name"] == "mnemos_capture"][-1]
run("a capture is signed with the chat model and never shared", "Captured" in out and last["args"].get("signed_as") == CURRENT and "shared" not in last["args"])
run("Mnemos keeps its files under Lark's data folder", (MN_HOME / "lark.json").exists())
run("Lark's secrets are not passed to Mnemos", not any(x["lark_env_leaked"] for x in calls))
c.post("/api/chats/mnchat-0001/send", json={"content": "where is my dentist sysdump"})
for _ in range(50):
    time.sleep(0.1)
    if not c.get("/api/chats/mnchat-0001").json()["running"]:
        break
sysmsg = c.get("/api/chats/mnchat-0001").json()["messages"][-1]["content"]
run("the prompt carries Mnemos's briefing and what bears on the message", "Dr Okafor" in sysmsg and "Memories that may bear on this message" in sysmsg and "Memory: you keep your own" in sysmsg)
run("Lark's own notes stay out of the prompt", "Maya" not in sysmsg and "Possibly relevant memories" not in sysmsg)
calls = [json.loads(l) for l in (MN_HOME / "calls.jsonl").read_text().splitlines()]
run("Mnemos is told which model is writing", any(x["name"] == "mnemos_introduce" and x["args"].get("agent_model") == "a/model" for x in calls))
ov = c.get("/api/mnemos").json()
run("the Memory page has plain numbers, standing notes and a details card", ov["status"]["installed"] and ov["stats"]["memories"] >= 1
    and any("Dr Okafor" in t for t in ov["standing"]) and "safe to relay" not in ov["details"])
act = ov["activity"]
run("the Memory page lists what Mnemos was asked to remember, newest first", act and act[0]["label"] in ("Remembered", "Recalled")
    and any(a["label"] == "Remembered" and "Okafor" in a["text"] for a in act))
tt = {t.name: t for t in c.portal.call(_tools.available)}
run("chat chips read like plain words", tt["mnemos_capture"].title == "Remembered" and tt["mnemos_capture"].detail({"content": "Likes tea"}) == "Likes tea"
    and tt["mnemos_recall"].title == "Recalled")
run("Mnemos memory search", any("Okafor" in i["text"] for i in c.get("/api/mnemos/search?q=dentist okafor").json()["items"]))
run("learning pass is off with Mnemos", memory.enabled() is False)
n = len(memory.list_facts())
c.post("/api/chats/mnchat-0002/send", json={"content": "My cat is called Miso and she sleeps all day."})
for _ in range(50):
    time.sleep(0.1)
    if not c.get("/api/chats/mnchat-0002").json()["running"]:
        break
time.sleep(1.5)
run("Lark's own learner writes nothing while Mnemos is chosen", len(memory.list_facts()) == n)
run("the briefing is reused, not fetched for every message", sum(x["name"] == "mnemos_context" for x in
    [json.loads(l) for l in (MN_HOME / "calls.jsonl").read_text().splitlines()]) <= 3)
c.portal.call(mnemos.shutdown)
run("Mnemos restarts by itself on demand", any("Okafor" in i["text"] for i in c.get("/api/mnemos/search?q=dentist okafor").json()["items"]))
c.put("/api/settings", json={"memory_use": False})
run("memory off removes the Mnemos tools too", "mnemos_capture" not in "".join(e.get("text", "") for e in chat("tools")))
c.put("/api/settings", json={"memory_use": True, "memory_backend": "lark"})
run("switching back restores Lark's own memory, untouched", "remember" in "".join(e.get("text", "") for e in chat("tools")).split(",")
    and any("Maya" in f["text"] for f in memory.list_facts()))
c.portal.call(mnemos.shutdown)

print("all passed" if not FAILED else f"{len(FAILED)} failed: " + "; ".join(FAILED))
os._exit(1 if FAILED else 0)
