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
                            "SANDBOX_PORT": "8796", "SANDBOX_BIND": "127.0.0.1",
                            "SANDBOX_CHROMIUM": "/opt/pw-browsers/chromium"})
time.sleep(1)

c = TestClient(app, base_url="http://localhost")
c.__enter__()  # one event loop for all requests, so background runs outlive a request


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
run("web_search offered once a key exists", "".join(e.get("text", "") for e in ev) == "web_search,subagent")
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
run("sandbox tools offered", "".join(e.get("text", "") for e in ev) == "web_search,run_command,read_file,write_file,show_image,browser,subagent")
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
# browser tool against a local page
import http.server  # noqa: E402
import functools  # noqa: E402
site = Path(tempfile.mkdtemp())
(site / "index.html").write_text('<title>Shop</title><a href="/two.html">Next page</a><input placeholder="Find"><button onclick="document.title=\'clicked\'">Go</button>')
(site / "two.html").write_text("<title>Two</title><p>second page body</p>")
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 8898), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
snap = asyncio.run(_tools.browser({"action": "goto", "url": "http://127.0.0.1:8898/index.html"}))
run("browser snapshot lists elements", "Title: Shop" in snap and "[1] link 'Next page'" in snap and "[2] text 'Find'" in snap)
snap = asyncio.run(_tools.browser({"action": "click", "id": 1}))
run("browser click follows a link", "Title: Two" in snap and "second page body" in snap)
asyncio.run(_tools.browser({"action": "back"}))
snap = asyncio.run(_tools.browser({"action": "type", "id": 2, "text": "hello"}))
run("browser types into a field", "= 'hello'" in snap)
try:
    asyncio.run(_tools.browser({"action": "click", "id": 77}))
    bad = False
except _tools.ToolError as e:
    bad = "No element" in str(e)
run("browser reports a missing element", bad)
try:
    asyncio.run(_tools.browser({"action": "goto", "url": "file:///etc/passwd"}))
    bad = False
except _tools.ToolError as e:
    bad = "http and https" in str(e)
run("browser refuses file urls", bad)
ev = chat("tools")
run("browser tool in the tool list", "browser" in "".join(e.get("text", "") for e in ev).split(","))
httpd.shutdown()


def sse_events(path):
    with c.stream("GET", path) as r:
        return [json.loads(l[5:]) for l in r.iter_lines() if l.startswith("data:")]


def wait_idle(cid, secs=15):
    end = time.time() + secs
    while time.time() < end:
        if not c.get(f"/api/chats/{cid}").json()["running"]:
            return
        time.sleep(0.1)
    run("run finished in time", False)


# images: upload, serve, vision input
PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63fcffff3f0300050001a5f645400000000049454e44ae426082")
r = c.post("/api/uploads", content=PNG, headers={"content-type": "image/png"})
img = r.json().get("id", "")
run("png upload accepted", r.status_code == 200 and img.endswith(".png"))
run("uploaded file served with type", c.get(f"/api/files/{img}").content == PNG and c.get(f"/api/files/{img}").headers["content-type"] == "image/png")
run("non-image upload refused", c.post("/api/uploads", content=b"<svg onload=alert(1)>", headers={"content-type": "image/png"}).status_code == 400)
run("bad file names refused", b"openrouter" not in c.get("/api/files/..%2f..%2fsettings.json").content and c.get("/api/files/abc.png").status_code == 404)
c.post("/api/chats/img-test-0001/send", json={"content": "what is this", "images": [img, "ffffffffffffffffffffffffffffffff.png"]})
wait_idle("img-test-0001")
doc = c.get("/api/chats/img-test-0001").json()
run("image stored on the message, unknown ids dropped", doc["messages"][0]["images"] == [img])
run("model received the image as vision input", doc["messages"][1]["content"] == "Saw: vision 1 what is this")
c.post("/api/chats/img-test-0001/send", json={"content": "", "images": []})
run("image-only message allowed", c.post("/api/chats/img-test-0002/send", json={"content": "", "images": [img]}).status_code == 200)
wait_idle("img-test-0002")

