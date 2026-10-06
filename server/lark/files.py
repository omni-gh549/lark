"""Images saved on the server: ones you attach, and ones Lark shows (screenshots, sandbox files)."""
import base64
import re
import uuid

from . import vault

MAX_BYTES = 8_000_000
ID = re.compile(r"^[0-9a-f]{32}\.(png|jpg|gif|webp)$")
TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


class FileError(ValueError):
    pass


def _dir():
    d = vault.DATA_DIR / "uploads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sniff(data: bytes) -> str:
    """Extension for a supported image, judged by content rather than by what the sender claims."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise FileError("Only PNG, JPEG, GIF and WebP images are supported.")


def save(data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise FileError("That image is too large (8 MB max).")
    name = f"{uuid.uuid4().hex}.{sniff(data)}"
    path = _dir() / name
    path.write_bytes(data)
    path.chmod(0o600)
    return name


def valid(name: str) -> bool:
    return bool(ID.match(name))


def path(name: str):
    p = _dir() / name
    return p if valid(name) and p.is_file() else None


def mime(name: str) -> str:
    return TYPES[name.rsplit(".", 1)[1]]


def data_url(name: str) -> str | None:
    p = path(name)
    if not p:
        return None
    return f"data:{mime(name)};base64,{base64.b64encode(p.read_bytes()).decode()}"
