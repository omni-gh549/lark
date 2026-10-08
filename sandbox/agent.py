"""Exec agent that runs inside the sandbox container. Standard library only.

Endpoints (all need `Authorization: Bearer $SANDBOX_TOKEN`):
  POST /browser {action, ...}   GET  /health   POST /exec {command, timeout}   GET /file?path=   PUT /file {path, content}   POST /wipe
"""
import asyncio
import base64
import hmac
import json
import os
import re
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
STATE_FILE = os.path.join(os.environ.get("SANDBOX_HOME", "/home/lark"), ".browser-state.json")  # cookies and local storage
IDLE_CLOSE = 300  # seconds; the browser is the memory hog, so it shuts down when unused

def _proxy(url):
    """Playwright wants the proxy's credentials apart from its address."""
    if not url:
        return None
    from urllib.parse import unquote, urlparse
    u = urlparse(url)
    conf = {"server": f"{u.scheme or 'http'}://{u.hostname}{':' + str(u.port) if u.port else ''}"}
    if u.username:
        conf.update(username=unquote(u.username), password=unquote(u.password or ""))
    return conf


STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5].map(i => ({ name: 'Plugin ' + i })) });
if (!window.chrome) window.chrome = { runtime: {}, app: {}, loadTimes() {}, csi() {} };
const _q = navigator.permissions && navigator.permissions.query;
if (_q) navigator.permissions.query = p => p && p.name === 'notifications' ? Promise.resolve({ state: Notification.permission === 'default' ? 'prompt' : Notification.permission }) : _q.call(navigator.permissions, p);
for (const C of [WebGLRenderingContext, typeof WebGL2RenderingContext !== 'undefined' ? WebGL2RenderingContext : null]) {
  if (!C) continue;
  const g = C.prototype.getParameter;
  C.prototype.getParameter = function (p) {
    if (p === 37445) return 'Google Inc. (Intel)';
    if (p === 37446) return 'ANGLE (Intel, Mesa Intel(R) UHD Graphics 620 (KBL GT2), OpenGL 4.6)';
    return g.call(this, p);
  };
}
"""

SNAPSHOT_JS = """() => {
  const roots = [document];  // the page plus every open shadow root (cookie popups often live in one)
  for (let i = 0; i < roots.length && roots.length < 60; i++)
    for (const e of roots[i].querySelectorAll('*')) if (e.shadowRoot) roots.push(e.shadowRoot);
  roots.forEach(r => r.querySelectorAll('[data-lark]').forEach(e => e.removeAttribute('data-lark')));
  const sel = 'a[href],button,input,select,textarea,summary,[role=button],[role=link],[role=tab],[role=menuitem],[onclick],[contenteditable=true]';
  const els = [];
  const found = roots.flatMap(r => Array.from(r.querySelectorAll(sel)));
  for (const e of found) {
    const r = e.getBoundingClientRect(), cs = getComputedStyle(e);
    if (r.width < 2 || r.height < 2 || cs.visibility === 'hidden' || cs.display === 'none' || e.disabled) continue;
    if (e.type === 'hidden') continue;
    if (r.bottom < 0 || r.right < 0 || r.top > innerHeight || r.left > innerWidth) { /* off-screen: still clickable after scrolling */ }
    const hit = (e.getRootNode().elementFromPoint ? e.getRootNode() : document).elementFromPoint(Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1), Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1));
    const covered = r.top >= 0 && r.bottom <= innerHeight && hit && hit !== e && !e.contains(hit) && !hit.contains(e);
    const n = els.length + 1;
    e.setAttribute('data-lark', n);
    const tag = e.tagName.toLowerCase();
    const label = (e.getAttribute('aria-label') || e.innerText || e.placeholder || e.title || e.name || (['submit', 'button', 'reset'].includes(e.type) ? e.value : '') || '')
      .trim().replace(/\\s+/g, ' ').slice(0, 80);
    els.push({ n, covered: !!covered, cover: covered ? (hit.getAttribute('aria-label') || hit.id || hit.className || hit.tagName).toString().slice(0, 40) : '', kind: tag === 'a' ? 'link' : tag === 'input' ? (e.type || 'text') : tag, label,
      href: tag === 'a' ? e.getAttribute('href') : null,
      value: ['input', 'textarea', 'select'].includes(tag) && !/password|hidden/.test(e.type || '') && !/one-time-code|username|current-password|new-password|cc-/.test(e.autocomplete || '') && !/otp|2fa|passcode|\\bpin\\b|token|secret|verif|code/i.test((e.name || '') + ' ' + (e.id || '')) ? String(e.value || '').slice(0, 40) : null });
    if (els.length >= 80) break;
  }
  return { url: location.href, title: document.title, text: (document.body ? document.body.innerText : '').slice(0, 5000), els,
    y: Math.round(scrollY), height: document.documentElement.scrollHeight, view: innerHeight };
}"""


FRAME_BUTTONS_JS = """() => Array.from(document.querySelectorAll('button,a[href],[role=button]'))
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 2 && r.height > 2; })
  .map(e => (e.innerText || e.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 40))
  .filter(Boolean).slice(0, 12)"""


_NAVIGATED = re.compile(r"context was destroyed|frame was detached|ERR_ABORTED|navigat|Target closed|Execution context", re.I)
NAV_RACE = re.compile(r"context was destroyed|frame was detached|ERR_ABORTED|navigat|Target closed|Execution context", re.I)


class Browser:
    """One headless Chromium page, driven by element numbers. It lives on its own asyncio loop so a screenshot
    for the live view can be taken while an action (a slow page load, say) is still running."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.acting = asyncio.run_coroutine_threadsafe(self._make_lock(), self.loop).result()
        self.pw = self.browser = self.page = None
        self.last_used = 0.0
        self.persist = False  # whether this browser keeps its cookies on disk (a Settings switch)
        self.ctx = None
        self.ua = None
        self.hold = False  # a person is signing in: don't close the browser for being idle
        self.cursor = None  # where the last click or typing landed, as fractions of the viewport, for the live view
        self.els: dict = {}  # element number -> (kind, label, href) from the latest snapshot, to survive renumbering
        self.last_url = ""  # so a person taking over a closed browser can get the page back

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
        if self.page and not self.page.is_closed() and self.browser.is_connected():
            return
        await self._close()
        from playwright.async_api import async_playwright
        self.pw = await async_playwright().start()
        proxy = os.environ.get("SANDBOX_PROXY")  # optional, e.g. a residential proxy for sites that block data-centre addresses
        self.browser = await self.pw.chromium.launch(executable_path=CHROME, headless=True, proxy=_proxy(proxy), args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--mute-audio", "--disable-extensions",
            "--disable-background-networking", "--disable-blink-features=AutomationControlled", "--js-flags=--max-old-space-size=384"])
        # Headless Chromium announces itself ("HeadlessChrome", navigator.webdriver, software WebGL), which makes many sites
        # refuse to run their scripts, so present as an ordinary desktop Chrome, consistently: the user agent, the
        # client hints and the page's own view of itself must all agree or bot checks notice.
        full = self.browser.version  # e.g. 131.0.6778.204
        major = full.split(".")[0]
        ua = f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
        saved = STATE_FILE if self.persist and os.path.exists(STATE_FILE) else None
        ctx = self.ctx = await self.browser.new_context(
            storage_state=saved, viewport={"width": 1280, "height": 800}, locale="en-GB", timezone_id="Europe/London",
            user_agent=ua, extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"})
        await ctx.add_init_script(STEALTH_JS)
        self.ua = {"userAgent": ua, "acceptLanguage": "en-GB,en;q=0.9", "platform": "Linux x86_64", "userAgentMetadata": {
            "brands": [{"brand": "Chromium", "version": major}, {"brand": "Not_A Brand", "version": "24"}, {"brand": "Google Chrome", "version": major}],
            "fullVersionList": [{"brand": "Chromium", "version": full}, {"brand": "Not_A Brand", "version": "24.0.0.0"}, {"brand": "Google Chrome", "version": full}],
            "fullVersion": full, "platform": "Linux", "platformVersion": "6.1.0", "architecture": "x86", "model": "", "mobile": False}}

        async def gate(route):
            if route.request.resource_type == "media":  # no autoplaying video: it costs memory and tells Lark nothing
                await route.abort()
            else:
                await route.continue_()

        await ctx.route("**/*", gate)
        self.page = await ctx.new_page()
        self.page.set_default_timeout(10000)
        try:
            await (await ctx.new_cdp_session(self.page)).send("Emulation.setUserAgentOverride", self.ua)  # client hints that match the UA
        except Exception:
            pass

    async def _save_state(self):
        if self.persist and self.ctx:
            try:
                await self.ctx.storage_state(path=STATE_FILE)
                os.chmod(STATE_FILE, 0o600)
            except Exception:
                pass

    async def _close(self):
        await self._save_state()
        self.ctx = None
        for obj, fn in ((self.browser, "close"), (self.pw, "stop")):
            try:
                if obj:
                    await getattr(obj, fn)()
            except Exception:
                pass
        self.pw = self.browser = self.page = None
        self.cursor = None

    def close_if_idle(self):
        if self.page and not self.hold and time.time() - self.last_used > IDLE_CLOSE:
            asyncio.run_coroutine_threadsafe(self._close(), self.loop)

    async def _read_page(self) -> dict:
        """The page as numbered elements. A page that is navigating destroys the script's context, so wait and read again."""
        page = self.page
        for attempt in range(4):
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
                await page.wait_for_load_state("networkidle", timeout=1200)
            except Exception:
                pass
            try:
                return await page.evaluate(SNAPSHOT_JS)
            except Exception as e:
                if attempt == 3 or not NAV_RACE.search(str(e)):
                    raise
                await page.wait_for_timeout(700 * (attempt + 1))

    async def _snapshot(self) -> str:
        page = self.page
        d = await self._read_page()
        if len(d["text"].strip()) < 60 and not d["els"]:
            await page.wait_for_timeout(2000)  # script-heavy pages can still be drawing themselves
            d = await self._read_page()
        self.els = {e["n"]: (e["kind"], e["label"], e["href"]) for e in d["els"]}
        self.last_url = d["url"] or self.last_url
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
        framed = []
        for frame in page.frames[1:6]:
            try:
                framed += await frame.evaluate(FRAME_BUTTONS_JS)
            except Exception:
                pass
        if framed:
            lines.append("")
            lines.append("Buttons and links inside embedded frames (not numbered; click them by text): "
                         + ", ".join(repr(t) for t in dict.fromkeys(framed)))
        if any(e.get("covered") for e in d["els"]):
            lines.append("")
            lines.append("Something is covering the page (a popup or banner). If its buttons aren't listed, click them by their "
                         "visible text (click with text, e.g. 'Accept all') or take a screenshot and click at x, y.")
        return "\n".join(lines)

    async def _act(self, a: dict) -> dict:
        """One browser action. A click, key press or typing that navigates destroys the page's script context, and
        Playwright reports that as a failure although the action went through. Repeating it would add things twice,
        so wait for the new page and describe it instead."""
        try:
            return await self._act_raw(a)
        except Exception as e:
            if a.get("action") not in ("click", "type", "press") or not _NAVIGATED.search(str(e)):
                raise
        last = None
        for wait in (0.8, 1.5, 2.5):
            await asyncio.sleep(wait)
            try:
                return {"snapshot": await self._snapshot()}
            except Exception as e:
                last = e
                if not _NAVIGATED.search(str(e)):
                    break
        raise last


    async def _act_raw(self, a: dict) -> dict:
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
            try:  # let scripts and images settle a moment so the snapshot isn't of a half-built page
                await page.wait_for_load_state("load", timeout=4000)
            except Exception:
                pass
        elif action == "click" and (a.get("text") or a.get("x") is not None) and a.get("id") is None:
            await self._click_visible(a)
        elif action in ("click", "type"):
            n = int(a.get("id"))
            target = page.locator(f'[data-lark="{n}"]').first
            if await target.count() == 0:
                target = await self._renumbered(n)
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
            keys = a.get("keys")
            if isinstance(keys, list) and keys:
                for k in keys[:100]:
                    await page.keyboard.press(str(k))
                    await page.wait_for_timeout(120)  # a game needs a moment to take each move
            else:
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
        if action in ("click", "type", "press"):
            try:  # a click that navigates: wait for the new page to be usable before describing it
                await page.wait_for_load_state("domcontentloaded", timeout=2500)
            except Exception:
                pass
        out["snapshot"] = await self._snapshot()
        return out

    async def _renumbered(self, n: int):
        """The page changed since the snapshot and the numbers moved. Find the element the model meant by what it was
        (its kind, label and link) in a fresh numbering; if it's gone, say so with the page as it is now."""
        page = self.page
        old = self.els.get(n)
        d = await self._read_page()
        fresh = {e["n"]: (e["kind"], e["label"], e["href"]) for e in d["els"]}
        if old:
            for m, sig in fresh.items():
                if sig == old:
                    return page.locator(f'[data-lark="{m}"]').first
            for m, sig in fresh.items():  # same kind and label, link changed
                if sig[:2] == old[:2]:
                    return page.locator(f'[data-lark="{m}"]').first
        self.els = fresh
        raise ValueError("The page changed and that element is gone. Here is the page now, with new numbers:\n" + await self._snapshot())

    async def _click_visible(self, a: dict):
        """Click by visible text (searching every frame, so consent popups in iframes work) or by x, y pixels."""
        page = self.page
        size = page.viewport_size or {"width": 1280, "height": 800}
        if a.get("x") is not None:
            x, y = float(a["x"]), float(a.get("y", 0))
        else:
            text = str(a["text"])
            target = None
            loose = re.compile(r"\s+".join(re.escape(w) for w in text.split()), re.I)
            hidden = None  # a match that exists but isn't "visible" (virtualised lists, buttons in transitions)
            for attempt in range(6):  # banners and popups often appear a moment after the page does
                for frame in page.frames:
                    for loc in (frame.get_by_role("button", name=loose), frame.get_by_label(loose), frame.get_by_role("link", name=loose),
                                frame.get_by_text(loose)):
                        try:
                            for i in range(min(await loc.count(), 10)):
                                cand = loc.nth(i)
                                if await cand.is_visible():
                                    target = cand
                                    break
                                hidden = hidden or cand
                        except Exception:
                            continue
                        if target:
                            break
                    if target:
                        break
                if target:
                    break
                if attempt >= 3 and hidden:
                    break  # it is on the page: bring it into view below rather than wait for it to look visible
                await page.wait_for_timeout(600)
            if not target and hidden:
                try:
                    await hidden.scroll_into_view_if_needed(timeout=2500)
                    target = hidden
                except Exception:
                    pass
            if not target:
                seen = []
                for frame in page.frames[:6]:
                    try:
                        seen += await frame.evaluate(FRAME_BUTTONS_JS)
                    except Exception:
                        pass
                hint = (" Visible buttons and links: " + ", ".join(repr(t) for t in list(dict.fromkeys(seen))[:15])) if seen else ""
                raise ValueError(f"No visible button, link or text matching {text!r}.{hint}")
            try:
                await target.scroll_into_view_if_needed(timeout=2500)  # below the fold: the mouse can only click what's on screen
            except Exception:
                pass
            box = await target.bounding_box()
            if not box:
                raise ValueError("That element has no position on screen.")
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        self.cursor = {"x": round(min(max(x / size["width"], 0), 1), 4), "y": round(min(max(y / size["height"], 0), 1), 4),
                       "click": True, "seq": (self.cursor or {}).get("seq", 0) + 1}
        await asyncio.sleep(0.6)
        await page.mouse.click(x, y)

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
                persist = bool(a.get("persist"))
                if persist != self.persist:
                    await self._close()  # the switch changed: start a fresh browser in the new mode
                    self.persist = persist
                    if not persist and os.path.exists(STATE_FILE):
                        os.unlink(STATE_FILE)  # turning it off forgets what was saved
                try:
                    out = await self._act(a)
                except Exception as e:
                    if a.get("action") in ("goto", "snapshot", "screenshot", "scroll", "back") and re.search(r"closed|crash|disconnected", str(e), re.I):
                        await self._close()  # the page died: start a fresh browser and try once more
                        out = await self._act(a)
                    elif a.get("action") in ("goto", "snapshot", "screenshot", "scroll", "back") and NAV_RACE.search(str(e)):
                        await asyncio.sleep(1.0)  # a navigation was in flight: let it land and try once more
                        out = await self._act(a)
                    else:
                        raise
                await self._save_state()
                return out
            except Exception as e:  # playwright errors are long; keep the first line
                msg = str(e).strip().split("\n")[0][:300] or type(e).__name__
                return {"error": f"Browser error: {msg}"}

    async def _input(self, a: dict) -> dict:
        """Mouse and keyboard from a person taking over the live view. Positions are fractions of the page."""
        if not self.page or self.page.is_closed():
            if not self.last_url:
                raise ValueError("The browser is closed. Ask Lark to open a page.")
            await self._ensure()  # it closed (idle, crashed): reopen where it was so the person's input still lands
            await self.page.goto(self.last_url, wait_until="domcontentloaded", timeout=30000)
        page = self.page
        kind = a.get("type")
        size = page.viewport_size or {"width": 1280, "height": 800}
        if kind in ("click", "scroll"):
            x = min(max(float(a.get("x", 0.5)), 0), 1) * size["width"]
            y = min(max(float(a.get("y", 0.5)), 0), 1) * size["height"]
            if kind == "click":
                await page.mouse.click(x, y)
                await page.wait_for_timeout(300)
            else:
                await page.mouse.move(x, y)
                await page.mouse.wheel(0, max(-2000, min(2000, float(a.get("dy", 0)))))
        elif kind == "key":
            await page.keyboard.press(str(a.get("key", ""))[:30])
        elif kind == "text":
            await page.keyboard.insert_text(str(a.get("text", ""))[:2000])
        else:
            raise ValueError("Unknown input.")
        if kind in ("click", "key"):
            await self._save_state()
        return {"ok": True}

    def input(self, a: dict) -> dict:
        self.last_used = time.time()
        try:
            return asyncio.run_coroutine_threadsafe(self._input(a), self.loop).result(timeout=15)
        except ValueError as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"Browser error: {str(e).strip().splitlines()[0][:200] if str(e).strip() else type(e).__name__}"}

    async def _clear(self):
        async with self.acting:
            await self._close()
            if os.path.exists(STATE_FILE):
                os.unlink(STATE_FILE)

    def clear(self):
        asyncio.run_coroutine_threadsafe(self._clear(), self.loop).result(timeout=30)

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
            if (method, url.path) == ("POST", "/browser/input"):
                r = browser.input(self.body())
                return self.reply(400 if "error" in r else 200, r)
            if (method, url.path) == ("POST", "/browser/hold"):
                browser.hold = bool(self.body().get("on"))
                browser.last_used = time.time()
                return self.reply(200, {"ok": True})
            if (method, url.path) == ("POST", "/browser/clear"):
                browser.clear()
                return self.reply(200, {"ok": True})
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
