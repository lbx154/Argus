"""Private tenant compute queue; run with --config FILE --uds SOCKET.

The model Store authenticates invitations only: compute accounting is independent.
Never remove the ledger or retained containers while this service is in use.
Missing/ambiguous execution evidence keeps the reservation and blocks that job's
resources until evidence returns; it never causes an automatic resubmission.
Optional egress_socket_dir mounts the public-only HTTPS proxy socket directory
read-only; the trusted image entrypoint bootstraps its loopback forwarder.
Optional cgroup_parent joins the deployment's aggregate systemd slice. Production
requires mounted tenant volumes with a matching .tenant-volume marker.
interactive_memory_gib reserves interactive containers' permitted memory growth
within the shared limit; the remaining memory is the compute job pool.
Dispatch preserves 64 GiB of physical free space on the state_dir filesystem.
Admission caps each tenant at 100 outstanding jobs, with 32 KiB of command text
inside a 64 KiB request; CPU-only work is subject to the same queue and time caps.
"""
from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
from contextlib import asynccontextmanager, closing, contextmanager
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .store import Store, TrialError

LOG = logging.getLogger(__name__)
GIB = 1024 ** 3
GPU_SECONDS = 200 * 3600
ACTIVE = ("starting", "running", "cancelling", "blocked")
FINAL = ("succeeded", "failed", "cancelled", "timed_out")
LABEL = "io.argus.compute"
MAX_COMMAND_BYTES = 32768


class InfrastructureError(RuntimeError):
    pass


def validate_command(command):
    if (not isinstance(command, list) or not 1 <= len(command) <= 128
            or any(not isinstance(arg, str) or "\0" in arg for arg in command)
            or not command[0] or command[0].startswith("-")):
        raise TrialError(400, "invalid_command", "command must contain 1..128 strings, at most 32768 UTF-8 bytes.")
    try:
        size = sum(len(arg.encode("utf-8")) for arg in command)
    except UnicodeEncodeError as exc:
        raise TrialError(400, "invalid_command", "command must contain valid UTF-8 text") from exc
    if size > MAX_COMMAND_BYTES:
        raise TrialError(400, "invalid_command", "command exceeds 32768 UTF-8 bytes")
    return command


