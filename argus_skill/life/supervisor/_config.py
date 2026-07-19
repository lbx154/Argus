from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ...core.ports import EventSink
from ...core.usage import (
    UsageLedger,
    UsageSummary,
    project_usage_summary,
    summarize_usage,
)
from ..memory import BacklogItem, EventJournal, Journal

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - production daemon is POSIX
    _fcntl = None

_GLOBAL_RESERVATION_THREAD_LOCK = threading.Lock()
_RESERVATION_STATE_FILE = "budget-reservations.json"
_RESERVATION_LOCK_FILE = "budget-reservations.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


@contextmanager
def _reservation_state_lock(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with _GLOBAL_RESERVATION_THREAD_LOCK:
        fd = os.open(str(root / _RESERVATION_LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_EX)
            yield
        finally:
            if _fcntl is not None:
                try:
                    _fcntl.flock(fd, _fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)


def _read_reservations(root: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads((root / _RESERVATION_STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return []
    rows = payload.get("reservations") if isinstance(payload, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _write_reservations(root: Path, rows: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / _RESERVATION_STATE_FILE
    fd, tmp_name = tempfile.mkstemp(prefix=".budget-reservations-", dir=str(root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "reservations": rows}, handle, indent=2)
            handle.write("\n")
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, target)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


@dataclass
class GlobalBudgetReservation:
    root: Path | None = None
    reservation_id: str = ""

    def release(self) -> None:
        if self.root is None or not self.reservation_id:
            return
        with _reservation_state_lock(self.root):
            rows = _read_reservations(self.root)
            kept = [row for row in rows if row.get("id") != self.reservation_id]
            if len(kept) != len(rows):
                _write_reservations(self.root, kept)
        self.reservation_id = ""


def reserve_global_daily_budget(
    *,
    cap_usd: float,
    amount_usd: float,
    global_root: Path | None = None,
    owner: str = "",
    now: float | None = None,
) -> tuple[GlobalBudgetReservation | None, str]:
    """Atomically reserve one mission envelope against the host-wide cap."""
    cap = max(0.0, float(cap_usd or 0.0))
    amount = max(0.0, float(amount_usd or 0.0))
    if cap <= 0 or amount <= 0:
        return GlobalBudgetReservation(), ""
    if global_root is None:
        from ...core.paths import global_root as resolve_global_root

        root = resolve_global_root()
    else:
        root = Path(global_root).expanduser()
    ts = time.time() if now is None else float(now)
    with _reservation_state_lock(root):
        rows = [
            row
            for row in _read_reservations(root)
            if _pid_alive(int(row.get("pid") or 0))
        ]
        reserved = sum(max(0.0, float(row.get("amount_usd") or 0.0)) for row in rows)
        spent = global_daily_spend(global_root=root, now=ts)
        if spent + reserved + amount > cap:
            _write_reservations(root, rows)
            return None, (
                f"global daily spend ${spent:.2f} + active reservations "
                f"${reserved:.2f} + mission cap ${amount:.2f} would exceed "
                f"global daily cap ${cap:.2f}"
            )
        reservation_id = uuid.uuid4().hex
        rows.append({
            "id": reservation_id,
            "pid": os.getpid(),
            "owner": owner,
            "amount_usd": amount,
            "created_at": ts,
        })
        _write_reservations(root, rows)
    return GlobalBudgetReservation(root=root, reservation_id=reservation_id), ""


class _MemoryView(Protocol):
    @property
    def root(self) -> Path: ...

    @property
    def backlog(self) -> Any: ...

    @property
    def journal(self) -> Any: ...

    def render_prelude(self) -> str: ...

@dataclass
class MissionBudget:
    """A LIVE per-mission spend probe handed to the engine so it can enforce the
    per-mission cap MID-mission (F3). ``spent`` is a callable (bound to the live
    ``cost_sink.total_usd``), NOT a snapshot — it is read each round. ``cap_usd``
    <= 0 disables the breaker (``exceeded`` is then always False)."""
    cap_usd: float
    spent: Callable[[], float]

    def exceeded(self) -> bool:
        return self.cap_usd > 0 and self.spent() >= self.cap_usd


def _local_day_start(now: float) -> float:
    local = time.localtime(now)
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))


def global_daily_usage_summary(
    *,
    global_root: Path | None = None,
    now: float | None = None,
) -> UsageSummary:
    """Call-ledger usage across all projects since local midnight."""
    now = time.time() if now is None else float(now)
    day_start = _local_day_start(now)
    if global_root is None:
        from ...core.paths import global_root as resolve_global_root

        root = resolve_global_root()
    else:
        root = Path(global_root).expanduser()
    projects_dir = root / "projects"
    try:
        project_dirs = sorted(p for p in projects_dir.iterdir() if p.is_dir())
    except OSError:
        return summarize_usage([])

    records = []
    for project_dir in project_dirs:
        try:
            records.extend(UsageLedger(project_dir).records(since=day_start))
        except Exception:  # noqa: BLE001 — one corrupt project must not hide others
            continue
    return summarize_usage(records)


def global_daily_spend(*, global_root: Path | None = None, now: float | None = None) -> float:
    """Known call-ledger spend across all projects since local midnight."""
    return global_daily_usage_summary(
        global_root=global_root,
        now=now,
    ).known_cost_usd


@dataclass
class LifeBudget:
    """Layered cost / iteration limits.

    Enforcement points:

    1. **Preflight per-mission cap**: before starting a backlog item we
       refuse if ``item.max_cost_usd > per_mission_cap_usd`` OR if
       ``daily_remaining < per_mission_cap_usd``. Either condition
       pauses the supervisor with a journal entry — we do not silently
       trim caps.
    2. **Daily cap**: cumulative known cost from call-level usage records
       whose completion timestamp is ≥ start-of-current-day-local. The supervisor
       refreshes this number on each loop tick so a long-running
       supervisor honours LOCAL-day rollover (the pause clears at local
       midnight, matching ``remaining_today``'s ``time.localtime`` math).
    3. **Iteration cap**: hard count of autonomous missions completed
       in this supervisor run (NOT cumulative across restarts). Once
       reached, supervisor exits cleanly even if backlog is non-empty.

    Field semantics:

    - ``per_mission_cap_usd``: the highest a single mission is allowed
      to cost (sum of engineer + reviewer + author tokens × prices).
    - ``daily_cap_usd``: ceiling on call-ledger cost in the current local day.
    - ``global_daily_cap_usd``: optional ceiling on call-ledger cost across ALL
      projects under the global root for the current local day.
    - ``max_missions``: hard cap on missions run by THIS supervisor
      process (resets per ``LifeSupervisor`` instance).
    """

    per_mission_cap_usd: float = 200.0
    daily_cap_usd: float = 1_200.0
    global_daily_cap_usd: float = 0.0
    max_missions: int = 6

    def remaining_today(self, journal: Journal, *, now: float | None = None) -> float:
        """USD remaining in today's budget."""
        now = now if now is not None else time.time()
        day_start = _local_day_start(now)
        path = Path(getattr(journal, "path", ""))
        if isinstance(journal, EventJournal) or path.name == "events.jsonl":
            spent = project_usage_summary(
                path.parent,
                since=day_start,
            ).known_cost_usd
        else:
            # Compatibility for callers still using an isolated legacy Journal.
            spent = journal.total_cost_since(day_start)
        return max(0.0, float(self.daily_cap_usd) - float(spent))

    def effective_per_mission_cap(self, item: BacklogItem) -> float:
        """The cap actually enforced for ``item``: the smaller of the operator's
        per-item budget and the global per-mission cap. Single source of truth for
        both the preflight ``can_start`` check and the mid-mission breaker (F3)."""
        return float(min(item.max_cost_usd, self.per_mission_cap_usd))

    def can_start(
        self,
        *,
        item: BacklogItem,
        journal: Journal,
        now: float | None = None,
        global_root: Path | None = None,
    ) -> tuple[bool, str]:
        """Return ``(allowed, reason)``. ``reason`` is empty when allowed.

        We do NOT refuse a mission just because ``item.max_cost_usd``
        exceeds ``per_mission_cap_usd`` — that's a daemon-vs-item
        misconfiguration and a 7×24 product should keep working. The
        per-mission cap is enforced inside the supervisor by clamping
        the effective per-mission budget (see
        ``LifeSupervisor._effective_per_mission_cap``); this method
        only blocks on the *daily* budget envelope, which is the real
        bottom line.
        """
        remain = self.remaining_today(journal, now=now)
        # Use the smaller of (operator-requested mission budget, our
        # per-mission cap) when comparing to daily remaining — same
        # number the supervisor will actually permit.
        effective_cap = self.effective_per_mission_cap(item)
        if remain < effective_cap:
            return False, (
                f"daily budget remaining ${remain:.2f} < "
                f"effective mission cap ${effective_cap:.2f}"
            )
        global_cap = float(self.global_daily_cap_usd or 0.0)
        if global_cap > 0:
            spent = global_daily_spend(global_root=global_root, now=now)
            if spent + effective_cap > global_cap:
                return False, (
                    f"global daily spend ${spent:.2f} + effective mission cap "
                    f"${effective_cap:.2f} would exceed global daily cap ${global_cap:.2f}"
                )
        return True, ""

@dataclass
class LifeSupervisorConfig:
    """Knobs for one ``LifeSupervisor`` run."""

    budget: LifeBudget = field(default_factory=LifeBudget)
    poll_interval_seconds: float = 5.0
    # The real repository worktree for this project. When present, the
    # supervisor should run engineer / planner work there instead of in
    # the life metadata directory.
    project_worktree: Path | None = None
    # Highest-level kill switch — the supervisor checks this between
    # missions. The CLI sets it on SIGTERM/SIGINT.
    stop_event: threading.Event | None = None
    # Optional callable consulted at the start of every mission; should
    # return one pending operator nudge per call (or ``None`` when the
    # bus is empty). The supervisor splices each message into the
    # prelude_context so the engineer sees it as live operator
    # guidance. The default ``None`` disables the bus.
    user_inbox: Any = None  # Callable[[], str | None] | None
    # Runtime context injected into the prelude of every mission so
    # the agent knows its own backend, models, and budget constraints.
    # Set by the cockpit / daemon worker; empty string disables injection.
    runtime_context: str = ""
    # Defaults for tasks generated by the continuous planner. Manual backlog
    # items already use the BacklogItem defaults; keep planner-generated work
    # equally capable instead of cutting it off after one local polish cycle.
    planner_task_iteration_max_cycles: int = 6
    planner_task_iteration_budget_usd: float = 200.0
    # Subagent family failure circuit breaker (F6). A planner-generated task
    # is reworded from scratch every cycle, so the exact-text dedup below
    # (``_planner_task_signature``) cannot catch "the SAME underlying
    # experiment keeps failing" across differently-worded retries — and the
    # mission itself is often graded a success (the engineer DID resubmit +
    # monitor + document real work) even while the subagent job it launched
    # keeps erroring. ``_recent_subagent_family_failures`` reads the subagent
    # registry directly (``.argus_subagents/*.json``) and flags an experiment
    # family once it has failed this many times in a row, unresolved, within
    # the trailing window — independent of mission-level wording or grading.
    # See ``life/supervisor/_subagent_family_failures.py`` for the observed
    # pathology (SWE-bench full-canary retried ~20x/2 days before this fix).
    subagent_family_failure_streak_limit: int = 3
    subagent_family_failure_window_hours: float = 72.0
    # --- Continuous improvement mode -----------------------------------
    # When enabled, the supervisor does not exit when the backlog is
    # empty. Instead it invokes the planner to inspect the
    # project and generate the next batch of tasks. The supervisor
    # only stops when the planner declares the project done, or when
    # budget / stop_event fires.
    continuous: bool = False
    continuous_objective: str = ""
    # Explicit mission-type signals (replace the old keyword sniffing of the
    # objective text). ``paper_mission`` toggles the long-horizon paper guidance
    # the planner hands to bounded items; ``full_paper_gate`` requires the L2
    # reviewer's full-pipeline checklist to be certified before ``project_done``
    # is honoured (and drives the auto-stop once that gate passes). Both default
    # False: callers enable these only after the Manager has resolved
    # a vertical whose completion gate is explicitly ``full_paper``.
    paper_mission: bool = False
    full_paper_gate: bool = False
    # ``open_ended`` controls what happens when the planner certifies
    # ``project_done`` on a continuous mission: when True the supervisor does
    # NOT hard-stop — it logs a planner retry and keeps the mission alive so the
    # 7×24 lifetime agent keeps generating new work. Replaces the old keyword
    # sniffing of the objective text ("ongoing"/"perpetual"/"7×24"/…). Defaults
    # False at this low level (honour project_done); the daemon/cockpit entry paths
    # default it True unless ``--bounded`` is passed.
    open_ended: bool = False
    # Optional callback returning ``(enabled, objective)`` — the
    # supervisor calls it each iteration to hot-reload from disk or
    # elsewhere. When ``None``, the static ``continuous`` /
    # ``continuous_objective`` fields are used unchanged.
    continuous_config_provider: Any = None  # Callable[[], tuple[bool, str]] | None
    # Optional mission-boundary yield signal. A live operator Manager request
    # uses this to make ``run()`` return before the next tick/planner cycle so
    # the host can release its outer pipeline lock and commit configuration.
    manager_pipeline_yield_provider: Any = None  # Callable[[], bool] | None
    # Optional callback consulted immediately before each continuous
    # planner cycle. Return a non-empty stop reason to let the host
    # process defer planning and yield control, e.g. for daemon handoff.
    planner_cycle_gate: Any = None  # Callable[[], str] | None
    # Optional context injected into the planner prompt. The daemon uses
    # this to tell L4 that runtime source changed without making another
    # agent call.
    planner_runtime_context_provider: Any = None  # Callable[[], str] | None
    # Optional handler invoked only when the planner verdict explicitly
    # requests a daemon restart. Return True when the host is yielding.
    planner_restart_handler: Any = None  # Callable[[str], bool] | None
    # Optional mission-boundary hook. The host may use this to perform
    # process-level actions that are only safe between missions (for example
    # blue/green handoff after the agent modifies its own daemon/runtime
    # architecture). Return a non-empty stop reason to end this drain pass.
    post_mission_hook: Any = None  # Callable[[dict[str, Any]], str] | None
    # Optional runtime directory for mission telemetry. When set, the
    # supervisor starts a daemon-owned heartbeat around runner.execute()
    # so long-running shell experiments still show process/artifact progress.
    telemetry_dir: Path | None = None
    telemetry_interval_seconds: float = 10.0
    # Session-scoped root for pipeline/checklist/domain artifacts. The command
    # working tree may be a git repo, but harness state must not leak across
    # sessions that share that repo.
    artifact_root: Path | None = None


class _MissionRunner(Protocol):
    """Structural type for the MissionExecutor we drive.

    We keep this loose so tests can substitute a fake without dragging
    ArgusBot in. Real callers pass an ``argus_skill.daemon.mission_executor.MissionExecutor``.
    """

    def execute(
        self,
        *,
        objective: str,
        original_objective: str = "",
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
        scope: str = "",
        per_mission_budget: "MissionBudget | None" = None,
        preplanned: bool = False,
    ) -> Any:  # MissionOutcome
        raise NotImplementedError
