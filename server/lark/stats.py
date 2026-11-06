"""A small log of model calls (which model, when, how many tokens when the provider says), for the Usage page."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone

from . import vault

_lock = threading.Lock()
MAX_BYTES = 5_000_000  # past this the oldest half is dropped


def _path():
    return vault.DATA_DIR / "usage.jsonl"


def record(provider: str, model: str, usage: dict | None = None) -> None:
    usage = usage if isinstance(usage, dict) else {}
    row = {"t": int(time.time()), "p": provider, "m": model,
           "i": usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None,
           "o": usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None}
    try:
        with _lock:
            vault._private_dir()
            p = _path()
            if p.exists() and p.stat().st_size > MAX_BYTES:
                lines = p.read_text().splitlines()
                p.write_text("\n".join(lines[len(lines) // 2:]) + "\n")
            with p.open("a") as f:
                f.write(json.dumps(row) + "\n")
    except OSError:
        pass  # statistics must never get in the way of a reply


def summary(days: int = 30) -> dict:
    now = datetime.now(timezone.utc)
    start = int((now - timedelta(days=days)).timestamp())
    by_model: dict[tuple[str, str], dict] = {}
    by_day: dict[str, int] = {}
    try:
        lines = _path().read_text().splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("t", 0) < start:
            continue
        m = by_model.setdefault((r["p"], r["m"]), {"provider": r["p"], "model": r["m"], "calls": 0, "input": 0, "output": 0})
        m["calls"] += 1
        m["input"] += r.get("i") or 0
        m["output"] += r.get("o") or 0
        d = datetime.fromtimestamp(r["t"], timezone.utc).strftime("%Y-%m-%d")
        by_day[d] = by_day.get(d, 0) + 1
    day_list = [(now - timedelta(days=n)).strftime("%Y-%m-%d") for n in range(days - 1, -1, -1)]
    return {
        "days": days,
        "models": sorted(by_model.values(), key=lambda m: -m["calls"]),
        "daily": [{"day": d, "calls": by_day.get(d, 0)} for d in day_list],
        "calls": sum(by_day.values()),
        "chats": sum(1 for _ in (vault.DATA_DIR / "chats").glob("*.json")) if (vault.DATA_DIR / "chats").exists() else 0,
    }