# Lark shows an image from the sandbox
import base64 as _b64  # noqa: E402
asyncio.run(_tools.run_command({"command": f"echo {_b64.b64encode(PNG).decode()} | base64 -d > /home/lark/pic.png || echo {_b64.b64encode(PNG).decode()} | base64 -d > pic.png"}))
c.post("/api/chats/img-test-0003/send", json={"content": "show pic.png"})
wait_idle("img-test-0003")
part = c.get("/api/chats/img-test-0003").json()["messages"][1]["parts"][0]
run("show_image attaches a stored copy", part["title"] == "Show image" and len(part["images"]) == 1
    and c.get("/api/files/" + part["images"][0]).content == PNG)
c.post("/api/chats/img-test-0004/send", json={"content": "show nothing.png"})
wait_idle("img-test-0004")
run("show_image on a missing file is a tool error", c.get("/api/chats/img-test-0004").json()["messages"][1]["parts"][0]["state"] == "error")

# live browser view and screenshots
c.post("/api/chats/img-test-0005/send", json={"content": "shot"})
wait_idle("img-test-0005", 30)
part = c.get("/api/chats/img-test-0005").json()["messages"][1]["parts"][0]
run("browser screenshot is shown as an image", part["title"] == "Browser" and len(part.get("images", [])) == 1
    and c.get("/api/files/" + part["images"][0]).content[:3] == b"\xff\xd8\xff")
import lark.main as _main  # noqa: E402
_main.STREAM_SECONDS = 1.5
with c.stream("GET", "/api/browser/stream") as r:
    first = b"".join(r.iter_bytes())
run("live view streams jpeg frames", r.headers["content-type"].startswith("multipart/x-mixed-replace") and b"image/jpeg" in first)

# subagents (two in parallel) through the stateless endpoint
ev = chat("delegate alpha, beta")
starts = [e["tool_start"] for e in ev if "tool_start" in e]
ends = [e["tool_end"] for e in ev if "tool_end" in e]
run("two subagents started", [s["title"] for s in starts] == ["Subagent", "Subagent"] and [s["detail"] for s in starts] == ["alpha", "beta"])
run("subagent results returned", sorted(e["output"] for e in ends) == ["Sub result: alpha", "Sub result: beta"])
run("parent continues after subagents", "Tool said: Sub result" in "".join(e.get("text", "") for e in ev))

# a live reader sees every chunk, including ones that arrive while it is mid-stream
from lark import runs as _runs  # noqa: E402


async def _live_reader():
    r = _runs.Run("x")
    got = []

    async def reader():
        async for ev in r.stream():
            got.append(ev.get("text", ""))
            await asyncio.sleep(0)

    task = asyncio.ensure_future(reader())
    for part in ["a", "b", "c", "d"]:
        r.push({"text": part})
        await asyncio.sleep(0)
    r.finished = True
    r.wake()
    await task
    return "".join(got)


run("live reader gets all text", asyncio.run(_live_reader()) == "abcd")

# server-side runs
r = c.post("/api/chats/run-test-0001/send", json={"content": "run sleep 1; echo ran"})
run("send starts a run", r.status_code == 200)
run("second send while running is refused", c.post("/api/chats/run-test-0001/send", json={"content": "x"}).status_code == 409)
run("chat shows as running", c.get("/api/chats/run-test-0001").json()["running"] is True
    and c.get("/api/chats").json()["chats"][0]["running"] is True)
ev = sse_events("/api/chats/run-test-0001/events")
run("events replay from the start and end", ev[0].get("tool_start", {}).get("title") == "Run command" and ev[-1] == {"done": True})
wait_idle("run-test-0001")
doc = c.get("/api/chats/run-test-0001").json()
run("finished reply saved with tool part", [m["role"] for m in doc["messages"]] == ["user", "assistant"]
    and doc["messages"][1]["parts"][0]["state"] == "ok" and "ran" in doc["messages"][1]["parts"][0]["output"]
    and doc["messages"][1]["content"].startswith("Tool said"))
