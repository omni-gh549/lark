import json
import os
import subprocess
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA = tempfile.mkdtemp()
os.environ["LARK_DATA"] = DATA
os.environ.pop("LARK_PASSWORD", None)
os.environ.pop("LARK_SECRET_KEY", None)

import mock_upstream  # noqa: E402
from lark import providers, search, vault  # noqa: E402
from lark.main import app  # noqa: E402

srv = uvicorn.Server(uvicorn.Config(mock_upstream.app, port=8791, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1)
for p in providers.PROVIDERS.values():
    p["base"] = "http://127.0.0.1:8791"

search.SEARCH_PROVIDERS["brave"]["base"] = "http://127.0.0.1:8791/brave"
sbx = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[2] / "sandbox" / "agent.py")],
                       env={**os.environ, "SANDBOX_TOKEN": "tok-1", "SANDBOX_HOME": tempfile.mkdtemp(),
                            "SANDBOX_PORT": "8796", "SANDBOX_BIND": "127.0.0.1"})
time.sleep(1)

c = TestClient(app, base_url="http://localhost")


def run(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        sys.exit(1)


s = c.get("/api/settings").json()
run("defaults", s["provider"] == "openrouter" and s["providers"]["openrouter"]["key_hint"] is None)

r = c.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]})
run("chat without key is a clear 400", r.status_code == 400 and "Settings" in r.json()["error"])

r = c.put("/api/keys/openrouter", json={"key": "sk-good-key-1234"})
run("save key returns only hint", r.status_code == 200 and r.json()["providers"]["openrouter"]["key_hint"] == "1234"
    and "sk-good" not in r.text)
raw = (Path(DATA) / "settings.json").read_text()
run("key is encrypted on disk", "sk-good" not in raw and "1234" not in raw.replace("deepseek", ""))
mode = stat.S_IMODE((Path(DATA) / "settings.json").stat().st_mode)
run("settings file is 0600", mode == 0o600)
run("master key file is 0600", stat.S_IMODE((Path(DATA) / "secret.key").stat().st_mode) == 0o600)

run("test key ok", c.post("/api/keys/openrouter/test").json()["ok"])
c.put("/api/keys/gateway", json={"key": "sk-bad-key-0000"})
r = c.post("/api/keys/gateway/test")
run("test bad key reports provider message", r.status_code == 400 and "Invalid API key" in r.json()["error"])
run("models sorted", c.get("/api/providers/openrouter/models").json()["models"] == ["a/model", "b/model"])
r = c.get("/api/providers/gateway/models")
run("models error surfaces", r.status_code == 502 and "Invalid API key" in r.json()["error"])


