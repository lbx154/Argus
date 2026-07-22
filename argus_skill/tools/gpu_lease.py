"""GPU lease helper — coordinate with an operator "keep-alive" loader.

On managed boxes the scheduler reclaims GPUs that sit idle too long, so the
operator runs a small *keep-alive* loader (e.g. ``gpu_load.py``) that holds the
cards at a low duty cycle. That keep-alive must step aside before the agent
runs real training/inference and step back in afterwards, so the cards are
never reclaimed during quiet periods.

The hard part for an AUTONOMOUS agent is correctness under concurrency and
detached jobs: a naive "stop the process / restart the process" toggle re-parks
on top of a still-running job, races between missions, and loses track of who
should restore the keep-alive after a mission ends. So this tool manages a
**lease**, not just a process:

* ``run [--detach] -- <cmd>`` — the recommended path. Frees the cards, records
  a lease for the job's lifetime, runs the command, and on exit releases the
  lease and re-parks the keep-alive **iff no other lease is active**. With
  ``--detach`` a supervisor owns the lease independently of the agent, so the
  keep-alive is restored even if the mission ends first.
* ``claim`` — manually free the cards (stop keep-alive, wait for VRAM).
* ``park``  — manually re-hold the cards; refuses while a job lease is active.
* ``status`` — keep-alive state, active leases, per-GPU snapshot.
* ``watchdog`` — re-park once the cards are unheld, lease-free AND idle for a
  grace period (a safety net, never authoritative over an active lease).

Keep-alive argv/cwd/match-token live in
``~/.argus-skill/capabilities/gpu_keepalive.json`` so the tool restarts the
*exact* program the operator started. Killing only ever targets processes whose
command line contains the configured match token (plus their descendants),
never the caller or its ancestors.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Iterable, Iterator

from ..core.file_lock import exclusive_file_lock
from ..core.paths import capabilities_root, logs_root, resolve_runtime_path, run_root

# -- config -----------------------------------------------------------------

def _config_path() -> Path:
    env = os.environ.get("ARGUS_SKILL_GPU_KEEPALIVE_CONFIG")
    if env:
        return resolve_runtime_path(env, context="ARGUS_SKILL_GPU_KEEPALIVE_CONFIG")
    return capabilities_root() / "gpu_keepalive.json"


def _state_dir() -> Path:
    env = os.environ.get("ARGUS_SKILL_GPU_LEASE_STATE_DIR")
    base = (
        resolve_runtime_path(env, context="ARGUS_SKILL_GPU_LEASE_STATE_DIR")
        if env
        else run_root() / "gpu_lease"
    )
    (base / "leases").mkdir(parents=True, exist_ok=True)
    return base


def _default_config() -> dict:
    return {
        "command": ["python", "gpu_load.py", "--util", "0.5", "--mem", "0.5"],
        "cwd": str(Path.home()),
        "match": "gpu_load.py",
        "log": str(logs_root() / "gpu_keepalive.log"),
    }


def load_config() -> dict:
    """Load keep-alive config, falling back to sane defaults if absent."""
    defaults = _default_config()
    cfg = dict(defaults)
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            cfg.update({k: v for k, v in data.items() if v is not None})
    except (OSError, ValueError):
        pass
    if not cfg.get("match"):
        cfg["match"] = defaults["match"]
    if not cfg.get("command"):
        cfg["command"] = list(defaults["command"])
    return cfg


# -- cross-process locking --------------------------------------------------

@contextlib.contextmanager
def _lock() -> Iterator[None]:
    """Hold an exclusive flock so claim/park/lease mutations are serialized
    across concurrent missions and subagents."""
    lock_path = _state_dir() / "lock"
    fh = open(lock_path, "a+b")
    try:
        with exclusive_file_lock(fh):
            yield
    finally:
        fh.close()


# -- process discovery ------------------------------------------------------

def _read_proc_cmdlines() -> list[tuple[int, str]]:
    """Return (pid, cmdline) for every readable process via /proc."""
    out: list[tuple[int, str]] = []
    try:
        entries = [e for e in Path("/proc").iterdir() if e.name.isdigit()]
    except OSError:
        return out
    for entry in entries:
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        cmd = raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
        if cmd:
            out.append((int(entry.name), cmd))
    return out


def find_pids_in(cmdlines: Iterable[tuple[int, str]], match: str,
                 *, self_pid: int | None = None) -> list[int]:
    """Pure: pids whose cmdline contains ``match`` (excluding ``self_pid``)."""
    self_pid = os.getpid() if self_pid is None else self_pid
    return [pid for pid, cmd in cmdlines if match in cmd and pid != self_pid]


def find_keepalive_pids(match: str) -> list[int]:
    """Live pids of keep-alive launcher processes matching ``match``."""
    return find_pids_in(_read_proc_cmdlines(), match)


def _read_ppids() -> dict[int, int]:
    """Map pid -> ppid for every readable process via /proc/<pid>/stat."""
    out: dict[int, int] = {}
    try:
        entries = [e for e in Path("/proc").iterdir() if e.name.isdigit()]
    except OSError:
        return out
    for entry in entries:
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rparen = stat.rfind(")")  # comm may contain spaces/parens
        if rparen == -1:
            continue
        rest = stat[rparen + 2:].split()
        if len(rest) >= 2:
            with contextlib.suppress(ValueError):
                out[int(entry.name)] = int(rest[1])
    return out


def collect_descendants(roots: list[int], ppids: dict[int, int]) -> list[int]:
    """All descendant pids of ``roots`` (children, grandchildren, ...)."""
    children: dict[int, list[int]] = {}
    for pid, ppid in ppids.items():
        children.setdefault(ppid, []).append(pid)
    seen: set[int] = set()
    stack = list(roots)
    while stack:
        pid = stack.pop()
        for child in children.get(pid, []):
            if child not in seen:
                seen.add(child)
                stack.append(child)
    return sorted(seen)


def _ancestors(pid: int, ppids: dict[int, int]) -> set[int]:
    """Ancestor pids of ``pid`` (parent, grandparent, ...), guarding cycles."""
    out: set[int] = set()
    cur = ppids.get(pid)
    while cur and cur not in out and cur > 1:
        out.add(cur)
        cur = ppids.get(cur)
    return out


def keepalive_tree(match: str) -> list[int]:
    """Matched keep-alive pids PLUS their descendant workers.

    The launcher matches ``match`` in its cmdline, but the per-GPU workers it
    spawns do not (their cmdline is the multiprocessing bootstrap), so
    signalling only the matched launcher would orphan workers that keep holding
    the cards. Signal the whole tree — but NEVER the caller or its ancestors.
    """
    roots = find_keepalive_pids(match)
    if not roots:
        return []
    ppids = _read_ppids()
    tree = set(roots) | set(collect_descendants(roots, ppids))
    self_pid = os.getpid()
    tree -= {self_pid}
    tree -= _ancestors(self_pid, ppids)
    return sorted(tree)


def _alive(pid: int) -> bool:
    """True if ``pid`` exists and is signalable (not reaped/zombie)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# -- GPU snapshot (best effort) --------------------------------------------

