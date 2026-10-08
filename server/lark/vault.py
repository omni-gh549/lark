"""Settings and API keys on disk. Keys are encrypted with Fernet and never leave the server."""
import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

DATA_DIR = Path(os.environ.get("LARK_DATA", "data")).resolve()
SETTINGS = DATA_DIR / "settings.json"
MASTER = DATA_DIR / "secret.key"

DEFAULTS = {
    "provider": "openrouter",
    "models": {"openrouter": "deepseek/deepseek-v4.1-flash", "gateway": ""},
    "search": "brave",
    "browser_cookies": False,  # keep the sandbox browser's cookies and logins between sessions
    "memory_use": True,  # remember across chats: the notes in the prompt and the memory tools
    "memory_learn": True,  # learn from conversations automatically, after each reply
    "generative_ui": True,  # let the web chat show tables, plans and checklists as small interfaces (OpenUI)
    "memory_model": "",  # optional cheaper model for the learning pass (empty = the chat model)
    "embedding_model": "",  # optional: match memories by meaning (needs an embeddings model on the chosen provider)
    "keys": {},
}


def _private_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        DATA_DIR.chmod(0o700)
    except OSError:
        pass


def _fernet() -> Fernet:
    env = os.environ.get("LARK_SECRET_KEY")
    if env:
        return Fernet(env.encode())
    _private_dir()
    if not MASTER.exists():
        fd = os.open(MASTER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return Fernet(MASTER.read_bytes().strip())


# Suggested cheap models, used until you pick your own (OpenRouter only; empty elsewhere).
SUGGESTED = {"openrouter": {"memory_model": "qwen/qwen3.7-flash", "embedding_model": "qwen/qwen3-embedding-8b"}}


def load() -> dict:
    data = json.loads(SETTINGS.read_text()) if SETTINGS.exists() else {}
    for k, v in SUGGESTED.get(data.get("provider", DEFAULTS["provider"]), {}).items():
        if not os.environ.get("LARK_NO_SUGGESTED") and k not in data:
            data[k] = v
    return {
        "provider": data.get("provider", DEFAULTS["provider"]),
        "models": {**DEFAULTS["models"], **data.get("models", {})},
        "search": data.get("search", DEFAULTS["search"]),
        "browser_cookies": bool(data.get("browser_cookies", False)),
        "generative_ui": bool(data.get("generative_ui", True)),
        "memory_use": bool(data.get("memory_use", True)),
        "memory_learn": bool(data.get("memory_learn", True)),
        "memory_model": str(data.get("memory_model", DEFAULTS["memory_model"])),
        "embedding_model": str(data.get("embedding_model", DEFAULTS["embedding_model"])),
        "keys": data.get("keys", {}),
    }


def _save(data: dict):
    _private_dir()
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, SETTINGS)


def update(provider=None, models=None, search=None, browser_cookies=None, memory_use=None, memory_learn=None, embedding_model=None, memory_model=None, generative_ui=None):
    data = load()
    if generative_ui is not None:
        data["generative_ui"] = bool(generative_ui)
    if browser_cookies is not None:
        data["browser_cookies"] = bool(browser_cookies)
    if memory_use is not None:
        data["memory_use"] = bool(memory_use)
    if memory_learn is not None:
        data["memory_learn"] = bool(memory_learn)
    if memory_model is not None:
        data["memory_model"] = memory_model.strip()[:200]
    if embedding_model is not None:
        data["embedding_model"] = embedding_model.strip()[:200]
    if provider:
        data["provider"] = provider
    if search:
        data["search"] = search
    if models:
        data["models"].update(models)
    _save(data)


def set_key(name: str, key: str):
    data = load()
    data["keys"][name] = _fernet().encrypt(key.encode()).decode()
    _save(data)


def delete_key(name: str):
    data = load()
    data["keys"].pop(name, None)
    _save(data)


def get_key(name: str) -> str | None:
    token = load()["keys"].get(name)
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return None  # master key changed; treat as unset so the user re-enters it


def key_hint(name: str) -> str | None:
    key = get_key(name)
    return key[-4:] if key else None
