import asyncio
import os

import pytest

from argus_skill.trial.socket_forward import create_forward_server


def test_concurrent_forwarding_and_half_close_do_not_fork(tmp_path, monkeypatch):
    def no_fork():
        raise AssertionError("Connections must not consume new processes")

    monkeypatch.setattr(os, "fork", no_fork)

    async def exercise():
        path = str(tmp_path / "upstream.sock")

        async def echo(reader, writer):
            payload = await reader.read()
            writer.write(payload)
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        upstream = await asyncio.start_unix_server(echo, path)
        relay = await create_forward_server(0, path)
        port = relay.sockets[0].getsockname()[1]
        async with upstream, relay:
            async def exchange(index):
                payload = bytes([index]) * 200000
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(payload)
                await writer.drain()
                writer.write_eof()
                assert await reader.read() == payload
                writer.close()
                await writer.wait_closed()

            await asyncio.wait_for(
                asyncio.gather(*(exchange(i) for i in range(32))), timeout=10,
            )

    asyncio.run(exercise())


def test_missing_socket_fails_explicitly(tmp_path):
    with pytest.raises(RuntimeError, match="Required service socket is missing"):
        asyncio.run(create_forward_server(0, str(tmp_path / "missing.sock")))


def test_upstream_failure_does_not_kill_listener(tmp_path, caplog):
    async def exercise():
        path = str(tmp_path / "upstream.sock")
        upstream = await asyncio.start_unix_server(lambda r, w: w.close(), path)
        relay = await create_forward_server(0, path)
        port = relay.sockets[0].getsockname()[1]
        upstream.close()
        await upstream.wait_closed()
        async with relay:
            for _ in range(2):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                assert await asyncio.wait_for(reader.read(), timeout=2) == b""
                writer.close()
                await writer.wait_closed()
            assert relay.is_serving()

    asyncio.run(exercise())
    assert "Service connection failed" in caplog.text
