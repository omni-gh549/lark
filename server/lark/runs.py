"""Chat runs that live on the server, so a reply keeps going when the browser leaves.

A run executes the agent loop as a background task and keeps every event it emits. Clients attach to the
event stream (replayed from the start, then live) and can leave and come back. When the run ends, the
assistant message is written into the saved chat.
"""
import asyncio
import json

from . import agent, chats, memory, sandbox

_runs: dict[str, "Run"] = {}


class Run:
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.events: list[dict] = []
        self.waiters: set[asyncio.Event] = set()
        self.finished = False
        self.task: asyncio.Task | None = None
        self.discard = False  # the chat was deleted mid-run

    def push(self, ev: dict):
        # append only: a client may already have read earlier events, so they must never change
        self.events.append(ev)
        self.wake()

    def wake(self):
        for w in self.waiters:
            w.set()

    async def stream(self):
        """Yields every event so far, then new ones until the run ends."""
        i = 0
        w = asyncio.Event()
        self.waiters.add(w)
        try:
            while True:
                while i < len(self.events):
                    yield self.events[i]
                    i += 1
                if self.finished:
                    return
                w.clear()
                if i >= len(self.events) and not self.finished:
                    await w.wait()
        finally:
            self.waiters.discard(w)


def running() -> int:
    return sum(1 for r in _runs.values() if not r.finished)


def get(chat_id: str) -> "Run | None":
    r = _runs.get(chat_id)
    return r if r and not r.finished else None


def running_ids() -> set[str]:
    return {k for k, r in _runs.items() if not r.finished}


def assistant_message(events: list[dict]) -> dict | None:
    """Folds a run's events into the stored message shape, or None if the run produced nothing."""
    content, parts = "", []
    for ev in events:
        if "text" in ev:
            content += ev["text"]
            if parts and parts[-1]["type"] == "text":
                parts[-1]["text"] += ev["text"]
            else:
                parts.append({"type": "text", "text": ev["text"]})
        elif "tool_start" in ev:
            parts.append({"type": "tool", "state": "running", "output": "", **ev["tool_start"]})
        elif "tool_end" in ev:
            for p in parts:
                if p["type"] == "tool" and p["id"] == ev["tool_end"]["id"]:
                    p.update(state="ok" if ev["tool_end"]["ok"] else "error", output=ev["tool_end"]["output"])
                    for k in ("steps", "images"):
                        if ev["tool_end"].get(k):
                            p[k] = ev["tool_end"][k]
    for p in parts:
        if p["type"] == "tool" and p["state"] == "running":
            p["state"] = "error"
    if not content and not any(p["type"] == "tool" for p in parts):
        return None
    return {"role": "assistant", "content": content, "parts": parts}


async def _execute(run: Run, name: str, key: str, model: str, history: list[dict]):
    error = None
    memory.CURRENT_CHAT.set(run.chat_id)  # the memory tools record where a note came from
    try:
        try:
            notes = await memory.context(run.chat_id, history)
        except Exception:
            notes = ""  # memory must never stop a reply
        if not memory.is_contact_chat(run.chat_id):
            try:
                from . import telegram
                notes = f"{telegram.owner_brief()}\n\n{notes}".strip()
            except Exception:
                pass  # a problem with contacts must never stop a reply
        if run.chat_id == "telegram-owner":
            notes = f"{agent.TELEGRAM_PROMPT}\n\n{notes}".strip()
        async for ev in agent.run(name, key, model, history, notes):
            if "error" in ev:
                error = ev["error"]
            run.push(ev)
    except asyncio.CancelledError:
        run.push({"stopped": True})
        if sandbox.configured():
            await sandbox.kill_all()  # a command it started would otherwise keep running after Stop
    except Exception:
        error = "Something went wrong running that reply."
        run.push({"error": error})
    finally:
        if not run.discard:
            doc = chats.load(run.chat_id) or {"messages": []}
            msg = assistant_message(run.events)
            messages = doc["messages"] + ([msg] if msg else [])
            chats.save(run.chat_id, messages, error=error)
            if not error and not any("stopped" in e for e in run.events):
                asyncio.ensure_future(memory.learn(run.chat_id, name, key, model))
        run.finished = True
        run.wake()


def start(chat_id: str, name: str, key: str, model: str, history: list[dict]) -> Run:
    run = Run(chat_id)
    _runs[chat_id] = run
    run.task = asyncio.ensure_future(_execute(run, name, key, model, history))
    return run


def stop(chat_id: str, discard: bool = False) -> bool:
    r = get(chat_id)
    if not r or not r.task:
        return False
    r.discard = discard
    r.task.cancel()
    return True


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"
