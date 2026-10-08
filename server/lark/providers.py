"""Chat providers. Both speak the OpenAI chat completions protocol."""
import asyncio
import json
from collections.abc import AsyncIterator

import httpx

PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter",
        "base": "https://openrouter.ai/api/v1",
        "keys_url": "https://openrouter.ai/settings/keys",
        "headers": {"X-OpenRouter-Title": "Lark"},
    },
    "gateway": {
        "label": "Vercel AI Gateway",
        "base": "https://ai-gateway.vercel.sh/v1",
        "keys_url": "https://vercel.com/~/ai-gateway/api-keys",
        "headers": {},
    },
}

TIMEOUT = httpx.Timeout(30.0, read=300.0)


def _headers(name: str, key: str) -> dict:
    return {"Authorization": f"Bearer {key}", **PROVIDERS[name]["headers"]}


def _error_text(resp_text: str, status: int) -> str:
    try:
        err = json.loads(resp_text).get("error")
        msg = err.get("message") if isinstance(err, dict) else err
        if msg:
            return f"{msg} ({status})"
    except (ValueError, AttributeError):
        pass
    return f"The provider returned an error ({status})."


async def list_models(name: str, key: str | None) -> list[str]:
    headers = _headers(name, key) if key else PROVIDERS[name]["headers"]
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{PROVIDERS[name]['base']}/models", headers=headers)
    if r.status_code != 200:
        raise RuntimeError(_error_text(r.text, r.status_code))
    return sorted(m["id"] for m in r.json().get("data", []))


async def check_key(name: str, key: str) -> str:
    """Raises RuntimeError if the key is rejected. Returns a short detail line."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        if name == "openrouter":
            r = await client.get(f"{PROVIDERS[name]['base']}/key", headers=_headers(name, key))
        else:
            r = await client.get(f"{PROVIDERS[name]['base']}/models", headers=_headers(name, key))
    if r.status_code != 200:
        raise RuntimeError(_error_text(r.text, r.status_code))
    return "Key accepted."


RETRIES = 3  # attempts per model call when the provider fails before saying anything
BACKOFF = (1.0, 3.0)  # seconds to wait before the 2nd and 3rd attempt


def friendly(status: int | None, detail: str = "") -> str:
    """What to tell the user when a model call failed for good."""
    if status in (401, 403):
        text = "The provider rejected the API key. Check it in Settings."
    elif status == 402:
        text = "The provider says the account is out of credit."
    elif status == 404:
        text = "The provider doesn't know that model. Pick another in Settings."
    elif status == 429:
        text = "The model is busy right now. Try again in a minute."
    elif status and status >= 500:
        text = "The model provider is having trouble right now. Try again in a moment."
    else:
        text = "Couldn't reach the model provider. Check the connection and try again."
    return f"{text} ({detail})" if detail and status not in (429,) and not (status and status >= 500) else text


async def stream_round(name: str, key: str, model: str, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[dict]:
    """One model call. Yields {"text"} chunks, then {"tool_calls": [...]} if the model asked for tools,
    then {"done": True}; or {"error": str}. Transient failures before any output are retried quietly."""
    last = {"error": friendly(None)}
    for attempt in range(RETRIES):
        spoke = False
        retry = False
        async for ev in _once(name, key, model, messages, tools):
            if "error" in ev:
                last = ev
                retry = not spoke and ev.get("transient", False)
                break
            spoke = spoke or "text" in ev or "tool_calls" in ev
            yield ev
        else:
            return
        if not retry or attempt == RETRIES - 1:
            break
        await asyncio.sleep(min(ev.get("wait") or BACKOFF[min(attempt, len(BACKOFF) - 1)], 10))
    yield {"error": last["error"]}


async def _once(name: str, key: str, model: str, messages: list[dict], tools: list[dict] | None):
    body = {"model": model, "messages": messages, "stream": True}
    if tools:
        body["tools"] = tools
    calls: dict[int, dict] = {}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            async with client.stream(
                "POST",
                f"{PROVIDERS[name]['base']}/chat/completions",
                json=body,
                headers=_headers(name, key),
            ) as r:
                if r.status_code != 200:
                    text = (await r.aread()).decode(errors="replace")
                    wait = None
                    try:
                        wait = float(r.headers.get("retry-after", ""))
                    except ValueError:
                        pass
                    detail = _error_text(text, r.status_code)
                    yield {"error": friendly(r.status_code, detail.rsplit(" (", 1)[0] if r.status_code < 500 else ""),
                           "transient": r.status_code in (408, 409, 425, 429) or r.status_code >= 500, "wait": wait}
                    return
                async for line in r.aiter_lines():
                    if not line.startswith("data:"):
                        continue  # keep-alive comments
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if obj.get("error"):
                        err = obj["error"]
                        msg = err.get("message", "") if isinstance(err, dict) else str(err)
                        yield {"error": msg or "The provider stopped mid-reply.", "transient": True}
                        return
                    for choice in obj.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield {"text": delta["content"]}
                        for tc in delta.get("tool_calls") or []:
                            i = tc.get("index", 0)
                            call = calls.setdefault(i, {"id": "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                call["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name") and not call["name"]:
                                call["name"] = fn["name"]
                            call["arguments"] += fn.get("arguments") or ""
    except httpx.HTTPError as e:
        yield {"error": friendly(None), "transient": True}
        return
    if calls:
        yield {"tool_calls": [{**c, "id": c["id"] or f"call_{i}"} for i, c in sorted(calls.items())]}
    yield {"done": True}
