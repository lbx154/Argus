"""Parent-owned opt-in for actual hosted Pi calls; no transcript-file imports."""
from __future__ import annotations

import json
import os
import select
import socket
from contextlib import contextmanager
from pathlib import Path

from .research_controls import SID

EXTENSION = str(Path(__file__).with_name("pi_training_extension.mjs"))
SOCKET_ENV = "ARGUS_TRAINING_BRIDGE_SOCKET"
LEASE_ENV = "ARGUS_TRAINING_LEASE_TOKEN"
_daemon_launch = None


def _request(path, action, value, lease=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1.0)
        client.connect(path)
        data = json.dumps({"action": action, "lease": lease, "value": value}, separators=(",", ":")).encode()
        client.sendall(data + b"\n")
        with client.makefile("rb") as stream:
            raw = stream.readline(32769)
        if len(raw) > 32768:
            raise ValueError("Invalid training bridge response")
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("error"):
            raise ValueError("Training bridge unavailable")
        return result


def _project(ctx):
    # usage_project_root comes from the backend's bound project context, never
    # from the user's chat body, a tool argument, or an extension-provided label.
    root = ctx.usage_project_root
    if root is None:
        return None
    from ..core.paths import global_root

    directory = Path(root).resolve()
    expected = (global_root() / "projects").resolve()
    if directory.parent != expected or not SID.fullmatch(directory.name):
        return None
    return directory.name


def _daemon_sid(config):
    directory = getattr(config, "life_dir", None)
    if directory is None:
        return None
    from ..core.paths import global_root

    try:
        directory = Path(directory).resolve()
        expected = Path(getattr(config, "global_root", None) or global_root()).resolve() / "projects"
    except (OSError, ValueError, RuntimeError):
        return None
    return directory.name if directory.parent == expected and SID.fullmatch(directory.name) else None


def daemon_launch_payload(config):
    """Mint a metadata-only launch ticket in the trusted web parent.

    Return it exclusively through the existing private helper stdin payload.
    It never enters environment variables, process arguments, configuration
    files, logs, or training samples and does not grant permission to capture.
    """
    path = os.environ.get(SOCKET_ENV)
    if os.name != "posix" or os.environ.get("ARGUS_TRIAL_HARNESS") != "argus-pi" or not path:
        return None
    sid = _daemon_sid(config)
    if sid:
        try:
            reply = _request(path, "daemon_prepare", {"sid": sid})
            if isinstance(reply.get("ticket"), str):
                return {"sid": sid, "ticket": reply["ticket"]}
        except (OSError, ValueError, TypeError):
            pass
    return None


def arm_daemon_launch(payload, config):
    """The exact fresh helper consumes the web parent's one-use ticket."""
    global _daemon_launch
    _daemon_launch = None
    path = os.environ.get(SOCKET_ENV)
    if (not path or not isinstance(payload, dict) or set(payload) != {"sid", "ticket"}
            or payload["sid"] != _daemon_sid(config) or not isinstance(payload["ticket"], str)):
        return
    try:
        reply = _request(path, "daemon_helper", {"sid": payload["sid"]}, payload["ticket"])
        if isinstance(reply.get("ticket"), str):
            _daemon_launch = {"path": path, "sid": payload["sid"], "ticket": reply["ticket"]}
    except (OSError, ValueError, TypeError):
        pass


def prepare_daemon_fork(config):
    """First fork child proves its living helper parent; return an ack pipe.

    The pipe keeps this intermediate parent alive until the second fork child
    is attested, avoiding the race with reparenting to tini/PID1.
    """
    global _daemon_launch
    current, _daemon_launch = _daemon_launch, None
    if current is None or current["sid"] != _daemon_sid(config):
        return None
    try:
        reply = _request(current["path"], "daemon_fork", {"sid": current["sid"]}, current["ticket"])
        if not isinstance(reply.get("ticket"), str):
            return None
        pipe = os.pipe()
        _daemon_launch = {**current, "ticket": reply["ticket"]}
        return pipe
    except (OSError, ValueError, TypeError):
        return None


def wait_daemon_claim(pipe):
    """Intermediate parent waits at most four seconds, then exits normally."""
    global _daemon_launch
    _daemon_launch = None
    if pipe is None:
        return
    reader, writer = pipe
    os.close(writer)
    try:
        if select.select([reader], [], [], 4.0)[0]:
            os.read(reader, 1)
    except OSError:
        pass
    finally:
        os.close(reader)


def complete_daemon_fork(pipe, *, forked=True):
    """Register the real grandchild before it becomes an orphan; erase ticket."""
    global _daemon_launch
    current, _daemon_launch = _daemon_launch, None
    if pipe is None:
        return
    reader, writer = pipe
    os.close(reader)
    try:
        if forked and current is not None:
            _request(current["path"], "daemon_claim", {"sid": current["sid"]}, current["ticket"])
    except (OSError, ValueError, TypeError):
        pass
    finally:
        try:
            os.write(writer, b"1")
        except OSError:
            pass
        os.close(writer)


@contextmanager
def capture_runtime_call(ctx, options):
    """Attach a short-lived producer capability only after live purpose consent.

    Collection outages never alter the runtime prompt, tool access, or answer.
    Resumed calls are not promoted to fresh training episodes; later support
    requires an explicit history/consent-bound protocol rather than reading logs.
    """
    lease = None
    path = os.environ.get(SOCKET_ENV)
    enabled = (getattr(ctx.backend, "_backend_name", None) == "pi"
               and os.environ.get("ARGUS_TRIAL_HARNESS") == "argus-pi"
               and path and ctx.resume_thread_id is None and not options.disable_tools
               and not options.isolate_workdir
               and not getattr(options, "trusted_extensions", None)
               and not getattr(options, "trusted_tool_names", None)
               and not getattr(options, "extension_env", None))
    if enabled:
        # Ambient extension/resource flags would invalidate the pinned observer
        # profile. The ordinary call still runs, without a training attestation.
        extras = [*getattr(ctx.backend, "_default_extra_args", []), *(options.extra_args or [])]
        enabled = not any(arg in {"-e", "--extension", "--session", "--continue", "-c"}
                          or arg.startswith(("--extension=", "--session=")) for arg in extras)
    try:
        if enabled:
            sid = _project(ctx)
            if sid:
                try:
                    options._training_extension = EXTENSION
                    command = ctx.backend._runner._build_command(resume_thread_id=None, options=options)
                    mission = ctx.usage_mission_id
                    if not isinstance(mission, str) or not SID.fullmatch(mission):
                        mission = None
                    result = _request(path, "register", {"sid": sid, "call_id": ctx.call_id,
                                                        "run_label": ctx.run_label, "command": command,
                                                        "mission_id": mission})
                    if result.get("enabled") is True and isinstance(result.get("lease"), str):
                        from ..agent_cli.agent_cli_runner import PrivateRunnerEnvironment

                        lease = result["lease"]
                        options._training_environment = PrivateRunnerEnvironment({SOCKET_ENV: path, LEASE_ENV: lease})
                except (OSError, ValueError, TypeError):
                    pass
                if not lease and hasattr(options, "_training_extension"):
                    options._training_extension = None
        yield
    finally:
        for name in ("_training_extension", "_training_environment"):
            if hasattr(options, name):
                setattr(options, name, None)
        if lease:
            try:
                _request(path, "close", {}, lease)
            except (OSError, ValueError, TypeError):
                pass
