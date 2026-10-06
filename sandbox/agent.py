"""Exec agent that runs inside the sandbox container. Standard library only.

Endpoints (all need `Authorization: Bearer $SANDBOX_TOKEN`):
  POST /browser {action, ...}   GET  /health   POST /exec {command, timeout}   GET /file?path=   PUT /file {path, content}   POST /wipe
"""
import asyncio
import base64
import hmac
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

TOKEN = os.environ["SANDBOX_TOKEN"]
HOME = os.environ.get("SANDBOX_HOME", "/home/lark")
PORT = int(os.environ.get("SANDBOX_PORT", "8791"))
BIND = os.environ.get("SANDBOX_BIND", "0.0.0.0")
MAX_OUTPUT = 200_000
MAX_FILE = 8_000_000
MAX_BODY = 4_000_000
MAX_JOBS = 4
jobs = threading.BoundedSemaphore(MAX_JOBS)
running: set = set()  # commands in flight, so Stop can kill them


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
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) { /* off-screen: still clickable after scrolling */ }
    const hit = document.elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1), Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
    const covered = r.top >= 0 && r.bottom <= innerHeight && hit && hit !== e && !e.contains(hit) && !hit.contains(e);
    const n = els.length + 1;
    e.setAttribute('data-lark', n);
    const tag = e.tagName.toLowerCase();
    const label = (e.getAttribute('aria-label') || e.innerText || e.placeholder || e.title || e.name || e.value || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 80);
    els.push({ n, covered: !!covered, cover: covered ? (hit.getAttribute('aria-label') || hit.id || hit.className || hit.tagName).toString().slice(0, 40) : '', kind: tag === 'a' ? 'link' : tag === 'input' ? (e.type || 'text') : tag, label,
      href: tag === 'a' ? e.getAttribute('href') : null,
      value: ['input', 'textarea', 'select'].includes(tag) && e.type !== 'password' ? String(e.value || '').slice(0, 40) : null });
    if (els.length >= 80) break;
  }
  return { url: location.href, title: document.title, text: (document.body ? document.body.innerText : '').slice(0, 5000), els,
    y: Math.round(scrollY), height: document.documentElement.scrollHeight, view: innerHeight };
}"""


class Browser:
    """One headless Chromium page, driven by element numbers. It lives on its own asyncio loop so a screenshot
    for the live view can be taken while an action (a slow page load, say) is still running."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.acting = asyncio.run_coroutine_threadsafe(self._make_lock(), self.loop).result()
        self.pw = self.browser = self.page = None
        self.last_used = 0.0
        self.cursor = None  # where the last click or typing landed, as fractions of the viewport, for the live view

    async def _make_lock(self):
        return asyncio.Lock()

    def available(self) -> bool:
        if not os.path.exists(CHROME):
            return False
        try:
            import playwright  # noqa: F401
            return True
        except ImportError:
            return False

    async def _ensure(self):
        if self.page and self.browser.is_connected():
            return
        await self._close()
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(executable_path=CHROME, headless=True, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio", "--disable-extensions",
            "--disable-background-networking", "--js-flags=--max-old-space-size=384"])
        ctx = await self.browser.new_context(viewport={"width": 1280, "height": 800}, locale="en-GB")

        async def gate(route):
            if route.request.resource_type == "media":  # no autoplaying video: it costs memory and tells Lark nothing
                await route.abort()
            else:
                await route.continue_()

        await ctx.route("**/*", gate)
        self.page = await ctx.new_page()
        self.page.set_default_timeout(10000)

    async def _close(self):
        for obj, fn in ((self.browser, "close"), (self.pw, "stop")):
            try:
                if obj:
                    await getattr(obj, fn)()
            except Exception:
                pass
        self.pw = self.browser = self.page = None
        self.cursor = None

    def close_if_idle(self):
        if self.page and time.time() - self.last_used > IDLE_CLOSE:
            asyncio.run_coroutine_threadsafe(self._close(), self.loop)

    async def _snapshot(self) -> str:
        page = self.page
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
            await page.wait_for_load_state("networkidle", timeout=1200)
        except Exception:
            pass
        d = await page.evaluate(SNAPSHOT_JS)
        lines = [f"URL: {d['url']}", f"Title: {d['title']}",
                 f"Scroll: {d['y']}/{max(d['height'] - d['view'], 0)}", "", "Page text:", d["text"].strip() or "(no text)", "",
                 "Interactive elements:"]
        for e in d["els"]:
            extra = f" -> {e['href']}" if e["href"] else ""
            val = f" = {e['value']!r}" if e["value"] else ""
            cov = f" (covered by {e['cover']!r}; deal with that first)" if e.get("covered") else ""
            lines.append(f"[{e['n']}] {e['kind']} {e['label']!r}{val}{extra}{cov}")
        if not d["els"]:
            lines.append("(none)")
        return "\n".join(lines)

    async def _act(self, a: dict) -> dict:
        await self._ensure()
        page = self.page
        action = a.get("action")
        out = {}
        if action == "goto":
            url = a.get("url") or ""
            if "://" not in url:
                url = "https://" + url
            if not url.startswith(("http://", "https://")):
                raise ValueError("Only http and https pages can be opened.")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        elif action in ("click", "type"):
            target = page.locator(f'[data-lark="{int(a.get("id"))}"]').first
            if await target.count() == 0:
                raise ValueError("No element with that number. Use the numbers from the latest snapshot.")
            await target.scroll_into_view_if_needed(timeout=5000)
            await self._point(target, action == "click")
            await asyncio.sleep(0.6)  # let the live view's cursor glide there before the page reacts
            if action == "click":
                try:
                    await target.click(timeout=2500)
                except Exception:
                    # Playwright waits for the element to be unobscured and still; pages with overlays or
                    # animations stall it, so after a short wait click anyway, then fall back to a script click.
                    try:
                        await target.click(timeout=2000, force=True)
                    except Exception:
                        await target.evaluate("e => e.click()")
            else:
                await target.fill(str(a.get("text", "")), timeout=4000)
                if a.get("submit"):
                    await target.press("Enter")
        elif action == "press":
            await page.keyboard.press(str(a.get("key", "Enter")))
        elif action == "scroll":
            dy = 700 if a.get("direction", "down") != "up" else -700
            await page.evaluate("dy => window.scrollBy(0, dy)", dy)
        elif action == "back":
            await page.go_back(wait_until="domcontentloaded", timeout=15000)
        elif action == "screenshot":
            out["image"] = base64.b64encode(await page.screenshot(type="jpeg", quality=70, timeout=8000)).decode()
        elif action != "snapshot":
            raise ValueError("Unknown browser action.")
        out["snapshot"] = await self._snapshot()
        return out

    async def _point(self, target, click: bool):
        # The element's centre as a fraction of the visible page, measured in the page itself so it matches
        # exactly what the screenshot shows.
        pos = await target.evaluate("""e => {
            const r = e.getBoundingClientRect();
            return { x: (r.left + r.width / 2) / innerWidth, y: (r.top + r.height / 2) / innerHeight };
        }""")
        x, y = (min(max(float(pos[k]), 0), 1) for k in ("x", "y"))
        self.cursor = {"x": round(x, 4), "y": round(y, 4), "click": click, "seq": (self.cursor or {}).get("seq", 0) + 1}

    async def _do(self, a: dict) -> dict:
        async with self.acting:  # one action at a time
            try:
                return await self._act(a)
            except Exception as e:  # playwright errors are long; keep the first line
                msg = str(e).strip().split("\n")[0][:300] or type(e).__name__
                return {"error": f"Browser error: {msg}"}

    def do(self, a: dict) -> dict:
        self.last_used = time.time()
        out = asyncio.run_coroutine_threadsafe(self._do(a), self.loop).result(timeout=90)
        self.last_used = time.time()
        return out

    async def _frame(self):
        if not self.page:
            return None
        try:
            return await self.page.screenshot(type="jpeg", quality=55, timeout=4000)
        except Exception:
            return None

    def frame(self) -> bytes | None:
        """The current page as a small JPEG for the live view, or None when no page is open."""
        try:
            return asyncio.run_coroutine_threadsafe(self._frame(), self.loop).result(timeout=6)
        except Exception:
            return None


