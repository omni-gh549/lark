"""Chat providers. All speak the OpenAI chat completions protocol."""
import asyncio
import contextvars
import json
from collections.abc import AsyncIterator

import httpx

from . import stats

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
    "opencode": {
        "label": "OpenCode Go",
        "base": "https://opencode.ai/inference/go/openai/v1",
        "keys_url": "https://opencode.ai/auth",
        # Go asks clients to identify themselves honestly rather than use a generic HTTP library name
        "headers": {"User-Agent": "lark/1.0 (+https://github.com/omni-gh549/lark)"},
    },
}

# the chat a model call belongs to; OpenCode Go uses it to route and cache each conversation
SESSION: contextvars.ContextVar[str | None] = contextvars.ContextVar("session", default=None)

TIMEOUT = httpx.Timeout(30.0, read=300.0)

# Set only inside a swarm run (each agent task gets its own copy of the context):
GATE: contextvars.ContextVar = contextvars.ContextVar("gate", default=None)  # an async context manager that limits model calls running at once
USAGE: contextvars.ContextVar = contextvars.ContextVar("usage", default=None)  # fn(provider, model, usage|None, chars_in, chars_out) after each call


def _headers(name: str, key: str) -> dict:
    extra = {"x-opencode-session": SESSION.get()} if name == "opencode" and SESSION.get() else {}
    return {"Authorization": f"Bearer {key}", **PROVIDERS[name]["headers"], **extra}


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
    data = r.json().get("data", [])
    if name == "opencode":  # Go also lists models that only speak other APIs (Anthropic, Responses); Lark speaks chat completions
        data = [m for m in data if (m.get("api") or {}).get("chat-completions")]
    return sorted(m["id"] for m in data)


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


async def stream_round(name: str, key: str, model: str, messages: list[dict], tools: list[dict] | None = None, extra: dict | None = None) -> AsyncIterator[dict]:
    """One model call. Yields {"text"} chunks, then {"tool_calls": [...]} if the model asked for tools,
    then {"done": True}; or {"error": str}. Transient failures before any output are retried quietly.
    Inside a swarm run the call waits its turn at the gate, so a hundred agents don't mean a hundred requests at once."""
    gate = GATE.get()
    if gate is None:
        async for ev in _stream_round(name, key, model, messages, tools, extra):
            yield ev
        return
    async with gate:
        async for ev in _stream_round(name, key, model, messages, tools, extra):
            yield ev


async def _stream_round(name: str, key: str, model: str, messages: list[dict], tools: list[dict] | None = None, extra: dict | None = None) -> AsyncIterator[dict]:
    last = {"error": friendly(None)}
    for attempt in range(RETRIES):
        spoke = False
        retry = False
        async for ev in _once(name, key, model, messages, tools, extra):
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


async def _once(name: str, key: str, model: str, messages: list[dict], tools: list[dict] | None, extra: dict | None = None):
    body = {"model": model, "messages": messages, "stream": True, **(extra or {})}
    if tools:
        body["tools"] = tools
    sink = USAGE.get()
    if sink and name == "openrouter":
        body.setdefault("usage", {"include": True})  # OpenRouter then reports what the call cost
    calls: dict[int, dict] = {}
    used = None
    written = 0
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
                    if isinstance(obj.get("usage"), dict):
                        used = obj["usage"]  # only some providers send this on a streamed reply
                    for choice in obj.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            written += len(delta["content"])
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
                            written += len(fn.get("arguments") or "")
    except httpx.HTTPError as e:
        yield {"error": friendly(None), "transient": True}
        return
    stats.record(name, model, used)
    if sink:
        try:
            # tokens when the provider says; otherwise a character count the budget can estimate from
            sink(name, model, used, 0 if isinstance(used, dict) and used.get("prompt_tokens") else len(json.dumps(messages, default=str)), written)
        except Exception:
            pass  # accounting must never break a call
    if calls:
        yield {"tool_calls": [{**c, "id": c["id"] or f"call_{i}"} for i, c in sorted(calls.items())]}
    yield {"done": True}


async def openrouter_usage(key: str) -> dict:
    """Credit balance and recent spend for an OpenRouter key. Raises RuntimeError on a refusal."""
    h = _headers("openrouter", key)
    base = PROVIDERS["openrouter"]["base"]
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        kr = await client.get(f"{base}/key", headers=h)
        cr = await client.get(f"{base}/credits", headers=h)
    if kr.status_code != 200:
        raise RuntimeError(_error_text(kr.text, kr.status_code))
    k = (kr.json() or {}).get("data") or {}
    out = {
        "day": k.get("usage_daily"), "week": k.get("usage_weekly"), "month": k.get("usage_monthly"),
        "key_used": k.get("usage"), "key_limit": k.get("limit"), "key_remaining": k.get("limit_remaining"),
        "free_tier": k.get("is_free_tier"), "total": None, "used": None, "remaining": None,
    }
    if cr.status_code == 200:
        c = (cr.json() or {}).get("data") or {}
        if c.get("total_credits") is not None and c.get("total_usage") is not None:
            out["total"], out["used"] = c["total_credits"], c["total_usage"]
            out["remaining"] = c["total_credits"] - c["total_usage"]
    return out


OPENCODE_USAGE = "https://opencode.ai/zen/go/v1/usage"
WINDOWS = (("rolling", "Last 5 hours"), ("weekly", "This week"), ("monthly", "This month"))


async def opencode_usage(key: str) -> dict:
    """How much of each OpenCode Go allowance is used: {"windows": [{"id", "label", "percent", "resets_at"|"resets_in"}]}.
    The endpoint isn't documented by OpenCode, so read what it gives defensively."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(OPENCODE_USAGE, headers=_headers("opencode", key))
    if r.status_code != 200:
        raise RuntimeError(_error_text(r.text, r.status_code))
    try:
        data = r.json()
    except ValueError:
        data = {}
    if isinstance(data, dict):
        data = next((data[k] for k in ("usage", "data") if isinstance(data.get(k), dict)), data)
    else:
        data = {}
    out = []
    for wid, label in WINDOWS:
        w = data.get(wid)
        if not isinstance(w, dict):
            continue
        pct = w.get("percent", w.get("usagePercent"))
        if not isinstance(pct, (int, float)):
            continue
        out.append({"id": wid, "label": label, "percent": max(0, min(100, pct)),
                    "resets_at": w.get("resetsAt"), "resets_in": w.get("resetInSec")})
    if not out:
        raise RuntimeError("OpenCode didn't return usage in a form Lark understands.")
    return {"windows": out}
