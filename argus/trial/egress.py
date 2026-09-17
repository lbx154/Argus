"""HTTPS CONNECT over a private socket, with no route to host/private addresses."""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import logging
import socket
from pathlib import Path

log = logging.getLogger(__name__)
MAX_CONNECTIONS = 100


async def public_addresses(host: str) -> list[tuple[int, str]]:
    addresses = await asyncio.get_running_loop().getaddrinfo(
        host, 443, type=socket.SOCK_STREAM,
    )
    result = []
    for family, _, _, _, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global or address.is_multicast:
            raise ValueError("Private destinations are not allowed")
        result.append((family, str(address)))
    if not result:
        raise ValueError("Destination has no public address")
    return result


async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()


class Proxy:
    def __init__(self) -> None:
        self.active = 0

    async def connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    ) -> None:
        upstream = None
        if self.active >= MAX_CONNECTIONS:
            writer.write(b"HTTP/1.1 503 Busy\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return
        self.active += 1
        established = False
        try:
            async with asyncio.timeout(15):
                header = await reader.readuntil(b"\r\n\r\n")
                first = header.split(b"\r\n", 1)[0].decode("ascii")
                method, authority, version = first.split(" ")
                if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
                    raise ValueError("Only HTTPS CONNECT is supported")
                host, separator, port = authority.rpartition(":")
                if not separator or port != "443" or not host or any(
                    character in host for character in "/\\@%?# \t"
                ):
                    raise ValueError("Only HTTPS port 443 is supported")
                host = host.removeprefix("[").removesuffix("]")
                addresses = await public_addresses(host)
                # Connect the already-validated IP, never resolve the hostname twice.
                family, address = addresses[0]
                upstream_reader, upstream = await asyncio.open_connection(
                    address, 443, family=family,
                )
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            established = True
            async with asyncio.timeout(3600):
                tasks = [
                    asyncio.create_task(relay(reader, upstream)),
                    asyncio.create_task(relay(upstream_reader, writer)),
                ]
                try:
                    await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
        except (ValueError, UnicodeError, asyncio.LimitOverrunError):
            if not established:
                writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                await writer.drain()
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            log.warning("Egress connection failed: %s", type(exc).__name__)
            if not established and not writer.is_closing():
                writer.write(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
        finally:
            self.active -= 1
            if upstream is not None:
                upstream.close()
            writer.close()


async def serve(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if not path.is_socket():
            raise ValueError("Egress socket path is not a socket")
        path.unlink()
    proxy = Proxy()
    server = await asyncio.start_unix_server(proxy.connection, path=path, limit=8192)
    path.chmod(0o600)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uds", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(args.uds))


if __name__ == "__main__":
    main()
