import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, auth, chats, files, providers, runs, sandbox, search, telegram, vault

KEY_NAMES = set(providers.PROVIDERS) | set(search.SEARCH_PROVIDERS) | {"telegram"}
DIST = Path(os.environ.get("LARK_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))

@asynccontextmanager
async def lifespan(_app):
    poller = asyncio.ensure_future(telegram.poll())  # the Telegram bot, when a token is saved
    yield
    poller.cancel()


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.middleware("http")
async def guard(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
    if (request.url.path == "/api/health" and request.client and request.client.host in ("127.0.0.1", "::1")
            and auth.host_is_local(request.headers.get("host", ""))):
        # for the deploy script on the server itself: wait until nothing is running before a restart
        return JSONResponse({"running": runs.running()})
    if not auth.password():
        if not auth.host_is_local(request.headers.get("host", "")):
            return err(403, "Set LARK_PASSWORD on the server before exposing Lark beyond localhost.")
    elif request.url.path != "/api/login" and not auth.session_ok(request.cookies.get(auth.COOKIE)):
        return err(401, "Sign in required.")
    return await call_next(request)


class Login(BaseModel):
    password: str


@app.post("/api/login")
async def login(body: Login):
    if not auth.password():
        return {"ok": True}
    wait = auth.seconds_paused()
    if wait:
        return err(429, f"Too many wrong passwords. Try again in {wait // 60 + 1} min.")
    if not auth.password_ok(body.password):
        auth.record_miss()
        await asyncio.sleep(1)
        return err(401, "Wrong password.")
    auth.record_success()
    res = JSONResponse({"ok": True})
    res.set_cookie(auth.COOKIE, auth.token(), httponly=True, samesite="strict",
                   secure=bool(os.environ.get("LARK_SECURE_COOKIE")), max_age=60 * 60 * 24 * 30)
    return res


@app.post("/api/logout")
async def logout():
    res = JSONResponse({"ok": True})
    res.delete_cookie(auth.COOKIE)
    return res


def view_settings() -> dict:
    s = vault.load()
    return {
        "provider": s["provider"],
        "models": s["models"],
        "search": s["search"],
        "browser_cookies": s["browser_cookies"],
        "auth": bool(auth.password()),
        "sandbox": sandbox.configured(),
        "providers": {
            name: {"label": p["label"], "keys_url": p["keys_url"], "key_hint": vault.key_hint(name)}
            for name, p in providers.PROVIDERS.items()
        },
        "telegram": {"label": "Telegram bot", "keys_url": "https://t.me/BotFather", "key_hint": vault.key_hint("telegram")},
        "search_providers": {
            name: {"label": p["label"], "keys_url": p["keys_url"], "key_hint": vault.key_hint(name)}
            for name, p in search.SEARCH_PROVIDERS.items()
        },
    }


@app.get("/api/settings")
async def get_settings():
    return view_settings()


class SettingsIn(BaseModel):
    provider: str | None = None
    models: dict[str, str] | None = None
    search: str | None = None
    browser_cookies: bool | None = None


@app.put("/api/settings")
async def put_settings(body: SettingsIn):
    if body.provider and body.provider not in providers.PROVIDERS:
        return err(400, "Unknown provider.")
    if body.models and set(body.models) - set(providers.PROVIDERS):
        return err(400, "Unknown provider.")
    if body.search and body.search not in search.SEARCH_PROVIDERS:
        return err(400, "Unknown search provider.")
    vault.update(body.provider, {k: v.strip() for k, v in (body.models or {}).items()}, body.search, body.browser_cookies)
    return view_settings()


class KeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=500)


@app.put("/api/keys/{name}")
async def put_key(name: str, body: KeyIn):
    if name not in KEY_NAMES:
        return err(404, "Unknown provider.")
    vault.set_key(name, body.key.strip())
    if name == "telegram":
        telegram.poke()
    return view_settings()


@app.delete("/api/keys/{name}")
async def delete_key(name: str):
    if name not in KEY_NAMES:
        return err(404, "Unknown provider.")
    vault.delete_key(name)
    return view_settings()


@app.post("/api/keys/{name}/test")
async def test_key(name: str):
    if name not in KEY_NAMES:
        return err(404, "Unknown provider.")
    key = vault.get_key(name)
    if not key:
        return err(400, "No key saved.")
    try:
        if name == "telegram":
            me = await telegram.api("getMe", key)
            return {"ok": True, "detail": f"Bot @{me['username']} is reachable."}
        if name in search.SEARCH_PROVIDERS:
            rows = await search.search(name, key, "test", 1)
            return {"ok": True, "detail": "Key works." if rows is not None else ""}
        return {"ok": True, "detail": await providers.check_key(name, key)}
    except Exception as e:
        return err(400, str(e) if isinstance(e, RuntimeError) else "Could not reach the provider.")


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    policy: str = Field(default="draft", pattern="^(draft|auto|relay|blocked)$")
    scope: str = Field(default="", max_length=500)


class ContactEdit(BaseModel):
    policy: str | None = Field(default=None, pattern="^(draft|auto|relay|blocked)$")
    scope: str | None = Field(default=None, max_length=500)


@app.get("/api/telegram")
async def telegram_status():
    out = {"configured": bool(vault.get_key("telegram")), **telegram.view(), "error": telegram.status["error"], "bot": None,
           "polling": time.time() - telegram.status["polled"] < 60}
    if out["configured"]:
        try:
            out["bot"] = (await telegram.bot_info())["username"]
        except telegram.TelegramError as e:
            out["error"] = str(e)
    return out


@app.post("/api/telegram/link")
async def telegram_link():
    try:
        return {"url": await telegram.link_url()}
    except telegram.TelegramError as e:
        return err(400, str(e))


@app.delete("/api/telegram/owner")
async def telegram_unlink():
    st = telegram.load()
    st["owner"] = None
    telegram.save(st)
    return {"ok": True}


@app.post("/api/telegram/invites")
async def telegram_invite(body: ContactIn):
    try:
        return {"url": await telegram.invite_url(body.name.strip(), body.policy, body.scope.strip())}
    except telegram.TelegramError as e:
        return err(400, str(e))


@app.put("/api/telegram/contacts/{cid}")
async def telegram_edit_contact(cid: str, body: ContactEdit):
    st = telegram.load()
    c = st["contacts"].get(cid)
    if not c:
        return err(404, "No such contact.")
    if body.policy:
        c["policy"] = body.policy
    if body.scope is not None:
        c["scope"] = body.scope.strip()
    telegram.save(st)
    return telegram.view()


@app.delete("/api/telegram/contacts/{cid}")
async def telegram_remove_contact(cid: str):
    st = telegram.load()
    st["contacts"].pop(cid, None)
    telegram.save(st)
    chats.delete(telegram.contact_chat_id(cid))
    return telegram.view()


@app.get("/api/providers/{name}/models")
async def models(name: str):
    if name not in providers.PROVIDERS:
        return err(404, "Unknown provider.")
    try:
        return {"models": await providers.list_models(name, vault.get_key(name))}
    except Exception as e:
        return err(502, str(e) if isinstance(e, RuntimeError) else "Could not reach the provider.")


class Message(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=200_000)


class ChatIn(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=500)


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def ready():
    """The provider settings a chat needs: (name, key, model), or an error response."""
    s = vault.load()
    name = s["provider"]
    label = providers.PROVIDERS[name]["label"]
    key = vault.get_key(name)
    model = s["models"].get(name, "")
    if not key:
        return err(400, f"No {label} key yet. Add one in Settings.")
    if not model:
        return err(400, f"Choose a {label} model in Settings.")
    return name, key, model


STREAM_HEADERS = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}


@app.post("/api/chat")
async def chat(body: ChatIn):
    """Stateless one-shot: stream a reply to the given messages. The app itself uses /api/chats/{id}/send."""
    conf = ready()
    if isinstance(conf, JSONResponse):
        return conf
    history = [m.model_dump() for m in body.messages]

    async def events():
        async for chunk in agent.run(*conf, history):
            yield sse(chunk)

    return StreamingResponse(events(), media_type="text/event-stream", headers=STREAM_HEADERS)


class SavedChat(BaseModel):
    messages: list[dict] = Field(max_length=1000)


@app.get("/api/chats")
async def list_chats():
    live = runs.running_ids()
    return {"chats": [{**c, "running": c["id"] in live} for c in chats.listing()]}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str):
    doc = chats.load(chat_id) if chats.valid(chat_id) else None
    if not doc:
        return err(404, "No such chat.")
    return {**doc, "running": bool(runs.get(chat_id))}


