"""Mnemos as an alternative memory backend (https://github.com/Riley-Coyote/mnemos-mcp).

Mnemos is an MCP server that gives an agent a memory in its own words, kept in one SQLite file. Lark runs it as a child
process (`mnemos serve`) and speaks MCP over its stdin and stdout; there is no extra Python dependency here, because the
stdio transport is just one JSON-RPC message per line.

Settings -> Memory picks the backend:

  lark    (default) the notes, past-chat search, background learning pass and Memory page in memory.py
  mnemos  Mnemos replaces all of that: the model wakes with Mnemos's briefing and writes its own memory through the
          mnemos_* tools. Lark's notes, past-chat search and learning pass are switched off while it is chosen (the
          data is untouched, so switching back loses nothing).

The child starts on first use and is stopped again after a quiet spell, so an idle Lark does not hold its memory.
Everything is configurable: the command in Settings (or LARK_MNEMOS_COMMAND), the data folder (LARK_MNEMOS_HOME, default
<data>/mnemos), and the idle timeout (LARK_MNEMOS_IDLE seconds, default 600).
"""
import asyncio
import json
import os
import re
import shlex
import shutil
import time
import contextvars

from . import health, vault

PROTOCOL = "2025-03-26"
CALL_TIMEOUT = 60  # seconds for one tool call
START_TIMEOUT = 45  # seconds to start and handshake (the first start imports a lot)
AGENT_ID = "lark"
PERSON_ID = "owner"
CONTEXT_TTL = 1800  # seconds a chat reuses its briefing before asking again
MAX_PACKET = 6000  # characters of briefing that go into the system prompt

# The tools the chat model gets. The rest are housekeeping that Lark does itself (context, introduce, maintain,
# health) or need a terminal and a browser on the same machine (page).
MODEL_TOOLS = ("mnemos_capture", "mnemos_recall", "mnemos_correct", "mnemos_reflect", "mnemos_handoff", "mnemos_journal", "mnemos_note")
WRITE_TOOLS = {"mnemos_capture", "mnemos_correct", "mnemos_reflect", "mnemos_handoff", "mnemos_journal", "mnemos_note"}

CURRENT_MODEL: contextvars.ContextVar[str] = contextvars.ContextVar("current_model", default="")


class MnemosError(Exception):
    """Mnemos could not be started or did not answer. The message is safe to show the owner."""


def backend() -> str:
    return "mnemos" if vault.load().get("memory_backend") == "mnemos" else "lark"


def active() -> bool:
    """Mnemos is the chosen backend and memory is switched on."""
    s = vault.load()
    return s["memory_use"] and s["memory_backend"] == "mnemos"


def home() -> str:
    return os.environ.get("LARK_MNEMOS_HOME") or str(vault.DATA_DIR / "mnemos")


def command() -> list[str]:
    raw = (vault.load().get("mnemos_command") or os.environ.get("LARK_MNEMOS_COMMAND") or "").strip()
    if not raw:
        # no setup: `mnemos` on the PATH, else where deploy/install-mnemos.sh puts it
        installed = "/opt/mnemos/bin/mnemos"
        raw = "mnemos" if shutil.which("mnemos") or not os.path.exists(installed) else installed
    try:
        if os.path.exists(raw):
            parts = [raw]  # a plain path, spaces and all
        else:
            parts = shlex.split(raw, posix=os.name != "nt")
            parts = [p[1:-1] if len(p) > 1 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]  # Windows keeps the quotes
    except ValueError:
        raise MnemosError("The Mnemos command is not valid.")
    if not parts:
        raise MnemosError("The Mnemos command is empty.")
    return parts


