"""Web search backends. Each needs an API key, saved in Settings."""
import html
import re

import httpx

SEARCH_PROVIDERS = {
    "brave": {
        "label": "Brave Search",
        "keys_url": "https://brave.com/search/api/",
        "base": "https://api.search.brave.com/res/v1/web/search",
    },
    "tavily": {
        "label": "Tavily",
        "keys_url": "https://app.tavily.com/home",
        "base": "https://api.tavily.com/search",
    },
}

TIMEOUT = httpx.Timeout(20.0)


class SearchError(RuntimeError):
    pass


def _plain(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


async def search(name: str, key: str, query: str, count: int = 5) -> list[dict]:
    """Returns [{"title", "url", "snippet"}]. Raises SearchError with a readable message."""
    base = SEARCH_PROVIDERS[name]["base"]
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            if name == "brave":
                r = await client.get(
                    base,
                    params={"q": query, "count": count},
                    headers={"X-Subscription-Token": key, "Accept": "application/json"},
                )
            else:
                r = await client.post(
                    base,
                    json={"query": query, "max_results": count},
                    headers={"Authorization": f"Bearer {key}"},
                )
    except httpx.HTTPError as e:
        raise SearchError(f"Could not reach {SEARCH_PROVIDERS[name]['label']}: {type(e).__name__}.")
    if r.status_code != 200:
        detail = ""
        try:
            body = r.json()
            err = body.get("error") or body.get("detail") or body.get("message")
            detail = err.get("message", "") if isinstance(err, dict) else str(err or "")
        except ValueError:
            pass
        raise SearchError(f"{SEARCH_PROVIDERS[name]['label']} refused the request ({r.status_code}). {detail}".strip())
    data = r.json()
    if name == "brave":
        rows = (data.get("web") or {}).get("results", [])
        return [{"title": _plain(x.get("title")), "url": x.get("url", ""), "snippet": _plain(x.get("description"))} for x in rows]
    return [{"title": _plain(x.get("title")), "url": x.get("url", ""), "snippet": _plain(x.get("content"))} for x in data.get("results", [])]
