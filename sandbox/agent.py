"""Exec agent that runs inside the sandbox container. Standard library only.

Endpoints (all need `Authorization: Bearer $SANDBOX_TOKEN`):
  POST /browser {action, ...}   GET  /health   POST /exec {command, timeout}   GET /file?path=   PUT /file {path, content}   POST /wipe
"""
import hmac
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
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


CHROME = os.environ.get("SANDBOX_CHROMIUM", "/usr/bin/chromium")
IDLE_CLOSE = 300  # seconds; the browser is the memory hog, so it shuts down when unused

SNAPSHOT_JS = """() => {
  document.querySelectorAll('[data-lark]').forEach(e => e.removeAttribute('data-lark'));
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],[onclick],[contenteditable=true]';
  const els = [];
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none' || e.disabled) continue;
    if (e.type === 'hidden') continue;
    const n = els.length + 1;
    e.setAttribute('data-lark', n);
    const tag = e.tagName.toLowerCase();
    const label = (e.getAttribute('aria-label') || e.innerText || e.placeholder || e.title || e.name || e.value || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 80);
    els.push({ n, kind: tag === 'a' ? 'link' : tag === 'input' ? (e.type || 'text') : tag, label,
      href: tag === 'a' ? e.getAttribute('href') : null,
      value: ['input', 'textarea', 'select'].includes(tag) && e.type !== 'password' ? String(e.value || '').slice(0, 40) : null });
    if (els.length >= 80) break;
  }
  return { url: location.href, title: document.title, text: (document.body ? document.body.innerText : '').slice(0, 5000), els,
    y: Math.round(scrollY), height: document.documentElement.scrollHeight, view: innerHeight };
}"""


class Browser:
    """One headless Chromium page, driven by element numbers. Playwright's sync API is bound to the thread
    that created it, so every call runs on a single worker thread."""

    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pw = self.browser = self.page = None
        self.last_used = 0.0

    def available(self) -> bool:
        if not os.path.exists(CHROME):
            return False
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure(self):
        if self.page and self.browser.is_connected():
            return
        self._close()
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.launch(executable_path=CHROME, headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio"])
        ctx = self.browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-GB")
        self.page = ctx.new_page()
        self.page.set_default_timeout(10000)

    def _close(self):
        for obj, fn in ((self.browser, "close"), (self.pw, "stop")):
            try:
                if obj:
                    getattr(obj, fn)()
            except Exception:
                pass
        self.pw = self.browser = self.page = None

    def close_if_idle(self):
        if self.page and time.time() - self.last_used > IDLE_CLOSE:
            self.pool.submit(self._close)

    def _snapshot(self) -> str:
        page = self.page
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
            page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:
            pass
        d = page.evaluate(SNAPSHOT_JS)
        lines = [f"URL: {d['url']}", f"Title: {d['title']}",
                 f"Scroll: {d['y']}/{max(d['height'] - d['view'], 0)}", "", "Page text:", d["text"].strip() or "(no text)", "",
                 "Interactive elements:"]
        for e in d["els"]:
            extra = f" -> {e['href']}" if e["href"] else ""
            val = f" = {e['value']!r}" if e["value"] else ""
            lines.append(f"[{e['n']}] {e['kind']} {e['label']!r}{val}{extra}")
        if not d["els"]:
            lines.append("(none)")
        return "\n".join(lines)

    def _act(self, a: dict) -> str:
        self._ensure()
        page = self.page
        action = a.get("action")
        if action == "goto":
            url = a.get("url") or ""
            if "://" not in url:
                url = "https://" + url
            if not url.startswith(("http://", "https://")):
                raise ValueError("Only http and https pages can be opened.")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        elif action in ("click", "type"):
            target = page.locator(f'[data-lark="{int(a.get("id"))}"]').first
            if target.count() == 0:
                raise ValueError("No element with that number. Use the numbers from the latest snapshot.")
            if action == "click":
                target.click(timeout=8000)
            else:
                target.fill(str(a.get("text", "")), timeout=8000)
                if a.get("submit"):
                    target.press("Enter")
        elif action == "press":
            page.keyboard.press(str(a.get("key", "Enter")))
        elif action == "scroll":
            dy = 700 if a.get("direction", "down") != "up" else -700
            page.evaluate("dy => window.scrollBy(0, dy)", dy)
        elif action == "back":
            page.go_back(wait_until="domcontentloaded", timeout=15000)
        elif action != "snapshot":
            raise ValueError("Unknown browser action.")
        return self._snapshot()

    def do(self, a: dict) -> dict:
        self.last_used = time.time()

        def job():
            try:
                return {"snapshot": self._act(a)}
            except Exception as e:  # playwright errors are long; keep the first line
                msg = str(e).strip().split("\n")[0][:300] or type(e).__name__
                return {"error": f"Browser error: {msg}"}

        out = self.pool.submit(job).result(timeout=90)
        self.last_used = time.time()
        return out


browser = Browser()


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
                return self.reply(200, {"home": HOME, "browser": browser.available(),
                                        "disk_free_mb": shutil.disk_usage(HOME if os.path.isdir(HOME) else "/").free // 2**20})
            if (method, url.path) == ("POST", "/browser"):
                if not browser.available():
                    return self.reply(501, {"error": "This sandbox has no browser. Rebuild it with sandbox.sh up."})
                r = browser.do(self.body())
                return self.reply(400 if "error" in r else 200, r)
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


def _janitor():
    while True:
        time.sleep(30)
        browser.close_if_idle()


if __name__ == "__main__":
    threading.Thread(target=_janitor, daemon=True).start()
    ThreadingHTTPServer((BIND, PORT), Handler).serve_forever()
