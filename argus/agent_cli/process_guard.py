"""OS ownership shim for a TypeScript execution owner, independent of its UI.

The private stdin pipe belongs to the runtime/daemon, not a terminal or HTTP
client. EOF means that owner disappeared. Provider bytes are framed separately
from the guard's exit receipt; provider output cannot impersonate that receipt.
"""
from __future__ import annotations

import base64
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from ..core.contract_resources import contract_schema_path
from ..core.windows_job import spawn_owned_process, terminate_owned_process

CONTRACT = json.loads(contract_schema_path("process_guard_protocol.json").read_text())


def configuration(raw: bytes) -> dict[str, Any]:
    if len(raw) > CONTRACT["max_config_bytes"] or not raw.endswith(b"\n"):
        raise ValueError("invalid process configuration length")
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {
        "protocol", "version", "command", "cwd", "env", "input", "wall_ms", "grace_ms",
    }:
        raise ValueError("invalid process configuration")
    if value["protocol"] != CONTRACT["protocol"] or type(value["version"]) is not int or value["version"] != CONTRACT["version"]:
        raise ValueError("incompatible process protocol")
    command = value["command"]
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and "\0" not in arg for arg in command) or not command[0]:
        raise ValueError("invalid provider command")
    if not isinstance(value["cwd"], str) or not os.path.isabs(value["cwd"]):
        raise ValueError("provider cwd must be absolute")
    if not isinstance(value["input"], str) or not isinstance(value["env"], dict):
        raise ValueError("invalid provider input/environment")
    if not all(isinstance(k, str) and isinstance(v, str) and "\0" not in k + v and "=" not in k for k, v in value["env"].items()):
        raise ValueError("invalid provider environment")
    if not all(type(value[key]) is int and 0 < value[key] <= 2_147_483_647 for key in ("wall_ms", "grace_ms")):
        raise ValueError("invalid process deadline")
    return value


class Guard:
    def __init__(self):
        self.process = None
        self.output_lock = threading.Lock()
        self.ending = False
        self.stopping = threading.Event()
        self.finished = threading.Event()
        self.isolated = os.name == "nt" or os.getpgrp() == os.getpid()

    def emit(self, frame: dict) -> None:
        with self.output_lock:
            if self.ending:
                return
            sys.stdout.write(json.dumps({"protocol": CONTRACT["protocol"], "version": CONTRACT["version"], **frame}) + "\n")
            sys.stdout.flush()

    def force_stop(self) -> None:
        # This path must not wait behind stdout backpressure or a stuck reader.
        try:
            if os.name != "nt":
                if self.isolated:
                    os.killpg(os.getpid(), signal.SIGKILL)
            elif self.process is not None:
                terminate_owned_process(self.process)
        finally:
            os._exit(1)

    def watch_lease(self) -> None:
        # There are no commands after the initial configuration. EOF, or any
        # unexpected data, ends the lease. Descendants never inherit this pipe.
        sys.stdin.buffer.read(1)
        self.stopping.set()
        self.force_stop()

    def watch_deadline(self, seconds: float) -> None:
        if not self.finished.wait(seconds):
            self.stopping.set()
            self.force_stop()

    def copy_output(self, stream: str, pipe) -> None:
        try:
            while chunk := pipe.read(CONTRACT["chunk_bytes"]):
                self.emit({"type": "data", "stream": stream, "data": base64.b64encode(chunk).decode("ascii")})
        except (BrokenPipeError, OSError):
            self.force_stop()
        finally:
            pipe.close()

    def finish(self, *, code: int | None, sig: str | None, error: str | None) -> None:
        if os.name == "nt" and self.process is not None:
            if not terminate_owned_process(self.process):
                error = error or "provider Job did not terminate within its deadline"
        # On POSIX the guard anchors the group until the terminal frame is
        # flushed. The final group SIGKILL includes the guard and every member;
        # the Node owner recognizes this expected teardown only with a receipt.
        with self.output_lock:
            self.ending = True
            sys.stdout.write(json.dumps({"protocol": CONTRACT["protocol"], "version": CONTRACT["version"],
                "type": "exit", "code": code, "signal": sig, "error": error}) + "\n")
            sys.stdout.flush()
        self.finished.set()
        if os.name != "nt" and self.isolated:
            os.killpg(os.getpid(), signal.SIGKILL)

    def run(self) -> None:
        error = None
        code = None
        sig = None
        try:
            config = configuration(sys.stdin.buffer.readline(CONTRACT["max_config_bytes"] + 1))
            if not self.isolated:
                raise ValueError("process guard requires its own POSIX session")
            threading.Thread(target=self.watch_lease, daemon=True).start()
            threading.Thread(target=self.watch_deadline, args=(config["wall_ms"] / 1000,), daemon=True).start()
            if self.stopping.is_set():
                return
            # A finite prompt file avoids blocking startup on pipe backpressure.
            with tempfile.TemporaryFile() as prompt:
                prompt.write(config["input"].encode("utf-8"))
                prompt.seek(0)
                self.process = spawn_owned_process(
                    config["command"], cwd=config["cwd"], env=config["env"], stdin=prompt,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, close_fds=True,
                    **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
                )
            readers = [threading.Thread(target=self.copy_output, args=(name, getattr(self.process, name)), daemon=True)
                       for name in ("stdout", "stderr")]
            for reader in readers:
                reader.start()
            returncode = self.process.wait()
            if returncode < 0 and os.name != "nt":
                sig = signal.Signals(-returncode).name
            else:
                code = returncode
            deadline = time.monotonic() + config["grace_ms"] / 1000
            for reader in readers:
                reader.join(max(0, deadline - time.monotonic()))
            if any(reader.is_alive() for reader in readers):
                error = "provider exited but its output pipes did not close"
        except Exception as exc:  # fixed protocol channel, never raw tracebacks
            error = f"process guard: {type(exc).__name__}: {exc}"
        finally:
            try:
                self.finish(code=code, sig=sig, error=error)
            except (BrokenPipeError, OSError):
                self.force_stop()


if __name__ == "__main__":
    Guard().run()
