"""Daemon-resident Curator: the persistent owner of the teammate pool.

The Curator is a managed component/thread inside the daemon process. It keeps N
teammates in flight, is the single reaper, and maintains the leaderboard. Its
lifetime is tied to the daemon rather than to a lead mission.

Ownership model (load-bearing): the Curator is a *thread* of the daemon, so it
shares the daemon's process group. Teammates are therefore launched as their
OWN session leaders (``start_new_session=True``) and the Curator owns each one by
**retaining its ``Popen`` handle** — reaping via ``proc.poll()`` and killing via
*per-child* ``killpg(os.getpgid(pid), …)``. A shared process group would let a
stop kill the daemon itself.

Restart durability: in-memory ``Popen`` handles are lost when the daemon dies
uncleanly (SIGKILL/crash/forced restart), so a fresh Curator would never see the
prior teammates — they keep running as init-orphans while it over-spawns
duplicates on the same tasks. The roster is the on-disk PID registry; on first
sight of each root the Curator **adopts** still-alive roster members (verified
against ``/proc`` cmdline so a recycled PID can't be mistaken for one) into its
tracked pool, so every lifecycle path (live-owner accounting, deadline reaping,
``stop`` kill) covers them too. Adoption is what makes orphaning impossible
*across restarts*, not just within one daemon life.
"""
from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import completion, leaderboard, pool, registry, roster, task_board

log = logging.getLogger(__name__)


def _pid_is_teammate(pid: int, member_id: str, root: Path | None = None) -> bool:
    """Verify an adopted PID against exact teammate command-line arguments."""
    if os.name == "nt":
        return False
    try:
        argv = [
            part.decode("utf-8", "replace")
            for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\x00")
            if part
        ]
    except OSError:
        return False
    if "argus_skill.team.teammate_entry" not in argv:
        return False

    def option(name: str) -> str:
        try:
            return argv[argv.index(name) + 1]
        except (ValueError, IndexError):
            return ""

    if option("--member-id") != member_id:
        return False
    if root is None:
        return True
    recorded_root = option("--root")
    if not recorded_root:
        return False
    try:
        return Path(recorded_root).expanduser().resolve() == Path(root).expanduser().resolve()
    except (OSError, RuntimeError):
        return False


class _AdoptedProc:
    """Popen-shaped handle for a teammate adopted from the roster after a daemon
    restart. We never had the original ``Popen``, so liveness/teardown go through
    the raw pid; ``poll``/``wait`` mimic the subset ``TrackedTeammate`` and
    ``_terminate`` rely on, so adopted children flow through every owned-child
    path unchanged."""

    def __init__(self, pid: int, member_id: str, root: Path) -> None:
        self.pid = int(pid)
        self._member_id = member_id
        self._root = Path(root)

    def poll(self) -> int | None:
        return None if _pid_is_teammate(self.pid, self._member_id, self._root) else 0

    def wait(self, timeout: float | None = None) -> int:
        end = (time.time() + timeout) if timeout else None
        while self.poll() is None:
            if end is not None and time.time() >= end:
                raise subprocess.TimeoutExpired(self._member_id, timeout or 0)
            time.sleep(0.1)
        return 0


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


def _child_key(root: Path, member_id: str) -> tuple[str, str]:
    """Namespace a member id by campaign root.

    Member sequences intentionally restart at ``w1`` for each campaign, so a
    process registry shared by the daemon must never key on member id alone.
    """
    return (str(Path(root).expanduser().resolve()), str(member_id))


