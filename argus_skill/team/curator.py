"""Daemon-resident Curator: the persistent owner of the teammate pool.

The Curator is a managed component/thread *inside* the daemon process. It
replaces the old detached ``nohup coordinator``: it keeps N teammates in flight,
is the single reaper, and (M2) maintains the leaderboard. Because it is
daemon-resident and persistent it can never be orphaned by a finished lead
mission — which is what makes the "control the teammate lifecycle" problem
disappear by construction.

Ownership model (load-bearing): the Curator is a *thread* of the daemon, so it
shares the daemon's process group. Teammates are therefore launched as their
OWN session leaders (``start_new_session=True``) and the Curator owns each one by
**retaining its ``Popen`` handle** — reaping via ``proc.poll()`` and killing via
*per-child* ``killpg(os.getpgid(pid), …)``. A shared process group would let a
stop kill the daemon itself.
"""
from __future__ import annotations

import contextlib
import logging
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import leaderboard, pool, registry, roster, task_board

log = logging.getLogger(__name__)

_CURATOR_FALLBACK = (
    "You are the team Curator. You do NOT engineer. From the leaderboard below, "
    "for the stalled / weakest targets name the single highest-expected-value "
    "next move per target — either carry the leading approach further toward "
    "completion, or try a genuinely different one (never an approach already "
    "tried) — and say which targets to prioritize. Judge each target by its "
    "recorded outcome; a target with no recorded outcome is unproven, not good. "
    "Output a concise prioritized strategy.md."
)


class TrackedTeammate:
    """A teammate process the Curator owns by holding its ``Popen`` handle."""

    def __init__(self, proc: Any, *, member_id: str, task_id: str, root: Path,
                 started_at: float, timeout_s: float, hard_grace_s: float) -> None:
        self.proc = proc
        self.member_id = member_id
        self.task_id = task_id
        self.root = Path(root)
        self.started_at = started_at
        self.timeout_s = timeout_s
        self.hard_grace_s = hard_grace_s

    @property
    def pid(self) -> int:
        return int(self.proc.pid)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def hard_deadline(self) -> float:
        return self.started_at + self.timeout_s + self.hard_grace_s