def gpu_snapshot() -> list[dict]:
    """Per-GPU (index, mem_used_mib, mem_total_mib, util_pct) via nvidia-smi.

    Returns ``[]`` if nvidia-smi is unavailable so callers degrade gracefully.
    """
    try:
        res = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=index,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    gpus: list[dict] = []
    for line in res.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 4:
            continue
        try:
            gpus.append({
                "index": int(parts[0]),
                "mem_used_mib": int(parts[1]),
                "mem_total_mib": int(parts[2]),
                "util_pct": int(parts[3]),
            })
        except ValueError:
            continue
    return gpus


def gpus_idle(snapshot: list[dict], *, util_pct_max: int = 5,
              mem_used_mib_max: int = 2048) -> bool:
    """True when every GPU is below the util and memory thresholds."""
    if not snapshot:
        return False
    return all(
        g["util_pct"] <= util_pct_max and g["mem_used_mib"] <= mem_used_mib_max
        for g in snapshot
    )


# -- lease registry ---------------------------------------------------------
# A lease is a JSON file under <state>/leases/<id>.json describing one owner of
# the GPUs. The keep-alive must stay DOWN while any lease is active. A lease is
# active iff its file exists and, when it records a supervisor ``pid``, that pid
# is alive; a ``ttl`` bounds bare (pid-less) leases. park/watchdog refuse to
# re-hold the cards while any lease is active, so they can never clobber a job.

def _leases_dir() -> Path:
    return _state_dir() / "leases"


