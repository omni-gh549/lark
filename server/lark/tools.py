"""Tools the model can call. Each tool is available only when its backend is set up."""
from dataclasses import dataclass
from typing import Awaitable, Callable

import base64
import re

from . import files, sandbox, search, vault

MAX_OUTPUT = 12_000


class ToolError(RuntimeError):
    """A failure the model should see and can react to."""


@dataclass
class Tool:
    name: str
    title: str  # shown in the UI, e.g. "Web search"
    description: str
    parameters: dict
    run: Callable[[dict], Awaitable]  # returns text, or (text, [image file names])
    detail: Callable[[dict], str]  # short line for the UI, e.g. the query

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


def clip(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-limit // 3:]
    return f"{head}\n… [{len(text) - limit} characters cut] …\n{tail}"


def _str(args: dict, key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ToolError(f"Missing '{key}'.")
    return v


def search_ready() -> bool:
    return bool(vault.get_key(vault.load()["search"]))


async def web_search(args: dict) -> str:
    query = _str(args, "query")
    name = vault.load()["search"]
    key = vault.get_key(name)
    if not key:
        raise ToolError("Web search has no API key.")
    try:
        rows = await search.search(name, key, query)
    except search.SearchError as e:
        raise ToolError(str(e))
    if not rows:
        return "No results."
    return "\n\n".join(f"{i}. {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(rows, 1))


OWN_BROWSER = re.compile(r"playwright|puppeteer|selenium|webdriver", re.I)
NO_OWN_BROWSER = ("Don't script your own browser in the sandbox: it has limited CPU and memory and starves the real one. "
                  "Use the browser tool instead (press accepts a list of keys for games and fast input).")


async def run_command(args: dict) -> str:
    command = _str(args, "command")
    if OWN_BROWSER.search(command):
        raise ToolError(NO_OWN_BROWSER)
    if re.search(r"(pkill|killall|kill)\b.*(chrom|playw)", command, re.I):
        raise ToolError("Don't kill the browser processes: they belong to the browser tool. Use its actions instead.")
    timeout = args.get("timeout", 60)
    timeout = timeout if isinstance(timeout, int) and 1 <= timeout <= 600 else 60
    try:
        r = await sandbox.run(command, timeout)
    except sandbox.SandboxError as e:
        raise ToolError(str(e))
    out = r.get("output", "")
    status = "timed out and was killed" if r.get("timed_out") else f"exit code {r.get('exit_code')}"
    return clip(f"{out}\n[{status}]" if out else f"[{status}]")


async def read_file(args: dict) -> str:
    try:
        r = await sandbox.read(_str(args, "path"))
    except sandbox.SandboxError as e:
        raise ToolError(str(e))
    return clip(r.get("content", ""))


async def write_file(args: dict) -> str:
    content = args.get("content")
    if not isinstance(content, str):
        raise ToolError("Missing 'content'.")
    if OWN_BROWSER.search(content):
        raise ToolError(NO_OWN_BROWSER)
    try:
        r = await sandbox.write(_str(args, "path"), content)
    except sandbox.SandboxError as e:
        raise ToolError(str(e))
    return f"Wrote {r.get('bytes', len(content))} bytes to {r.get('path')}."


async def browser(args: dict):
    action = _str(args, "action")
    if action not in ("goto", "click", "type", "press", "scroll", "back", "snapshot", "screenshot"):
        raise ToolError("Unknown action.")
    try:
        r = await sandbox.browse(args)
    except sandbox.SandboxError as e:
        raise ToolError(str(e))
    text = clip(r["snapshot"], 9000)
    if r.get("image"):
        name = files.save(base64.b64decode(r["image"]))
        return text, [name]
    return text


async def show_image(args: dict):
    path = _str(args, "path")
    try:
        data = await sandbox.read_binary(path)
        name = files.save(data)
    except sandbox.SandboxError as e:
        raise ToolError(str(e))
    except files.FileError as e:
        raise ToolError(str(e))
    caption = str(args.get("caption") or "").strip()
    return (f"Showing {path} to the user." + (f" Caption: {caption}" if caption else "")), [name]


def _browser_detail(a: dict) -> str:
    action = str(a.get("action", ""))
    if action == "goto":
        return str(a.get("url", ""))
    if action == "click":
        if a.get("id") is None and a.get("text"):
            return f"click \"{str(a['text'])[:40]}\""
        if a.get("id") is None and a.get("x") is not None:
            return f"click at {a.get('x')},{a.get('y')}"
        return f"click #{a.get('id', '')}"
    if action == "press" and isinstance(a.get("keys"), list):
        return f"press {len(a['keys'])} keys"
    if action == "type":
        return f"type in #{a.get('id', '')}"
    return action


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}


WEB_SEARCH = Tool(
    "web_search", "Web search",
    "Search the web. Returns titles, URLs and snippets. Use it for anything recent or that you are unsure of.",
    _obj({"query": {"type": "string"}}, ["query"]), web_search, lambda a: str(a.get("query", "")))

RUN_COMMAND = Tool(
    "run_command", "Run command",
    "Run a bash command in your persistent Linux sandbox (Debian, internet access, no access to the owner's machine). "
    "Files under /home/lark persist between calls. Install packages with apt or pip if needed. "
    "Long-running commands are killed at the timeout (seconds, default 60, max 600). "
    "Don't launch browsers here (use the browser tool); memory and CPU are limited.",
    _obj({"command": {"type": "string"}, "timeout": {"type": "integer"}}, ["command"]),
    run_command, lambda a: str(a.get("command", "")).strip().split("\n")[0][:120])

READ_FILE = Tool(
    "read_file", "Read file", "Read a text file from the sandbox.",
    _obj({"path": {"type": "string"}}, ["path"]), read_file, lambda a: str(a.get("path", "")))

WRITE_FILE = Tool(
    "write_file", "Write file", "Write a text file in the sandbox, creating folders as needed. Overwrites.",
    _obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    write_file, lambda a: str(a.get("path", "")))


BROWSER = Tool(
    "browser", "Browser",
    "Use a real web browser (headless Chromium in the sandbox). Every call returns a snapshot of the page: its text and a "
    "numbered list of links, buttons and fields. Actions: goto (url), click (id; or text: the visible words on a button or link, which also reaches cookie popups that have no number; or x and y pixel position, 1280 by 800, as in a screenshot), type (id, text, optional submit), "
    "press (key, e.g. Enter, or keys: a list of keys pressed in order, up to 100, for games and fast input), scroll (direction up or down), back, snapshot, screenshot (shows the page to the user). Element numbers only last until the next "
    "action, so use the latest snapshot. Use it for pages that need clicking or logging in, or that search can't read.",
    _obj({"action": {"type": "string", "enum": ["goto", "click", "type", "press", "scroll", "back", "snapshot", "screenshot"]},
          "url": {"type": "string"}, "id": {"type": "integer"}, "text": {"type": "string"},
          "submit": {"type": "boolean"}, "key": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
          "keys": {"type": "array", "items": {"type": "string"}}, "direction": {"type": "string"}}, ["action"]),
    browser, _browser_detail)


SHOW_IMAGE = Tool(
    "show_image", "Show image",
    "Show an image file from the sandbox (PNG, JPEG, GIF or WebP, up to 8 MB) to the user in the chat. "
    "Use it for charts, renders and screenshots you made.",
    _obj({"path": {"type": "string"}, "caption": {"type": "string"}}, ["path"]),
    show_image, lambda a: str(a.get("path", "")))


async def available() -> list[Tool]:
    tools = []
    if search_ready():
        tools.append(WEB_SEARCH)
    if sandbox.configured():
        tools += [RUN_COMMAND, READ_FILE, WRITE_FILE, SHOW_IMAGE]
        if await sandbox.has_browser():
            tools.append(BROWSER)
    return tools
