from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from ...core.ports import EventSink
from ..memory import BacklogItem, Journal


class _MemoryView(Protocol):
    @property
    def backlog(self) -> Any: ...

    @property
    def journal(self) -> Any: ...

    def render_prelude(self, *, objective: str) -> str: ...

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


@dataclass
class LifeBudget:
    """Layered cost / iteration limits.

    Enforcement points:

    1. **Preflight per-mission cap**: before starting a backlog item we
       refuse if ``item.max_cost_usd > per_mission_cap_usd`` OR if
       ``daily_remaining < per_mission_cap_usd``. Either condition
       pauses the supervisor with a journal entry — we do not silently
       trim caps.
    2. **Daily cap**: cumulative ``cost_usd`` from journal entries
       whose timestamp is ≥ start-of-current-day-local. The supervisor
       refreshes this number on each loop tick so a long-running
       supervisor honours LOCAL-day rollover (the pause clears at local
       midnight, matching ``remaining_today``'s ``time.localtime`` math).
    3. **Iteration cap**: hard count of autonomous missions completed
       in this supervisor run (NOT cumulative across restarts). Once
       reached, supervisor exits cleanly even if backlog is non-empty.

    Field semantics:

    - ``per_mission_cap_usd``: the highest a single mission is allowed
      to cost (sum of engineer + reviewer + author tokens × prices).
    - ``daily_cap_usd``: ceiling on summed cost of mission entries in
      ``journal.jsonl`` whose timestamp falls in the current local day.
    - ``max_missions``: hard cap on missions run by THIS supervisor
      process (resets per ``LifeSupervisor`` instance).
    """

    per_mission_cap_usd: float = 30.0
    daily_cap_usd: float = 180.0
    max_missions: int = 6

    def remaining_today(self, journal: Journal, *, now: float | None = None) -> float:
        """USD remaining in today's budget."""
        now = now if now is not None else time.time()
        # Local day start.
        local = time.localtime(now)
        day_start = time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)
        )
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
    # Set by the REPL / daemon worker; empty string disables injection.
    runtime_context: str = ""
    # Defaults for tasks generated by the continuous planner. Manual backlog
    # items already use the BacklogItem defaults; keep planner-generated work
    # equally capable instead of cutting it off after one local polish cycle.
    planner_task_iteration_max_cycles: int = 6
    planner_task_iteration_budget_usd: float = 30.0
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
    # the planner hands to bounded items; ``full_emnlp_gate`` requires the L2
    # reviewer's full-pipeline checklist to be certified before ``project_done``
    # is honoured (and drives the auto-stop once that gate passes). Both default
    # True because the life supervisor is the autonomous EMNLP-research driver;
    # set them False for non-paper continuous missions.
    paper_mission: bool = True
    full_emnlp_gate: bool = True
    # ``open_ended`` controls what happens when the planner certifies
    # ``project_done`` on a continuous mission: when True the supervisor does
    # NOT hard-stop — it logs a planner retry and keeps the mission alive so the
    # 7×24 lifetime agent keeps generating new work. Replaces the old keyword
    # sniffing of the objective text ("ongoing"/"perpetual"/"7×24"/…). Defaults
    # False at this low level (honour project_done); the daemon/REPL entry paths
    # default it True unless ``--bounded`` is passed.
    open_ended: bool = False
    # Optional callback returning ``(enabled, objective)`` — the
    # supervisor calls it each iteration to hot-reload from disk or
    # elsewhere. When ``None``, the static ``continuous`` /
    # ``continuous_objective`` fields are used unchanged.
    continuous_config_provider: Any = None  # Callable[[], tuple[bool, str]] | None
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
    ) -> Any:  # MissionOutcome
        raise NotImplementedError
