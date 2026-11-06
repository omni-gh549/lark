"""Read a web page as plain text without the browser: httpx plus a small HTML-to-text pass. Used by the swarm's researchers.

Pages are untrusted data. The fetch refuses addresses on the server's own network (loopback, private ranges, link-local
and cloud metadata) at every redirect, and caps size and time."""
import asyncio
import ipaddress
import re
import socket
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 1_500_000
PAGE = 9000  # characters returned per call; `start` pages through longer ones
MAX_REDIRECTS = 4
TIMEOUT = httpx.Timeout(10.0, read=20.0)
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; Lark/1.0; +https://github.com/omni-gh549/lark)", "Accept": "text/html,text/plain,application/xhtml+xml,application/json;q=0.8,*/*;q=0.5"}
TEXT_TYPES = ("text/", "application/xhtml", "application/json", "application/xml", "application/rss", "application/atom")


class FetchError(RuntimeError):
    pass


def _public(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%")[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast or a.is_reserved or a.is_unspecified
                or (isinstance(a, ipaddress.IPv4Address) and a in ipaddress.ip_network("100.64.0.0/10")))


async def check_url(url: str) -> str:
    """Raises FetchError unless the URL is http(s) and every address its host resolves to is public."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only http and https addresses can be opened.")
    host = parts.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise FetchError(f"Couldn't find {host}.")
    if not infos or not all(_public(i[4][0]) for i in infos):
        raise FetchError("That address is on a private network, so it can't be opened.")
    return parts.geturl()


SKIP = {"script", "style", "noscript", "template", "svg", "iframe", "canvas", "head", "nav", "footer", "aside", "form", "button", "select", "dialog"}
BREAK = {"p", "div", "section", "article", "main", "br", "tr", "ul", "ol", "table", "blockquote", "pre", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "li", "hr"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.title = ""
        self.skip = 0
        self.in_title = False
        self.links = 0

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self.in_title = True
        if tag in SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in BREAK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            self.out.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag in BREAK:
            self.out.append("\n")

    def handle_data(self, data):
        if self.in_title:
            self.title += data
        elif not self.skip:
            self.out.append(data)


def to_text(html: str) -> tuple[str, str]:
    """(title, readable text) from an HTML page."""
    # prefer the main article when the page has one: it drops menus and sidebars that survive the tag filter
    m = re.search(r"<(article|main)\b[^>]*>(.*)</\1>", html, re.S | re.I)
    body = m.group(2) if m and len(m.group(2)) > 800 else html
    p = _Text()
    try:
        p.feed(body)
        p.close()
    except Exception:
        pass
    title = " ".join(unescape(p.title).split())
    if not title:
        t = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
        title = " ".join(unescape(t.group(1)).split()) if t else ""
    lines = [" ".join(l.split()) for l in "".join(p.out).splitlines()]
    text = "\n".join(l for l in lines if l)
    return title, re.sub(r"\n{3,}", "\n\n", text)


async def fetch(url: str, start: int = 0) -> str:
    """The page as text, with its address and title, `PAGE` characters from `start`. Raises FetchError."""
    seen = 0
    async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS, follow_redirects=False) as client:
        while True:
            url = await check_url(url)
            try:
                async with client.stream("GET", url) as r:
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        seen += 1
                        if seen > MAX_REDIRECTS:
                            raise FetchError("Too many redirects.")
                        url = urljoin(url, r.headers["location"])
                        continue
                    if r.status_code >= 400:
                        raise FetchError(f"The page returned an error ({r.status_code}).")
                    ctype = r.headers.get("content-type", "text/html").split(";")[0].strip().lower()
                    if not ctype.startswith(TEXT_TYPES):
                        raise FetchError(f"That is a {ctype or 'binary'} file, not a page of text. Download it in the sandbox if you need it.")
                    data = b""
                    async for chunk in r.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_BYTES:
                            data = data[:MAX_BYTES]
                            break
                    enc = r.encoding or "utf-8"
            except httpx.HTTPError:
                raise FetchError("Couldn't load that page.")
            break
    raw = data.decode(enc, errors="replace")
    title, text = (to_text(raw) if ctype in ("text/html", "application/xhtml+xml") else ("", raw.strip()))
    if not text:
        raise FetchError("The page has no readable text (it may need JavaScript; try the browser).")
    start = max(0, start)
    chunk = text[start:start + PAGE]
    if not chunk:
        raise FetchError(f"The page has only {len(text)} characters.")
    more = f"\n[Showing characters {start}-{start + len(chunk)} of {len(text)}. Pass start={start + len(chunk)} for more.]" if start + len(chunk) < len(text) else ""
    head = f"{url}\n{title}\n" if title else f"{url}\n"
    return f"{head}\n{chunk}{more}"