def _child_env() -> dict:
    """The child gets Lark's environment without Lark's own secrets, with its home pointed at Lark's data folder."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LARK_")}
    h = home()
    env.update({
        "HOME": h, "USERPROFILE": h,  # Mnemos keeps everything under ~/.mnemos
        "MNEMOS_AGENT_ID": AGENT_ID, "MNEMOS_PERSON_ID": PERSON_ID,
        "MNEMOS_MAINTENANCE": "inline",  # upkeep runs inside the call that asks for it, never as a stray background process
        "MNEMOS_SHARED_MEMORY": "off",  # nothing here is shared with other agents on the machine
        "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "NO_COLOR": "1",
    })
    hf = os.environ.get("LARK_MNEMOS_MODELS") or "/opt/mnemos/hf"
    if "HF_HOME" not in env and os.path.isdir(hf):
        env.update({"HF_HOME": hf, "HF_HUB_OFFLINE": "1"})  # the embedding model deploy/install-mnemos.sh downloaded; no network at run time
    return env


class Client:
    """One `mnemos serve` child and the JSON-RPC conversation with it."""

    def __init__(self):
        self.proc: asyncio.subprocess.Process | None = None
        self.tools: list[dict] = []
        self.instructions = ""
        self.version = ""
        self.last_used = 0.0
        self._next = 1
        self._pending: dict[int, asyncio.Future] = {}
        self._reader: asyncio.Task | None = None
        self._lock = asyncio.Lock()  # starting and stopping
        self._introduced = ""
        self._idle: asyncio.Task | None = None

    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def _read(self, proc):
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", "replace"))
                except ValueError:
                    continue  # stray output that is not protocol
                fut = self._pending.pop(msg.get("id"), None) if isinstance(msg, dict) and "method" not in msg else None
                if fut and not fut.done():
                    fut.set_result(msg)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(MnemosError("Mnemos stopped unexpectedly."))
            self._pending.clear()

    async def _send(self, obj: dict) -> None:
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())
        await self.proc.stdin.drain()

    async def _rpc(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT) -> dict:
        rid = self._next
        self._next += 1
        fut = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            await self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(rid, None)
            raise MnemosError("Mnemos took too long to answer.")
        except (BrokenPipeError, ConnectionError, OSError):
            self._pending.pop(rid, None)
            raise MnemosError("Mnemos stopped unexpectedly.")
        if "error" in msg:
            raise MnemosError(str((msg["error"] or {}).get("message") or "Mnemos returned an error.")[:300])
        return msg.get("result") or {}

    async def start(self) -> None:
        async with self._lock:
            if self.running():
                return
            cmd = command()
            if not shutil.which(cmd[0]) and not os.path.exists(cmd[0]):
                raise MnemosError(f"Mnemos is not installed: the command '{cmd[0]}' was not found. "
                                  "Install it (pip install mnemos-continuity) and set its path in Settings -> Memory.")
            os.makedirs(home(), exist_ok=True)
            try:
                self.proc = await asyncio.create_subprocess_exec(
                    *cmd, "serve", stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL, env=_child_env(), limit=8 * 1024 * 1024)
            except OSError as e:
                raise MnemosError(f"Could not start Mnemos: {e.strerror or type(e).__name__}.")
            self._reader = asyncio.ensure_future(self._read(self.proc))
            try:
                init = await self._rpc("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                                     "clientInfo": {"name": "lark", "version": "1"}}, START_TIMEOUT)
                await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
                self.version = str((init.get("serverInfo") or {}).get("version") or "")
                self.instructions = str(init.get("instructions") or "")
                self.tools = (await self._rpc("tools/list", {}, START_TIMEOUT)).get("tools") or []
            except Exception:
                await self._kill()
                raise
            self._introduced = ""
            self.last_used = time.time()
            if not self._idle or self._idle.done():
                self._idle = asyncio.ensure_future(self._watch_idle())

    async def _kill(self) -> None:
        proc, self.proc = self.proc, None
        if proc and proc.returncode is None:
            try:
                proc.stdin.close()
                await asyncio.wait_for(proc.wait(), 5)
            except Exception:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), 5)
                except Exception:
                    pass
        if self._reader:
            self._reader.cancel()
            self._reader = None

    async def stop(self) -> None:
        async with self._lock:
            await self._kill()

    async def _watch_idle(self):
        idle = float(os.environ.get("LARK_MNEMOS_IDLE", "600"))
        while self.running():
            await asyncio.sleep(max(5, min(60, idle / 4)))
            if time.time() - self.last_used > idle and not self._pending:
                await self.stop()
                return

    async def call(self, name: str, arguments: dict | None = None) -> str:
        """Run a Mnemos tool and return its text. Raises MnemosError."""
        if not self.running():
            await self.start()
        self.last_used = time.time()
        res = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        self.last_used = time.time()
        text = "\n".join(c.get("text", "") for c in res.get("content") or [] if isinstance(c, dict) and c.get("type") == "text").strip()
        if res.get("isError"):
            raise MnemosError(text[:300] or "Mnemos could not do that.")
        return text

    async def introduce(self, model: str) -> None:
        """Tell Mnemos which model is writing, so its notes are signed. Cheap, and only repeated when the model changes."""
        model = (model or "").strip()[:200]
        if model and model != self._introduced:
            await self.call("mnemos_introduce", {"agent_model": model, "agent_name": "Lark"})
            self._introduced = model


CLIENT = Client()


async def shutdown() -> None:
    await CLIENT.stop()


# ---- what the chat model sees ----------------------------------------------------------------------------------

_briefings: dict[str, tuple[float, str]] = {}


def forget_briefing(chat_id: str | None = None) -> None:
    if chat_id is None:
        _briefings.clear()
    else:
        _briefings.pop(chat_id, None)


GUIDE = ("Memory: you keep your own long-term memory with Mnemos. What you woke with is below, in your own words from earlier. "
         "Use it the way a person who knows the user would: naturally, without announcing that you looked anything up or repeating it back. "
         "Capture anything durable the moment it appears (preferences, decisions, corrections, people, project state), because a chat can end at any time. "
         "When the user corrects something you remembered, use mnemos_correct instead of adding a second note. "
         "Search with mnemos_recall when you need something you didn't wake with. Never mention memory ids, tools or the database to the user. You already woke with your memory, so there is no need to load it again.")


async def context(chat_id: str, history_text: str, model: str) -> str:
    """The briefing for the system prompt: Mnemos's own wake packet, plus what bears on the latest message."""
    if not active():
        return ""
    try:
        if not CLIENT.running():
            await CLIENT.start()
        await CLIENT.introduce(model)
        cached = _briefings.get(chat_id)
        if cached and time.time() - cached[0] < CONTEXT_TTL:
            packet = cached[1]
        else:
            packet = await CLIENT.call("mnemos_context", {"max_results": 5})
            _briefings[chat_id] = (time.time(), packet)
        cue = ""
        if history_text.strip() and len(history_text.split()) >= 4:
            cue = await CLIENT.call("mnemos_recall", {"query": history_text[:500], "max_results": 3})
    except MnemosError as e:
        health.record("mnemos", str(e))
        return ""
    parts = [GUIDE, ""]
    if CLIENT.instructions:
        parts += ["How to use it:", CLIENT.instructions.strip(), ""]
    parts += ["What you woke with:", packet[:MAX_PACKET]]
    if cue and ("Durable memories:" in cue or "Continuity notes:" in cue):
        parts += ["", "Memories that may bear on this message:", cue[:2500]]
    return "\n".join(parts).strip()