def _lease_active(meta: dict) -> bool:
    pid = meta.get("pid")
    if pid is not None:
        return _alive(int(pid))
    expires = meta.get("expires_at")
    if expires is not None:
        return time.time() < float(expires)
    return True  # pid-less, ttl-less lease stays active until explicitly released


def active_leases() -> list[dict]:
    """Return active lease metadata, pruning stale lease files as a side effect."""
    out: list[dict] = []
    for path in _leases_dir().glob("*.json"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            with contextlib.suppress(OSError):
                path.unlink()
            continue
        if _lease_active(meta):
            out.append(meta)
        else:
            with contextlib.suppress(OSError):
                path.unlink()  # supervisor died / ttl expired -> reap
    return out


def _write_lease(lease_id: str, owner: str, pid: int | None,
                 ttl: float | None, gpus: str) -> dict:
    meta = {
        "id": lease_id,
        "owner": owner,
        "pid": pid,
        "gpus": gpus,
        "created_at": time.time(),
        "ttl_seconds": ttl,
        "expires_at": (time.time() + ttl) if ttl is not None else None,
    }
    path = _leases_dir() / f"{lease_id}.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta), encoding="utf-8")
    os.replace(tmp, path)
    return meta


def _remove_lease(lease_id: str) -> None:
    with contextlib.suppress(OSError):
        (_leases_dir() / f"{lease_id}.json").unlink()


# -- low-level actions ------------------------------------------------------

def _signal_pids(pids: list[int], sig: int) -> None:
    for pid in pids:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)


def _stop_keepalive(match: str, *, timeout: float) -> dict:
    """SIGTERM (then SIGKILL) the keep-alive tree. Returns freed-pid info."""
    tree = keepalive_tree(match)
    if not tree:
        return {"freed": [], "already_free": True}
    _signal_pids(tree, signal.SIGTERM)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not keepalive_tree(match):
            return {"freed": tree, "already_free": False}
        time.sleep(0.5)
    _signal_pids(
        keepalive_tree(match),
        getattr(signal, "SIGKILL", signal.SIGTERM),
    )
    time.sleep(1.0)
    return {"freed": tree, "already_free": False,
            "leftover": keepalive_tree(match)}


def _wait_vram_released(match: str, *, timeout: float = 30.0) -> list[dict]:
    """Wait until the keep-alive tree is gone and report a GPU snapshot.

    Driver cleanup can lag a killed CUDA process, so we settle briefly. We do
    NOT require globally-zero VRAM (other users' jobs may legitimately hold
    memory) — only that our keep-alive is gone.
    """
    deadline = time.time() + timeout
    while time.time() < deadline and keepalive_tree(match):
        time.sleep(0.5)
    time.sleep(1.0)
    return gpu_snapshot()


def claim(cfg: dict, *, timeout: float = 20.0) -> dict:
    """Stop the keep-alive so the GPUs are free, and wait for VRAM release.

    Idempotent and lock-serialized. Low-level: prefer ``run`` for real jobs so
    the keep-alive is restored automatically.
    """
    match = cfg["match"]
    with _lock():
        res = _stop_keepalive(match, timeout=timeout)
    res["gpus"] = _wait_vram_released(match)
    res["leftover"] = keepalive_tree(match)
    res["freed_ok"] = not res["leftover"]
    return res


def _start_keepalive(cfg: dict) -> dict:
    command = [str(part) for part in cfg["command"]]
    cwd = str(cfg.get("cwd") or Path.home())
    log_path = Path(str(cfg.get("log") or _default_config()["log"])).expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log_fh:
        proc = subprocess.Popen(
            command, cwd=cwd, stdout=log_fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=os.name != "nt",
        )
    # Record what we started for auditing / preferred discovery.
    with contextlib.suppress(OSError):
        (_state_dir() / "keepalive.pid").write_text(
            json.dumps({"pid": proc.pid, "argv": command, "cwd": cwd,
                        "started_at": time.time()}),
            encoding="utf-8",
        )
    time.sleep(2.0)  # let it spawn per-GPU workers before reporting
    return {"started": True, "pid": proc.pid,
            "pids": find_keepalive_pids(cfg["match"]), "log": str(log_path)}


