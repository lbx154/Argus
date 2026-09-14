import errno
import queue
import threading

import pytest

from argus.agent_cli._run_exec import _consume_pipe_lines


@pytest.mark.parametrize("error", [OSError(errno.EBADF, "closed"), ValueError("closed stream")])
def test_closed_pipe_keeps_complete_lines_and_publishes_eof(error):
    def pipe():
        yield '{"type":"message_end","usage":{"input":3}}\n'
        raise error

    output = queue.Queue()
    _consume_pipe_lines("stdout", pipe(), output, threading.Event(), [0.0])
    assert output.get_nowait() == ("stdout", '{"type":"message_end","usage":{"input":3}}')
    stream, message = output.get_nowait()
    assert stream == "stderr" and "pipe closed" in message
    assert output.get_nowait() == ("stdout", None)
    assert output.empty()


def test_intentional_shutdown_does_not_publish_spurious_pipe_error():
    stop = threading.Event()

    def pipe():
        stop.set()
        raise OSError(errno.EBADF, "closed by final drain cleanup")
        yield  # pragma: no cover

    output = queue.Queue()
    _consume_pipe_lines("stdout", pipe(), output, stop, [0.0])
    assert output.empty()
