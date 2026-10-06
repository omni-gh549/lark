"""Tools the model can call. Each tool is available only when its backend is set up."""
from dataclasses import dataclass
from typing import Awaitable, Callable

from . import sandbox, search, vault

MAX_OUTPUT = 12_000


class ToolError(RuntimeError):
    """A failure the model should see and can react to."""


@dataclass
class Tool:
    name: str
    title: str  # shown in the UI, e.g. "Web search"
    description: str
    parameters: dict
    run: Callable[[dict], Awaitable[str]]
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


async def run_command(args: dict) -> str:
    command = _str(args, "command")
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
    try:
        r = await sandbox.write(_str(args, "path"), content)
    except sandbox.SandboxError as e:
        raise ToolError(str(e))
    return f"Wrote {r.get('bytes', len(content))} bytes to {r.get('path')}."


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
    "Long-running commands are killed at the timeout (seconds, default 60, max 600).",
    _obj({"command": {"type": "string"}, "timeout": {"type": "integer"}}, ["command"]),
    run_command, lambda a: str(a.get("command", "")).strip().split("\n")[0][:120])

READ_FILE = Tool(
    "read_file", "Read file", "Read a text file from the sandbox.",
    _obj({"path": {"type": "string"}}, ["path"]), read_file, lambda a: str(a.get("path", "")))

WRITE_FILE = Tool(
    "write_file", "Write file", "Write a text file in the sandbox, creating folders as needed. Overwrites.",
    _obj({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    write_file, lambda a: str(a.get("path", "")))


def available() -> list[Tool]:
    tools = []
    if search_ready():
        tools.append(WEB_SEARCH)
    if sandbox.configured():
        tools += [RUN_COMMAND, READ_FILE, WRITE_FILE]
    return tools
