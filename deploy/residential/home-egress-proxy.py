#!/usr/bin/env python3
"""lark-egress: a small authenticated HTTP proxy so Lark's cloud browser can use this home connection.

Listens only on the Tailscale address, needs a password, and refuses private, loopback, link-local and
Tailscale destinations, so it can only reach the public internet (never this home network).
Standard library only. Config: ~/.lark-egress/config (LISTEN=ip:port, TOKEN=...).
"""
import asyncio
import base64
import hmac
import ipaddress
import os
import socket
import sys

CONF = os.path.expanduser("~/.lark-egress/config")
BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4", "::1/128", "fc00::/7", "fe80::/10", "fd7a:115c:a1e0::/48")]


def load():
    cfg = {}
    with open(CONF) as f:
        for line in f:
            if "=" in line:
                k, v = line.strip().split("=", 1)
                cfg[k] = v
    host, port = cfg["LISTEN"].rsplit(":", 1)
    return host, int(port), cfg["TOKEN"]


HOST, PORT, TOKEN = load()
WANT = "Basic " + base64.b64encode(f"lark:{TOKEN}".encode()).decode()


async def resolve_public(host: str, port: int):
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for fam, _, _, _, addr in infos:
        ip = ipaddress.ip_address(addr[0])
        if not any(ip in n for n in BLOCKED):
            return addr[0]
    raise PermissionError("destination not allowed")


async def pipe(r, w):
    try:
        while data := await r.read(65536):
            w.write(data)
            await w.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            w.close()
        except Exception:
            pass


async def handle(cr, cw):
    try:
        head = await asyncio.wait_for(cr.readuntil(b"\r\n\r\n"), 20)
    except Exception:
        cw.close()
        return
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, target, _ = lines[0].split(" ", 2)
    except ValueError:
        cw.close()
        return
    headers = {}
    for l in lines[1:]:
        if ":" in l:
            k, v = l.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    if not hmac.compare_digest(headers.get("proxy-authorization", ""), WANT):
        cw.write(b'HTTP/1.1 407 Proxy Authentication Required\r\nProxy-Authenticate: Basic realm="lark"\r\nContent-Length: 0\r\n\r\n')
        await cw.drain()
        cw.close()
        return
    try:
        if method == "CONNECT":
            host, port = target.rsplit(":", 1)
            ip = await resolve_public(host.strip("[]"), int(port))
            ur, uw = await asyncio.wait_for(asyncio.open_connection(ip, int(port)), 20)
            cw.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await cw.drain()
        else:  # plain http://host/path
            from urllib.parse import urlsplit
            u = urlsplit(target)
            port = u.port or 80
            ip = await resolve_public(u.hostname, port)
            ur, uw = await asyncio.wait_for(asyncio.open_connection(ip, port), 20)
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            out = [f"{method} {path} HTTP/1.1"] + [l for l in lines[1:] if l and not l.lower().startswith(("proxy-", "connection:"))]
            uw.write(("\r\n".join(out) + "\r\nConnection: close\r\n\r\n").encode("latin-1"))
            await uw.drain()
    except PermissionError:
        cw.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    except Exception:
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        await cw.drain()
        cw.close()
        return
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    server = await asyncio.start_server(handle, HOST, PORT)
    print(f"lark-egress listening on {HOST}:{PORT}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
