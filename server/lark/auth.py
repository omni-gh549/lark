"""Optional single-password login. Without LARK_PASSWORD the app only answers on localhost."""
import hashlib
import hmac
import os
import time

COOKIE = "lark_session"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def password() -> str | None:
    return os.environ.get("LARK_PASSWORD") or None


def token() -> str:
    pw = password() or ""
    return hmac.new(pw.encode(), b"lark-session-v1", hashlib.sha256).hexdigest()


def password_ok(attempt: str) -> bool:
    return hmac.compare_digest(attempt.encode(), (password() or "").encode())


def session_ok(cookie: str | None) -> bool:
    return bool(cookie) and hmac.compare_digest(cookie, token())


def host_is_local(host_header: str) -> bool:
    host = host_header.rsplit(":", 1)[0] if not host_header.endswith("]") else host_header
    return host in LOCAL_HOSTS


# Failed-login throttle. Behind a reverse proxy every client shares one address,
# so this is global: five misses in ten minutes pause all logins for five minutes.
MAX_MISSES, WINDOW, PAUSE = 5, 600, 300
_misses: list[float] = []
_paused_until = 0.0


def seconds_paused() -> int:
    return max(0, int(_paused_until - time.monotonic()) + 1) if _paused_until > time.monotonic() else 0


def record_miss():
    global _paused_until
    now = time.monotonic()
    _misses[:] = [t for t in _misses if now - t < WINDOW] + [now]
    if len(_misses) >= MAX_MISSES:
        _paused_until = now + PAUSE
        _misses.clear()


def record_success():
    _misses.clear()