def chat(text):
    with c.stream("POST", "/api/chat", json={"messages": [{"role": "user", "content": text}]}) as r:
        return [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")]


ev = chat("hello")
run("stream text then done", "".join(e.get("text", "") for e in ev) == "Echo: hello" and ev[-1] == {"done": True})
ev = chat("boom")
run("mid-stream error forwarded", ev[-1] == {"error": "upstream exploded"})

# saved chats
run("no chats yet", c.get("/api/chats").json() == {"chats": []})
r = c.put("/api/chats/abcdef123456", json={"messages": [{"role": "user", "content": "Hello   there"}, {"role": "assistant", "content": "Hi", "parts": []}]})
run("chat saved with title", r.status_code == 200 and r.json()["title"] == "Hello there")
run("chat loads back", c.get("/api/chats/abcdef123456").json()["messages"][1]["content"] == "Hi")
run("chat listed", [x["id"] for x in c.get("/api/chats").json()["chats"]] == ["abcdef123456"])
run("bad chat id rejected", c.put("/api/chats/bad", json={"messages": []}).status_code == 400 and c.get("/api/chats/short").status_code == 404)
c.delete("/api/chats/abcdef123456")
run("chat deleted", c.get("/api/chats").json() == {"chats": []} and c.get("/api/chats/abcdef123456").status_code == 404)

# tools
ev = chat("tools")
run("no tools offered when nothing is set up", "".join(e.get("text", "") for e in ev) == "")
r = c.put("/api/keys/brave", json={"key": "brave-bad-key"})
run("search key saved, hint only", r.json()["search_providers"]["brave"]["key_hint"] == "-key" and "brave-bad" not in r.text)
r = c.post("/api/keys/brave/test")
run("bad search key reported", r.status_code == 400 and "refused" in r.json()["error"])
c.put("/api/keys/brave", json={"key": "brave-good-key"})
run("good search key tests ok", c.post("/api/keys/brave/test").json()["ok"])
run("bad search provider rejected", c.put("/api/settings", json={"search": "nope"}).status_code == 400)
run("search provider persisted", c.put("/api/settings", json={"search": "brave"}).json()["search"] == "brave")
ev = chat("tools")
run("web_search offered once a key exists", "".join(e.get("text", "") for e in ev) == "web_search")
ev = chat("search cats")
kinds = [next(iter(e)) for e in ev]
run("tool loop events in order", kinds[:2] == ["tool_start", "tool_end"] and kinds[-1] == "done")
run("tool_start has title and detail", ev[0]["tool_start"]["title"] == "Web search" and ev[0]["tool_start"]["detail"] == "cats")
run("tool_end carries cleaned results", ev[1]["tool_end"]["ok"] and "Result for cats" in ev[1]["tool_end"]["output"]
    and "<strong>" not in ev[1]["tool_end"]["output"] and "A & B" in ev[1]["tool_end"]["output"])
run("model sees tool result", "Tool said: 1. Result for cats" in "".join(e.get("text", "") for e in ev))
run("sandbox off by default", c.get("/api/sandbox").json() == {"configured": False})
ev = chat("run echo hi")
run("run_command not offered without sandbox", ev[0].get("tool_start", {}).get("title") != "Run command")

os.environ.update(LARK_SANDBOX_URL="http://127.0.0.1:8796", LARK_SANDBOX_TOKEN="tok-1")
st = c.get("/api/sandbox").json()
run("sandbox status ok", st["configured"] and st["ok"])
ev = chat("tools")
run("sandbox tools offered", "".join(e.get("text", "") for e in ev) == "web_search,run_command,read_file,write_file")
ev = chat("run echo hi && exit 2")
run("run_command output and exit code", "hi" in ev[1]["tool_end"]["output"] and "exit code 2" in ev[1]["tool_end"]["output"])
os.environ["LARK_SANDBOX_TOKEN"] = "wrong"
run("bad sandbox token is a tool error, not a crash", chat("run echo hi")[1]["tool_end"]["ok"] is False)
os.environ["LARK_SANDBOX_TOKEN"] = "tok-1"
from lark import tools as _tools  # noqa: E402
import asyncio  # noqa: E402
run("write then read file", "Wrote 5 bytes" in asyncio.run(_tools.write_file({"path": "n/a.txt", "content": "hello"}))
    and asyncio.run(_tools.read_file({"path": "n/a.txt"})) == "hello")
c.post("/api/sandbox/wipe")
try:
    asyncio.run(_tools.read_file({"path": "n/a.txt"}))
    gone = False
except _tools.ToolError:
    gone = True
run("wipe clears files", gone)
sbx.terminate()
os.environ.pop("LARK_SANDBOX_URL"); os.environ.pop("LARK_SANDBOX_TOKEN")
c.put("/api/settings", json={"provider": "gateway", "models": {"gateway": "x/y"}})
ev = chat("hello")
run("bad key at chat time gives error event", ev == [{"error": "Invalid API key (401)"}])

run("bad provider rejected", c.put("/api/settings", json={"provider": "nope"}).status_code == 400)
run("role validation", c.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422)

c.delete("/api/keys/gateway")
run("delete key", c.get("/api/settings").json()["providers"]["gateway"]["key_hint"] is None)

r = TestClient(app, base_url="http://lark.example.com").get("/api/settings")
run("non-local host refused without password", r.status_code == 403)

os.environ["LARK_PASSWORD"] = "hunter2hunter2"
c2 = TestClient(app, base_url="https://lark.example.com")
run("401 without session", c2.get("/api/settings").status_code == 401)
run("wrong password", c2.post("/api/login", json={"password": "nope"}).status_code == 401)
for _ in range(4):
    c2.post("/api/login", json={"password": "nope"})
r = c2.post("/api/login", json={"password": "hunter2hunter2"})
run("login paused after repeated misses, even with the right password", r.status_code == 429)
from lark import auth  # noqa: E402
auth._paused_until = 0.0
r = c2.post("/api/login", json={"password": "hunter2hunter2"})
run("login sets httponly strict cookie", r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    and "samesite=strict" in r.headers["set-cookie"].lower())
run("session works", c2.get("/api/settings").status_code == 200)

# key survives a server restart (master key persisted)
run("key still decrypts", vault.get_key("openrouter") == "sk-good-key-1234")
print("all passed")
