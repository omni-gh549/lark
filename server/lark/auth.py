"""Optional single-password login. Without LARK_PASSWORD the app only answers on localhost."""
import hashlib
import hmac
import os

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
