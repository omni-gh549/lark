"""The blackboard: the run's shared, durable memory. One SQLite file per run.

Agents write typed entries (claims with sources, files, open questions, decisions, risks) instead of prose, and read the
board instead of asking each other, which is how a hundred agents share facts without a hundred conversations.
It also keeps the agent list, the message log and the phase checkpoints, so a restart can carry on."""
import json
import re
import sqlite3
import time
from pathlib import Path

from .. import vault

KINDS = ("claim", "source", "file", "question", "decision", "risk", "report", "draft")
WRITABLE = ("claim", "source", "file", "question", "decision", "risk")  # what workers may write
STATUSES = ("open", "verified", "disputed", "dropped", "answered")
CONF = {"low": 0, "med": 1, "high": 2}
KEEP_RUNS = 25

SCHEMA = """
create table if not exists entries(id integer primary key, ts real, kind text, author text, squad text, text text, data text default '{}', status text default 'open', note text default '');
create table if not exists agents(id text primary key, parent text, squad text, kind text, depth int, task text, state text, result text default '', error text default '', born real, ended real, rounds int default 0, spent real default 0);
create table if not exists messages(id integer primary key, ts real, frm text, "to" text, kind text, text text);
create table if not exists kv(k text primary key, v text);
"""


def _dir() -> Path:
    d = vault.DATA_DIR / "swarm"
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_for(run: str) -> Path:
    return _dir() / f"{run}.db"


def exists(run: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]{6,32}", run or "")) and path_for(run).exists()


def prune():
    """Keeps the newest few runs' boards; older ones are only history."""
    try:
        files = sorted(_dir().glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[KEEP_RUNS:]:
            for ext in ("", "-wal", "-shm"):
                Path(str(p) + ext).unlink(missing_ok=True)
    except OSError:
        pass


class Board:
    def __init__(self, run: str):
        self.run = run
        self.db = sqlite3.connect(path_for(run), check_same_thread=False, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        try:
            self.db.close()
        except sqlite3.Error:
            pass

    # ---- entries --------------------------------------------------------------------------------------------------
    def add(self, kind: str, author: str, text: str, squad: str = "", **data) -> int:
        cur = self.db.execute("insert into entries(ts,kind,author,squad,text,data) values(?,?,?,?,?,?)",
                              (time.time(), kind, author, squad, text.strip()[:2000], json.dumps({k: v for k, v in data.items() if v not in (None, "")})))
        self.db.commit()
        return cur.lastrowid

    def get(self, eid: int) -> dict | None:
        r = self.db.execute("select * from entries where id=?", (eid,)).fetchone()
        return self._row(r) if r else None

    def mark(self, eid: int, status: str, note: str = "") -> bool:
        cur = self.db.execute("update entries set status=?, note=? where id=?", (status, note.strip()[:600], eid))
        self.db.commit()
        return cur.rowcount > 0

    def find(self, kind: str | None = None, status: str | None = None, squad: str | None = None, query: str = "",
             since: int = 0, limit: int = 40) -> list[dict]:
        sql, args = "select * from entries where id>?", [since]
        if kind:
            sql += " and kind=?"
            args.append(kind)
        if status:
            sql += " and status=?"
            args.append(status)
        if squad:
            sql += " and squad=?"
            args.append(squad)
        for word in re.findall(r"\w{3,}", query.lower())[:6]:
            sql += " and lower(text || ' ' || data) like ?"
            args.append(f"%{word}%")
        sql += " order by id desc limit ?"
        args.append(limit)
        return [self._row(r) for r in reversed(self.db.execute(sql, args).fetchall())]

    def count(self, kind: str, status: str | None = None) -> int:
        sql, args = "select count(*) from entries where kind=?", [kind]
        if status:
            sql += " and status=?"
            args.append(status)
        return self.db.execute(sql, args).fetchone()[0]

    def sources(self) -> int:
        urls = set()
        for r in self.db.execute("select data from entries where kind in ('claim','source')"):
            u = json.loads(r[0] or "{}").get("url")
            if u:
                urls.add(u)
        return len(urls)

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["data"] = json.loads(d["data"] or "{}")
        return d

    @staticmethod
    def line(e: dict, width: int = 400) -> str:
        d = e["data"]
        bits = [f"#{e['id']} {e['kind']}"]
        if e["status"] != "open" or e["kind"] == "claim":
            bits.append(f"[{e['status']}]")
        if e["squad"]:
            bits.append(f"({e['squad']})")
        if d.get("confidence"):
            bits.append(f"conf {d['confidence']}")
        text = e["text"][:width] + ("…" if len(e["text"]) > width else "")
        tail = ""
        if d.get("url"):
            tail += f" — {d['url']}"
        if d.get("quote"):
            tail += f' — "{d["quote"][:200]}"'
        if d.get("path"):
            tail += f" — file {d['path']}"
        if d.get("to"):
            tail += f" — to {d['to']}"
        if e["note"]:
            tail += f" — note: {e['note'][:200]}"
        return " ".join(bits) + ": " + text + tail

    # ---- agents and messages --------------------------------------------------------------------------------------
    def save_agent(self, a) -> None:
        self.db.execute("insert or replace into agents(id,parent,squad,kind,depth,task,state,result,error,born,ended,rounds,spent) values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (a.id, a.parent, a.squad, a.kind, a.depth, a.task, a.state, a.result[:6000], a.error[:600], a.born, a.ended, a.rounds, a.spent))
        self.db.commit()

    def agents(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("select * from agents order by born")]

    def log_message(self, frm: str, to: str, kind: str, text: str) -> None:
        self.db.execute('insert into messages(ts,frm,"to",kind,text) values(?,?,?,?,?)', (time.time(), frm, to, kind, text[:1500]))
        self.db.commit()

    # ---- checkpoints ----------------------------------------------------------------------------------------------
    def set(self, k: str, v) -> None:
        self.db.execute("insert or replace into kv(k,v) values(?,?)", (k, json.dumps(v)))
        self.db.commit()

    def get_kv(self, k: str, default=None):
        r = self.db.execute("select v from kv where k=?", (k,)).fetchone()
        return json.loads(r[0]) if r else default
