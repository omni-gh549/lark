import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import agent, auth, chats, providers, sandbox, search, vault

KEY_NAMES = set(providers.PROVIDERS) | set(search.SEARCH_PROVIDERS)
DIST = Path(os.environ.get("LARK_DIST", Path(__file__).resolve().parents[2] / "web" / "dist"))

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def err(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@app.middleware("http")
async def guard(request: Request, call_next):
    if not request.url.path.startswith("/api/"):
        return await call_next(request)
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
        "auth": bool(auth.password()),
        "sandbox": sandbox.configured(),
        "providers": {
            name: {"label": p["label"], "keys_url": p["keys_url"], "key_hint": vault.key_hint(name)}
            for name, p in providers.PROVIDERS.items()
        },
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


@app.put("/api/settings")
async def put_settings(body: SettingsIn):
    if body.provider and body.provider not in providers.PROVIDERS:
        return err(400, "Unknown provider.")
    if body.models and set(body.models) - set(providers.PROVIDERS):
        return err(400, "Unknown provider.")
    if body.search and body.search not in search.SEARCH_PROVIDERS:
        return err(400, "Unknown search provider.")
    vault.update(body.provider, {k: v.strip() for k, v in (body.models or {}).items()}, body.search)
    return view_settings()


class KeyIn(BaseModel):
    key: str = Field(min_length=8, max_length=500)


@app.put("/api/keys/{name}")
async def put_key(name: str, body: KeyIn):
    if name not in KEY_NAMES:
        return err(404, "Unknown provider.")
    vault.set_key(name, body.key.strip())
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
        if name in search.SEARCH_PROVIDERS:
            rows = await search.search(name, key, "test", 1)
            return {"ok": True, "detail": "Key works." if rows is not None else ""}
        return {"ok": True, "detail": await providers.check_key(name, key)}
    except Exception as e:
        return err(400, str(e) if isinstance(e, RuntimeError) else "Could not reach the provider.")


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


@app.post("/api/chat")
async def chat(body: ChatIn):
    s = vault.load()
    name = s["provider"]
    label = providers.PROVIDERS[name]["label"]
    key = vault.get_key(name)
    model = s["models"].get(name, "")
    if not key:
        return err(400, f"No {label} key yet. Add one in Settings.")
    if not model:
        return err(400, f"Choose a {label} model in Settings.")

    history = [m.model_dump() for m in body.messages]

    async def events():
        async for chunk in agent.run(name, key, model, history):
            yield sse(chunk)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


class SavedChat(BaseModel):
    messages: list[dict] = Field(max_length=1000)


@app.get("/api/chats")
async def list_chats():
    return {"chats": chats.listing()}


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str):
    doc = chats.load(chat_id) if chats.valid(chat_id) else None
    return doc or err(404, "No such chat.")


@app.put("/api/chats/{chat_id}")
async def put_chat(chat_id: str, body: SavedChat):
    if not chats.valid(chat_id):
        return err(400, "Bad chat id.")
    try:
        return chats.save(chat_id, body.messages)
    except ValueError as e:
        return err(413, str(e))


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    if chats.valid(chat_id):
        chats.delete(chat_id)
    return {"ok": True}


@app.get("/api/sandbox")
async def sandbox_status():
    if not sandbox.configured():
        return {"configured": False}
    try:
        return {"configured": True, "ok": True, **await sandbox.health()}
    except sandbox.SandboxError as e:
        return {"configured": True, "ok": False, "error": str(e)}


@app.post("/api/sandbox/wipe")
async def sandbox_wipe():
    if not sandbox.configured():
        return err(400, "No sandbox is set up.")
    try:
        await sandbox.wipe()
    except sandbox.SandboxError as e:
        return err(502, str(e))
    return {"ok": True}


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str):
        file = (DIST / path).resolve()
        if path and DIST in file.parents and file.is_file():
            return FileResponse(file)
        return FileResponse(DIST / "index.html")
