"""Readiness-checked loopback access to a service's mounted Unix socket."""
from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path


async def _copy(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    while data := await reader.read(65536):
        writer.write(data)
        await writer.drain()
    if writer.can_write_eof():
        writer.write_eof()
        await writer.drain()


async def create_forward_server(port: int, path: str) -> asyncio.Server:
    if not Path(path).is_socket():
        raise RuntimeError(f"Required service socket is missing: {path}")

    async def forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream = None
        tasks = []
        try:
            incoming, upstream = await asyncio.open_unix_connection(path)
            tasks = [
                asyncio.create_task(_copy(reader, upstream)),
                asyncio.create_task(_copy(incoming, writer)),
            ]
            await asyncio.gather(*tasks)
        except (ConnectionError, OSError) as exc:
            logging.getLogger(__name__).warning(
                "Service connection failed on port %s: %s", port, exc,
            )
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
                    try:
                        await stream.wait_closed()
                    except (ConnectionError, OSError):
                        logging.getLogger(__name__).debug("Service peer disconnected")

    return await asyncio.start_server(forward, "127.0.0.1", port)


def start_forward(port: int, path: str) -> subprocess.Popen:
    if not Path(path).is_socket():
        raise RuntimeError(f"Required service socket is missing: {path}")
    process = subprocess.Popen([
        sys.executable, str(Path(__file__).resolve()), str(port), path,
    ])
    deadline = time.monotonic() + 5
    while process.poll() is None:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return process
        except ConnectionRefusedError:
            if time.monotonic() >= deadline:
                process.terminate()
                process.wait(timeout=5)
                raise RuntimeError(f"Service forwarder did not start on port {port}") from None
            time.sleep(0.02)
    raise RuntimeError(f"Service forwarder exited with status {process.returncode}: {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port", type=int)
    parser.add_argument("socket")
    args = parser.parse_args()

    async def run() -> None:
        server = await create_forward_server(args.port, args.socket)
        async with server:
            await server.serve_forever()

    asyncio.run(run())


if __name__ == "__main__":
    main()