# Short, plain titles for the chat (a tool chip reads "Remembered · The user's cat is called Miso").
LABELS = {"mnemos_capture": "Remembered", "mnemos_recall": "Recalled", "mnemos_correct": "Updated a memory",
          "mnemos_reflect": "Reflected on a memory", "mnemos_handoff": "Left itself a note", "mnemos_journal": "Wrote in its journal",
          "mnemos_note": "Left you a note"}
_MAIN_FIELD = {"mnemos_capture": "content", "mnemos_recall": "query", "mnemos_correct": "correction", "mnemos_reflect": "text",
               "mnemos_handoff": "text", "mnemos_journal": "text", "mnemos_note": "text"}


def _detail(name: str, args: dict) -> str:
    text = str(args.get(_MAIN_FIELD.get(name, ""), "") or args.get("query") or args.get("target_id") or "")
    return " ".join(text.split())[:100]


# ---- what Mnemos has been doing -----------------------------------------------------------------------------------
# Mnemos keeps its own memory; this small log (data/mnemos/activity.jsonl) only records Lark's calls to it, so the Memory page
# can show what was remembered or recalled and when.

MAX_ACTIVITY = 300


def _activity_path():
    return os.path.join(home(), "activity.jsonl")


def log_activity(name: str, args: dict, ok: bool) -> None:
    try:
        from . import memory
        row = {"ts": time.time(), "tool": name, "label": LABELS.get(name, name), "text": " ".join(str(args.get(_MAIN_FIELD.get(name, ""), "") or args.get("query", "")).split())[:300],
               "chat": memory.CURRENT_CHAT.get(), "ok": ok}
        path = _activity_path()
        os.makedirs(home(), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if os.path.getsize(path) > 200_000:  # trim to the newest rows now and then
            rows = open(path, encoding="utf-8").read().splitlines()[-MAX_ACTIVITY:]
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(rows) + "\n")
    except Exception:
        pass  # a log must never get in the way of memory


def recent_activity(limit: int = 40) -> list[dict]:
    try:
        rows = open(_activity_path(), encoding="utf-8").read().splitlines()[-limit:]
        return [json.loads(r) for r in reversed(rows)]
    except (OSError, ValueError):
        return []


