"""Chat runs that live on the server, so a reply keeps going when the browser leaves.

A run executes the agent loop as a background task and keeps every event it emits. Clients attach to the
event stream (replayed from the start, then live) and can leave and come back. When the run ends, the
assistant message is written into the saved chat.
"""
import asyncio
import json
import logging
import os
import tempfile
import time

from . import agent, chats, health, memory, sandbox, titles, vault

log = logging.getLogger("lark")

RELAYS: dict = {}  # chat id -> async fn(run): follows every run in that chat elsewhere (the Telegram owner chat)
history_builder = None  # set by main: saved messages -> what the model sees
MAX_RESUMES = 2  # a run cut off by restarts this many times in a row is given up on

_runs: dict[str, "Run"] = {}


class Run:
    def __init__(self, chat_id: str):
        self.chat_id = chat_id
        self.events: list[dict] = []
        self.waiters: set[asyncio.Event] = set()
        self.finished = False
        self.task: asyncio.Task | None = None
        self.discard = False  # the chat was deleted mid-run
        self.conf: tuple | None = None  # (provider, key, model), kept to start follow-up runs
        self.queued = 0  # messages sent while this run was busy: already saved in the chat, answered in a follow-up run
        self.user_stop = False  # Stop was pressed, as opposed to the server shutting down
        self.interrupted = False  # the server went away mid-run: the journal lets the next start carry on
        self.resumed = False
        self.skip = 0  # events that were replayed from before a restart (followers must not repeat them)
        self.attempts = 0
        self.steer: list[dict] = []  # messages sent mid-run, waiting for the next step
        self._journaled = 0.0

    def push(self, ev: dict):
        # append only: a client may already have read earlier events, so they must never change
        self.events.append(ev)
        if "tool_end" in ev or time.time() - self._journaled > 3:
            _journal(self)
        self.wake()

    def wake(self):
        for w in self.waiters:
            w.set()

    async def stream(self, start: int = 0):
        """Yields every event so far, then new ones until the run ends."""
        i = start
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


# ---- the journal: what a run was doing, so a restart doesn't eat the reply ---------------------------------------

def _dir():
    d = vault.DATA_DIR / "runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _journal(run: "Run"):
    run._journaled = time.time()
    try:
        data = json.dumps({"chat_id": run.chat_id, "attempts": run.attempts, "events": run.events[-400:]})
        fd, tmp = tempfile.mkstemp(dir=_dir(), suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _dir() / f"{run.chat_id}.json")
    except (OSError, ValueError, TypeError):
        pass  # the journal only helps after a crash: never let it break a reply


def _unjournal(chat_id: str):
    try:
        (_dir() / f"{chat_id}.json").unlink()
    except OSError:
        pass


RESTART_NOTE = ("[Note from the system: the server restarted while you were replying. Your reply so far, and the steps you had finished, are "
                "listed here. Carry on from where you stopped without repeating anything that already completed; messages already sent stay sent.]")


def _summary(events: list[dict]) -> str:
    lines, ends = [], {e["tool_end"]["id"]: e["tool_end"] for e in events if "tool_end" in e}
    for e in events:
        if "tool_start" in e:
            t = e["tool_start"]
            end = ends.get(t["id"])
            result = (f"{'ok' if end['ok'] else 'failed'}: {str(end['output'])[:200]}" if end else "interrupted, may not have finished")
            lines.append(f"- {t['title']} {t['detail']}".rstrip() + f" -> {result}")
    return "\n".join(lines[-30:])


