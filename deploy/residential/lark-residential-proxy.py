#!/usr/bin/env python3
"""Selective egress for Lark's sandbox browser.

Listens on the sandbox bridge. Domains listed in the domains file are forwarded
through the authenticated home proxy. Every other public site is connected
directly from this server. Private, loopback and link-local targets are refused
so the sandbox cannot use this process to reach the host or cloud metadata.

Config: /etc/lark/residential-proxy.env
Domains: path named by DOMAINS_FILE, one suffix per line. Edits apply to the
next request.
"""
import asyncio
import base64
import ipaddress
import socket
import sys

CONFIG = "/etc/lark/residential-proxy.env"
BLOCKED = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
    "172.16.0.0/12", "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "::/128", "::1/128", "fc00::/7", "fe80::/10", "ff00::/8",
)]


def load_config():
    cfg = {}
    with open(CONFIG) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            cfg[key] = value
    return cfg


def domain_list(path):
    found = []
    with open(path) as handle:
        for line in handle:
            line = line.strip().lower().rstrip(".")
            if not line or line.startswith("#"):
                continue
            if line.startswith("*."):
                line = line[2:]
            found.append(line)
    return found


def matches(host, domains):
    host = host.lower().rstrip(".").split("%", 1)[0]
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def public_ip(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return not any(ip in network for network in BLOCKED)


def allowed_peer(ip):
    try:
        parsed = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return parsed.is_loopback or parsed in ipaddress.ip_network("172.30.0.0/24")


async def pipe(reader, writer):
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def open_public(host, port):
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except Exception as exc:
        raise OSError("dns failed") from exc
    last = None
    for family, socktype, proto, _, sockaddr in infos:
        if not public_ip(sockaddr[0]):
            last = PermissionError("destination not allowed")
            continue
        try:
            return await asyncio.wait_for(asyncio.open_connection(sockaddr[0], sockaddr[1]), 20)
        except Exception as exc:
            last = exc
    raise last or OSError("no address")


def reject(writer, status, reason):
    body = reason.encode()
    writer.write(
        f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
    )


async def direct(client_r, client_w, host, port, origin_request):
    try:
        remote_r, remote_w = await open_public(host, port)
    except PermissionError:
        reject(client_w, "403 Forbidden", "destination not allowed")
        await client_w.drain()
        return
    except Exception:
        reject(client_w, "502 Bad Gateway", "direct connection failed")
        await client_w.drain()
        return
    if origin_request is None:
        client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_w.drain()
    else:
        remote_w.write(origin_request)
        await remote_w.drain()
    await asyncio.gather(pipe(client_r, remote_w), pipe(remote_r, client_w))


async def via_home(client_r, client_w, host, port, upstream, origin_lines):
    try:
        remote_r, remote_w = await asyncio.wait_for(
            asyncio.open_connection(upstream["host"], upstream["port"]), 20
        )
    except Exception:
        reject(client_w, "502 Bad Gateway", "home proxy unavailable")
        await client_w.drain()
        return
    auth = "Basic " + base64.b64encode(f"{upstream['user']}:{upstream['password']}".encode()).decode()
    if origin_lines is None:
        remote_w.write(
            f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
            f"Proxy-Authorization: {auth}\r\n\r\n".encode()
        )
        await remote_w.drain()
        try:
            reply = await asyncio.wait_for(remote_r.readuntil(b"\r\n\r\n"), 30)
        except Exception:
            reject(client_w, "502 Bad Gateway", "home proxy did not answer")
            await client_w.drain()
            remote_w.close()
            return
        status = reply.split(b"\r\n", 1)[0]
        if b" 200 " not in status:
            reject(client_w, "502 Bad Gateway", "home proxy refused the site")
            await client_w.drain()
            remote_w.close()
            return
        client_w.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await client_w.drain()
    else:
        forwarded = [origin_lines[0]]
        forwarded += [
            line for line in origin_lines[1:]
            if line and not line.lower().startswith(("proxy-", "connection:"))
        ]
        forwarded.append("Proxy-Authorization: " + auth)
        forwarded.append("Connection: close")
        remote_w.write(("\r\n".join(forwarded) + "\r\n\r\n").encode("latin-1", "surrogateescape"))
        await remote_w.drain()
    print(f"home {host}:{port}", flush=True)
    await asyncio.gather(pipe(client_r, remote_w), pipe(remote_r, client_w))


async def handle(client_r, client_w, upstream, domains_path):
    peer = client_w.get_extra_info("peername")
    if not peer or not allowed_peer(peer[0]):
        print("rejected", peer, flush=True)
        client_w.close()
        return
    try:
        head = await asyncio.wait_for(client_r.readuntil(b"\r\n\r\n"), 20)
    except Exception:
        client_w.close()
        return
    lines = head.decode("latin-1", "surrogateescape").split("\r\n")
    try:
        method, target, _version = lines[0].split(" ", 2)
    except ValueError:
        client_w.close()
        return
    try:
        domains = domain_list(domains_path)
        if method == "CONNECT":
            host, port_s = target.rsplit(":", 1)
            host = host.strip("[]")
            port = int(port_s)
            if matches(host, domains):
                await via_home(client_r, client_w, host, port, upstream, None)
            else:
                print(f"direct {host}:{port}", flush=True)
                await direct(client_r, client_w, host, port, None)
            return
        from urllib.parse import urlsplit
        url = urlsplit(target)
        host = url.hostname or ""
        port = url.port or (443 if url.scheme == "https" else 80)
        path = (url.path or "/") + (f"?{url.query}" if url.query else "")
        origin = [f"{method} {path} HTTP/1.1"]
        origin += [
            line for line in lines[1:]
            if line and not line.lower().startswith(("proxy-", "connection:"))
        ]
        origin.append("Connection: close")
        request = ("\r\n".join(origin) + "\r\n\r\n").encode("latin-1", "surrogateescape")
        if matches(host, domains):
            await via_home(client_r, client_w, host, port, upstream, lines)
        else:
            print(f"direct {host}:{port}", flush=True)
            await direct(client_r, client_w, host, port, request)
    except Exception as exc:
        print(f"error {type(exc).__name__}", flush=True)
        try:
            reject(client_w, "502 Bad Gateway", "proxy error")
            await client_w.drain()
        except Exception:
            pass
    finally:
        try:
            client_w.close()
        except Exception:
            pass


async def main():
    cfg = load_config()
    upstream = {
        "host": cfg["UPSTREAM_HOST"],
        "port": int(cfg["UPSTREAM_PORT"]),
        "user": cfg["UPSTREAM_USER"],
        "password": cfg["UPSTREAM_PASS"],
    }
    listen = cfg.get("LISTEN", "172.30.0.1")
    port = int(cfg.get("PORT", "8898"))
    domains_path = cfg["DOMAINS_FILE"]
    async def client(reader, writer):
        await handle(reader, writer, upstream, domains_path)

    server = None
    for _ in range(60):
        try:
            server = await asyncio.start_server(client, listen, port)
            break
        except OSError as exc:
            print(f"waiting for {listen}:{port} ({exc})", flush=True)
            await asyncio.sleep(1)
    if server is None:
        sys.exit(1)
    print(f"listening on {listen}:{port}", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
