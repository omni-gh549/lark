"""Saved chats, one JSON file each under the data directory."""
import json
import os
import re
import tempfile
import time

from . import vault

ID = re.compile(r"^[a-zA-Z0-9-]{8,40}$")
MAX_BYTES = 5_000_000


def _dir():
    d = vault.DATA_DIR / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def valid(chat_id: str) -> bool:
    return bool(ID.match(chat_id))


def _title(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user" and m.get("content"):
            text = " ".join(str(m["content"]).split())
            return text[:60] + ("…" if len(text) > 60 else "")
    return "Image" if any(m.get("images") for m in messages) else "New chat"


def save(chat_id: str, messages: list[dict], error: str | None = None) -> dict:
    path = _dir() / f"{chat_id}.json"
    created = time.time()
    if path.exists():
        try:
            created = json.loads(path.read_text()).get("created", created)
        except ValueError:
            pass
    doc = {"id": chat_id, "title": _title(messages), "created": created, "updated": time.time(), "messages": messages}
    if error:
        doc["error"] = error
    data = json.dumps(doc)
    if len(data) > MAX_BYTES:
        raise ValueError("This chat is too large to save.")
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    try:
        from . import memory  # late: memory reads chats
        memory.index_chat(chat_id, doc)
    except Exception:
        pass  # search is a convenience; saving the chat must not depend on it
    return {k: doc[k] for k in ("id", "title", "created", "updated")}


def load(chat_id: str) -> dict | None:
    path = _dir() / f"{chat_id}.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def delete(chat_id: str) -> None:
    try:
        (_dir() / f"{chat_id}.json").unlink()
    except OSError:
        pass
    try:
        from . import memory
        memory.drop_chat(chat_id)
    except Exception:
        pass


def listing() -> list[dict]:
    rows = []
    for p in _dir().glob("*.json"):
        try:
            d = json.loads(p.read_text())
            rows.append({k: d[k] for k in ("id", "title", "created", "updated")})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(rows, key=lambda r: r["updated"], reverse=True)
