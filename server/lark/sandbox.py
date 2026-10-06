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


async def read(path: str) -> dict:
    return await _call("GET", "/file", params={"path": path})


async def write(path: str, content: str) -> dict:
    return await _call("PUT", "/file", json={"path": path, "content": content})


async def health() -> dict:
    return await _call("GET", "/health")


async def wipe() -> dict:
    return await _call("POST", "/wipe", timeout=120)
