"""Client for the sandbox exec agent (sandbox/agent.py). Set LARK_SANDBOX_URL and LARK_SANDBOX_TOKEN to enable."""
import os

import httpx


class SandboxError(RuntimeError):
    pass


def configured() -> bool:
    return bool(os.environ.get("LARK_SANDBOX_URL") and os.environ.get("LARK_SANDBOX_TOKEN"))


async def _call(method: str, path: str, *, json=None, params=None, timeout: float = 15.0) -> dict:
    url = os.environ["LARK_SANDBOX_URL"].rstrip("/") + path
    headers = {"Authorization": f"Bearer {os.environ['LARK_SANDBOX_TOKEN']}"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.request(method, url, json=json, params=params, headers=headers)
    except httpx.HTTPError:
        raise SandboxError("The sandbox isn't reachable. Is the container running?")
    data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200:
        raise SandboxError(data.get("error") or f"The sandbox returned an error ({r.status_code}).")
    return data


async def run(command: str, timeout: int) -> dict:
    return await _call("POST", "/exec", json={"command": command, "timeout": timeout}, timeout=timeout + 15)


async def kill_all() -> None:
    """Kill every command running in the sandbox (used when a reply is stopped)."""
    try:
        await _call("POST", "/exec/kill", timeout=5)
    except SandboxError:
        pass


async def read(path: str) -> dict:
    return await _call("GET", "/file", params={"path": path})


async def write(path: str, content: str) -> dict:
    return await _call("PUT", "/file", json={"path": path, "content": content})


async def browse(args: dict) -> dict:
    """Returns {"snapshot": str, "image": base64 jpeg (screenshot action only)}."""
    keep = {k: args[k] for k in ("action", "url", "id", "x", "y", "text", "submit", "key", "keys", "direction") if k in args}
    return await _call("POST", "/browser", json=keep, timeout=100)


async def frame() -> bytes | None:
    """The live view's latest JPEG, or None when the browser isn't open."""
    url = os.environ["LARK_SANDBOX_URL"].rstrip("/") + "/browser/frame"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {os.environ['LARK_SANDBOX_TOKEN']}"})
    except httpx.HTTPError:
        return None
    return r.content if r.status_code == 200 and r.content else None


async def cursor() -> dict | None:
    """Where the browser last clicked or typed ({x, y, click, seq}, as fractions of the page), or None."""
    try:
        return (await _call("GET", "/browser/cursor", timeout=5)).get("cursor")
    except SandboxError:
        return None


async def read_binary(path: str) -> bytes:
    import base64
    return base64.b64decode((await _call("GET", "/file", params={"path": path, "binary": "1"}, timeout=30))["b64"])


_features: dict = {"at": 0.0, "browser": False}


async def has_browser() -> bool:
    """Whether the sandbox image includes a browser. Cached briefly so each chat turn doesn't probe it."""
    import time
    if not configured():
        return False
    if time.time() - _features["at"] > 60:
        try:
            _features["browser"] = bool((await health()).get("browser"))
        except SandboxError:
            _features["browser"] = False
        _features["at"] = time.time()
    return _features["browser"]


async def health() -> dict:
    return await _call("GET", "/health")


async def wipe() -> dict:
    return await _call("POST", "/wipe", timeout=120)
