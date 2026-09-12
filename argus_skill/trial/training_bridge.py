"""Tenant-bound, private Unix IPC for the read-only hosted Pi observer.

No route is registered on the public portal. SO_PEERCRED, the container's mounted
tenant filesystem and the actual parent/child process identity
bind an episode to an actual spawned Pi process. A client cannot supply a tenant,
observer_verified, allowlist, or runtime attestation.
"""
from __future__ import annotations

import json
import os
import secrets
import socket
import socketserver
import stat
import struct
import threading
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .analytics import AnalyticsError
from .research_controls import SID
from .training_capture import (
    HOSTED_PAYLOAD_BYTES,
    HOSTED_PROFILE,
    HOSTED_TOOLS,
    INIT_FAILURE_REASONS,
)

IMAGE_PACKAGE = Path("/opt/argus/argus_skill/trial")
EXTENSION_NAME = "pi_training_extension.mjs"


def _process(pid):
    """Use start ticks as well as PID so a recycled process cannot inherit a lease."""
    directory = Path("/proc") / str(pid)
    fields = (directory / "stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": pid, "parent": int(fields[1]), "started": fields[19],
            "argv": (directory / "cmdline").read_bytes().decode().split("\0"), "root": directory / "root"}


class PeerVerifier:
    def __init__(self, tenant, data_dir, web_uds):
        self.tenant, self.data_dir = tenant, Path(data_dir)
        self.web_uds = str(web_uds)
        self.verified = {}

    def _runtime_parent(self, proc):
        # Bind registration to the actual server that owns the provisioned
        # tenant web socket. A tool-spawned Python/Node process must not become
        # a registrar merely by putting 'argus_skill' in its command line.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
            endpoint.settimeout(0.25)
            endpoint.connect(self.web_uds)
            root_pid, uid, _ = struct.unpack("3i", endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != os.getuid():
            raise AnalyticsError(403, "training_runtime_parent_untrusted")
        current = proc
        for _ in range(32):
            executable = Path(os.readlink(Path("/proc") / str(current["pid"]) / "exe")).name
            if not executable.startswith("python"):
                break
            if current["pid"] == root_pid:
                return
            if current["parent"] <= 1:
                break
            current = _process(current["parent"])
        raise AnalyticsError(403, "training_runtime_parent_untrusted")

    def _image_identity(self, proc, uid):
        """Bind to the mounted tenant; code digests do not gate collection."""
        mounted = proc["root"] / "tenant"
        if (mounted / ".tenant-volume").read_text().strip() != self.tenant:
            raise AnalyticsError(403, "training_peer_tenant_mismatch")
        expected, actual = self.data_dir.stat(), mounted.stat()
        if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
            raise AnalyticsError(403, "training_peer_tenant_mismatch")
        return {}

    def __call__(self, peer, *, producer=False, parent=None, control_parent=None, registered=None):
        pid, uid, _gid = peer
        if uid != os.getuid():
            raise AnalyticsError(403, "training_peer_untrusted")
        proc = _process(pid)
        binding = control_parent or parent
        cache_key = (pid, proc["started"], producer, binding["pid"] if binding else None,
                     registered.get("launch_id") if registered else None)
        if cache_key in self.verified:
            executable = Path(os.readlink(Path("/proc") / str(pid) / "exe")).name
            if (executable != "node" if producer else not executable.startswith("python")):
                raise AnalyticsError(403, "training_peer_executable_changed")
            if binding and (proc["parent"] != binding["pid"] or _process(binding["pid"])["started"] != binding["started"]):
                raise AnalyticsError(403, "training_peer_parent_mismatch")
            if not producer and control_parent is None and registered is None:
                self._runtime_parent(proc)
            return self.verified[cache_key]
        self._image_identity(proc, uid)
        if producer:
            if parent is None or proc["parent"] != parent["pid"] or _process(parent["pid"])["started"] != parent["started"]:
                raise AnalyticsError(403, "training_peer_parent_mismatch")
            # Pi sets process.title, overwriting /proc/PID/cmdline. Launch argv is
            # attested by the verified parent adapter at registration; validate
            # the actual child executable and PID/start/parent here instead.
            executable = Path("/proc") / str(pid) / "exe"
            if Path(os.readlink(executable)).name != "node":
                raise AnalyticsError(403, "training_producer_executable_mismatch")
        elif control_parent is not None:
            executable = Path(os.readlink(Path("/proc") / str(pid) / "exe")).name
            if (not executable.startswith("python") or proc["parent"] != control_parent["pid"]
                    or _process(control_parent["pid"])["started"] != control_parent["started"]):
                raise AnalyticsError(403, "training_peer_parent_mismatch")
            # Both fork children retain the exact fresh-interpreter helper argv.
            if [arg for arg in proc["argv"][1:] if arg] != ["-m", "argus_skill.daemon.spawn_helper"]:
                raise AnalyticsError(403, "training_daemon_helper_arguments_mismatch")
        elif registered is not None:
            executable = Path(os.readlink(Path("/proc") / str(pid) / "exe")).name
            if (not executable.startswith("python") or registered.get("pid") != pid
                    or registered.get("started") != proc["started"]):
                raise AnalyticsError(403, "training_daemon_process_mismatch")
        else:
            self._runtime_parent(proc)
        result = {"pid": pid, "started": proc["started"]}
        if len(self.verified) >= 1024:
            self.verified.clear()
        self.verified[cache_key] = result
        return result


class HostPeerVerifier(PeerVerifier):
    """The preserved local workspace uses the same UDS parent/child binding.

    It already lives on the host, so no container volume marker is expected.
    The actual workspace web listener owns registration, including daemon
    launches; an unrelated host process cannot register an episode.
    """

    def _image_identity(self, proc, uid):
        actual, expected = proc["root"].stat(), Path("/").stat()
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
            raise AnalyticsError(403, "training_peer_tenant_mismatch")
        return {}

    @staticmethod
    def accepts_extension(path):
        # The authorized local web parent selects its installed observer path.
        # A release directory change must not disable the next genuine call.
        return isinstance(path, str) and Path(path).is_absolute() and Path(path).name == EXTENSION_NAME


@dataclass
class Lease:
    sid: str
    parent: dict
    runtime: dict
    expires: float
    producer: dict | None = None
    episode_id: int | None = None
    initialization_error: str | None = None


@dataclass
class DaemonLaunch:
    sid: str
    parent: dict
    stage: str
    launch_id: str
    chain: list
    expires: float


class TrainingBridge:
    def __init__(self, training, tenant, verify):
        self.training, self.tenant, self.verify = training, tenant, verify
        self.leases = {}
        self.daemon_tickets = {}
        self.lock = threading.RLock()
        self.counts = Counter()
        self.last_error_code = None
        self.last_diagnostic = None
        self.last_event_at = None
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        with training.analytics._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS training_runtime_workers (
                tenant_id TEXT NOT NULL, sid TEXT NOT NULL, pid INTEGER NOT NULL, started TEXT NOT NULL,
                boot_id TEXT NOT NULL, expires_at REAL NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY(tenant_id,pid,started)
            )""")

    def dispatch(self, data, peer):
        action = data.get("action") if isinstance(data, dict) else None
        try:
            result = self._dispatch(data, peer)
        except (AnalyticsError, ValueError, TypeError, OSError, KeyError, IndexError) as exc:
            code = exc.code if isinstance(exc, AnalyticsError) else "training_bridge_request_rejected"
            with self.lock:
                self.counts["registration_failed" if action == "register" else "requests_rejected"] += 1
                if code.startswith(("training_peer_", "training_producer_", "training_observer_", "training_pi_")):
                    self.counts["producer_verification_failed"] += 1
                self.last_error_code = code
            raise
        with self.lock:
            if action == "register":
                self.counts["registered" if result.get("enabled") else "registration_without_permission"] += 1
            elif action == "begin":
                self.counts["episodes_started"] += 1
            elif action in {"daemon_prepare", "daemon_helper", "daemon_fork", "daemon_claim"}:
                self.counts[action + "_completed"] += 1
            elif action == "init_failed":
                if not result.get("already_failed"):
                    self.counts["initialization_failed"] += 1
                    if result.get("state") in {"quarantined", "interrupted"}:
                        self.counts["episodes_" + result["state"]] += 1
                self.last_error_code = result["reason"]
                self.last_diagnostic = result.get("diagnostic")
            elif action == "event":
                self.counts["events_received"] += 1
                self.last_event_at = time.time()
                state = result.get("state")
                if state in {"complete", "quarantined", "interrupted"}:
                    self.counts["episodes_" + state] += 1
                if result.get("reason"):
                    self.last_error_code = result["reason"]
                    self.last_diagnostic = result.get("diagnostic")
        return result

    def status(self):
        with self.lock:
            return {"state": "listening", "counts": dict(self.counts), "active_leases": len(self.leases),
                    "pending_daemon_launches": len(self.daemon_tickets),
                    "last_error_code": self.last_error_code, "last_event_at": self.last_event_at,
                    "last_diagnostic": self.last_diagnostic}

    def _parent(self, peer, sid):
        with self.training.analytics._db() as db:
            db.execute("DELETE FROM training_runtime_workers WHERE expires_at<? OR boot_id!=?",
                       (self.training.analytics.clock(), self.boot_id))
            rows = db.execute("SELECT * FROM training_runtime_workers WHERE tenant_id=? AND pid=?",
                              (self.tenant, peer[0])).fetchall()
        if rows:
            started = _process(peer[0])["started"]
            for row in rows:
                if row["started"] != started:
                    continue
                if row["sid"] != sid:
                    raise AnalyticsError(403, "training_daemon_project_mismatch")
                metadata = json.loads(row["metadata"])
                verified = self.verify(peer, registered=metadata)
                return {**verified, "daemon_launch": metadata}
        return self.verify(peer)

    def _daemon_action(self, action, token, value, peer, now):
        if set(value) != {"sid"} or not isinstance(value["sid"], str) or not SID.fullmatch(value["sid"]):
            raise ValueError("Invalid daemon project binding")
        sid = value["sid"]
        if action == "daemon_prepare":
            if token is not None:
                raise ValueError("Invalid daemon launch request")
            parent = self._parent(peer, sid)
            if len(self.daemon_tickets) >= 256:
                raise AnalyticsError(429, "training_daemon_launch_capacity")
            token = secrets.token_urlsafe(32)
            chain = [{"pid": parent["pid"], "started": parent["started"], "kind": "runtime_parent"}]
            self.daemon_tickets[token] = DaemonLaunch(sid, parent, "helper", secrets.token_hex(16), chain, now + 120)
            return {"ticket": token}
        ticket = self.daemon_tickets.get(token) if isinstance(token, str) else None
        if ticket is None or ticket.sid != sid or action != "daemon_" + ticket.stage:
            raise AnalyticsError(403, "training_daemon_capability_invalid")
        child = self.verify(peer, control_parent=ticket.parent)
        chain = [*ticket.chain, {"pid": child["pid"], "started": child["started"], "kind": ticket.stage}]
        # Consume only after OS identity and parent/start-time checks pass.
        del self.daemon_tickets[token]
        if action == "daemon_claim":
            metadata = {"launch_id": ticket.launch_id, "pid": child["pid"], "started": child["started"],
                        "sid": sid, "boot_id": self.boot_id, "chain": chain}
            expires = self.training.analytics.clock() + min(30, self.training.analytics.retention_days) * 86400
            with self.training.analytics._db() as db:
                db.execute("INSERT OR REPLACE INTO training_runtime_workers VALUES (?,?,?,?,?,?,?)",
                           (self.tenant, sid, child["pid"], child["started"], self.boot_id, expires, json.dumps(metadata)))
            return {"registered": True, "launch_id": ticket.launch_id}
        following = "fork" if action == "daemon_helper" else "claim"
        token = secrets.token_urlsafe(32)
        self.daemon_tickets[token] = DaemonLaunch(sid, child, following, ticket.launch_id, chain, now + 120)
        return {"ticket": token}

    def _dispatch(self, data, peer):
        if not isinstance(data, dict) or set(data) != {"action", "lease", "value"}:
            raise ValueError("Invalid training bridge request")
        action, token, value = data["action"], data["lease"], data["value"]
        if not isinstance(value, dict):
            raise ValueError("Invalid training bridge payload")
        with self.lock:
            now = time.monotonic()
            self.leases = {key: row for key, row in self.leases.items() if row.expires > now}
            self.daemon_tickets = {key: row for key, row in self.daemon_tickets.items() if row.expires > now}
            if action in {"daemon_prepare", "daemon_helper", "daemon_fork", "daemon_claim"}:
                return self._daemon_action(action, token, value, peer, now)
            if action == "register":
                if token is not None or set(value) != {"sid", "call_id", "run_label", "command", "mission_id"}:
                    raise ValueError("Invalid runtime registration")
                if any(not isinstance(item, str) or not SID.fullmatch(item) for item in (value["sid"], value["call_id"])):
                    raise ValueError("Invalid runtime identity")
                if not isinstance(value["run_label"], str) or len(value["run_label"]) > 160:
                    raise ValueError("Invalid runtime label")
                if value["mission_id"] is not None and (not isinstance(value["mission_id"], str) or not SID.fullmatch(value["mission_id"])):
                    raise ValueError("Invalid mission identity")
                argv = value["command"]
                if not isinstance(argv, list) or not 1 <= len(argv) <= 512 or any(not isinstance(arg, str) or len(arg) > 4096 for arg in argv):
                    raise ValueError("Invalid runtime launch")
                extensions = [argv[index + 1] for index, arg in enumerate(argv[:-1]) if arg in {"-e", "--extension"}]
                accepts_extension = getattr(self.verify, "accepts_extension", None)
                if (not any(accepts_extension(path) for path in extensions) if accepts_extension
                        else str(IMAGE_PACKAGE / EXTENSION_NAME) not in extensions):
                    raise AnalyticsError(403, "training_observer_arguments_mismatch")
                parent = self._parent(peer, value["sid"])
                # Registration is metadata-only; grant eligibility is checked
                # before the producer even projects private in-memory messages.
                self.training.journal.poll(self.tenant)
                access = self.training.capture.authorize(self.tenant, value["sid"])
                if not access["enabled"]:
                    return {"enabled": False}
                if len(self.leases) >= 256:
                    raise AnalyticsError(429, "training_bridge_capacity")
                token = secrets.token_urlsafe(32)
                runtime = {"capture_id": secrets.token_hex(16), "call_id": value["call_id"],
                           "run_label": value["run_label"], "profile": HOSTED_PROFILE,
                           "mission_id": value["mission_id"],
                           "parent_pid": parent["pid"], "parent_started": parent["started"],
                           "capture_policy": "retain-observed-v2",
                           "provider_projection": "openai-chat-observed-v2", "model_context_complete": False}
                if parent.get("daemon_launch"):
                    runtime["daemon_launch"] = parent["daemon_launch"]
                self.leases[token] = Lease(value["sid"], parent, runtime, now + 12 * 3600)
                return {"enabled": True, "lease": token, "profile": HOSTED_PROFILE}
            if not isinstance(token, str) or token not in self.leases:
                raise AnalyticsError(403, "training_lease_invalid")
            lease = self.leases[token]
            if action == "close":
                parent = self._parent(peer, lease.sid)
                if (parent["pid"], parent["started"]) != (lease.parent["pid"], lease.parent["started"]):
                    raise AnalyticsError(403, "training_peer_parent_mismatch")
                if lease.episode_id is not None:
                    try:
                        self.training.capture.event(self.tenant, lease.sid, lease.episode_id,
                                                    "quarantine", {"reason": "runtime_call_unsettled"})
                    except AnalyticsError as exc:
                        if exc.status not in {404, 409}:
                            raise
                del self.leases[token]
                return {"closed": True}
            producer = self.verify(peer, producer=True, parent=lease.parent)
            if lease.producer and (producer["pid"], producer["started"]) != (lease.producer["pid"], lease.producer["started"]):
                raise AnalyticsError(403, "training_producer_changed")
            lease.producer = producer
            if action == "init_failed":
                if set(value) != {"reason"} or value["reason"] not in INIT_FAILURE_REASONS:
                    raise ValueError("Invalid training initialization diagnosis")
                if lease.initialization_error:
                    return {"state": "disabled", "reason": lease.initialization_error, "already_failed": True}
                result = {"state": "disabled", "reason": value["reason"]}
                if lease.episode_id is not None:
                    try:
                        result = self.training.capture.event(
                            self.tenant, lease.sid, lease.episode_id, "quarantine", {"reason": value["reason"]},
                        )
                    except AnalyticsError as exc:
                        if exc.status not in {404, 409}:
                            raise
                # dispatch's lease lock serializes this with a begin whose
                # response timed out. A failed initializer cannot start again.
                lease.initialization_error = value["reason"]
                return result
            if lease.initialization_error:
                raise AnalyticsError(409, "training_initialization_failed")
            if action == "authorize" and not value:
                return self.training.capture.authorize(self.tenant, lease.sid)
            if action == "begin" and "session_id" in value and not set(value) - {"session_id", "allowed_tools"}:
                if lease.episode_id is not None:
                    with self.training.analytics._db() as db:
                        previous = db.execute("SELECT state FROM training_tool_episodes WHERE id=?",
                                              (lease.episode_id,)).fetchone()
                    if previous is not None and previous["state"] == "capturing":
                        raise AnalyticsError(409, "training_lease_already_started")
                runtime = {**lease.runtime, "producer_pid": producer["pid"], "producer_started": producer["started"]}
                result = self.training.capture.begin(
                    self.tenant, lease.sid, value["session_id"], observer_verified=True,
                    allowed_tools=value.get("allowed_tools", sorted(HOSTED_TOOLS)),
                    runtime_profile=HOSTED_PROFILE, runtime_metadata=runtime,
                )
                lease.episode_id = result["episode_id"]
                return result
            if action == "event" and set(value) == {"episode_id", "kind", "payload"}:
                if lease.episode_id is None or type(value["episode_id"]) is not int or value["episode_id"] != lease.episode_id:
                    raise AnalyticsError(403, "training_episode_binding_mismatch")
                return self.training.capture.event(self.tenant, lease.sid, lease.episode_id, value["kind"], value["payload"])
            raise ValueError("Invalid training bridge action")


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    block_on_close = False


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(3)
        try:
            peer = struct.unpack("3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            raw = self.rfile.readline(HOSTED_PAYLOAD_BYTES + 8193)
            if len(raw) > HOSTED_PAYLOAD_BYTES + 8192 or not raw.endswith(b"\n"):
                raise ValueError("Training bridge request exceeds capacity")
            result = self.server.bridge.dispatch(json.loads(raw), peer)
        except AnalyticsError as exc:
            result = {"error": exc.code}
        except (ValueError, TypeError, OSError, KeyError, IndexError, RecursionError):
            result = {"error": "training_bridge_request_rejected"}
        try:
            self.wfile.write(json.dumps(result, separators=(",", ":")).encode() + b"\n")
        except OSError:
            pass


class BridgeServers:
    def __init__(self):
        self.servers = []

    def close(self):
        for server, path, identity in self.servers:
            server.shutdown()
            server.server_close()
            try:
                current = path.stat()
                if (current.st_dev, current.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass
        self.servers.clear()

    def status(self):
        return {"state": "listening" if self.servers else "not_configured",
                "tenants": {server.bridge.tenant: server.bridge.status() for server, _path, _identity in self.servers}}


def start_training_bridges(training, tenants):
    """Start alongside portal lifespan. Returns an idempotent .close() owner.

    Only endpoints with an existing Unix socket directory are provisioned.
    Startup failures raise OSError/ValueError and close any already-started peers.
    Call close in a worker thread during shutdown (socketserver may wait 0.5s).
    """
    owner = BridgeServers()
    try:
        for tenant, endpoint in tenants.items():
            uds = endpoint.get("uds") if isinstance(endpoint, dict) else getattr(endpoint, "uds", None)
            if not uds or tenant not in training.analytics.tenants:
                continue
            directory = Path(uds).parent
            if not directory.is_dir():
                continue
            path = directory / "training.sock"
            if path.exists():
                mode = path.lstat()
                if not stat.S_ISSOCK(mode.st_mode) or mode.st_uid != os.getuid():
                    raise ValueError("Unexpected training socket owner")
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.settimeout(0.1)
                    probe.connect(str(path))
                except (ConnectionRefusedError, FileNotFoundError):
                    path.unlink()
                else:
                    raise ValueError("Training socket already active")
                finally:
                    probe.close()
            server = _Server(str(path), _Handler)
            path.chmod(0o600)
            mode = path.stat()
            configured = training.analytics.tenants[tenant]
            verifier = HostPeerVerifier if configured.get("runtime_mode") == "host" else PeerVerifier
            server.bridge = TrainingBridge(training, tenant, verifier(tenant, configured["data_dir"], uds))
            owner.servers.append((server, path, (mode.st_dev, mode.st_ino)))
            threading.Thread(target=server.serve_forever, daemon=True, name="training-bridge").start()
    except Exception:
        owner.close()
        raise
    return owner