def park(cfg: dict, *, force: bool = False) -> dict:
    """(Re)start the keep-alive to hold the cards. Idempotent.

    REFUSES while any job lease is active (so it cannot clobber a running job),
    unless ``force``. A match is only "already running" if genuinely alive, so a
    zombie/transient match can never leave the cards unprotected.
    """
    match = cfg["match"]
    with _lock():
        leases = active_leases()
        if leases and not force:
            return {"started": False, "pid": None, "already_running": False,
                    "refused": True,
                    "reason": "active GPU lease(s) hold the cards free",
                    "active_leases": [m["id"] for m in leases]}
        existing = [p for p in find_keepalive_pids(match) if _alive(p)]
        if existing:
            return {"started": False, "pid": None, "already_running": True,
                    "pids": existing}
        return _start_keepalive(cfg)


def status(cfg: dict) -> dict:
    match = cfg["match"]
    pids = find_keepalive_pids(match)
    leases = active_leases()
    snap = gpu_snapshot()
    return {
        "keepalive_running": bool(pids),
        "keepalive_pids": pids,
        "keepalive_tree": keepalive_tree(match),
        "active_leases": leases,
        "gpus": snap,
        "gpus_idle": gpus_idle(snap) if not pids and not leases else False,
    }


# -- lease lifecycle (run / supervise) -------------------------------------

def _acquire(cfg: dict, owner: str, pid: int | None, ttl: float | None,
             gpus: str) -> dict:
    """Stop the keep-alive and register a lease (lock-serialized)."""
    lease_id = uuid.uuid4().hex[:12]
    with _lock():
        _stop_keepalive(cfg["match"], timeout=20.0)
        meta = _write_lease(lease_id, owner, pid, ttl, gpus)
    _wait_vram_released(cfg["match"])
    return meta


def _release(cfg: dict, lease_id: str) -> dict:
    """Remove a lease; re-park the keep-alive iff no lease remains."""
    with _lock():
        _remove_lease(lease_id)
        remaining = active_leases()
        if remaining:
            return {"released": lease_id, "parked": False,
                    "active_leases": [m["id"] for m in remaining]}
        existing = [p for p in find_keepalive_pids(cfg["match"]) if _alive(p)]
        if existing:
            return {"released": lease_id, "parked": False,
                    "already_running": True}
        res = _start_keepalive(cfg)
        return {"released": lease_id, "parked": True, **res}


#: Bootstrap TTL for the detached-run placeholder lease — long enough to cover a
#: cold ``import argus_skill`` in the supervisor on a loaded pod, short enough that
#: a supervisor that never cold-starts can't leak a phantom lease holding the cards.
_DETACH_BOOTSTRAP_TTL_S = 120.0


def run(cfg: dict, command: list[str], *, detach: bool, owner: str,
        gpus: str, ttl: float | None) -> dict:
    """Free the cards, run ``command`` under a lease, restore on completion.

    ``--detach`` spawns a supervisor that owns the lease and re-parks the
    keep-alive when the job ends — even if the launching mission has ended.
    """
    if detach:
        lease_id = uuid.uuid4().hex[:12]
        # Stop the keep-alive AND write the lease under the SAME lock (mirroring
        # _acquire), so there is never a window where the cards are freed but no
        # lease exists — a concurrent park/_release would otherwise re-park the
        # keep-alive on top of the just-launched job. A short bootstrap TTL bounds
        # this pid-less placeholder so a supervisor that never cold-starts can't
        # leak a phantom lease; _supervise adopts the same lease_id with its own
        # pid + the job's real ttl below.
        with _lock():
            _stop_keepalive(cfg["match"], timeout=20.0)
            _write_lease(lease_id, owner, None, _DETACH_BOOTSTRAP_TTL_S, gpus)
        _wait_vram_released(cfg["match"])
        log_path = _state_dir() / f"job-{lease_id}.log"
        sup_cmd = [
            os.environ.get("ARGUS_SKILL_PYTHON") or "python",
            "-m", "argus_skill.tools.gpu_lease", "_supervise",
            "--lease", lease_id, "--owner", owner, "--gpus", gpus,
        ]
        if ttl is not None:
            sup_cmd += ["--ttl", str(ttl)]
        sup_cmd += ["--", *command]
        with open(log_path, "ab") as log_fh:
            proc = subprocess.Popen(
                sup_cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=os.name != "nt",
            )
        return {"detached": True, "lease": lease_id, "supervisor_pid": proc.pid,
                "log": str(log_path), "command": command}

    # Inline: own the lease in this process; always release on exit.
    meta = _acquire(cfg, owner, os.getpid(), ttl, gpus)
    try:
        rc = subprocess.call(command)
        return {"detached": False, "lease": meta["id"], "returncode": rc,
                **_release(cfg, meta["id"])}
    finally:
        _release(cfg, meta["id"])


