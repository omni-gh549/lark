"""A short memory of what went wrong, so failures are visible in the logs and countable. Never holds message content."""
import logging
import time
from collections import Counter, deque

log = logging.getLogger("lark")
_recent: deque = deque(maxlen=60)


def record(kind: str, detail: str = ""):
    """kind: where it happened (tool:browser, run, telegram, provider, ...). detail: error class or a short reason."""
    detail = " ".join(str(detail).split())[:160]
    _recent.append({"t": time.time(), "kind": kind, "detail": detail})
    log.warning("problem %s: %s", kind, detail)


def summary(window: float = 3600) -> dict:
    now = time.time()
    rows = [r for r in _recent if r["t"] > now - window]
    return {"last_hour": dict(Counter(r["kind"] for r in rows)),
            "latest": [{"ago": int(now - r["t"]), "kind": r["kind"], "detail": r["detail"]} for r in list(rows)[-10:]]}