def parse_health(card: str) -> dict:
    """The few numbers the Memory page shows, out of Mnemos's health card."""
    def num(pattern):
        m = re.search(pattern, card)
        return int(m.group(1)) if m else 0
    sem = re.search(r"^Semantic:\s+(\w+)", card, re.M)
    return {"memories": num(r"Memories:\s+(\d+) active"), "dormant": num(r"(\d+) dormant"), "archived": num(r"(\d+) archived"),
            "journal": num(r"Journal:\s+(\d+) entr"), "meaning_search": bool(sem and sem.group(1).lower() == "on"),
            "created": "not created yet" not in card and "No memory store exists" not in card}


def parse_items(text: str) -> list[dict]:
    """Memories in a recall result: '- [how it matched] words' followed by an 'id=... kept DATE' line."""
    items: list[dict] = []
    for line in text.splitlines():
        m = re.match(r"^\s*-\s+(?:\[[^\]]*\]\s*)?(.+)$", line)
        if m and not line.startswith("  "):
            items.append({"text": m.group(1).strip(), "kept": ""})
        elif items and (k := re.search(r"kept (\d{4}-\d{2}-\d{2})", line)):
            items[-1]["kept"] = k.group(1)
    return items


def _strip_signed(schema: dict) -> dict:
    props = {k: v for k, v in (schema.get("properties") or {}).items() if k not in ("signed_as", "shared")}
    out = {**schema, "properties": props}
    if "required" in out:
        out["required"] = [r for r in out["required"] if r in props]
    return out


def tool_specs() -> list[dict]:
    """The tools Mnemos advertises that the chat model gets, from the running child (empty when it can't start)."""
    by_name = {t.get("name"): t for t in CLIENT.tools}
    return [by_name[n] for n in MODEL_TOOLS if n in by_name]  # in the order above, whatever order Mnemos lists them


async def model_tools(Tool) -> list:
    """Lark Tool objects for the Mnemos tools. Starts Mnemos if needed; returns [] and records the problem if it can't."""
    if not active():
        return []
    try:
        if not CLIENT.running():
            await CLIENT.start()
    except MnemosError as e:
        health.record("mnemos", str(e))
        return []
    out = []
    for spec in tool_specs():
        name = spec["name"]

        async def run(args, _name=name):
            args = {k: v for k, v in args.items() if k not in ("shared",)}
            if _name in WRITE_TOOLS and CURRENT_MODEL.get():
                args["signed_as"] = CURRENT_MODEL.get()
            try:
                text = await CLIENT.call(_name, args)
            except MnemosError as e:
                from .tools import ToolError
                log_activity(_name, args, False)
                raise ToolError(str(e))
            log_activity(_name, args, True)
            if _name in WRITE_TOOLS:
                forget_briefing()
            return text

        schema = _strip_signed(spec.get("inputSchema") or {"type": "object", "properties": {}})
        desc = " ".join((spec.get("description") or "").split())[:1500]
        out.append(Tool(name, LABELS.get(name, name), desc, schema, run, lambda a, _n=name: _detail(_n, a)))
    return out


# ---- the Memory page and Settings ------------------------------------------------------------------------------

async def status() -> dict:
    """Whether Mnemos can run, for Settings. Starts it if it isn't running (and stops it again when idle)."""
    out = {"installed": False, "running": CLIENT.running(), "version": CLIENT.version, "error": "", "command": " ".join(shlex.quote(p) for p in _safe_command())}
    try:
        await CLIENT.start()
        out.update(installed=True, running=True, version=CLIENT.version)
    except MnemosError as e:
        out["error"] = str(e)
    return out


def _safe_command() -> list[str]:
    try:
        return command()
    except MnemosError:
        return []


async def overview() -> dict:
    """For the Memory page: plain numbers, what it keeps standing, and what it has done lately. Read-only."""
    out = {"stats": None, "standing": [], "activity": recent_activity(), "details": "", "error": ""}
    try:
        if not CLIENT.running():
            await CLIENT.start()
        card = await CLIENT.call("mnemos_health")
        out["stats"] = parse_health(card)
        out["details"] = "\n".join(l for l in card.splitlines() if "safe to relay" not in l).strip()
        out["standing"] = [i["text"] for i in parse_items(await CLIENT.call("mnemos_recall", {"query": "", "standing": True, "max_results": 20}))]
    except MnemosError as e:
        out["error"] = str(e)
    return out


async def search(query: str) -> str:
    if not query.strip():
        return ""
    return await CLIENT.call("mnemos_recall", {"query": query[:500], "max_results": 15})


async def search_items(query: str) -> list[dict]:
    return parse_items(await search(query))