@app.put("/api/chats/{chat_id}")
async def put_chat(chat_id: str, body: SavedChat):
    if not chats.valid(chat_id):
        return err(400, "Bad chat id.")
    if runs.get(chat_id):
        return err(409, "This chat is busy replying.")
    try:
        return chats.save(chat_id, body.messages)
    except ValueError as e:
        return err(413, str(e))


class SendIn(BaseModel):
    content: str = Field(default="", max_length=100_000)
    images: list[str] = Field(default=[], max_length=8)  # file names from /api/uploads


RECENT_WITH_IMAGES = 8  # older images are dropped from what the model sees, to keep requests small


def model_history(messages: list[dict]) -> list[dict]:
    out = []
    cutoff = len(messages) - RECENT_WITH_IMAGES
    for i, m in enumerate(messages):
        text = m.get("content") or ""
        urls = [u for u in (files.data_url(n) for n in m.get("images", [])) if u] if i >= cutoff else []
        if not text and not m.get("images"):
            continue
        if urls:
            content = [{"type": "text", "text": text or "(image)"}] + [{"type": "image_url", "image_url": {"url": u}} for u in urls]
        else:
            content = text or "(image)"
        out.append({"role": m["role"], "content": content})
    return out


@app.post("/api/chats/{chat_id}/send")
async def send_to_chat(chat_id: str, body: SendIn):
    if not chats.valid(chat_id):
        return err(400, "Bad chat id.")
    if runs.get(chat_id):
        return err(409, "Lark is still replying in this chat.")
    conf = ready()
    if isinstance(conf, JSONResponse):
        return conf
    images = [n for n in body.images if files.path(n)]
    if not body.content.strip() and not images:
        return err(400, "Write a message or attach an image.")
    doc = chats.load(chat_id) or {"messages": []}
    user = {"role": "user", "content": body.content.strip()}
    if images:
        user["images"] = images
    messages = doc["messages"] + [user]
    try:
        chats.save(chat_id, messages)
    except ValueError as e:
        return err(413, str(e))
    runs.start(chat_id, *conf, model_history(messages))
    return {"ok": True}