run("no run left over", sse_events("/api/chats/run-test-0001/events") == [{"idle": True}])
# a second client attaching late still gets everything
c.post("/api/chats/run-test-0001/send", json={"content": "hello"})
wait_idle("run-test-0001")
run("history includes earlier turns", len(c.get("/api/chats/run-test-0001").json()["messages"]) == 4)
# stop keeps what was produced
c.post("/api/chats/run-test-0002/send", json={"content": "run sleep 30"})
time.sleep(0.5)
run("stop reports ok", c.post("/api/chats/run-test-0002/stop").json() == {"ok": True})
wait_idle("run-test-0002")
doc = c.get("/api/chats/run-test-0002").json()
part = doc["messages"][-1]["parts"][0]
run("stopped run saved, tool marked failed", doc["messages"][-1]["role"] == "assistant" and part["state"] == "error")
# errors are stored on the chat
c.put("/api/settings", json={"provider": "gateway", "models": {"gateway": "x/y"}})
c.put("/api/keys/gateway", json={"key": "sk-bad-key-0000"})
c.post("/api/chats/run-test-0003/send", json={"content": "hi"})
wait_idle("run-test-0003")
run("failed run keeps the user message and the error", c.get("/api/chats/run-test-0003").json()["error"] == "Invalid API key (401)")
c.put("/api/settings", json={"provider": "openrouter"})
c.post("/api/chats/run-test-0004/send", json={"content": "run sleep 30"})
time.sleep(0.4)
c.delete("/api/chats/run-test-0004")
time.sleep(0.6)
run("deleting a running chat doesn't bring it back", c.get("/api/chats/run-test-0004").status_code == 404)
run("empty send is refused", c.post("/api/chats/run-test-0005/send", json={"content": ""}).status_code == 400)

run("full reset not offered by default", c.post("/api/sandbox/reset").status_code == 400)
os.environ["LARK_SANDBOX_RESET_FILE"] = str(Path(DATA) / "reset-request")
run("full reset writes the request file", c.post("/api/sandbox/reset").json() == {"ok": True}
    and (Path(DATA) / "reset-request").exists())
run("status says reset is available", c.get("/api/sandbox").json()["reset_available"])
os.environ.pop("LARK_SANDBOX_RESET_FILE")

sbx.terminate()
os.environ.pop("LARK_SANDBOX_URL"); os.environ.pop("LARK_SANDBOX_TOKEN")
c.put("/api/settings", json={"provider": "gateway", "models": {"gateway": "x/y"}})
ev = chat("hello")
run("bad key at chat time gives error event", ev == [{"error": "Invalid API key (401)"}])

run("bad provider rejected", c.put("/api/settings", json={"provider": "nope"}).status_code == 400)
run("role validation", c.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422)


# ---- Telegram (the Bot API is faked) ----
from lark import telegram  # noqa: E402

sent = []


async def fake_api(method, token=None, files_=None, _timeout=20, **params):
    sent.append((method, params))
    if method == "getMe":
        return {"username": "lark_test_bot"}
    return {"message_id": len(sent)}


telegram.api = fake_api


def tg(update):
    c.portal.call(telegram.handle, update)


def msgs(chat_id):
    return [p for m, p in sent if m == "sendMessage" and p["chat_id"] == chat_id]


def say_to_bot(uid, text, name="Someone"):
    tg({"message": {"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": name}, "text": text}})


c.put("/api/settings", json={"provider": "openrouter", "models": {"openrouter": "a/model"}})
r = c.put("/api/keys/telegram", json={"key": "123456:ABC-test-token"})
run("telegram key saved, hint only", r.status_code == 200 and r.json()["telegram"]["key_hint"] == "oken" and "ABC-test" not in r.text)
st = c.get("/api/telegram").json()
run("telegram status", st["configured"] and st["bot"] == "lark_test_bot" and st["owner"] is None)
run("telegram key test", "lark_test_bot" in c.post("/api/keys/telegram/test").json()["detail"])

say_to_bot(999, "hello", "Stranger")
run("stranger gets a polite refusal", "invited" in msgs(999)[-1]["text"])
say_to_bot(111, "/start link-deadbeef", "Oscar")
run("bad link code does not link", c.get("/api/telegram").json()["owner"] is None)
url = c.post("/api/telegram/link").json()["url"]
run("link url points at the bot", url.startswith("https://t.me/lark_test_bot?start=link-"))
say_to_bot(111, "/start " + url.split("start=")[1], "Oscar")
run("owner linked", c.get("/api/telegram").json()["owner"] == {"name": "Oscar"})
say_to_bot(222, "/start link-" + url.split("link-")[1], "Mallory")
run("link code is single use", c.get("/api/telegram").json()["owner"] == {"name": "Oscar"})

