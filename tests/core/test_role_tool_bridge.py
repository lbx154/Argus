import http.client
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from argus_skill.core.role_tool_bridge import (
    MAX_ACTIVE_OPERATIONS,
    MAX_HANDLER_THREADS,
    CallBoundBridge,
    bridge_request,
)


def post(bridge, body, *, token=None, declared_size=None):
    connection = http.client.HTTPConnection("127.0.0.1", bridge.server.server_address[1], timeout=5)
    try:
        connection.request("POST", "/work", body, {
            "Authorization": "Bearer " + (token or bridge.environment["ARGUS_PLUGIN_TEST_TOKEN"]),
            "Content-Length": str(len(body) if declared_size is None else declared_size),
        })
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("body,token,size,status", [
    (b'{}', "forged-capability", None, 403),
    (b'{"number":NaN}', None, None, 400),
    (b'[]', None, None, 400),
    (b'{}', None, 65537, 400),
])
def test_untrusted_protocol_never_reaches_domain_dispatch(body, token, size, status):
    calls = []
    with CallBoundBridge(lambda operation, payload: calls.append(payload) or {}, env_prefix="ARGUS_PLUGIN_TEST") as bridge:
        assert post(bridge, body, token=token, declared_size=size)[0] == status
    assert calls == []


def test_concurrency_is_bounded_and_cancel_keeps_a_reserved_path(monkeypatch):
    release, full = threading.Event(), threading.Event()
    guard = threading.Lock()
    active = peak = handlers = peak_handlers = 0

    def dispatch(operation, _payload):
        nonlocal active, peak
        if operation == "cancel":
            return {"cancelled": True}
        with guard:
            active += 1
            peak = max(peak, active)
            if active == MAX_ACTIVE_OPERATIONS:
                full.set()
        try:
            assert release.wait(5)
            return {"ok": True}
        finally:
            with guard:
                active -= 1

    with CallBoundBridge(dispatch, env_prefix="ARGUS_PLUGIN_TEST") as bridge:
        original = bridge.server.process_request_thread

        def tracked(*args):
            nonlocal handlers, peak_handlers
            with guard:
                handlers += 1
                peak_handlers = max(peak_handlers, handlers)
            try:
                return original(*args)
            finally:
                with guard:
                    handlers -= 1

        monkeypatch.setattr(bridge.server, "process_request_thread", tracked)
        with ThreadPoolExecutor(max_workers=24) as executor:
            long_calls = [executor.submit(post, bridge, b'{}') for _ in range(MAX_ACTIVE_OPERATIONS)]
            try:
                assert full.wait(3)

                def overflow_call():
                    with pytest.raises(ValueError, match="busy"):
                        bridge_request("ARGUS_PLUGIN_TEST", "work", {}, env=bridge.environment)

                overflow = [executor.submit(overflow_call) for _ in range(12)]
                for future in overflow:
                    future.result(3)
                assert bridge_request("ARGUS_PLUGIN_TEST", "cancel", {}, env=bridge.environment) == {"cancelled": True}
            finally:
                release.set()
            assert all(future.result(3)[0] == 200 for future in long_calls)
    assert peak == MAX_ACTIVE_OPERATIONS
    assert peak_handlers <= MAX_HANDLER_THREADS


def test_shutdown_does_not_wait_for_incomplete_headers_or_bodies():
    bridge = CallBoundBridge(lambda *_args: {}, env_prefix="ARGUS_PLUGIN_TEST")
    bridge.__enter__()
    sockets = [socket.create_connection(bridge.server.server_address) for _ in range(3)]
    sockets[1].sendall(b"POST /work HTTP/1.0\r\nContent-Length: 50\r\n")
    time.sleep(0.05)
    started = time.monotonic()
    try:
        bridge.__exit__(None, None, None)
        assert time.monotonic() - started < 0.5
    finally:
        for stream in sockets:
            stream.close()