@app.get("/api/chats/{chat_id}/events")
async def chat_events(chat_id: str):
    run = runs.get(chat_id) if chats.valid(chat_id) else None

    async def events():
        if not run:
            yield runs.sse({"idle": True})
            return
        async for ev in run.stream():
            yield runs.sse(ev)

    return StreamingResponse(events(), media_type="text/event-stream", headers=STREAM_HEADERS)


@app.post("/api/chats/{chat_id}/stop")
async def stop_chat(chat_id: str):
    return {"ok": bool(chats.valid(chat_id) and runs.stop(chat_id))}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    if chats.valid(chat_id):
        runs.stop(chat_id, discard=True)
        chats.delete(chat_id)
    return {"ok": True}


@app.get("/api/sandbox")
async def sandbox_status():
    if not sandbox.configured():
        return {"configured": False}
    try:
        return {"configured": True, "ok": True, "reset_available": bool(os.environ.get("LARK_SANDBOX_RESET_FILE")),
                **await sandbox.health()}
    except sandbox.SandboxError as e:
        return {"configured": True, "ok": False, "reset_available": bool(os.environ.get("LARK_SANDBOX_RESET_FILE")),
                "error": str(e)}


@app.post("/api/sandbox/wipe")
async def sandbox_wipe():
    if not sandbox.configured():
        return err(400, "No sandbox is set up.")
    try:
        await sandbox.wipe()
    except sandbox.SandboxError as e:
        return err(502, str(e))
    return {"ok": True}


