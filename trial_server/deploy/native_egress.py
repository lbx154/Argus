"""A public-internet HTTP proxy for runtimes with no host network access."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit


async def public_address(host: str, port: int) -> str:
    if port not in (80, 443):
        raise ValueError("Only HTTP and HTTPS destinations are allowed")
    rows = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = [row[4][0] for row in rows]
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Private network destinations are unavailable")
    # Connect to this validated address, never resolve it a second time.
    return addresses[0]


async def copy_stream(reader, writer):
    transferred = 0
    try:
        while chunk := await asyncio.wait_for(reader.read(65536), 120):
            transferred += len(chunk)
            if transferred > 100 * 1024 * 1024:
                break
            writer.write(chunk)
            await writer.drain()
    finally:
        writer.close()


async def bridge(reader, writer, *, target_socket: str):
    upstream = None
    try:
        other_reader, upstream = await asyncio.open_unix_connection(target_socket)
        await asyncio.gather(copy_stream(reader, upstream), copy_stream(other_reader, writer))
    except (OSError, TimeoutError, ConnectionError):
        pass
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def handle_proxy(reader, writer):
    upstream = None
    try:
        header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 20)
        line, _, rest = header.partition(b"\r\n")
        method, target, protocol = line.decode("ascii").split(" ")
        if protocol not in ("HTTP/1.1", "HTTP/1.0"):
            raise ValueError("Invalid protocol")
        if method == "CONNECT":
            host, raw_port = target.rsplit(":", 1)
            host = host.strip("[]")
            port = int(raw_port)
        else:
            parsed = urlsplit(target)
            if parsed.scheme != "http" or not parsed.hostname or parsed.username:
                raise ValueError("HTTP proxy expects an absolute public URL")
            host, port = parsed.hostname, parsed.port or 80
        address = await public_address(host, port)
        other_reader, upstream = await asyncio.wait_for(asyncio.open_connection(address, port), 15)
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        else:
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            headers = [row for row in rest.split(b"\r\n") if row and row.split(b":", 1)[0].lower()
                       not in (b"connection", b"proxy-connection", b"proxy-authorization", b"host")]
            authority = host if port == 80 else f"{host}:{port}"
            upstream.write(f"{method} {path} {protocol}\r\nHost: {authority}\r\nConnection: close\r\n".encode()
                           + b"\r\n".join(headers) + b"\r\n\r\n")
            await upstream.drain()
        await writer.drain()
        await asyncio.gather(copy_stream(reader, upstream), copy_stream(other_reader, writer))
    except (ValueError, OSError, TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        if upstream is None:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
    finally:
        writer.close()
        if upstream:
            upstream.close()


async def serve(path: str):
    from pathlib import Path

    Path(path).unlink(missing_ok=True)
    server = await asyncio.start_unix_server(handle_proxy, path=path, limit=65536)
    Path(path).chmod(0o600)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    asyncio.run(serve(parser.parse_args().socket))
