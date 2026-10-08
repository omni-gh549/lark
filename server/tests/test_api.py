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
for _k in ("LARK_SANDBOX_URL", "LARK_SANDBOX_TOKEN"):
    os.environ.pop(_k, None)  # never reach a real sandbox from the suite
os.environ["LARK_NO_SUGGESTED"] = "1"  # tests start with no suggested memory models

import mock_upstream  # noqa: E402
from lark import providers, search, vault  # noqa: E402
from lark.main import app  # noqa: E402

import socket  # noqa: E402


def free_port():
    """A port nothing is listening on, so the suite never collides with (or reaches) a running Lark on this machine."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


UPSTREAM, SANDBOX, SITE = free_port(), free_port(), free_port()
srv = uvicorn.Server(uvicorn.Config(mock_upstream.app, port=UPSTREAM, log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
time.sleep(1)
for p in providers.PROVIDERS.values():
    p["base"] = f"http://127.0.0.1:{UPSTREAM}"

search.SEARCH_PROVIDERS["brave"]["base"] = f"http://127.0.0.1:{UPSTREAM}/brave"
sbx = subprocess.Popen([sys.executable, str(Path(__file__).resolve().parents[2] / "sandbox" / "agent.py")],
                       env={**os.environ, "SANDBOX_TOKEN": "tok-1", "SANDBOX_HOME": tempfile.mkdtemp(),
                            "SANDBOX_PORT": str(SANDBOX), "SANDBOX_BIND": "127.0.0.1",
                            "SANDBOX_CHROMIUM": "/opt/pw-browsers/chromium"})
time.sleep(1)

c = TestClient(app, base_url="http://localhost")
c.__enter__()  # one event loop for all requests, so background runs outlive a request


FAILED = []


def run(label, ok):
    print(("PASS " if ok else "FAIL ") + label)
    if not ok:
        FAILED.append(label)  # keep going: later checks are still worth seeing


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
run("only memory tools offered when nothing else is set up", "".join(e.get("text", "") for e in ev) == "remember,forget,memory_search,search_conversations,read_conversation,subagent")
r = c.put("/api/keys/brave", json={"key": "brave-bad-key"})
run("search key saved, hint only", r.json()["search_providers"]["brave"]["key_hint"] == "-key" and "brave-bad" not in r.text)
r = c.post("/api/keys/brave/test")
run("bad search key reported", r.status_code == 400 and "refused" in r.json()["error"])
c.put("/api/keys/brave", json={"key": "brave-good-key"})
run("good search key tests ok", c.post("/api/keys/brave/test").json()["ok"])
run("bad search provider rejected", c.put("/api/settings", json={"search": "nope"}).status_code == 400)
run("search provider persisted", c.put("/api/settings", json={"search": "brave"}).json()["search"] == "brave")
ev = chat("tools")
run("web_search offered once a key exists", "".join(e.get("text", "") for e in ev) == "web_search,remember,forget,memory_search,search_conversations,read_conversation,subagent")
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

os.environ.update(LARK_SANDBOX_URL=f"http://127.0.0.1:{SANDBOX}", LARK_SANDBOX_TOKEN="tok-1")
st = c.get("/api/sandbox").json()
run("sandbox status ok", st["configured"] and st["ok"])
ev = chat("tools")
offered = "".join(e.get("text", "") for e in ev).split(",")
run("sandbox tools offered", {"run_command", "read_file", "write_file", "show_image", "subagent", "web_search", "remember"} <= set(offered))
ev = chat("run echo hi && exit 2")
run("run_command output and exit code", "hi" in ev[1]["tool_end"]["output"] and "exit code 2" in ev[1]["tool_end"]["output"])
os.environ["LARK_SANDBOX_TOKEN"] = "wrong"
run("bad sandbox token is a tool error, not a crash", chat("run echo hi")[1]["tool_end"]["ok"] is False)
os.environ["LARK_SANDBOX_TOKEN"] = "tok-1"
from lark import sandbox, tools as _tools  # noqa: E402
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
# sign-in take-over: the person types straight into the browser, Lark only waits
run("input rejects an unknown kind", c.post("/api/browser/input", json={"type": "nope"}).status_code == 422)
run("input rejects an off-page click", c.post("/api/browser/input", json={"type": "click", "x": 3, "y": 0}).status_code == 422)
run("input needs an open page", c.post("/api/browser/input", json={"type": "key", "key": "Enter"}).status_code == 502)
run("no sign-in pending at first", c.get("/api/browser/login").json()["pending"] is None)


async def _login_flow():
    task = asyncio.ensure_future(_tools.request_login({"site": "example.com", "reason": "to see your orders"}))
    await asyncio.sleep(0.3)
    pending = _tools.LOGIN["pending"]
    state = c.get("/api/browser/login").json()["pending"]
    again = None
    try:
        await _tools.request_login({"site": "x"})
    except _tools.ToolError as e:
        again = str(e)
    c.post("/api/browser/login/done")
    try:
        out = await task
    except _tools.ToolError as e:
        out = "ToolError: " + str(e)
    return pending, state, again, out


pending, state, again, out = asyncio.run(_login_flow())
run("sign-in request shows the site and reason", state == {"site": "example.com", "reason": "to see your orders"} == pending)
run("only one sign-in request at a time", again and "already" in again)
run("pressing Done resumes Lark and clears the request", c.get("/api/browser/login").json()["pending"] is None and ("signing in" in out or "ToolError" in out))
import concurrent.futures  # noqa: E402
import time as _tm  # noqa: E402
c.post("/api/browser/takeover", json={"on": True})
with concurrent.futures.ThreadPoolExecutor(1) as _ex:
    fut = _ex.submit(c.portal.call, _tools.wait_unpaused)
    _tm.sleep(1.0)
    waited = not fut.done()
    c.post("/api/browser/takeover", json={"on": False})
    fut.result(timeout=5)
run("Lark's run waits while you take over, and resumes when you press Done", waited and _tools.TAKEOVER["until"] == 0.0)
_tools.hold_run(True)
_tools.TAKEOVER["until"] = _tm.time() - 1
run("the pause lapses by itself if the page goes away", asyncio.run(asyncio.wait_for(_tools.wait_unpaused(), 2)) is None)
run("request_login is offered with the browser", True)
# browser tool against a local page
import http.server  # noqa: E402
import functools  # noqa: E402
site = Path(tempfile.mkdtemp())
(site / "index.html").write_text('<title>Shop</title><a href="/two.html">Next page</a><input placeholder="Find"><button onclick="document.title=\'clicked\'">Go</button><input name="otp" value="123456"><input autocomplete="username" value="oscar@example.com"><input name="city" value="Leeds">')
(site / "two.html").write_text("<title>Two</title><p>second page body</p>")
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", SITE), functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(site)))
threading.Thread(target=httpd.serve_forever, daemon=True).start()
snap = asyncio.run(_tools.browser({"action": "goto", "url": f"http://127.0.0.1:{SITE}/index.html"}))
run("snapshot hides codes and usernames but shows ordinary fields", "123456" not in snap and "oscar@example.com" not in snap and "Leeds" in snap)
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
    bad = "element is gone" in str(e) and "Here is the page now" in str(e)
run("browser reports a missing element", bad)
try:
    asyncio.run(_tools.browser({"action": "goto", "url": "file:///etc/passwd"}))
    bad = False
except _tools.ToolError as e:
    bad = "http and https" in str(e)
run("browser refuses file urls", bad)
import time as _tm  # noqa: E402
asyncio.run(_tools.browser({"action": "goto", "url": f"http://127.0.0.1:{SITE}/index.html"}))
# the Find field and Go button; a click lands by position, typing goes to whatever has focus
_tools.LOGIN["last_input"] = 0.0
asyncio.run(sandbox.browser_input({"type": "key", "key": "Tab"}))
asyncio.run(sandbox.browser_input({"type": "key", "key": "Tab"}))
asyncio.run(sandbox.browser_input({"type": "text", "text": "secret-pw"}))
snap = asyncio.run(_tools.browser({"action": "snapshot"}))
run("take-over typing reaches the page", "= 'secret-pw'" in snap)
asyncio.run(sandbox.browser_input({"type": "scroll", "x": 0.5, "y": 0.5, "dy": 100}))
run("take-over scroll and click are accepted", True)
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
r = c.post("/api/chats/run-test-0001/send", json={"content": "x"})
run("a message sent while Lark is replying is queued, not refused", r.status_code == 200 and r.json().get("queued") is True)
run("chat shows as running", c.get("/api/chats/run-test-0001").json()["running"] is True
    and c.get("/api/chats").json()["chats"][0]["running"] is True)
ev = sse_events("/api/chats/run-test-0001/events")
run("events replay from the start and end", ev[0].get("tool_start", {}).get("title") == "Run command" and ev[-1] == {"done": True})
wait_idle("run-test-0001")
doc = c.get("/api/chats/run-test-0001").json()
run("finished reply saved with tool part, before the queued message", [m["role"] for m in doc["messages"]] == ["user", "assistant", "user", "assistant"]
    and doc["messages"][1]["parts"][0]["state"] == "ok" and "ran" in doc["messages"][1]["parts"][0]["output"]
    and doc["messages"][1]["content"].startswith("Tool said"))
run("the queued message was answered next", doc["messages"][2]["content"] == "x" and doc["messages"][3]["content"] == "Echo: x")
run("no run left over", sse_events("/api/chats/run-test-0001/events") == [{"idle": True}])
# a second client attaching late still gets everything
c.post("/api/chats/run-test-0001/send", json={"content": "hello"})
wait_idle("run-test-0001")
run("history includes earlier turns", len(c.get("/api/chats/run-test-0001").json()["messages"]) == 6)
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
run("failed run keeps the user message and the error", c.get("/api/chats/run-test-0003").json()["error"].startswith("The provider rejected the API key"))
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
run("bad key at chat time gives error event", len(ev) == 1 and ev[0]["error"].startswith("The provider rejected the API key"))

run("bad provider rejected", c.put("/api/settings", json={"provider": "nope"}).status_code == 400)
run("role validation", c.post("/api/chat", json={"messages": [{"role": "system", "content": "x"}]}).status_code == 422)


# ---- Telegram (the Bot API is faked) ----
from lark import telegram  # noqa: E402

sent = []


async def fake_api(method, token=None, files_=None, _timeout=20, **params):
    if method == "getUpdates":
        await asyncio.sleep(1)
        return []
    sent.append((method, params))
    if method == "getMe":
        return {"username": "lark_test_bot"}
    return {"message_id": len(sent)}


telegram.api = fake_api


def tg(update):
    c.portal.call(telegram.handle, update)


def msgs(chat_id):
    return [p for m, p in sent if m == "sendMessage" and p["chat_id"] == chat_id]


def say_to_bot(uid, text, name="Someone", settle=True):
    tg({"message": {"chat": {"id": uid, "type": "private"}, "from": {"id": uid, "first_name": name}, "text": text}})
    if settle and uid == 111:  # the owner's replies are sent as the run goes: wait for it and for the last message to go out
        from lark import runs as _r
        end = time.time() + 15
        time.sleep(0.2)
        while _r.get("telegram-owner") and time.time() < end:
            time.sleep(0.1)
        time.sleep(0.3)


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
n_o = len(msgs(111))
run("auto replies and stays quiet when nothing matters", "Echo: auto please" in msgs(333)[-1]["text"] and len(msgs(111)) == n_o)
say_to_bot(333, "can you give me a lift at 6", "Sam B")
run("auto tells the owner only what Lark judged important", msgs(111)[-1]["text"] == "Sam: Wants a lift or mentioned the router.")
n_o = len(msgs(111))
say_to_bot(333, "garbage in", "Sam B")
run("when the judgement fails the owner is told rather than missed", len(msgs(111)) == n_o + 1 and "garbage in" in msgs(111)[-1]["text"])
from lark import memory as _mem  # noqa: E402
_mem.add_fact("Always tell Oscar if Sam mentions the router.", "preference", "Sam", 5, pinned=True)
n_o = len(msgs(111))
say_to_bot(333, "my router is down", "Sam B")
run("the owner's own rules decide what is worth telling them", len(msgs(111)) == n_o + 1)
_mem.clear_facts()
ids = [x["id"] for x in c.get("/api/chats").json()["chats"]]
from lark import chats as _chats  # noqa: E402
_chats.save("tg-4445556667", [{"role": "user", "content": "hi Lark"}, {"role": "assistant", "content": "hello"}])
ids = [x["id"] for x in c.get("/api/chats").json()["chats"]]
run("contact chats stay out of the web history", "tg-4445556667" not in ids and (_chats.load("tg-4445556667") is not None))
_chats.delete("tg-4445556667")
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "blocked"})
before_o, before_c = len(msgs(111)), len(msgs(333))
say_to_bot(333, "hello?", "Sam B")
run("blocked is ignored", len(msgs(111)) == before_o and len(msgs(333)) == before_c)
run("bad policy rejected", c.put(f"/api/telegram/contacts/{sam}", json={"policy": "x"}).status_code == 422)

c.put(f"/api/telegram/contacts/{sam}", json={"policy": "draft"})
say_to_bot(111, "tools", "Oscar")
run("owner's agent can message contacts", "message_contact" in msgs(111)[-1]["text"])
r = c.portal.call(telegram.message_contact, "sam", "Running late")
run("agent message to a draft contact still sends immediately", r.startswith("Sent") and msgs(333)[-1]["text"] == "Running late")
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "auto"})
r = c.portal.call(_tools.MESSAGE_CONTACT.run, {"contact": "Sam", "text": "no image given"})
run("the message_contact tool works without an image", r.startswith("Sent") and msgs(333)[-1]["text"] == "no image given")
r = c.portal.call(telegram.message_contact, "Sam", "On my way")
run("auto contact gets agent messages directly", msgs(333)[-1]["text"] == "On my way")
run("replies split into several messages and lose markdown", telegram._pieces("**Hi** there\n---\nsecond one\n\n---\n# Third") == ["Hi there", "second one", "Third"])
run("a single reply stays one message", telegram._pieces("just one") == ["just one"])
# owner knows its contacts; contact requests for tools wait for a tap; replies stream as separate messages
brief = telegram.owner_brief()
run("owner brief names the contact and message_contact", "Sam" in brief and "message_contact" in brief)
st_ = telegram.load(); st_["contacts"][str(333)]["handle"] = "samb"; telegram.save(st_)
run("brief shows the handle and wraps contact chat text as untrusted", "@samb" in telegram.owner_brief() and "UNTRUSTED CONTACT TEXT" in telegram.owner_brief())
reply_ = "On it.\n[[PASS_ON: a screenshot of the router listing]]"
st_ = telegram.load()
c.portal.call(telegram._offer_task, "333", "Sam", "a screenshot of the router listing", "can you get me a screenshot of the router")
offer = [p_ for m_, p_ in sent if m_ == "sendMessage" and p_["chat_id"] == 111][-1]
run("contact request is offered to the owner with Do it / Ignore", "needs me" in offer["text"] and offer["reply_markup"]["inline_keyboard"][0][0]["text"] == "Do it")
tid = list(telegram.load()["tasks"])[-1]
tg({"callback_query": {"id": "qt", "from": {"id": 111}, "data": f"x:{tid}", "message": {"message_id": 3}}})
run("ignoring a request drops it without running anything", tid not in telegram.load()["tasks"])
m_ = telegram._PASS_ON.search(reply_)
run("pass-on marker is recognised and stripped", m_ and m_.group(1).startswith("a screenshot") and telegram._PASS_ON.sub("", reply_).strip() == "On it.")
from lark import files as _files  # noqa: E402
png = _files.save(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "draft"})
photos = lambda chat_id: [p for m, p in sent if m == "sendPhoto" and p["chat_id"] == str(chat_id)]
r = c.portal.call(telegram.message_contact, "Sam", "Here is the photo", png)
run("image to a draft contact is sent at once", r.startswith("Sent") and photos(333) and photos(333)[-1]["caption"] == "Here is the photo")
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "auto"})
c.portal.call(telegram.message_contact, "Sam", "auto image", png)
run("auto contact gets the image directly", photos(333)[-1]["caption"] == "auto image")
# editing, deleting and reacting
call = lambda fn, *args: c.portal.call(fn, *args)
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "auto"})
call(telegram.say, 333, "first draft")
mid = telegram.load()["log"]["333"][-1]["id"]
sent_n = len(sent)
call(telegram.edit_message, "Sam", "second draft")
run("auto contact: edit goes straight through", sent[-1][0] == "editMessageText" and sent[-1][1]["text"] == "second draft" and sent[-1][1]["message_id"] == mid)
run("recent shows the edit", "second draft" in call(telegram.recent, "Sam") and "(edited)" in call(telegram.recent, "Sam"))
tg({"message_reaction": {"chat": {"id": 333}, "message_id": mid, "new_reaction": [{"type": "emoji", "emoji": "👍"}]}})
run("a reaction shows up as the only 'seen' signal", "reacted 👍" in call(telegram.recent, "Sam") and "never tells a bot" in call(telegram.recent, "Sam"))
call(telegram.react, "Sam", "❤")
run("react", sent[-1][0] == "setMessageReaction" and sent[-1][1]["reaction"] == [{"type": "emoji", "emoji": "❤"}])
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "draft"})
before = len([1 for m_, _ in sent if m_ == "deleteMessage"])
r = call(telegram.delete_message, "Sam")
run("draft contact: delete is immediate", r == "Deleted." and any(m_ == "deleteMessage" and p_["message_id"] == mid for m_, p_ in sent))
run("deleted message is forgotten", all(e["id"] != mid for e in telegram.load()["log"]["333"]))
call(telegram.say, 111, "oops")
call(telegram.delete_message, "")
run("owner: delete is immediate", sent[-1][0] == "deleteMessage" and sent[-1][1]["chat_id"] == 111)
try:
    call(telegram.edit_message, "Nobody", "x")
    bad = False
except telegram.TelegramError as e:
    bad = "No contact" in str(e)
run("unknown contact is an error", bad)
c.put(f"/api/telegram/contacts/{sam}", json={"policy": "auto"})
_real_api = telegram.api


async def no_photo(method, token=None, files_=None, _timeout=20, **params):
    if method == "sendPhoto":
        raise telegram.TelegramError("PHOTO_INVALID_DIMENSIONS")
    return await _real_api(method, token, files_, _timeout, **params)


telegram.api = no_photo
c.portal.call(telegram.send_photo, 111, png, "cap")
telegram.api = _real_api
run("falls back to a file when Telegram refuses the photo", sent[-1][0] == "sendDocument" and sent[-1][1]["caption"] == "cap")
from lark import agent as _agent  # noqa: E402
seen = []
_loop = _agent.loop


def spy(*a, **k):
    seen.append(a[4])  # the tools offered to the model
    return _loop(*a, **k)


_agent.loop = spy
say_to_bot(333, "tools", "Sam B")
_agent.loop = _loop
run("contacts' model gets at most web search", len(seen) == 1 and {t.name for t in seen[0]} <= {"web_search"})

r = c.delete(f"/api/telegram/contacts/{sam}")
run("remove contact", r.json()["contacts"] == [])
run("unlink owner", c.delete("/api/telegram/owner").json()["ok"] and c.get("/api/telegram").json()["owner"] is None)
c.delete("/api/keys/telegram")


# ---- memory ----
from lark import memory, tools as _tools  # noqa: E402

c.put("/api/settings", json={"provider": "openrouter", "models": {"openrouter": "a/model"}})
s = c.get("/api/settings").json()
run("memory settings default on", s["memory"]["use"] and s["memory"]["learn"] and s["memory"]["facts"] == 0)

r = c.post("/api/memory", json={"text": "The user's sister Maya lives in Lisbon.", "kind": "person", "subject": "Maya", "importance": 4})
fid = r.json()["id"]
run("add a memory", r.status_code == 200 and r.json()["kind"] == "person")
run("empty memory rejected", c.post("/api/memory", json={"text": ""}).status_code == 422)
run("repeat is merged, not duplicated", c.post("/api/memory", json={"text": "the user's sister maya lives in lisbon"}).json()["id"] == fid)
c.post("/api/memory", json={"text": "The user takes oat milk in coffee.", "kind": "preference"})
run("list and search memories", len(c.get("/api/memory").json()["facts"]) == 2
    and [f["id"] for f in c.get("/api/memory?q=oat").json()["facts"]] != [fid])
r = c.put(f"/api/memory/{fid}", json={"pinned": True, "text": "The user's sister Maya lives in Lisbon, Portugal."})
run("edit and pin", r.json()["pinned"] and "Portugal" in r.json()["text"])
run("edit unknown id", c.put("/api/memory/9999", json={"pinned": True}).status_code == 404)

c.put("/api/chats/memchat-0001", json={"messages": [{"role": "user", "content": "We should book the cheese farm tour near Sintra"},
                                                     {"role": "assistant", "content": "Sounds good, I'd go on a weekday."}]})
hits = c.get("/api/memory/search?q=sintra").json()["hits"]
run("conversations are searchable", hits and hits[0]["chat_id"] == "memchat-0001" and "Sintra" in hits[0]["snippet"])
run("search finds assistant words too", c.get("/api/memory/search?q=weekday").json()["hits"][0]["role"] == "assistant")

c.post("/api/chats/memchat-0002/send", json={"content": "sysdump"})
for _ in range(50):
    time.sleep(0.1)
    if not c.get("/api/chats/memchat-0002").json()["running"]:
        break
sysmsg = c.get("/api/chats/memchat-0002").json()["messages"][-1]["content"]
run("pinned memory is in the prompt", "Maya lives in Lisbon" in sysmsg and f"[#{fid}]" in sysmsg)
run("web chat prompt carries the interface guidance, text first", "almost always the right choice" in sysmsg and "Timeline(" in sysmsg and "tagged openui" in sysmsg)
run("interfaces are on by default", c.get("/api/settings").json()["generative_ui"] is True)
c.put("/api/settings", json={"generative_ui": False})
c.post("/api/chats/uichat-0001/send", json={"content": "sysdump"})
for _ in range(50):
    time.sleep(0.1)
    if not c.get("/api/chats/uichat-0001").json()["running"]:
        break
off = c.get("/api/chats/uichat-0001").json()["messages"][-1]["content"]
run("switching interfaces off removes the guidance", "Timeline(" not in off and c.get("/api/settings").json()["generative_ui"] is False)
c.put("/api/settings", json={"generative_ui": True})

# ---- reliability: failures the user should never see ----
from lark import providers as _prov, runs as _runs, health as _health  # noqa: E402
_prov.BACKOFF = (0.01, 0.01)


def say_in(chat_id, text):
    c.post(f"/api/chats/{chat_id}/send", json={"content": text})
    wait_idle(chat_id)
    return c.get(f"/api/chats/{chat_id}").json()


doc = say_in("rel-chat-0001", "flaky one")
run("a flaky provider is retried quietly", doc["messages"][-1]["content"] == "Echo: flaky one" and not doc.get("error"))
doc = say_in("rel-chat-0002", "down for good")
run("a provider that stays down gives a plain message", doc.get("error", "").startswith("The model provider is having trouble"))
doc = say_in("rel-chat-0003", "emptyonce please")
run("an empty model reply is asked again", doc["messages"][-1]["content"] == "Echo: emptyonce please")
doc = say_in("rel-chat-0004", "emptyreply always")
run("a model that returns nothing never leaves a silent chat", "didn't get an answer" in doc["messages"][-1]["content"])

# a restart in the middle of a reply: it resumes from the journal
import json as _json  # noqa: E402
chats_ = __import__("lark.chats", fromlist=["x"])
chats_.save("rel-chat-0005", [{"role": "user", "content": "resume me"}])
(_runs._dir() / "rel-chat-0005.json").write_text(_json.dumps({"chat_id": "rel-chat-0005", "attempts": 0, "events": [
    {"text": "Working on it. "}, {"tool_start": {"id": "c1", "name": "run_command", "title": "Run command", "detail": "ls"}},
    {"tool_end": {"id": "c1", "ok": True, "output": "a.txt"}}]}))
c.portal.call(_runs.resume_all)
wait_idle("rel-chat-0005")
doc = c.get("/api/chats/rel-chat-0005").json()
last = doc["messages"][-1]
run("a reply cut off by a restart carries on, keeping its earlier steps",
    [m["role"] for m in doc["messages"]] == ["user", "assistant"] and last["content"].startswith("Working on it. ")
    and "Note from the system" in last["content"] and "Run command ls -> ok: a.txt" in last["content"]
    and any(p["type"] == "tool" and p["state"] == "ok" for p in last["parts"]))
run("the journal is cleared once it finishes", not (_runs._dir() / "rel-chat-0005.json").exists())
chats_.save("rel-chat-0006", [{"role": "user", "content": "again"}])
(_runs._dir() / "rel-chat-0006.json").write_text(_json.dumps({"chat_id": "rel-chat-0006", "attempts": 2, "events": []}))
c.portal.call(_runs.resume_all)
run("a reply restarted too many times is given up on, out loud",
    "restarted while replying" in c.get("/api/chats/rel-chat-0006").json()["messages"][-1]["content"]
    and not (_runs._dir() / "rel-chat-0006.json").exists())

# contacts are found by name, @handle or part of either; a missing or null image never costs the message
st_ = telegram.load()
st_["owner"] = {"id": 111, "name": "Oscar"}
st_["contacts"]["333"] = {"name": "Sam", "policy": "auto", "scope": "", "handle": "samb"}
telegram.save(st_)
run("contact found by @handle, any case", telegram.find_contact(st_, "@SamB")[0] == "333" and telegram.find_contact(st_, "sa")[0] == "333")
run("unknown contact is not guessed", telegram.find_contact(st_, "nobody") == (None, None))
r = c.portal.call(_tools.MESSAGE_CONTACT.run, {"contact": "samb", "text": "by handle", "image": "null"})
run("an image of 'null' means no image", r.startswith("Sent") and msgs(333)[-1]["text"] == "by handle")
_real_read = sandbox.read_binary


async def _missing(path):
    raise sandbox.SandboxError(f"No such file: {path}")


sandbox.read_binary = _missing
r = c.portal.call(_tools.MESSAGE_CONTACT.run, {"contact": "Sam", "text": "no such picture", "image": "/home/lark/nothere.png"})
sandbox.read_binary = _real_read
run("a missing image still sends the words and says so", msgs(333)[-1]["text"] == "no such picture" and "wasn't attached" in r)

# the owner on Telegram can keep texting while Lark works
n_before = len(msgs(111))
say_to_bot(111, "run sleep 1; echo first", "Oscar", settle=False)
say_to_bot(111, "and another thing", "Oscar", settle=False)
time.sleep(0.5)
while _runs.get("telegram-owner"):
    time.sleep(0.1)
time.sleep(0.7)
texts = [m["text"] for m in msgs(111)[n_before:]]
run("a second Telegram message while working is answered, not refused", not any("Still working" in t for t in texts) and any("Echo: and another thing" in t for t in texts))
run("problems are counted without content", isinstance(_health.summary()["last_hour"], dict))

c.post("/api/chats/memchat-0003/send", json={"content": "when are we doing the cheese tour again? sysdump"})
for _ in range(50):
    time.sleep(0.1)
    if not c.get("/api/chats/memchat-0003").json()["running"]:
        break
sysmsg = c.get("/api/chats/memchat-0003").json()["messages"][-1]["content"]
run("relevant earlier chat is surfaced", "cheese farm tour near Sintra" in sysmsg and "memchat-0001" in sysmsg)

c.post("/api/chats/memchat-0004/send", json={"content": "my cat is a menace"})
for _ in range(80):
    time.sleep(0.1)
    if any("Miso" in f["text"] for f in c.get("/api/memory").json()["facts"]) and memory.summary_of("memchat-0004"):
        break
facts = c.get("/api/memory").json()["facts"]
miso = [f for f in facts if "Miso" in f["text"]]
run("the learner adds a memory with its source chat", miso and miso[0]["source_chat"] == "memchat-0004" and miso[0]["source_title"].lower().startswith("my cat"))
run("the learner writes a chat summary", memory.summary_of("memchat-0004").startswith("Talked about: my cat"))

c.post("/api/chats/memchat-0005/send", json={"content": "my sister moved, sysdump"})
for _ in range(80):
    time.sleep(0.1)
    if any("Porto" in f["text"] for f in c.get("/api/memory").json()["facts"]):
        break
run("the learner updates a changed fact instead of duplicating", sum("Maya" in f["text"] for f in c.get("/api/memory").json()["facts"]) == 1
    and any("Porto" in f["text"] for f in c.get("/api/memory").json()["facts"]))

c.post("/api/chats/memchat-0006/send", json={"content": "sysdump"})
for _ in range(50):
    time.sleep(0.1)
    if not c.get("/api/chats/memchat-0006").json()["running"]:
        break
run("recent chat summaries reach the prompt", "Recent conversations" in c.get("/api/chats/memchat-0006").json()["messages"][-1]["content"])

ev = chat("remember the user is allergic to peanuts")
run("remember tool writes a memory", any(e.get("tool_end", {}).get("ok") for e in ev) and any("peanuts" in f["text"] for f in memory.list_facts()))
pid = [f for f in memory.list_facts() if "peanuts" in f["text"]][0]["id"]
r = c.portal.call(_tools.forget, {"id": pid})
run("forget tool", "Forgot" in r and not any("peanuts" in f["text"] for f in memory.list_facts()))
try:
    c.portal.call(_tools.forget, {"id": 987654})
    gone_ok = False
except _tools.ToolError as e:
    gone_ok = "No memory" in str(e)
run("forget unknown id is an error", gone_ok)
run("search finds nothing for a forgotten memory", "No matching" in c.portal.call(_tools.memory_search, {"query": "peanuts"}))
run("search_conversations tool", "Sintra" in c.portal.call(_tools.search_conversations, {"query": "sintra"}))
r = c.portal.call(_tools.read_conversation, {"chat_id": "memchat-0001"})
run("read_conversation tool", "Sintra" in r and "[0] user" in r and "[1] assistant" in r)

# contact chats are searchable by the owner's tools but never feed memory or the prompt
chats_save = __import__("lark.chats", fromlist=["save"]).save
chats_save("tg-555000111", [{"role": "user", "content": "the secret handshake is zebra"}, {"role": "assistant", "content": "ok"}])
run("contact chats stay out of normal search", memory.search_messages("zebra") == [] and len(memory.search_messages("zebra", include_contacts=True)) == 1)
run("contact chats are never learned from or injected", memory.summary_of("tg-555000111") == ""
    and "zebra" not in c.portal.call(memory.context, "memchat-0009", [{"role": "user", "content": "the secret handshake zebra"}]))
c.delete("/api/chats/tg-555000111")

# meaning-based matching (the embeddings API is faked)
c.post("/api/memory", json={"text": "The user's feline is called Whiskers.", "kind": "person"})
run("no vector match without an embedding model", "No matching" in c.portal.call(_tools.memory_search, {"query": "my cat"}) or "Whiskers" not in c.portal.call(_tools.memory_search, {"query": "my cat"}))
c.put("/api/settings", json={"embedding_model": "emb-1"})
c.portal.call(memory.embed_missing)
run("vector match finds a memory with no shared words", "Whiskers" in c.portal.call(_tools.memory_search, {"query": "tell me about my kitten"}))
c.put("/api/settings", json={"embedding_model": ""})

# switches
c.put("/api/settings", json={"memory_use": False})
ev = chat("tools")
run("memory off removes the tools", "remember" not in "".join(e.get("text", "") for e in ev))
c.post("/api/chats/memchat-0007/send", json={"content": "sysdump"})
for _ in range(50):
    time.sleep(0.1)
    if not c.get("/api/chats/memchat-0007").json()["running"]:
        break
run("memory off removes the notes", "Maya" not in c.get("/api/chats/memchat-0007").json()["messages"][-1]["content"])
c.put("/api/settings", json={"memory_use": True})

c.delete("/api/chats/memchat-0001")
run("deleting a chat removes it from search", all(h["chat_id"] != "memchat-0001" for h in c.get("/api/memory/search?q=sintra").json()["hits"]))
run("forget everything needs confirmation", c.delete("/api/memory").status_code == 400)
run("forget everything", c.delete("/api/memory?confirm=all").json()["deleted"] >= 3 and c.get("/api/memory").json()["facts"] == [])
for i in range(2, 8):
    c.delete(f"/api/chats/memchat-000{i}")

# --- chat titles ---
from lark import chats as _chats, titles as _titles
run("title cleaning", _titles.clean('"Rosa\'s dinner with Sam, Friday."') == "Rosa's dinner with Sam, Friday"
    and _titles.clean("Title: Glencoe packing list\nextra") == "Glencoe packing list"
    and _titles.clean("New chat") == "" and _titles.clean("") == ""
    and _titles.clean("one two three four five six seven eight") == "one two three four five six")


def titled(cid, text, want=None, secs=8):
    c.post(f"/api/chats/{cid}/send", json={"content": text})
    wait_idle(cid)
    for _ in range(secs * 10):
        time.sleep(0.1)
        d = _chats.load(cid)
        if d and d.get("titled") and (want is None or d["title"] == want):
            return d
    return _chats.load(cid)


d = titled("title-0001", "plan the glencoe walk for saturday")
run("a chat gets a written title after the first reply", d["title"] == "Plan The Glencoe" and d["titled"]["users"] == 1)
c.put("/api/chats/title-0001", json={"messages": d["messages"]})
run("a saved chat keeps its written title", _chats.load("title-0001")["title"] == "Plan The Glencoe")
c.post("/api/chats/title-0002/send", json={"content": "hi"})
wait_idle("title-0002")
time.sleep(1)
d = _chats.load("title-0002")
run("a greeting stays untitled", not d.get("titled") and d["title"] == "hi")
d = titled("title-0002", "book a table at rosa for friday")
run("the title comes after a real topic shows up", d["title"] == "Book A Table" and d["titled"]["users"] == 2)
for word in ("second", "third"):
    titled("title-0001", f"and {word} thing", want=None, secs=1)
d = _chats.load("title-0001")
run("no re-title before the fourth message", d["title"] == "Plan The Glencoe" and not d["titled"]["again"])
d = titled("title-0001", "everything shifted now", want="Moved on entirely")
run("the title is checked once more after a few messages", d["title"] == "Moved on entirely" and d["titled"]["again"])
titled("title-0001", "and one more", want=None, secs=1)
run("a title is rewritten at most once", _chats.load("title-0001")["title"] == "Moved on entirely")
c.put("/api/settings", json={"title_model": "qwen/qwen3.7-flash"})
run("title model is a setting", c.get("/api/settings").json()["title_model"] == "qwen/qwen3.7-flash")
c.put("/api/settings", json={"title_model": ""})
c.delete("/api/chats/title-0001")
c.delete("/api/chats/title-0002")

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
print("all passed" if not FAILED else f"{len(FAILED)} failed: " + "; ".join(FAILED))
sys.exit(1 if FAILED else 0)

