"""Notion: search, read, query and write pages and databases through the official API.

Uses an internal-integration token (Settings -> Notion). The integration only sees pages that were shared with it in
Notion (••• menu -> Connections). Everything here returns plain text for the model; nothing is cached."""
import asyncio
import json
import re

import httpx

NOTION = {
    "label": "Notion",
    "keys_url": "https://www.notion.so/profile/integrations",
    "base": "https://api.notion.com/v1",
    "version": "2022-06-28",
}

TIMEOUT = httpx.Timeout(30.0)
MAX_BLOCK_CALLS = 40  # reading one page never makes more than this many requests for its blocks
MAX_DEPTH = 3
TEXT_LIMIT = 2000  # Notion's cap for one rich-text element
SHARE_HINT = ("Notion can't see it. Share the page or database with the integration in Notion "
              "(••• menu, then Connections), or check the id.")


class NotionError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


async def call(key: str, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {key}", "Notion-Version": NOTION["version"]}
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                r = await client.request(method, NOTION["base"] + path, json=body, params=params, headers=headers)
        except httpx.HTTPError as e:
            raise NotionError(f"Could not reach Notion: {type(e).__name__}.")
        if r.status_code == 429 and attempt == 0:
            try:
                wait = min(float(r.headers.get("retry-after", "1")), 5.0)
            except ValueError:
                wait = 1.0
            await asyncio.sleep(wait)
            continue
        break
    if r.status_code == 200:
        return r.json()
    message = ""
    try:
        message = str(r.json().get("message", ""))
    except ValueError:
        pass
    if r.status_code == 401:
        raise NotionError("Notion rejected the token. Check it in Settings.", 401)
    if r.status_code == 404:
        raise NotionError(SHARE_HINT, 404)
    if r.status_code == 403:
        raise NotionError(f"The integration isn't allowed to do that. {message}".strip(), 403)
    if r.status_code == 429:
        raise NotionError("Notion is rate limiting requests. Try again in a moment.", 429)
    raise NotionError(f"Notion refused the request ({r.status_code}). {message}".strip(), r.status_code)


async def check(key: str) -> str:
    me = await call(key, "GET", "/users/me")
    bot = me.get("bot") or {}
    ws = (bot.get("workspace_name") or "").strip()
    return f"Connected as {me.get('name') or 'the integration'}" + (f" in {ws}." if ws else ".")


_UUID = re.compile(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}")


def parse_id(value) -> str:
    """A page or database id from a bare id or any Notion URL (the first id in the path, not a ?v= view id)."""
    text = str(value or "").strip().split("?")[0].split("#")[0]
    m = _UUID.search(text)
    if not m:
        raise NotionError("That isn't a Notion id or link. Use notion_search to find it.")
    h = m.group(0).replace("-", "").lower()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


# ---- rich text <-> plain markdown ----

def _plain(rich: list) -> str:
    out = []
    for t in rich or []:
        s = t.get("plain_text", "")
        a = t.get("annotations") or {}
        if s.strip():
            if a.get("code"):
                s = f"`{s}`"
            if a.get("bold"):
                s = f"**{s}**"
        if t.get("href") and s.strip():
            s = f"[{s}]({t['href']})"
        out.append(s)
    return "".join(out)


_INLINE = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\((https?://[^)\s]+)\)")


def _rich(text: str) -> list:
    """Markdown-ish text to Notion rich text: **bold**, `code` and [links](https://…)."""
    parts, pos = [], 0

    def add(content: str, annotations=None, link=None):
        for i in range(0, len(content), TEXT_LIMIT):
            piece = {"type": "text", "text": {"content": content[i:i + TEXT_LIMIT]}}
            if link:
                piece["text"]["link"] = {"url": link}
            if annotations:
                piece["annotations"] = annotations
            parts.append(piece)

    for m in _INLINE.finditer(text):
        if m.start() > pos:
            add(text[pos:m.start()])
        if m.group(1) is not None:
            add(m.group(1), {"bold": True})
        elif m.group(2) is not None:
            add(m.group(2), {"code": True})
        else:
            add(m.group(3), link=m.group(4))
        pos = m.end()
    if pos < len(text):
        add(text[pos:])
    return parts[:100]


def blocks_from_markdown(text: str) -> list:
    blocks, lines, i = [], text.replace("\r\n", "\n").split("\n"), 0

    def block(kind: str, body: str, **extra):
        blocks.append({"object": "block", "type": kind, kind: {"rich_text": _rich(body), **extra}})

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        i += 1
        if not s:
            continue
        if s.startswith("```"):
            lang, code = s[3:].strip().lower() or "plain text", []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append({"object": "block", "type": "code", "code": {
                "rich_text": _chunks("\n".join(code)),
                "language": lang if re.fullmatch(r"[a-z+#. -]+", lang) else "plain text"}})
            continue
        m = re.match(r"(#{1,3})\s+(.*)", s)
        if m:
            block(f"heading_{len(m.group(1))}", m.group(2))
        elif re.fullmatch(r"-{3,}|\*{3,}", s):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        elif (m := re.match(r"[-*]\s+\[([ xX])\]\s+(.*)", s)):
            block("to_do", m.group(2), checked=m.group(1).lower() == "x")
        elif (m := re.match(r"[-*]\s+(.*)", s)):
            block("bulleted_list_item", m.group(1))
        elif (m := re.match(r"\d+[.)]\s+(.*)", s)):
            block("numbered_list_item", m.group(1))
        elif s.startswith(">"):
            block("quote", s.lstrip("> "))
        else:
            block("paragraph", s)
    return blocks


# ---- reading ----

def _title_of(obj: dict) -> str:
    if obj.get("object") == "database":
        return _plain(obj.get("title")) or "Untitled"
    for p in (obj.get("properties") or {}).values():
        if p.get("type") == "title":
            return _plain(p.get("title")) or "Untitled"
    return "Untitled"


def _value(p: dict) -> str:
    t = p.get("type")
    v = p.get(t)
    if v is None:
        return ""
    if t in ("title", "rich_text"):
        return _plain(v)
    if t in ("select", "status"):
        return v.get("name", "")
    if t == "multi_select":
        return ", ".join(x.get("name", "") for x in v)
    if t == "date":
        return v["start"] + (f" → {v['end']}" if v.get("end") else "")
    if t == "people":
        return ", ".join(x.get("name") or x.get("id", "") for x in v)
    if t == "relation":
        return ", ".join(x.get("id", "") for x in v)
    if t == "files":
        return ", ".join(x.get("name", "") for x in v)
    if t == "formula":
        return str(v.get(v.get("type"), "") if v.get("type") != "date" else (v.get("date") or {}).get("start", ""))
    if t == "rollup":
        return str(v.get(v.get("type"), "")) if v.get("type") in ("number", "string") else ""
    if t in ("created_by", "last_edited_by"):
        return v.get("name") or v.get("id", "")
    if t == "unique_id":
        return f"{v.get('prefix') or ''}{v.get('number', '')}"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, (str, int, float)):
        return str(v)
    return ""


def _props_lines(props: dict, skip_title=True) -> list[str]:
    out = []
    for name, p in props.items():
        if skip_title and p.get("type") == "title":
            continue
        v = _value(p).strip()
        if v:
            out.append(f"{name}: {v[:200]}")
    return out


def _block_text(b: dict) -> str:
    t = b.get("type")
    d = b.get(t) or {}
    if t in ("paragraph", "quote", "callout", "toggle"):
        text = _plain(d.get("rich_text"))
        return f"> {text}" if t == "quote" else text
    if t in ("heading_1", "heading_2", "heading_3"):
        return "#" * int(t[-1]) + " " + _plain(d.get("rich_text"))
    if t == "bulleted_list_item":
        return "- " + _plain(d.get("rich_text"))
    if t == "numbered_list_item":
        return "1. " + _plain(d.get("rich_text"))
    if t == "to_do":
        return f"- [{'x' if d.get('checked') else ' '}] " + _plain(d.get("rich_text"))
    if t == "code":
        return f"```{d.get('language', '')}\n{_plain(d.get('rich_text'))}\n```"
    if t == "divider":
        return "---"
    if t == "child_page":
        return f"[page: {d.get('title', 'Untitled')}] id={b['id']}"
    if t == "child_database":
        return f"[database: {d.get('title', 'Untitled')}] id={b['id']}"
    if t in ("bookmark", "embed", "link_preview"):
        return d.get("url", "")
    if t in ("image", "file", "pdf", "video", "audio"):
        src = d.get(d.get("type", ""), {}).get("url", "")
        cap = _plain(d.get("caption"))
        return f"[{t}{': ' + cap if cap else ''}] {src.split('?')[0]}".strip()
    if t == "equation":
        return d.get("expression", "")
    if t == "table_row":
        return "| " + " | ".join(_plain(c) for c in d.get("cells", [])) + " |"
    if t in ("table", "column_list", "column", "synced_block"):
        return ""
    return f"[{t}]"


async def _blocks(key: str, block_id: str, depth: int, budget: list) -> list[str]:
    out, cursor = [], None
    while budget[0] > 0:
        budget[0] -= 1
        r = await call(key, "GET", f"/blocks/{block_id}/children", params={"page_size": 100, **({"start_cursor": cursor} if cursor else {})})
        for b in r.get("results", []):
            text = _block_text(b)
            if text or b.get("has_children"):
                if text:
                    out.append("  " * depth + text)
                if b.get("has_children") and b.get("type") not in ("child_page", "child_database") and depth < MAX_DEPTH:
                    out += await _blocks(key, b["id"], depth + (0 if b.get("type") in ("column_list", "column", "table") else 1), budget)
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return out


def _db_schema_lines(db: dict) -> list[str]:
    lines = []
    for name, p in (db.get("properties") or {}).items():
        t = p.get("type")
        extra = ""
        if t in ("select", "multi_select", "status"):
            extra = ": " + ", ".join(o.get("name", "") for o in (p.get(t) or {}).get("options", []))[:300]
        lines.append(f"- {name} ({t}){extra}")
    return lines


async def read(key: str, ident: str) -> str:
    nid = parse_id(ident)
    try:
        page = await call(key, "GET", f"/pages/{nid}")
    except NotionError as e:
        if e.status not in (404, 400):
            raise
        db = await call(key, "GET", f"/databases/{nid}")  # not a page: maybe a database
        desc = _plain(db.get("description"))
        head = [f"Database \"{_title_of(db)}\" id={db['id']}", db.get("url", ""), *([desc] if desc else []),
                "Properties:", *_db_schema_lines(db), "Use notion_query to list its pages."]
        return "\n".join(x for x in head if x)
    head = [f"Page \"{_title_of(page)}\" id={page['id']}", page.get("url", ""),
            f"Last edited {page.get('last_edited_time', '')[:16].replace('T', ' ')}"]
    if page.get("archived"):
        head.append("(in the trash)")
    head += _props_lines(page.get("properties") or {})
    body = await _blocks(key, page["id"], 0, [MAX_BLOCK_CALLS])
    return "\n".join(head) + "\n\n" + ("\n".join(body) if body else "(no content)")


def _row(p: dict) -> str:
    bits = _props_lines(p.get("properties") or {})
    return f"- \"{_title_of(p)}\" id={p['id']}" + (f" | {'; '.join(bits)}" if bits else "")


async def search(key: str, query: str, kind: str | None, limit: int) -> str:
    body: dict = {"query": query, "page_size": max(1, min(limit, 25))}
    if kind in ("page", "database"):
        body["filter"] = {"property": "object", "value": kind}
    if not query:
        body["sort"] = {"direction": "descending", "timestamp": "last_edited_time"}
    r = await call(key, "POST", "/search", body)
    rows = []
    for o in r.get("results", []):
        edited = o.get("last_edited_time", "")[:10]
        rows.append(f"- {o.get('object')} \"{_title_of(o)}\" id={o['id']} edited {edited}\n  {o.get('url', '')}")
    if not rows:
        return "Nothing found. Notion only searches what has been shared with the integration."
    return "\n".join(rows)


async def query(key: str, database: str, filter_: dict | None, sorts: list | None, limit: int, cursor: str | None) -> str:
    nid = parse_id(database)
    body: dict = {"page_size": max(1, min(limit, 50))}
    if filter_:
        body["filter"] = filter_
    if sorts:
        body["sorts"] = sorts
    if cursor:
        body["start_cursor"] = cursor
    r = await call(key, "POST", f"/databases/{nid}/query", body)
    rows = [_row(p) for p in r.get("results", [])]
    if not rows:
        return "No pages match."
    if r.get("has_more"):
        rows.append(f"(more results: call again with cursor={r.get('next_cursor')})")
    return "\n".join(rows)


# ---- writing ----

def _chunks(text: str) -> list:
    return [{"type": "text", "text": {"content": text[i:i + TEXT_LIMIT]}} for i in range(0, len(text), TEXT_LIMIT)][:100]


def _names(v) -> list[str]:
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [str(x) for x in v or []]


def _convert(name: str, spec: dict, v):
    t = spec.get("type")
    if t == "title" or t == "rich_text":
        return {t: _chunks(str(v)) if v not in (None, "") else []}
    if t == "number":
        if v is None or v == "":
            return {"number": None}
        try:
            return {"number": float(v) if "." in str(v) else int(v)}
        except ValueError:
            raise NotionError(f"'{name}' needs a number.")
    if t in ("select", "status"):
        return {t: None if v in (None, "") else {"name": str(v)}}
    if t == "multi_select":
        return {t: [{"name": n} for n in _names(v)]}
    if t == "checkbox":
        return {t: v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1", "checked", "x")}
    if t == "date":
        if v in (None, ""):
            return {t: None}
        if isinstance(v, dict):
            return {t: {k: v[k] for k in ("start", "end", "time_zone") if v.get(k)}}
        start, _, end = str(v).partition("/")
        return {t: {"start": start.strip(), **({"end": end.strip()} if end.strip() else {})}}
    if t in ("url", "email", "phone_number"):
        return {t: None if v in (None, "") else str(v)}
    if t == "relation":
        return {t: [{"id": parse_id(x)} for x in _names(v)]}
    if t == "people":
        return {t: [{"object": "user", "id": parse_id(x)} for x in _names(v)]}
    raise NotionError(f"Property '{name}' is {t}, which can't be set from here.")


def properties_for(schema: dict, values: dict) -> dict:
    """Plain {name: value} to Notion's property payload, checked against the database schema."""
    lower = {k.lower(): k for k in schema}
    out = {}
    for name, v in (values or {}).items():
        real = name if name in schema else lower.get(str(name).lower())
        if not real:
            raise NotionError(f"No property '{name}'. This database has: {', '.join(schema)}.")
        out[real] = _convert(real, schema[real], v)
    return out


async def _append(key: str, block_id: str, blocks: list):
    for i in range(0, len(blocks), 100):
        await call(key, "PATCH", f"/blocks/{block_id}/children", {"children": blocks[i:i + 100]})


def _confirm(page: dict, verb: str) -> str:
    return f"{verb} \"{_title_of(page)}\" id={page['id']}\n{page.get('url', '')}"


async def create_page(key: str, parent: str, title: str, props: dict | None, content: str | None) -> str:
    pid = parse_id(parent)
    blocks = blocks_from_markdown(content or "")
    schema = None
    try:
        db = await call(key, "GET", f"/databases/{pid}")
        schema = db.get("properties") or {}
    except NotionError as e:
        if e.status not in (404, 400):
            raise
    if schema is not None:
        title_name = next((n for n, p in schema.items() if p.get("type") == "title"), "Name")
        properties = {title_name: {"title": _chunks(title)}} if title else {}
        properties.update(properties_for(schema, props or {}))
        body = {"parent": {"database_id": pid}, "properties": properties}
    else:
        if props:
            raise NotionError("A page under another page only has a title; put the rest in content.")
        body = {"parent": {"page_id": pid}, "properties": {"title": {"title": _chunks(title)}}}
    body["children"] = blocks[:100]
    page = await call(key, "POST", "/pages", body)
    if len(blocks) > 100:
        await _append(key, page["id"], blocks[100:])
    return _confirm(page, "Created")


async def update_page(key: str, page_id: str, title: str | None, props: dict | None, archived: bool | None, append: str | None) -> str:
    pid = parse_id(page_id)
    body: dict = {}
    if title is not None or props:
        page = await call(key, "GET", f"/pages/{pid}")
        parent = page.get("parent") or {}
        if parent.get("type") == "database_id":
            db = await call(key, "GET", f"/databases/{parent['database_id']}")
            schema = db.get("properties") or {}
        else:
            schema = {"title": {"type": "title"}}
        properties = properties_for(schema, props or {})
        if title is not None:
            title_name = next((n for n, p in schema.items() if p.get("type") == "title"), "title")
            properties[title_name] = {"title": _chunks(title)}
        body["properties"] = properties
    if archived is not None:
        body["archived"] = bool(archived)
    blocks = blocks_from_markdown(append or "")
    if not body and not blocks:
        raise NotionError("Nothing to change: give title, properties, archived or append.")
    page = await call(key, "PATCH", f"/pages/{pid}", body) if body else await call(key, "GET", f"/pages/{pid}")
    if blocks:
        await _append(key, pid, blocks)
    return _confirm(page, "Updated")


def json_arg(value, what: str):
    """The model sometimes sends an object as a JSON string."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise NotionError(f"'{what}' must be a JSON object.")
    return value
