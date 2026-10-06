"""Chat providers. Both speak the OpenAI chat completions protocol."""
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


async def stream_chat(name: str, key: str, model: str, messages: list[dict]) -> AsyncIterator[dict]:
    """Yields {"text": str} chunks, then {"done": True}, or {"error": str}."""
    body = {"model": model, "messages": messages, "stream": True}
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
                    yield {"error": _error_text(text, r.status_code)}
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
                        yield {"error": err.get("message", "The provider stopped mid-reply.") if isinstance(err, dict) else str(err)}
                        return
                    for choice in obj.get("choices", []):
                        text = (choice.get("delta") or {}).get("content")
                        if text:
                            yield {"text": text}
    except httpx.HTTPError as e:
        yield {"error": f"Could not reach {PROVIDERS[name]['label']}: {type(e).__name__}."}
        return
    yield {"done": True}