say_to_bot(111, "hello there", "Oscar")
run("owner chat reaches the agent", any("Echo: hello there" in m["text"] for m in msgs(111)))
run("owner chat is saved", telegram.OWNER_CHAT in [x["id"] for x in c.get("/api/chats").json()["chats"]])
say_to_bot(111, "/new", "Oscar")

inv = c.post("/api/telegram/invites", json={"name": "Sam", "policy": "draft", "scope": "arrange dinner"}).json()["url"]
token = inv.split("start=")[1]
say_to_bot(333, "/start " + token, "Sam B")
run("invite creates a contact", [x["name"] for x in c.get("/api/telegram").json()["contacts"]] == ["Sam"])
run("contact is told it is an AI", "AI assistant" in msgs(333)[-1]["text"])
run("owner is told about the new contact", "Sam joined" in msgs(111)[-1]["text"])
say_to_bot(444, "/start " + token, "Replay")
run("invite is single use", [x["name"] for x in c.get("/api/telegram").json()["contacts"]] == ["Sam"])

before = len(msgs(333))
say_to_bot(333, "Can we do 7pm?", "Sam B")
draft = msgs(111)[-1]
run("draft policy asks the owner first", "Reply to Sam:" in draft["text"] and "Echo: Can we do 7pm?" in draft["text"]
    and draft["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("s:") and len(msgs(333)) == before)
cb = draft["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
tg({"callback_query": {"id": "q1", "from": {"id": 999}, "data": cb, "message": {"message_id": 5}}})
run("a stranger can't approve", len(msgs(333)) == before)
tg({"callback_query": {"id": "q2", "from": {"id": 111}, "data": cb, "message": {"message_id": 5}}})
run("owner approval sends the reply", len(msgs(333)) == before + 1 and "Echo: Can we do 7pm?" in msgs(333)[-1]["text"])
tg({"callback_query": {"id": "q3", "from": {"id": 111}, "data": cb, "message": {"message_id": 5}}})
run("an approved draft can't be sent twice", len(msgs(333)) == before + 1)

sam = c.get("/api/telegram").json()["contacts"][0]["id"]
r = c.put(f"/api/telegram/contacts/{sam}", json={"policy": "relay"})
run("policy edit", r.json()["contacts"][0]["policy"] == "relay")
before = len(msgs(333))
say_to_bot(333, "ping", "Sam B")
run("relay forwards to the owner only", msgs(111)[-1]["text"] == "Sam: ping" and len(msgs(333)) == before)
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "auto"})
say_to_bot(333, "auto please", "Sam B")
run("auto replies and tells the owner", "Echo: auto please" in msgs(333)[-1]["text"] and "Lark replied" in msgs(111)[-1]["text"])
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "blocked"})
before_o, before_c = len(msgs(111)), len(msgs(333))
say_to_bot(333, "hello?", "Sam B")
run("blocked is ignored", len(msgs(111)) == before_o and len(msgs(333)) == before_c)
run("bad policy rejected", c.put(f"/api/telegram/contacts/{sam}", json={"policy": "x"}).status_code == 422)

c.put(f"/api/telegram/contacts/{sam}", json={"policy": "draft"})
say_to_bot(111, "tools", "Oscar")
run("owner's agent can message contacts", "message_contact" in msgs(111)[-1]["text"])
r = c.portal.call(telegram.message_contact, "sam", "Running late")
run("agent message waits for approval", "approve" in r and "Running late" in msgs(111)[-1]["text"])
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "auto"})
r = c.portal.call(telegram.message_contact, "Sam", "On my way")
run("auto contact gets agent messages directly", msgs(333)[-1]["text"] == "On my way")
from lark import agent as _agent  # noqa: E402
seen = []
_loop = _agent.loop


def spy(*a, **k):
    seen.append(a[4])  # the tools offered to the model
    return _loop(*a, **k)


_agent.loop = spy
say_to_bot(333, "tools", "Sam B")
_agent.loop = _loop
run("contacts' model has no tools", seen == [[]])

r = c.delete(f"/api/telegram/contacts/{sam}")
run("remove contact", r.json()["contacts"] == [])
run("unlink owner", c.delete("/api/telegram/owner").json()["ok"] and c.get("/api/telegram").json()["owner"] is None)
c.delete("/api/keys/telegram")

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