class Curator:
    """Keeps N teammates in flight per active campaign and reaps them.

    ``make_proc`` is the only injection seam (tests pass a fake-process factory);
    by default it launches the real headless ``teammate_entry`` (or ``exec_cmd``
    when given, e.g. a stub for tests/E2E). ``now_fn`` is injected so the reaper's
    deadlines are testable without sleeping.
    """

    def __init__(self, *, project_root: Path, default_width: int = 8,
                 tick_s: float = 5.0, teammate_timeout_s: float = 5400.0,
                 hard_grace_s: float = 600.0, exec_cmd: str = "",
                 now_fn: Callable[[], float] = time.time,
                 make_proc: Callable[..., Any] | None = None,
                 distill_fn: Callable[[str], str] | None = None,
                 distill_interval_s: float = 1260.0) -> None:
        self.project_root = Path(project_root)
        self.default_width = int(default_width)
        self.tick_s = float(tick_s)
        self.teammate_timeout_s = float(teammate_timeout_s)
        self.hard_grace_s = float(hard_grace_s)
        self._exec_cmd = exec_cmd
        self._now = now_fn
        self._make_proc = make_proc or self._default_make_proc
        self._distill_fn = distill_fn
        self.distill_interval_s = float(distill_interval_s)
        self._children: dict[str, TrackedTeammate] = {}
        self._fold_mtime: dict[str, float] = {}  # per-root shards mtime at last fold
        self._distill_at: dict[str, float] = {}  # per-root wall-clock of last distill
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- spawning a tracked child --------------------------------------
    def _default_make_proc(self, root: Path, member_id: str, task_id: str,
                           cwd: Path) -> Any:
        log_dir = Path(root) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / (member_id.replace(":", "_") + ".spawn.log")
        if self._exec_cmd:
            argv = shlex.split(self._exec_cmd)
        else:
            argv = [sys.executable, "-m", "argus_skill.team.teammate_entry",
                    "--root", str(root), "--member-id", member_id,
                    "--task-id", task_id, "--cwd", str(cwd)]
        log = open(log_path, "ab")
        devnull = open(os.devnull, "rb")
        # OWN session (own pgroup) — the Curator owns it via the retained handle,
        # NOT via a shared process group (which would be the daemon's).
        return subprocess.Popen(argv, cwd=str(cwd), stdin=devnull, stdout=log,
                                stderr=log, start_new_session=True)

    def _spawn_tracked(self, root: Path, *, member_id: str, task_id: str,
                       cwd: Path, now: float | None = None) -> int:
        proc = self._make_proc(Path(root), member_id, task_id, Path(cwd))
        self._children[member_id] = TrackedTeammate(
            proc, member_id=member_id, task_id=task_id, root=Path(root),
            started_at=(self._now() if now is None else now),
            timeout_s=self.teammate_timeout_s, hard_grace_s=self.hard_grace_s)
        roster.add_member(Path(root), {
            "id": member_id, "pid": proc.pid, "worktree": str(cwd),
            "task_id": task_id, "status": "running",
        })
        return int(proc.pid)

    def live_owner_ids(self, root: Path) -> set[str]:
        """Member ids whose tracked child is genuinely alive, for ``root``.

        Exact and free now that we own the handle: ``proc.poll() is None`` — no
        ``/proc`` cmdline archaeology, no PID-recycle false positives (BUG-5/6).
        """
        root = Path(root)
        return {mid for mid, tt in self._children.items()
                if tt.root == root and tt.alive()}

    # ---- refill: keep ``width`` teammates in flight from the backlog ----
    def _refill(self, root: Path, *, width: int, cwd: Path,
                now: float | None = None, ttl: float = 180.0) -> dict[str, Any]:
        """Top the in-flight count back up to ``width`` from the priority backlog.

        Hand stale-owned tasks back ONLY when their owner is not a live child,
        then claim the top-priority pending tasks and spawn a fresh teammate on
        each until the pool is full or the backlog dries. Occupancy is
        ``max(board in_flight, live children)`` so a just-spawned teammate that
        has not heartbeat'd yet, or a dead child whose task is still ``running``,
        can never be mistaken for a free slot (no over-spawn herd).
        """
        root = Path(root)
        now = self._now() if now is None else now
        live = self.live_owner_ids(root)
        reassigned = task_board.reassign_stale(root, ttl=ttl, now=now, live_owners=live)
        in_flight = task_board.count_in_flight(root)
        occupied = max(in_flight, len(live))
        free = max(0, int(width) - occupied)
        cap = int(os.environ.get("ARGUS_TEAM_MAX_SPAWN_PER_REFILL", "0") or 0)
        if cap > 0:
            free = min(free, cap)
        spawned: list[dict[str, Any]] = []
        for _ in range(free):
            mid = roster.next_member_id(root)
            task = task_board.claim_top(root, mid, now=now)
            if task is None:
                break  # backlog empty
            self._spawn_tracked(root, member_id=mid, task_id=task["task_id"],
                                cwd=cwd, now=now)
            spawned.append({"member_id": mid, "task_id": task["task_id"]})
        return {"spawned": spawned, "in_flight": in_flight, "live": len(live),
                "occupied": occupied, "free": free, "reassigned": reassigned}

    # ---- reaping --------------------------------------------------------
    def _terminate(self, tt: TrackedTeammate, *, grace: float = 2.0) -> None:
        """Kill one tracked child's process group (SIGTERM → grace → SIGKILL)."""
        proc = tt.proc
        if proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            return
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(OSError, ProcessLookupError):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=5)

    def _reap(self, now: float | None = None) -> dict[str, list[str]]:
        """Drop children that exited on their own; hard-kill+free those past the
        wall-clock deadline.

        An exited child already wrote its shard and marked its task done/failed
        (``teammate_entry``), so we just forget it. A child still alive past
        ``hard_deadline`` is wedged (e.g. stuck in a slow scoring call): we
        ``killpg`` it AND immediately ``task_board.fail`` its task, because the
        kill bypasses the teammate's own bookkeeping — otherwise the task would
        sit ``running`` until the stale-ttl (BUG-3: lost shard / dark slot).
        """
        now = self._now() if now is None else now
        dropped: list[str] = []
        hard_killed: list[str] = []
        for mid, tt in list(self._children.items()):
            if not tt.alive():
                del self._children[mid]
                dropped.append(mid)
                continue
            if now >= tt.hard_deadline():
                self._terminate(tt)
                with contextlib.suppress(Exception):
                    task_board.fail(tt.root, tt.task_id, reason="curator hard-timeout")
                del self._children[mid]
                hard_killed.append(mid)
        return {"dropped": dropped, "hard_killed": hard_killed}

    # ---- the resident loop ---------------------------------------------
    def _tick(self, now: float | None = None) -> None:
        """One maintenance pass: reap finished/wedged children, then for every
        active campaign keep the pool at its width (or wind a draining one down).

        Discovery is centralised over the registry markers, so there is exactly
        ONE place that manages every root — no per-lead-mission coordinator can
        spawn a second manager (the duplicate-coordinator leak is impossible).
        """
        now = self._now() if now is None else now
        self._reap(now=now)
        for marker in registry.list_markers(self.project_root):
            root = Path(marker["team_root"])
            cwd = Path(marker.get("cwd") or root)
            self._maybe_fold(root)
            doc = pool.read(root)
            state = doc.get("state", "running")
            if state in ("draining", "dissolved"):
                # Stop refilling and let in-flight teammates finish; the
                # hard-deadline reaper still cleans wedged ones. Remove the
                # marker once the campaign is genuinely empty.
                if task_board.count_in_flight(root) == 0 and not self.live_owner_ids(root):
                    registry.remove_marker(self.project_root, marker["team_id"])
                continue
            self._maybe_distill(root, now)
            width = int(doc["width"]) if "width" in doc else self.default_width
            self._refill(root, width=width, cwd=cwd, now=now)

    def _maybe_fold(self, root: Path) -> None:
        """Deterministically re-fold the leaderboard when shards have changed.

        Pure code (no LLM) — the high-frequency half of the curator design. The
        shards-dir mtime guards against re-reading thousands of shards every tick
        on a large campaign; we only fold when a new shard has landed."""
        sd = Path(root) / "shards"
        try:
            mtime = sd.stat().st_mtime if sd.exists() else 0.0
        except OSError:
            return
        key = str(root)
        if mtime and mtime != self._fold_mtime.get(key):
            try:
                leaderboard.fold(root)
            except Exception:  # noqa: BLE001 — leaderboard upkeep must never break the tick
                log.exception("curator: leaderboard fold failed for %s", root)
            self._fold_mtime[key] = mtime

    def _maybe_distill(self, root: Path, now: float) -> None:
        """Low-frequency LLM distill, at most every ``distill_interval_s``. No-op
        without a distill function (a deterministic-only Curator)."""
        if self._distill_fn is None:
            return
        key = str(root)
        if now - self._distill_at.get(key, 0.0) < self.distill_interval_s:
            return
        self._distill_at[key] = now  # advance even on failure — best-effort, don't hammer
        self._distill_root(root, self._distill_fn)

    def _distill_root(self, root: Path, distill_fn: Callable[[str], str]) -> bool:
        """Low-frequency LLM distill: read the leaderboard, ask the curator agent
        for a forward strategy, write ``strategy.md``. Best-effort — any failure
        or empty response keeps the prior strategy. Returns True iff it wrote."""
        board = leaderboard.read(root)
        if not board:
            return False
        try:
            text = distill_fn(self._distill_prompt(board))
        except Exception:  # noqa: BLE001 — distill must never break the tick
            log.exception("curator: distill failed for %s", root)
            return False
        if not text or not text.strip():
            return False
        (Path(root) / "strategy.md").write_text(text.strip() + "\n", encoding="utf-8")
        return True

    def _distill_prompt(self, board: dict[str, Any]) -> str:
        lines = ["# Current leaderboard (judge each target by its recorded outcome)", ""]
        for target, entry in sorted(board.items()):
            best = entry.get("best")
            best_s = (f"best `{best.get('mechanism') or '(unnamed)'}`={best.get('metric')}"
                      if best else "no recorded outcome yet")
            tried = ", ".join(
                f"{a.get('mechanism') or '(unnamed)'}"
                f"({'no outcome' if a.get('metric') is None else a.get('metric')})"
                for a in entry.get("attempts", []))
            lines.append(f"- {target}: {best_s}; tried: {tried or '(none)'}")
        return (self._curator_contract() + "\n\n" + "\n".join(lines)
                + "\n\nReply with ONLY the strategy as markdown — a short prioritized "
                "list of `target -> next move (build on best | try a different approach) "
                "-> one-line why`. Do NOT create, edit, or read any files; your reply IS "
                "the strategy.")

    def _curator_contract(self) -> str:
        try:
            from ..skills.role_context import load_builtin_skill_text
            return load_builtin_skill_text("curator/argus-curator-role.md", _CURATOR_FALLBACK)
        except Exception:  # noqa: BLE001 — fall back to the inline contract
            return _CURATOR_FALLBACK

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:  # noqa: BLE001 — a tick must never kill the loop
                log.exception("curator tick failed")
            self._stop.wait(self.tick_s)

    def start(self) -> None:
        """Launch the resident maintenance thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="argus-curator",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the loop and terminate every tracked child.

        Joins the thread FIRST (so no tick races the teardown), then does the
        explicit per-child killpg — daemon=True threads alone never reap child
        processes, so this explicit call is what the daemon must invoke on exit.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=self.tick_s + 5.0)
            self._thread = None
        for tt in list(self._children.values()):
            self._terminate(tt)
        self._children.clear()