def load_config(config):
    if isinstance(config, (str, Path)):
        config = json.loads(Path(config).read_text())
    config = dict(config)
    required = {"state_dir", "trial_db", "tenants", "image", "uid", "gid",
                "cpu_limit", "memory_gib", "gpu_devices", "gpu_hours_per_tenant"}
    if not required <= set(config) or set(config) - required - {
        "egress_socket_dir", "cgroup_parent", "interactive_memory_gib",
    }:
        raise ValueError("Compute config requires " + ", ".join(sorted(required))
                         + "; unknown optional field")
    if "cgroup_parent" in config and (
            not isinstance(config["cgroup_parent"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.slice", config["cgroup_parent"])):
        raise ValueError("cgroup_parent must name a systemd .slice")
    for key in ("state_dir", "trial_db"):
        path = Path(config[key])
        if not path.is_absolute():
            raise ValueError(f"{key} must be absolute")
        config[key] = path.resolve()
    for key, low, high in (("uid", 1, 2**31 - 1), ("gid", 1, 2**31 - 1),
                           ("cpu_limit", 1, 120), ("memory_gib", 1, 900)):
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"Invalid {key}")
    reserve = config.setdefault("interactive_memory_gib", 0)
    if type(reserve) is not int or not 0 <= reserve < config["memory_gib"]:
        raise ValueError("interactive_memory_gib must leave positive job memory capacity")
    if type(config["gpu_hours_per_tenant"]) is not int or config["gpu_hours_per_tenant"] != 200:
        raise ValueError("Each invitation must have exactly 200 lifetime GPU-hours")
    devices = config["gpu_devices"]
    if (not isinstance(devices, list) or len(devices) > 4
            or any(type(d) is not int or d < 0 for d in devices) or len(set(devices)) != len(devices)):
        raise ValueError("gpu_devices must contain up to four distinct nonnegative indices")
    if not isinstance(config["image"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@-]*", config["image"]):
        raise ValueError("Invalid image")
    tenants = config["tenants"]
    if not isinstance(tenants, dict) or not 1 <= len(tenants) <= 10:
        raise ValueError("Configure 1..10 invitation tenants")
    roots = []
    for tenant, options in tenants.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", tenant) or set(options) != {"data_dir"}:
            raise ValueError("Invalid tenant configuration")
        path = Path(options["data_dir"])
        if not path.is_absolute() or any(c in str(path) for c in ("\0", ",", "\n")):
            raise ValueError("Tenant data_dir must be an absolute mount-safe path")
        root = path.resolve(strict=True)
        if not root.is_dir() or any(root == p or root in p.parents or p in root.parents for p in roots):
            raise ValueError("Tenant data directories must be distinct and non-overlapping")
        if any(root == p or root in p.parents for p in (config["state_dir"], config["trial_db"])):
            raise ValueError("Private service state must not be inside tenant data")
        roots.append(root)
        options["data_dir"] = root
    if "egress_socket_dir" in config:
        path = Path(config["egress_socket_dir"])
        if not path.is_absolute() or any(c in str(path) for c in ("\0", ",", "\n")):
            raise ValueError("egress_socket_dir must be an absolute mount-safe path")
        path = path.resolve(strict=True)
        private_paths = [*roots, config["state_dir"], config["trial_db"]]
        if not path.is_dir() or any(path == p or path in p.parents or p in path.parents for p in private_paths):
            raise ValueError("egress_socket_dir must be separate from tenant and private service data")
        config["egress_socket_dir"] = path
    return config


def timestamp(value):
    if not value or value.startswith("0001-"):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Docker timestamp lacks timezone")
        return result.timestamp()
    except (ValueError, OverflowError) as exc:
        raise InfrastructureError("Invalid Docker execution timestamp") from exc


class DockerExecutor:
    """Only this boundary executes host commands; tests replace it entirely."""

    def validate_volume(self, root, tenant):
        if not root.is_mount():
            raise InfrastructureError(f"Tenant {tenant} data volume is not mounted")
        try:
            fd = os.open(root / ".tenant-volume", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as marker:
                if not stat.S_ISREG(os.fstat(marker.fileno()).st_mode):
                    raise InfrastructureError(f"Tenant {tenant} volume marker is not a regular file")
                if marker.read(82) not in (tenant.encode(), tenant.encode() + b"\n"):
                    raise InfrastructureError(f"Tenant {tenant} volume marker does not match")
        except OSError as exc:
            raise InfrastructureError(f"Tenant {tenant} volume marker is unavailable") from exc

    def command(self, argv):
        try:
            result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InfrastructureError(f"{argv[0]} command unavailable or timed out") from exc
        if result.returncode:
            # Docker stderr can include tenant output or private host paths.
            raise InfrastructureError(f"{argv[0]} {argv[1]} failed (exit {result.returncode})")
        return result.stdout

    def containers(self, owner):
        ids = self.command(["docker", "container", "ls", "-aq", "--no-trunc",
                            "--filter", f"label={LABEL}={owner}"]).split()
        if not ids:
            return []
        try:
            return json.loads(self.command(["docker", "container", "inspect", *ids]))
        except (ValueError, TypeError) as exc:
            raise InfrastructureError("Invalid Docker inventory response") from exc

    def busy_gpus(self, devices):
        if not devices:
            return set()
        rows = self.command(["nvidia-smi", "--query-gpu=index,uuid,memory.used,utilization.gpu,display_active",
                             "--format=csv,noheader,nounits"])
        apps = self.command(["nvidia-smi", "--query-compute-apps=gpu_uuid",
                             "--format=csv,noheader,nounits"])
        active = set(apps.split())
        seen, busy = set(), set()
        try:
            for line in rows.splitlines():
                index, uuid, memory, utilization, display = (part.strip() for part in line.split(","))
                index = int(index)
                if index not in devices:
                    continue
                seen.add(index)
                memory, utilization = int(memory), int(utilization)
                if display not in {"Enabled", "Disabled"} or memory < 0 or not 0 <= utilization <= 100:
                    raise ValueError("Unsupported GPU activity evidence")
                # These headless A6000s retain a measured 1 MiB driver baseline.
                # Accept it only with disabled display, no process, and zero load.
                if uuid in active or memory > 1 or utilization > 0 or display == "Enabled":
                    busy.add(index)
        except (ValueError, TypeError) as exc:
            raise InfrastructureError("GPU availability probe is invalid or unsupported") from exc
        if seen != set(devices):
            raise InfrastructureError("GPU availability probe omitted configured devices")
        return busy

    def available_memory(self):
        try:
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        except (OSError, ValueError) as exc:
            raise InfrastructureError("Host memory probe failed") from exc
        raise InfrastructureError("Host memory probe omitted MemAvailable")

    def available_storage(self, state_dir):
        try:
            return shutil.disk_usage(state_dir).free
        except OSError as exc:
            raise InfrastructureError("Host physical storage probe failed") from exc

    def launch(self, argv):
        return self.command(argv).strip()

    def kill(self, container_id):
        self.command(["docker", "container", "kill", container_id])

    def logs(self, container_id):
        # Keep stderr (the job's stderr is also a Docker log stream).
        try:
            result = subprocess.run(["docker", "container", "logs", "--tail", "200", container_id],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise InfrastructureError("Docker logs unavailable") from exc
        if result.returncode:
            raise InfrastructureError("Docker logs failed")
        output = result.stdout
        return output[-65536:].decode("utf-8", errors="replace"), len(output) > 65536


class ComputeService:
    def __init__(self, config, *, executor=None, clock=time.time):
        self.config = load_config(config)
        self.executor = executor if executor is not None else DockerExecutor()
        self.clock = clock
        self.mutex = threading.RLock()
        self.lock_file = None
        self.error = None
        self.path = self.config["state_dir"] / "compute.sqlite3"
        self.owner = str(self.config["state_dir"])

    @property
    def job_memory_gib(self):
        return self.config["memory_gib"] - self.config["interactive_memory_gib"]

    def open(self):
        self.config["state_dir"].mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_file = (self.config["state_dir"] / "owner.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("Another compute service owns this state directory") from None
        try:
            if isinstance(self.executor, DockerExecutor):
                for tenant, options in self.config["tenants"].items():
                    self.executor.validate_volume(options["data_dir"], tenant)
            if not self.config["trial_db"].is_file():
                raise ValueError("Existing model trial_db is required")
            self.auth = Store(self.config["trial_db"])
            with closing(sqlite3.connect(self.path)) as db:
                db.executescript("""
                    PRAGMA journal_mode=WAL;
                    CREATE TABLE IF NOT EXISTS tenants (
                        tenant_id TEXT PRIMARY KEY, last_dispatch INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS jobs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL,
                        status TEXT NOT NULL, spec TEXT NOT NULL, submitted_at REAL NOT NULL,
                        reserved INTEGER NOT NULL, charged INTEGER NOT NULL,
                        devices TEXT NOT NULL DEFAULT '[]', container_id TEXT,
                        started_at REAL, finished_at REAL, exit_code INTEGER,
                        stop_reason TEXT, error TEXT
                    );
                    CREATE INDEX IF NOT EXISTS jobs_tenant ON jobs(tenant_id, id);
                """)
                db.executemany("INSERT OR IGNORE INTO tenants(tenant_id) VALUES (?)",
                               [(t,) for t in self.config["tenants"]])
                db.commit()
            self.path.chmod(0o600)
        except Exception:
            self.close()
            raise

    def close(self):
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None

    @contextmanager
    def transaction(self):
        if self.lock_file is None:
            raise RuntimeError("Compute service is not open")
        with closing(sqlite3.connect(self.path, timeout=10)) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            with db:
                yield db

    def workdir(self, tenant, value):
        if not isinstance(value, str) or len(value) > 1024 or "\0" in value:
            raise TrialError(400, "invalid_workdir", "workdir must be relative beneath workspace")
        relative = PurePosixPath(value)
        if relative.is_absolute() or ".." in relative.parts or "\\" in value:
            raise TrialError(400, "invalid_workdir", "workdir must be relative beneath workspace")
        root = self.config["tenants"][tenant]["data_dir"]
        if isinstance(self.executor, DockerExecutor):
            self.executor.validate_volume(root, tenant)
        workspace = root / "workspace"
        try:
            if workspace.is_symlink() or not workspace.is_dir():
                raise ValueError("workspace is not a real directory")
            target = (workspace / str(relative)).resolve(strict=True)
            target.relative_to(workspace)
            if not target.is_dir():
                raise ValueError("workdir is not a directory")
        except (OSError, ValueError, RuntimeError) as exc:
            raise TrialError(400, "invalid_workdir", "workdir must be an existing directory beneath workspace") from exc
        return str(target.relative_to(workspace))

    def submit(self, tenant, payload):
        fields = {"command", "cpus", "memory_gib", "gpus", "timeout_seconds", "workdir", "name", "shm_gib"}
        if not isinstance(payload, dict) or set(payload) - fields:
            raise TrialError(400, "invalid_job", "Unknown job fields")
        spec = {"command": validate_command(payload.get("command"))}
        for field, default, low, high in (
            ("cpus", 8, 1, self.config["cpu_limit"]),
            ("memory_gib", 32, 1, self.job_memory_gib),
            ("gpus", 1, 0, len(self.config["gpu_devices"])),
            ("timeout_seconds", 3600, 1, 86400),
            ("shm_gib", 1, 1, 16),
        ):
            value = payload.get(field, default)
            if type(value) is not int or not low <= value <= high:
                raise TrialError(400, "invalid_resource", f"{field} must be an integer in {low}..{high}")
            spec[field] = value
        if spec["shm_gib"] > spec["memory_gib"]:
            raise TrialError(400, "invalid_resource", "shm_gib cannot exceed memory_gib")
        spec["workdir"] = self.workdir(tenant, payload.get("workdir", "."))
        name = payload.get("name", "")
        if not isinstance(name, str) or len(name) > 80 or any(ord(c) < 32 for c in name):
            raise TrialError(400, "invalid_name", "name must be at most 80 printable characters")
        try:
            name.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise TrialError(400, "invalid_name", "name must contain valid UTF-8 text") from exc
        spec["name"] = name
        reserved = spec["gpus"] * spec["timeout_seconds"]
        with self.transaction() as db:
            used = db.execute("SELECT coalesce(sum(charged),0) FROM jobs WHERE tenant_id=?", (tenant,)).fetchone()[0]
            if used + reserved > GPU_SECONDS:
                raise TrialError(402, "gpu_quota_exceeded", "Insufficient lifetime GPU-seconds")
            pending = db.execute("SELECT count(*) FROM jobs WHERE tenant_id=? AND status NOT IN (?,?,?,?)",
                                 (tenant, *FINAL)).fetchone()[0]
            if pending >= 100:
                raise TrialError(429, "queue_full", "At most 100 outstanding jobs per invitation")
            cursor = db.execute("""INSERT INTO jobs(tenant_id,status,spec,submitted_at,reserved,charged)
                                   VALUES (?,'queued',?,?,?,?)""",
                                (tenant, json.dumps(spec), self.clock(), reserved, reserved))
            job = db.execute("SELECT * FROM jobs WHERE id=?", (cursor.lastrowid,)).fetchone()
        return self.public(job)

    def public(self, row):
        result = {key: row[key] for key in ("id", "tenant_id", "status", "submitted_at", "started_at",
                                          "finished_at", "exit_code", "error")}
        result.update(json.loads(row["spec"]))
        result["gpu_seconds_reserved"] = row["reserved"]
        result["gpu_seconds_charged"] = row["charged"]
        return result

    def own_job(self, db, tenant, job_id):
        row = db.execute("SELECT * FROM jobs WHERE id=? AND tenant_id=?", (job_id, tenant)).fetchone()
        if row is None:
            raise TrialError(404, "job_not_found", "Job not found")
        return row

    def get(self, tenant, job_id):
        with self.transaction() as db:
            return self.public(self.own_job(db, tenant, job_id))

    def jobs(self, tenant, limit=100, offset=0):
        with self.transaction() as db:
            return [self.public(row) for row in db.execute(
                "SELECT * FROM jobs WHERE tenant_id=? ORDER BY id DESC LIMIT ? OFFSET ?",
                (tenant, limit, offset))]

    def status(self, tenant):
        with self.transaction() as db:
            spent, reserved = db.execute("""SELECT coalesce(sum(CASE WHEN status IN (?,?,?,?)
                THEN charged ELSE 0 END),0), coalesce(sum(CASE WHEN status NOT IN (?,?,?,?)
                THEN charged ELSE 0 END),0) FROM jobs WHERE tenant_id=?""", (*FINAL, *FINAL, tenant)).fetchone()
            counts = dict(db.execute("SELECT status,count(*) FROM jobs WHERE tenant_id=? GROUP BY status", (tenant,)))
            active = db.execute("SELECT spec FROM jobs WHERE status IN (?,?,?,?)", ACTIVE).fetchall()
        specs = [json.loads(row["spec"]) for row in active]
        return {"tenant_id": tenant, "gpu_hours_limit": 200, "gpu_seconds_limit": GPU_SECONDS,
                "gpu_seconds_used": spent, "gpu_seconds_reserved": reserved,
                "gpu_seconds_remaining": max(0, GPU_SECONDS - spent - reserved),
                "gpu_hours_remaining": max(0, GPU_SECONDS - spent - reserved) / 3600,
                "job_counts": counts, "scheduler_error": self.error,
                "capacity": {"cpus": self.config["cpu_limit"], "memory_gib": self.job_memory_gib,
                             "shared_memory_gib": self.config["memory_gib"],
                             "interactive_memory_gib": self.config["interactive_memory_gib"],
                             "gpus": len(self.config["gpu_devices"]),
                             "cpus_allocated": sum(s["cpus"] for s in specs),
                             "memory_gib_allocated": sum(s["memory_gib"] for s in specs),
                             "gpus_allocated": sum(s["gpus"] for s in specs),
                             "host_memory_reserve_gib": 64, "host_storage_reserve_gib": 64}}

    def docker_command(self, row):
        spec = json.loads(row["spec"])
        tenant = row["tenant_id"]
        workdir = self.workdir(tenant, spec["workdir"])
        devices = json.loads(row["devices"])
        argv = ["docker", "container", "run", "--detach", "--pull", "never",
                "--name", f"argus-compute-job-{row['id']}",
                "--label", f"{LABEL}={self.owner}", "--label", f"{LABEL}.job={row['id']}",
                "--label", f"{LABEL}.tenant={tenant}",
                "--user", f"{self.config['uid']}:{self.config['gid']}",
                "--network", "none", "--read-only", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "-1",
                "--cpus", str(spec["cpus"]), "--memory", f"{spec['memory_gib']}g",
                "--memory-swap", f"{spec['memory_gib']}g",
                "--tmpfs", "/tmp:rw,nosuid,nodev,size=1g",
                "--shm-size", f"{spec['shm_gib']}g", "--restart", "no",
                "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3",
                "--mount", f"type=bind,src={self.config['tenants'][tenant]['data_dir']},dst=/tenant",
                "--workdir", "/tenant/workspace" + ("" if workdir == "." else "/" + workdir),
                "--env", "HOME=/tenant/home",
                "--env", "NVIDIA_VISIBLE_DEVICES=" + (",".join(map(str, devices)) if devices else "void")]
        # Docker's client proxy config otherwise injects host proxy credentials.
        proxy = {}
        if "egress_socket_dir" in self.config:
            argv += ["--mount", f"type=bind,src={self.config['egress_socket_dir']},dst=/egress,readonly"]
            proxy = {"http_proxy": "http://127.0.0.1:3128", "https_proxy": "http://127.0.0.1:3128",
                     "no_proxy": "localhost,127.0.0.1,::1"}
        for variable in ("HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY", "NO_PROXY",
                         "http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "no_proxy"):
            argv += ["--env", variable + "=" + proxy.get(variable.lower(), "")]
        if devices:
            # Quotes are part of the argv value: Docker parses this flag as CSV.
            argv += ["--runtime", "nvidia", "--gpus", '"device=' + ",".join(map(str, devices)) + '"']
        if "cgroup_parent" in self.config:
            argv += ["--cgroup-parent", self.config["cgroup_parent"]]
        argv += [self.config["image"], *spec["command"]]
        return argv

    def inventory(self):
        containers = {}
        for evidence in self.executor.containers(self.owner):
            try:
                labels = evidence["Config"]["Labels"]
                job_id = int(labels[f"{LABEL}.job"])
                if (labels[LABEL] != self.owner or job_id in containers
                        or evidence["Name"] != f"/argus-compute-job-{job_id}"
                        or not re.fullmatch(r"[a-f0-9]{64}", evidence["Id"])):
                    raise ValueError("Container identity mismatch")
                state = evidence["State"]
                if (type(state["Running"]) is not bool or type(state["ExitCode"]) is not int
                        or not all(isinstance(state[key], str)
                                   for key in ("Status", "StartedAt", "FinishedAt"))):
                    raise ValueError("Invalid container state")
                containers[job_id] = evidence
            except (KeyError, ValueError, TypeError) as exc:
                raise InfrastructureError("Owned Docker inventory has ambiguous identity or state") from exc
        return containers

    def reconcile(self, containers):
        with self.transaction() as db:
            rows = db.execute("SELECT * FROM jobs").fetchall()
            by_id = {row["id"]: row for row in rows}
            if set(containers) - by_id.keys():
                raise InfrastructureError("Owned container has no ledger entry; operator reconciliation required")
            for row in rows:
                evidence = containers.get(row["id"])
                if evidence:
                    if (evidence["Config"]["Labels"].get(f"{LABEL}.tenant") != row["tenant_id"]
                            or (row["container_id"] and row["container_id"] != evidence["Id"])):
                        raise InfrastructureError("Container and ledger identity differ")
                    if row["status"] == "queued" or (
                            row["status"] in FINAL and evidence["State"]["Running"]):
                        raise InfrastructureError("Unexpected execution evidence; operator reconciliation required")
                if row["status"] not in ACTIVE:
                    continue
                if not evidence:
                    db.execute("UPDATE jobs SET status='blocked',error=? WHERE id=?",
                               ("Execution evidence missing; reservation retained", row["id"]))
                    continue
                state = evidence["State"]
                started, finished = timestamp(state["StartedAt"]), timestamp(state["FinishedAt"])
                if state["Running"]:
                    if started is None:
                        raise InfrastructureError("Running container has no start timestamp")
                    db.execute("""UPDATE jobs SET container_id=?,started_at=?,status=?,error=NULL WHERE id=?""",
                               (evidence["Id"], started, "cancelling" if row["stop_reason"] else "running", row["id"]))
                elif state["Status"] == "exited" and started is not None and finished is not None and finished >= started:
                    spec = json.loads(row["spec"])
                    charged = math.ceil((finished - started) * spec["gpus"])
                    outcome = row["stop_reason"] or ("succeeded" if state["ExitCode"] == 0 else "failed")
                    db.execute("""UPDATE jobs SET container_id=?,started_at=?,finished_at=?,status=?,
                                  charged=?,exit_code=?,error=NULL WHERE id=?""",
                               (evidence["Id"], started, finished, outcome, charged, state["ExitCode"], row["id"]))
                else:
                    db.execute("UPDATE jobs SET container_id=?,status='blocked',error=? WHERE id=?",
                               (evidence["Id"], "Ambiguous execution state; reservation retained", row["id"]))

    def cancel(self, tenant, job_id):
        with self.mutex, self.transaction() as db:
            row = self.own_job(db, tenant, job_id)
            if row["status"] == "queued":
                db.execute("UPDATE jobs SET status='cancelled',charged=0,finished_at=? WHERE id=?",
                           (self.clock(), job_id))
            elif row["status"] in ACTIVE:
                db.execute("UPDATE jobs SET stop_reason='cancelled',status='cancelling' WHERE id=? AND stop_reason IS NULL", (job_id,))
        return self.get(tenant, job_id)

    def logs(self, tenant, job_id):
        with self.mutex:
            with self.transaction() as db:
                row = self.own_job(db, tenant, job_id)
            if row["status"] == "queued" or (row["status"] == "cancelled" and row["container_id"] is None):
                return {"job_id": job_id, "logs": "", "text": "", "truncated": False}
            evidence = self.inventory().get(job_id)
            if (not evidence or evidence["Id"] != row["container_id"]
                    or evidence["Config"]["Labels"].get(f"{LABEL}.tenant") != tenant):
                raise TrialError(503, "logs_unavailable", "Verified execution logs are not available")
            text, truncated = self.executor.logs(evidence["Id"])
            return {"job_id": job_id, "logs": text, "text": text, "truncated": truncated}

    def tick(self):
        with self.mutex:
            containers = self.inventory()
            self.reconcile(containers)
            with self.transaction() as db:
                active = db.execute("SELECT * FROM jobs WHERE status IN (?,?,?,?)", ACTIVE).fetchall()
                for row in active:
                    if (row["started_at"] is not None and not row["stop_reason"]
                            and self.clock() >= row["started_at"] + json.loads(row["spec"])["timeout_seconds"]):
                        db.execute("UPDATE jobs SET stop_reason='timed_out',status='cancelling' WHERE id=?", (row["id"],))
                stopping = db.execute("SELECT * FROM jobs WHERE status='cancelling'").fetchall()
            for row in stopping:
                evidence = containers.get(row["id"])
                if evidence and evidence["State"]["Running"] and evidence["Id"] == row["container_id"]:
                    self.executor.kill(evidence["Id"])
            if stopping:
                self.reconcile(self.inventory())
            self.dispatch()
            self.error = None

    def dispatch(self):
        with self.transaction() as db:
            active = db.execute("SELECT * FROM jobs WHERE status IN (?,?,?,?)", ACTIVE).fetchall()
            pending = db.execute("""SELECT jobs.* FROM jobs JOIN tenants USING(tenant_id)
                WHERE status='queued' ORDER BY tenants.last_dispatch, jobs.id""").fetchall()
            queued = []
            for row in pending:
                if json.loads(row["spec"])["memory_gib"] > self.job_memory_gib:
                    db.execute(
                        "UPDATE jobs SET status='failed',charged=0,error=?,finished_at=? WHERE id=?",
                        ("Requested memory exceeds the current compute pool", self.clock(), row["id"]),
                    )
                else:
                    queued.append(row)
        if not queued:
            return
        busy_tenants = {row["tenant_id"] for row in active}
        allocated = [json.loads(row["spec"]) for row in active]
        cpus = self.config["cpu_limit"] - sum(s["cpus"] for s in allocated)
        memory = self.job_memory_gib - sum(s["memory_gib"] for s in allocated)
        assigned = {d for row in active for d in json.loads(row["devices"])}
        gpu_candidates = any(json.loads(row["spec"])["gpus"] for row in queued if row["tenant_id"] not in busy_tenants)
        probe_error = None
        try:
            busy = self.executor.busy_gpus(self.config["gpu_devices"]) if gpu_candidates else set()
        except InfrastructureError as exc:
            # CPU-only jobs remain eligible; GPU jobs fail closed and report why.
            busy, probe_error = set(self.config["gpu_devices"]), exc
        free = [d for d in self.config["gpu_devices"] if d not in busy | assigned]
        # Reserve headroom for active jobs which have not faulted in their full
        # limits yet. This is deliberately conservative about their resident RAM.
        available = self.executor.available_memory() // GIB - 64 - sum(s["memory_gib"] for s in allocated)
        for row in queued:
            spec = json.loads(row["spec"])
            if (row["tenant_id"] in busy_tenants or spec["cpus"] > cpus
                    or spec["memory_gib"] > min(memory, available) or spec["gpus"] > len(free)):
                continue
            if self.executor.available_storage(self.config["state_dir"]) < 64 * GIB:
                raise InfrastructureError("Host physical storage below 64 GiB reserve; queued work retained")
            devices = free[:spec["gpus"]]
            # Validate the mutable tenant directory before committing a start.
            try:
                proposed = dict(row)
                proposed["devices"] = json.dumps(devices)
                argv = self.docker_command(proposed)
            except TrialError as exc:
                with self.transaction() as db:
                    db.execute("UPDATE jobs SET status='failed',charged=0,error=?,finished_at=? WHERE id=?",
                               (str(exc), self.clock(), row["id"]))
                continue
            with self.transaction() as db:
                db.execute("UPDATE jobs SET status='starting',devices=? WHERE id=? AND status='queued'",
                           (json.dumps(devices), row["id"]))
                db.execute("UPDATE tenants SET last_dispatch=(SELECT coalesce(max(last_dispatch),0)+1 FROM tenants) WHERE tenant_id=?",
                           (row["tenant_id"],))
            # Never retry this invocation: even a CLI timeout can have started it.
            self.executor.launch(argv)
            self.reconcile(self.inventory())
            busy_tenants.add(row["tenant_id"])
            cpus -= spec["cpus"]
            memory -= spec["memory_gib"]
            available -= spec["memory_gib"]
            free = free[spec["gpus"]:]
        if probe_error:
            raise probe_error


def create_app(config, *, executor=None, clock=time.time, poll_interval=2):
    service = ComputeService(config, executor=executor, clock=clock)

    def poll():
        try:
            service.tick()
        except (InfrastructureError, sqlite3.Error, OSError) as exc:
            service.error = str(exc)
            LOG.error("Compute scheduling failed: %s", exc)

    @asynccontextmanager
    async def lifespan(app):
        service.open()
        task = None
        try:
            await asyncio.to_thread(poll)

            async def loop():
                while True:
                    await asyncio.sleep(poll_interval)
                    await asyncio.to_thread(poll)

            if poll_interval is not None:
                task = asyncio.create_task(loop())
            yield
        finally:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            # Wait for any in-flight synchronous poll before releasing ownership.
            def close_after_poll():
                with service.mutex:
                    service.close()

            await asyncio.to_thread(close_after_poll)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.compute = service

    @app.exception_handler(TrialError)
    async def trial_error(request, exc):
        return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)

    @app.exception_handler(InfrastructureError)
    @app.exception_handler(sqlite3.Error)
    @app.exception_handler(OSError)
    async def infrastructure_error(request, exc):
        LOG.error("Compute API infrastructure failure: %s", exc)
        return JSONResponse({"error": {"code": "compute_unavailable", "message": "Compute infrastructure unavailable"}},
                            status_code=503)

    def authenticate(request: Request):
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer ") or len(authorization) > 512:
            raise TrialError(401, "invalid_trial_key", "A Bearer invitation credential is required")
        tenant = service.auth.authenticate(authorization[7:])
        if tenant not in service.config["tenants"]:
            raise TrialError(403, "compute_not_enabled", "Compute is not enabled for this invitation")
        return tenant

    def writable(request: Request, tenant=Depends(authenticate)):
        # The portal strips browser input and supplies this kiosk-policy header.
        # Direct authenticated runtime clients omit it.
        if request.headers.get("x-argus-readonly", "false").lower() != "false":
            raise TrialError(403, "readonly_session", "This session cannot submit or cancel compute jobs")
        return tenant

    @app.get("/compute/status")
    def status(tenant=Depends(authenticate)):
        return service.status(tenant)

    @app.post("/compute/jobs", status_code=202)
    async def submit(request: Request, tenant=Depends(writable)):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise TrialError(413, "request_too_large", "Job request exceeds 65536 bytes")
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrialError(400, "invalid_json", "A JSON job object is required") from exc
        return {"job": await asyncio.to_thread(service.submit, tenant, payload)}

    @app.get("/compute/jobs")
    def jobs(limit: int = 100, offset: int = 0, tenant=Depends(authenticate)):
        if not 1 <= limit <= 100 or offset < 0:
            raise TrialError(400, "invalid_page", "limit must be 1..100 and offset nonnegative")
        return {"jobs": service.jobs(tenant, limit, offset)}

    @app.get("/compute/jobs/{job_id}")
    def job(job_id: int, tenant=Depends(authenticate)):
        return {"job": service.get(tenant, job_id)}

    @app.get("/compute/jobs/{job_id}/logs")
    def logs(job_id: int, tenant=Depends(authenticate)):
        return service.logs(tenant, job_id)

    @app.post("/compute/jobs/{job_id}/cancel", status_code=202)
    def cancel(job_id: int, tenant=Depends(writable)):
        return {"job": service.cancel(tenant, job_id)}

    return app


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--uds", type=Path, required=True)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    if not args.uds.is_absolute():
        parser.error("--uds must be an absolute socket path")
    import uvicorn

    uvicorn.run(create_app(config), uds=str(args.uds), access_log=False, proxy_headers=False)


if __name__ == "__main__":
    main()