async def resume_all():
    """After a restart: carry on any reply that was cut off, or say so."""
    from . import main
    for path in sorted(_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text())
            chat_id, events, attempts = data["chat_id"], data.get("events", []), int(data.get("attempts", 0))
        except (OSError, ValueError, KeyError):
            path.unlink(missing_ok=True)
            continue
        doc = chats.load(chat_id)
        conf = main.ready()
        if not doc or not doc["messages"] or doc["messages"][-1].get("role") != "user":
            _unjournal(chat_id)  # nothing waiting for an answer
            continue
        if attempts >= MAX_RESUMES or not isinstance(conf, tuple) or not history_builder:
            _unjournal(chat_id)
            health.record("run", "gave up resuming after restarts")
            msg = "I got restarted while replying and couldn't pick that up. Send it again."
            chats.save(chat_id, doc["messages"] + [{"role": "assistant", "content": msg, "parts": [{"type": "text", "text": msg}]}])
            if chat_id in RELAYS:
                run = Run(chat_id)
                run.events, run.finished = [{"error": msg}], True
                _runs[chat_id] = run
                asyncio.ensure_future(RELAYS[chat_id](run))
            continue
        history = history_builder(doc["messages"])
        partial = assistant_message(events)
        if partial:
            if partial["content"]:
                history.append({"role": "assistant", "content": partial["content"]})
            history.append({"role": "user", "content": f"{RESTART_NOTE}\n{_summary(events)}".strip()})
        health.record("run", "resuming a reply after a restart")
        start(chat_id, *conf, history, attempts=attempts + 1, prior=events)


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
        elif not memory.is_contact_chat(run.chat_id) and vault.load()["generative_ui"]:
            notes = f"{notes}\n\n{agent.ui_prompt()}".strip()  # the web chat can draw tables, plans and checklists
        async for ev in agent.run(name, key, model, history, notes, steer=lambda: _take_steer(run)):
            if "error" in ev:
                error = ev["error"]
            run.push(ev)
    except asyncio.CancelledError:
        if run.user_stop or run.discard:
            run.push({"stopped": True})
            if sandbox.configured():
                try:
                    await sandbox.kill_all()  # a command it started would otherwise keep running after Stop
                except Exception:
                    pass
        else:
            run.interrupted = True  # the server is going away: leave the journal for the next start
            _journal(run)
    except Exception as e:
        health.record("run", f"unexpected {type(e).__name__}")
        error = "Something went wrong running that reply. Send it again."
        run.push({"error": error})
    finally:
        if run.interrupted:
            run.finished = True
            run.wake()
            return
        follow = None
        try:
            if not run.discard:
                doc = chats.load(run.chat_id) or {"messages": []}
                msg = assistant_message(run.events)
                messages = doc["messages"]
                if msg:
                    # messages sent while it was busy are already saved after the question: the reply goes before them
                    at = len(messages) - run.queued
                    if run.queued and not (0 <= at <= len(messages) and all(m.get("role") == "user" for m in messages[at:])):
                        at = len(messages)
                    messages = messages[:at] + [msg] + messages[at:]
                try:
                    chats.save(run.chat_id, messages, error=error)
                except ValueError:
                    health.record("run", "chat too large to save")
                    chats.save(run.chat_id, messages[-60:], error=error)
                if not error and not any("stopped" in e for e in run.events):
                    asyncio.ensure_future(titles.name_chat(run.chat_id, name, key, model))
                    asyncio.ensure_future(memory.learn(run.chat_id, name, key, model))
                if run.queued and not run.user_stop and history_builder:
                    follow = history_builder(messages)
        except Exception as e:
            health.record("run", f"could not save a reply: {type(e).__name__}")
        finally:
            _unjournal(run.chat_id)
            if follow is not None:
                try:
                    start(run.chat_id, name, key, model, follow)
                except Exception as e:
                    health.record("run", f"could not start the next reply: {type(e).__name__}")
            run.finished = True
            run.wake()


def start(chat_id: str, name: str, key: str, model: str, history: list[dict], attempts: int = 0, prior: list[dict] | None = None) -> Run:
    run = Run(chat_id)
    run.conf = (name, key, model)
    run.attempts = attempts
    if prior:
        run.events = list(prior)  # what the chat showed before the restart; clients replay it
        run.skip = len(prior)
        run.resumed = True
    _runs[chat_id] = run
    _journal(run)
    run.task = asyncio.ensure_future(_execute(run, name, key, model, history))
    if chat_id in RELAYS:
        asyncio.ensure_future(RELAYS[chat_id](run))
    return run


def _take_steer(run: Run) -> list[dict]:
    """The mid-run messages not yet seen by the model, as conversation messages. They stop counting as queued."""
    if not run.steer or not history_builder:
        return []
    taken, run.steer = run.steer, []
    run.queued = max(0, run.queued - len(taken))
    out = history_builder(taken)
    for m in out:
        if isinstance(m["content"], str):
            m["content"] = "(Sent while you were working. Take it into account and carry on.)\n" + m["content"]
    return out


def queue(chat_id: str, user: dict) -> bool:
    """A message arrived while Lark was busy in this chat: save it now, answer it right after the current reply."""
    r = get(chat_id)
    if not r:
        return False
    doc = chats.load(chat_id) or {"messages": []}
    chats.save(chat_id, doc["messages"] + [user])
    r.queued += 1
    r.steer.append(user)
    return True


def stop(chat_id: str, discard: bool = False) -> bool:
    r = get(chat_id)
    if not r or not r.task:
        return False
    r.discard = discard
    r.user_stop = True
    r.task.cancel()
    return True


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev)}\n\n"