browser = Browser()


def resolve(path: str) -> str:
    return os.path.normpath(os.path.join(HOME, os.path.expanduser(path) if path.startswith("~") else path))


def run(command: str, timeout: int) -> dict:
    if not jobs.acquire(blocking=False):
        return {"error": "Too many commands running at once."}
    p = None
    try:
        os.makedirs(HOME, exist_ok=True)
        env = {**os.environ, "HOME": HOME, "TERM": "dumb", "DEBIAN_FRONTEND": "noninteractive"}
        env.pop("SANDBOX_TOKEN", None)
        p = subprocess.Popen(["bash", "-c", command], cwd=HOME, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        running.add(p)
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
        if p:
            running.discard(p)
        jobs.release()


def kill_all() -> int:
    n = 0
    for p in list(running):
        try:
            os.killpg(p.pid, signal.SIGKILL)
            n += 1
        except (ProcessLookupError, PermissionError):
            pass
    return n


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

    def reply_bytes(self, code: int, data: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
            if (method, url.path) == ("GET", "/browser/frame"):
                data = browser.frame() if browser.page else None
                if not data:
                    return self.reply_bytes(204, b"", "image/jpeg")
                return self.reply_bytes(200, data, "image/jpeg")
            if (method, url.path) == ("GET", "/browser/cursor"):
                return self.reply(200, {"cursor": browser.cursor if browser.page else None})
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
                    raw = f.read()
                if parse_qs(url.query).get("binary"):
                    return self.reply(200, {"path": path, "b64": base64.b64encode(raw).decode()})
                return self.reply(200, {"path": path, "content": raw.decode(errors="replace")})
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
            if (method, url.path) == ("POST", "/exec/kill"):
                return self.reply(200, {"killed": kill_all()})
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