def _supervise(cfg: dict, lease_id: str, owner: str, gpus: str,
               ttl: float | None, command: list[str]) -> int:
    """Detached supervisor: write lease, run child, release + maybe re-park."""
    with _lock():
        _write_lease(lease_id, owner, os.getpid(), ttl, gpus)
    rc = 1
    try:
        rc = subprocess.call(command)
    finally:
        res = _release(cfg, lease_id)
        print(f"gpu_lease supervisor: job {lease_id} exit={rc} {res}",
              flush=True)
    return rc


def watchdog(cfg: dict, *, idle_seconds: float = 600.0,
             poll_seconds: float = 30.0,
             max_seconds: float | None = None) -> int:
    """Safety net: re-park the keep-alive once the cards are unheld, LEASE-FREE
    and idle for a grace period. Never parks while a lease is active, so it
    cannot clobber a running job."""
    match = cfg["match"]
    started_at = time.time()
    idle_since: float | None = None
    while True:
        if max_seconds is not None and time.time() - started_at >= max_seconds:
            return 0
        if active_leases() or find_keepalive_pids(match):
            idle_since = None  # a lease holds it free, or it's already parked
        elif gpus_idle(gpu_snapshot()):
            if idle_since is None:
                idle_since = time.time()
            elif time.time() - idle_since >= idle_seconds:
                res = park(cfg)
                print(f"gpu_lease watchdog: re-parked keep-alive {res}",
                      flush=True)
                idle_since = None
        else:
            idle_since = None  # real work running; leave cards free
        time.sleep(poll_seconds)


def _print(obj: dict) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.tools.gpu_lease",
        description="Coordinate the GPU keep-alive: free cards for real work, "
                    "re-park them when idle.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="keep-alive + leases + GPU snapshot")
    p_claim = sub.add_parser("claim", help="stop keep-alive; free the cards")
    p_claim.add_argument("--timeout", type=float, default=20.0)
    p_park = sub.add_parser("park", help="(re)start keep-alive to hold cards")
    p_park.add_argument("--force", action="store_true",
                        help="park even if a job lease is active (dangerous)")
    p_run = sub.add_parser(
        "run", help="run a GPU command under a lease; auto re-park on exit")
    p_run.add_argument("--detach", action="store_true",
                       help="supervise in the background (survives mission end)")
    p_run.add_argument("--owner", default="agent")
    p_run.add_argument("--gpus", default="")
    p_run.add_argument("--ttl", type=float, default=None)
    p_run.add_argument("command", nargs=argparse.REMAINDER,
                       help="-- <command ...>")
    p_wd = sub.add_parser(
        "watchdog", help="auto-park after cards idle+lease-free for a grace gap")
    p_wd.add_argument("--idle-seconds", type=float, default=600.0)
    p_wd.add_argument("--poll-seconds", type=float, default=30.0)
    p_wd.add_argument("--max-seconds", type=float, default=None)
    p_sup = sub.add_parser("_supervise", help=argparse.SUPPRESS)
    p_sup.add_argument("--lease", required=True)
    p_sup.add_argument("--owner", default="agent")
    p_sup.add_argument("--gpus", default="")
    p_sup.add_argument("--ttl", type=float, default=None)
    p_sup.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    cfg = load_config()
    if args.cmd == "status":
        _print(status(cfg))
        return 0
    if args.cmd == "claim":
        _print(claim(cfg, timeout=args.timeout))
        return 0
    if args.cmd == "park":
        _print(park(cfg, force=args.force))
        return 0
    if args.cmd in ("run", "_supervise"):
        command = list(args.command)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            parser.error("run/_supervise requires: -- <command ...>")
        if args.cmd == "run":
            _print(run(cfg, command, detach=args.detach, owner=args.owner,
                       gpus=args.gpus, ttl=args.ttl))
            return 0
        return _supervise(cfg, args.lease, args.owner, args.gpus,
                          args.ttl, command)
    if args.cmd == "watchdog":
        return watchdog(cfg, idle_seconds=args.idle_seconds,
                        poll_seconds=args.poll_seconds,
                        max_seconds=args.max_seconds)
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
