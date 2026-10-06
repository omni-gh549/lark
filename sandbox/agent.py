"""Exec agent that runs inside the sandbox container. Standard library only.

Endpoints (all need `Authorization: Bearer $SANDBOX_TOKEN`):
  GET  /health   POST /exec {command, timeout}   GET /file?path=   PUT /file {path, content}   POST /wipe
"""
import hmac
import json
import os
import shutil
import signal
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = os.environ["SANDBOX_TOKEN"]
HOME = os.environ.get("SANDBOX_HOME", "/home/lark")
PORT = int(os.environ.get("SANDBOX_PORT", "8791"))
BIND = os.environ.get("SANDBOX_BIND", "0.0.0.0")
MAX_OUTPUT = 200_000
MAX_FILE = 2_000_000
MAX_BODY = 4_000_000
MAX_JOBS = 4
jobs = threading.BoundedSemaphore(MAX_JOBS)


def resolve(path: str) -> str:
    return os.path.normpath(os.path.join(HOME, os.path.expanduser(path) if path.startswith("~") else path))


def run(command: str, timeout: int) -> dict:
    if not jobs.acquire(blocking=False):
        return {"error": "Too many commands running at once."}
    try:
        os.makedirs(HOME, exist_ok=True)
        env = {**os.environ, "HOME": HOME, "TERM": "dumb", "DEBIAN_FRONTEND": "noninteractive"}
        env.pop("SANDBOX_TOKEN", None)
        p = subprocess.Popen(["bash", "-c", command], cwd=HOME, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        timed_out = False
        try:
            out, _ = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, signal.SIGKILL)
            out, _ = p.communicate()
        else:
            try:
                os.killpg(p.pid, signal.SIGKILL)  # strays left behind by the command
            except ProcessLookupError:
                pass
        text = out.decode(errors="replace")
        if len(text) > MAX_OUTPUT:
            text = text[: MAX_OUTPUT // 2] + "\n… [output cut] …\n" + text[-MAX_OUTPUT // 2:]
        return {"output": text, "exit_code": None if timed_out else p.returncode, "timed_out": timed_out}
    finally:
        jobs.release()


def wipe() -> dict:
    os.makedirs(HOME, exist_ok=True)
    for name in os.listdir(HOME):
        full = os.path.join(HOME, name)
        if os.path.isdir(full) and not os.path.islink(full):
            shutil.rmtree(full, ignore_errors=True)
        else:
            try:
                os.remove(full)
            except OSError:
                pass
    return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = "sandbox"

    def log_message(self, *a):
        pass

    def reply(self, code: int, obj: dict):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authed(self) -> bool:
        got = self.headers.get("Authorization", "")
        if hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode()):
            return True
        self.reply(401, {"error": "Bad sandbox token."})
        return False

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("Request too large.")
        data = json.loads(self.rfile.read(n) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Bad request.")
        return data

    def handle_any(self, method: str):
        if not self.authed():
            return
        url = urlparse(self.path)
        try:
            if (method, url.path) == ("GET", "/health"):
                return self.reply(200, {"home": HOME, "disk_free_mb": shutil.disk_usage(HOME if os.path.isdir(HOME) else "/").free // 2**20})
            if (method, url.path) == ("POST", "/exec"):
                b = self.body()
                command, timeout = b.get("command"), b.get("timeout", 60)
                if not isinstance(command, str) or not command.strip():
                    return self.reply(400, {"error": "Missing command."})
                timeout = timeout if isinstance(timeout, int) and 1 <= timeout <= 600 else 60
                r = run(command, timeout)
                return self.reply(429 if "error" in r else 200, r)
            if (method, url.path) == ("GET", "/file"):
                path = resolve((parse_qs(url.query).get("path") or [""])[0])
                if not os.path.isfile(path):
                    return self.reply(404, {"error": f"No such file: {path}"})
                if os.path.getsize(path) > MAX_FILE:
                    return self.reply(413, {"error": "File is too large to read."})
                with open(path, "rb") as f:
                    return self.reply(200, {"path": path, "content": f.read().decode(errors="replace")})
            if (method, url.path) == ("PUT", "/file"):
                b = self.body()
                if not isinstance(b.get("path"), str) or not isinstance(b.get("content"), str):
                    return self.reply(400, {"error": "Need path and content."})
                path = resolve(b["path"])
                os.makedirs(os.path.dirname(path), exist_ok=True)
                data = b["content"].encode()
                with open(path, "wb") as f:
                    f.write(data)
                return self.reply(200, {"path": path, "bytes": len(data)})
            if (method, url.path) == ("POST", "/wipe"):
                return self.reply(200, wipe())
            self.reply(404, {"error": "Not found."})
        except (ValueError, OSError) as e:
            self.reply(400, {"error": str(e) or "Bad request."})

    def do_GET(self):
        self.handle_any("GET")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.handle_any("PUT")


if __name__ == "__main__":
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