class Curator:
    """Keeps N teammates in flight per active campaign and reaps them.

    ``make_proc`` is the process-factory injection seam used by tests; by default
    it launches the real headless ``teammate_entry``. ``now_fn`` keeps reaper
    deadlines testable without sleeping.
    """

    def __init__(self, *, project_root: Path, default_width: int = 8,
                 tick_s: float = 5.0, teammate_timeout_s: float = 5400.0,
                 hard_grace_s: float = 600.0,
                 now_fn: Callable[[], float] = time.time,
                 make_proc: Callable[..., Any] | None = None,
                 distill_fn: Callable[[str], str] | None = None,
                 distill_interval_s: float = 1260.0,
                 completion_fn: Callable[[str], str] | None = None,
                 conversation_root: Path | None = None) -> None:
        self.project_root = Path(project_root)
        self.default_width = int(default_width)
        self.tick_s = float(tick_s)
        self.teammate_timeout_s = float(teammate_timeout_s)
        self.hard_grace_s = float(hard_grace_s)
        self._now = now_fn
        self._make_proc = make_proc or self._default_make_proc
        self._distill_fn = distill_fn
        self.distill_interval_s = float(distill_interval_s)
        self._completion_fn = completion_fn
        self.conversation_root = Path(conversation_root) if conversation_root is not None else None
        self._children: dict[tuple[str, str], TrackedTeammate] = {}
        self._adopted_roots: set[str] = set()  # roots whose roster orphans were adopted
        self._fold_mtime: dict[str, float] = {}  # per-root shards mtime at last fold
        self._distill_at: dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ---- spawning a tracked child --------------------------------------
    def _default_make_proc(self, root: Path, member_id: str, task_id: str,
                           cwd: Path) -> Any:
        log_dir = Path(root) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / (member_id.replace(":", "_") + ".spawn.log")
        argv = [sys.executable, "-m", "argus_skill.team.teammate_entry",
                "--root", str(root), "--member-id", member_id,
                "--task-id", task_id, "--cwd", str(cwd)]
        log = open(log_path, "ab")
        devnull = open(os.devnull, "rb")
        # OWN session (own pgroup) — the Curator owns it via the retained handle,
        # NOT via a shared process group (which would be the daemon's).
        return subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=devnull,
            stdout=log,
            stderr=log,
            start_new_session=os.name != "nt",
            creationflags=(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if os.name == "nt"
                else 0
            ),
        )

    def _spawn_tracked(self, root: Path, *, member_id: str, task_id: str,
                       cwd: Path, now: float | None = None) -> int:
        root = Path(root)
        key = _child_key(root, member_id)
        prior = self._children.get(key)
        if prior is not None and prior.alive():
            raise RuntimeError(f"teammate {member_id!r} is already running for {root}")
        proc = self._make_proc(root, member_id, task_id, Path(cwd))
        self._children[key] = TrackedTeammate(
            proc, member_id=member_id, task_id=task_id, root=root,
            started_at=(self._now() if now is None else now),
            timeout_s=self.teammate_timeout_s, hard_grace_s=self.hard_grace_s)
        roster.add_member(root, {
            "id": member_id, "pid": proc.pid, "cwd": str(cwd),
            "task_id": task_id, "status": "running",
        })
        return int(proc.pid)

    def live_owner_ids(self, root: Path) -> set[str]:
        """Member ids whose tracked child is genuinely alive for ``root``."""
        root_key = str(Path(root).expanduser().resolve())
        return {
            tt.member_id
            for (child_root, _member_id), tt in self._children.items()
            if child_root == root_key and tt.alive()
        }

    # ---- adoption: reclaim teammates left running by a prior daemon ------
    def _adopt_orphans(self, root: Path, *, now: float | None = None) -> list[str]:
        """Adopt roster members of ``root`` still running after a restart.

        A daemon that dies uncleanly never runs ``stop()``, so its tracked
        ``Popen`` handles are lost and the teammates reparent to init. The roster
        keeps each member's ``pid``/``cwd``/``task_id``, so a fresh Curator reads
        it once per root and folds any still-alive teammate back into ``_children``
        — verified via cmdline so a recycled pid can't be adopted. Without this
        they're invisible to live-owner accounting (→ duplicate spawns) and to the
        reaper (→ never killed). Runs once per root; later spawns are ours.
        """
        root = Path(root)
        root_key = str(root.expanduser().resolve())
        if root_key in self._adopted_roots:
            return []
        self._adopted_roots.add(root_key)
        now = self._now() if now is None else now
        adopted: list[str] = []
        for m in roster.members(root):
            mid, pid = m.get("id"), m.get("pid")
            child_key = _child_key(root, str(mid or ""))
            if not mid or child_key in self._children or not pid:
                continue
            if m.get("status") != "running" or not _pid_is_teammate(int(pid), mid, root):
                continue
            self._children[child_key] = TrackedTeammate(
                _AdoptedProc(int(pid), mid, root), member_id=mid,
                task_id=m.get("task_id", ""), root=root, started_at=now,
                timeout_s=self.teammate_timeout_s, hard_grace_s=self.hard_grace_s)
            adopted.append(mid)
        if adopted:
            log.info("curator: adopted %d orphaned teammate(s) for %s: %s",
                     len(adopted), root, ", ".join(adopted))
        return adopted

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
        failed_dead_cwd: list[str] = []
        for _ in range(free):
            mid = roster.next_member_id(root)
            task = task_board.claim_top(root, mid, now=now)
            if task is None:
                break  # backlog empty
            # Per-task cwd: a task may carry its own working dir (independent
            # project workdirs — one per kernel). Fall back to the shared campaign
            # cwd when the task didn't specify one (legacy single-repo campaigns).
            task_cwd = Path(task.get("cwd") or cwd)
            # A vanished working dir is UNRECOVERABLE for this unit of work. The
            # Curator is domain-agnostic plumbing: it must NOT silently re-home the
            # task in some other dir (running research work in the wrong place →
            # wrong/contaminated results), and it must NOT leave it pending/claimed
            # (it would be re-claimed and re-crash every ttl → a hot-loop that
            # starves the tick). So FAIL it honestly and ONCE: a failed task leaves
            # the pending set (claim_top only picks pending) so it is never retried,
            # and its recorded reason + this log keep the vanished path visible —
            # e.g. a deleted temporary workspace — instead of masking it.
            if not task_cwd.is_dir():
                task_board.fail(root, task["task_id"],
                                reason=f"working dir vanished before spawn: {task_cwd}")
                log.error("curator: task %s working dir vanished (%s) — failing the "
                          "task, not spawning; it will not be retried",
                          task["task_id"], task_cwd)
                failed_dead_cwd.append(task["task_id"])
                continue
            self._spawn_tracked(root, member_id=mid, task_id=task["task_id"],
                                cwd=task_cwd, now=now)
            spawned.append({"member_id": mid, "task_id": task["task_id"]})
        return {"spawned": spawned, "in_flight": in_flight, "live": len(live),
                "occupied": occupied, "free": free, "reassigned": reassigned,
                "failed_dead_cwd": failed_dead_cwd}

    # ---- reaping --------------------------------------------------------
    def _terminate(self, tt: TrackedTeammate, *, grace: float = 2.0) -> None:
        """Kill one tracked child's process group (SIGTERM → grace → SIGKILL)."""
        proc = tt.proc
        if proc.poll() is not None:
            return
        if os.name == "nt":
            proc.terminate()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
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
        for key, tt in list(self._children.items()):
            if not tt.alive():
                del self._children[key]
                with contextlib.suppress(Exception):
                    roster.set_member_status(tt.root, tt.member_id, "exited")
                dropped.append(tt.member_id)
                continue
            if now >= tt.hard_deadline():
                self._terminate(tt)
                with contextlib.suppress(Exception):
                    task_board.fail(tt.root, tt.task_id, reason="curator hard-timeout")
                    roster.set_member_status(tt.root, tt.member_id, "failed")
                del self._children[key]
                hard_killed.append(tt.member_id)
        return {"dropped": dropped, "hard_killed": hard_killed}

    # ---- the resident loop ---------------------------------------------
    def _tick(self, now: float | None = None) -> None:
        """One maintenance pass: reap finished/wedged children, then for every
        active campaign keep the pool at its width (or wind a draining one down).

        Discovery is centralised over registry markers, so the daemon has one
        process owner for every active campaign root.
        """
        now = self._now() if now is None else now
        self._reap(now=now)
        for marker in registry.list_markers(self.project_root):
            # Per-campaign isolation: a single poisoned marker (e.g. a working dir
            # that vanished under it, or a corrupt pool file) must NEVER abort the
            # whole tick and starve every OTHER campaign of its refill. Fail loudly
            # — full traceback, with the campaign id — and carry on to the next.
            try:
                self._tick_marker(marker, now=now)
            except Exception:  # noqa: BLE001 — one campaign must not sink the tick
                log.exception("curator: tick failed for campaign %s; skipping it "
                              "this tick", (marker or {}).get("team_id", "?"))

    def _tick_marker(self, marker: dict[str, Any], *, now: float) -> None:
        """Maintain ONE campaign for this tick: adopt prior-daemon orphans, fold
        the leaderboard, then refill the pool (or wind a draining campaign down).

        Split out of :meth:`_tick` so every campaign is processed inside its OWN
        try-boundary — a failure here (a vanished cwd, a corrupt pool file, …)
        stays contained to this campaign instead of aborting the tick for all.
        """
        root = Path(marker["team_root"])
        cwd = Path(marker.get("cwd") or root)
        self._adopt_orphans(root, now=now)  # reclaim prior-daemon teammates first
        self._maybe_fold(root)
        if task_board.count_in_flight(root) == 0 and not self.live_owner_ids(root):
            completion.publish_if_complete(
                root,
                marker=marker,
                conversation_root=self.conversation_root,
                summarize=self._completion_fn,
            )
        doc = pool.read(root)
        state = doc.get("state", "running")
        if state in ("draining", "dissolved"):
            # Stop refilling and let in-flight teammates finish; the hard-deadline
            # reaper still cleans wedged ones. Remove the marker once the campaign
            # is genuinely empty.
            if task_board.count_in_flight(root) == 0 and not self.live_owner_ids(root):
                registry.remove_marker(self.project_root, marker["team_id"])
            return
        self._maybe_distill(root, now)
        width = int(doc["width"]) if "width" in doc else self.default_width
        self._refill(root, width=width, cwd=cwd, now=now)

    def _maybe_fold(self, root: Path) -> None:
        """Deterministically re-fold the leaderboard when shards have changed.

        Pure code with no model call. The shards-directory mtime guards against
        re-reading thousands of shards every tick
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
                # Leave _fold_mtime UNCHANGED on failure: advancing it here would
                # record the (poison) shard-dir state as permanently processed, so
                # fold would never retry until a new shard bumps the mtime — and
                # then re-raise. Not advancing means the next tick retries.
                return
            self._fold_mtime[key] = mtime

    def _maybe_distill(self, root: Path, now: float) -> None:
        """Refresh strategy at a bounded cadence when a backend is available."""
        if self._distill_fn is None:
            return
        key = str(root)
        if now - self._distill_at.get(key, 0.0) < self.distill_interval_s:
            return
        self._distill_at[key] = now
        self._distill_root(root, self._distill_fn)

    def _distill_root(
        self,
        root: Path,
        distill_fn: Callable[[str], str],
    ) -> bool:
        board = leaderboard.read(root)
        if not board:
            return False
        try:
            text = distill_fn(self._distill_prompt(board))
        except Exception:  # noqa: BLE001 — strategy is best-effort
            log.exception("curator: distill failed for %s", root)
            return False
        if not text or not text.strip():
            return False
        (Path(root) / "strategy.md").write_text(
            text.strip() + "\n",
            encoding="utf-8",
        )
        return True

    def _distill_prompt(self, board: dict[str, Any]) -> str:
        lines = [
            "# Current leaderboard (judge each target by its recorded outcome)",
            "",
        ]
        for target, entry in sorted(board.items()):
            best = entry.get("best")
            best_text = (
                f"best `{best.get('mechanism') or '(unnamed)'}`={best.get('metric')}"
                if best
                else "no recorded outcome yet"
            )
            attempts = ", ".join(
                f"{attempt.get('mechanism') or '(unnamed)'}"
                f"({'no outcome' if attempt.get('metric') is None else attempt.get('metric')})"
                for attempt in entry.get("attempts", [])
            )
            lines.append(
                f"- {target}: {best_text}; tried: {attempts or '(none)'}"
            )
        return (
            self._curator_contract()
            + "\n\n"
            + "\n".join(lines)
            + "\n\nReply with ONLY the strategy as markdown — a short "
            "prioritized list of `target -> next move (build on best | try a "
            "different approach) -> one-line why`."
        )

    @staticmethod
    def _curator_contract() -> str:
        from ..skills.role_context import load_builtin_skill_text

        return load_builtin_skill_text("curator/argus-curator-role.md")

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
        stopped_roots: set[Path] = set()
        for tt in list(self._children.values()):
            status = "stopped" if tt.alive() else "exited"
            self._terminate(tt)
            stopped_roots.add(tt.root)
            with contextlib.suppress(Exception):
                roster.set_member_status(tt.root, tt.member_id, status)
        self._children.clear()
        # A clean daemon stop has killed every owned process, so leave its tasks
        # immediately claimable on restart instead of waiting for the stale TTL.
        for root in stopped_roots:
            with contextlib.suppress(Exception):
                task_board.reassign_stale(
                    root,
                    ttl=-1.0,
                    now=self._now(),
                    live_owners=set(),
                )