@app.post("/api/uploads")
async def upload(request: Request):
    """A single image as the raw request body. Returns the stored file name."""
    if int(request.headers.get("content-length") or 0) > files.MAX_BYTES:
        return err(413, "That image is too large (8 MB max).")
    data = await request.body()
    try:
        return {"id": files.save(data)}
    except files.FileError as e:
        return err(400 if len(data) <= files.MAX_BYTES else 413, str(e))


@app.get("/api/files/{name}")
async def get_file(name: str):
    p = files.path(name)
    if not p:
        return err(404, "No such file.")
    return FileResponse(p, media_type=files.mime(name), headers={
        "Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})


STREAM_SECONDS = 15 * 60  # how long one live-view connection stays open before the page reconnects


@app.get("/api/browser/stream")
async def browser_stream(request: Request):
    """The sandbox browser as a live MJPEG stream (a few frames a second while a page is open)."""
    if not sandbox.configured():
        return err(404, "No sandbox.")
    boundary = "frame"

    async def frames():
        end = asyncio.get_event_loop().time() + STREAM_SECONDS
        yield f"--{boundary}\r\n".encode()
        while asyncio.get_event_loop().time() < end:
            if await request.is_disconnected():
                return
            data = await sandbox.frame()
            if data:
                # Browsers paint a part once the next one starts arriving, so unchanged frames are resent
                # (a few KB each) rather than leaving the last one stuck behind a quiet connection.
                yield (f"Content-Type: image/jpeg\r\nContent-Length: {len(data)}\r\n\r\n").encode() + data + f"\r\n--{boundary}\r\n".encode()
            await asyncio.sleep(0.35)

    return StreamingResponse(frames(), media_type=f"multipart/x-mixed-replace; boundary={boundary}", headers=STREAM_HEADERS)


@app.get("/api/browser/cursor")
async def browser_cursor():
    if not sandbox.configured():
        return err(404, "No sandbox.")
    return {"cursor": await sandbox.cursor()}


@app.post("/api/browser/clear")
async def browser_clear():
    if not sandbox.configured():
        return err(400, "No sandbox is set up.")
    try:
        await sandbox.clear_browser()
    except sandbox.SandboxError as e:
        return err(502, str(e))
    return {"ok": True}


@app.post("/api/sandbox/reset")
async def sandbox_reset():
    """Full reset: the container is recreated from a clean image by a root-owned unit on the server."""
    path = os.environ.get("LARK_SANDBOX_RESET_FILE")
    if not path:
        return err(400, "Full reset isn't set up. Run sandbox/sandbox.sh install-reset on the server.")
    try:
        Path(path).write_text("reset\n")
    except OSError:
        return err(500, "Couldn't ask the server to reset the sandbox.")
    return {"ok": True}


if DIST.is_dir():
    class Assets(StaticFiles):
        async def get_response(self, path, scope):
            res = await super().get_response(path, scope)
            res.headers["Cache-Control"] = "public, max-age=31536000, immutable"  # file names carry a content hash
            return res

    app.mount("/assets", Assets(directory=DIST / "assets"), name="assets")
    NO_CACHE = {"Cache-Control": "no-cache"}  # the page itself is revalidated, so a deploy shows on a normal refresh

    @app.get("/{path:path}")
    async def spa(path: str):
        file = (DIST / path).resolve()
        if path and DIST in file.parents and file.is_file():
            return FileResponse(file, headers=NO_CACHE)
        return FileResponse(DIST / "index.html", headers=NO_CACHE)
