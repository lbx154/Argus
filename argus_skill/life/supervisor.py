"""``LifeSupervisor`` — owns the outer process, runs missions back-to-back.

Per the rubber-duck critique:

- Supervisor (not observer): we OWN the outer loop and call
  ``MissionExecutor.execute(...)`` once per backlog item. We never try
  to push ``/run`` into a finished single-mission daemon.
- Single inbox owner: we don't tail any JsonlCommandBus. The optional
  ``user_inbox`` callable lets a host process feed user-provided
  high-priority objectives into the supervisor's own queue without two
  consumers racing on the same offset file.
- Bounded autonomy: ``LifeBudget`` enforces a per-mission preflight cap
  AND a daily cap. Defaults are generous enough for long polish runs
  (max 6 autonomous missions in one supervisor run, $30/mission,
  $180/day).
- Memory injection is a separate channel (``prelude_context``) — the
  objective string passed to the executor is unmodified, so skill
  matching, mission-id hashing, and reviewer prompts are unaffected.
- Idle = sleep, not spin. We poll every 5 seconds when there's nothing
  to do.

The supervisor is intentionally **synchronous**: one mission at a
time, no thread pool. That matches "an agent with continuity" — the
agent is doing one thing, then the next, like a person.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from ..core.ports import EventSink
from ..core.pricing import price_for, usd_for_tokens
from .memory import (
    BacklogItem,
    Journal,
    JournalEntry,
)
from .project_lifecycle import (
    LifecycleEvent,
    ProjectState,
    apply_event,
    decide_next_state,
    infer_observable_status,
    is_token_allocatable,
)
from .project_lifecycle_io import (
    LifecycleIOError,
    apply_persisted_to_status,
)
from .project_lifecycle_io import (
    append_event as _lifecycle_append_event,
)
from .project_lifecycle_io import (
    lifecycle_path as _lifecycle_path,
)
from .project_lifecycle_io import (
    load_persisted as _lifecycle_load_persisted,
)

log = logging.getLogger(__name__)

_price_for = price_for


class _MemoryView(Protocol):
    @property
    def backlog(self) -> Any: ...

    @property
    def journal(self) -> Any: ...

    def render_prelude(self, *, objective: str) -> str: ...


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

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
       supervisor honours UTC day rollover.
    3. **Iteration cap**: hard count of autonomous missions completed
       in this supervisor run (NOT cumulative across restarts). Once
       reached, supervisor exits cleanly even if backlog is non-empty.

    Field semantics:

    - ``per_mission_cap_usd``: the highest a single mission is allowed
      to cost (sum of engineer + reviewer + scientist tokens × prices).
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
        effective_cap = min(item.max_cost_usd, self.per_mission_cap_usd)
        if remain < effective_cap:
            return False, (
                f"daily budget remaining ${remain:.2f} < "
                f"effective mission cap ${effective_cap:.2f}"
            )
        return True, ""


# ---------------------------------------------------------------------------
# Cost-tracking sink wrapper
# ---------------------------------------------------------------------------

def _normalize_planner_text(text: str) -> str:
    """Normalize planner task text for duplicate detection."""
    normalized = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", normalized).strip().casefold()


def _sanitize_planner_task_text(text: str) -> str:
    """Remove stale host-specific entry paths from planner-generated missions."""
    value = str(text)
    command_replacements = {
        (
            "PYTHONPATH=/home/argustest/argus-skill "
            "/home/argustest/miniconda3/bin/python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
        (
            "PYTHONPATH=/home/argustest/argus-skill "
            "python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
        (
            "/home/argustest/miniconda3/bin/python -m argus_skill"
        ): '"${ARGUS_SKILL_PYTHON:-python}" -m argus_skill',
    }
    for old, new in command_replacements.items():
        value = value.replace(old, new)
    path_replacements = {
        "`/home/argustest/research.md`": "the operator-provided research playbook if present",
        "/home/argustest/research.md": "the operator-provided research playbook if present",
        "`/home/argustest/argus-skill`": "the active Argus source/package",
        "/home/argustest/argus-skill": "the active Argus source/package",
        (
            "`/root/Auto-claude-code-research-in-sleep/skills/"
            "paper-illustration-image2/SKILL.md`"
        ): "`argus_builtin_skills/engineer/paper-illustration-image2.md`",
        (
            "/root/Auto-claude-code-research-in-sleep/skills/"
            "paper-illustration-image2/SKILL.md"
        ): "argus_builtin_skills/engineer/paper-illustration-image2.md",
    }
    for old, new in path_replacements.items():
        value = value.replace(old, new)
    return value


def _planner_task_signature(title: str, objective: str) -> tuple[str, str]:
    return (_normalize_planner_text(title), _normalize_planner_text(objective))


_PLANNER_DEDUP_STATUSES = {"pending", "running", "done"}
_PLANNER_RECENT_HISTORY_WINDOW = 20
_PLANNER_RECENT_FAILURE_STATUS = "no_progress"
_FOLLOWUP_CRITIC_MIN_IMPACT_SCORE = 5
_PLANNER_SCOPE_BOUNDED = "bounded"
_PLANNER_SCOPE_FINAL_SUBMISSION = "final_submission"

# Plan-cycle outcome sentinels returned by ``_plan_next_work`` and consumed
# by ``run()``. Kept as a small named set (not bare string literals scattered
# across call sites) so the control flow stays auditable.
_PLAN_TASKS_ADDED = "tasks_added"
_PLAN_PROJECT_DONE = "project_done"
_PLAN_RETRY = "planner_retry"
_PLAN_HANDOFF = "daemon_handoff"
_PLAN_ERROR = "planner_error"
_PLAN_AWAITING = "awaiting_external"

# Idle backoff for the "no new work" outcomes (awaiting-external / planner
# retry / planner error). Each consecutive idle plan-cycle doubles the host's
# re-check sleep, capped — so a project correctly waiting on a live external
# job (or a planner that keeps finding nothing) is polled every few minutes,
# not continuously. Reset to 0 the moment real work runs.
_IDLE_BACKOFF_BASE_SECONDS = 15.0
_IDLE_BACKOFF_CAP_SECONDS = 300.0

# Re-emit an unchanged lifecycle-block status/journal line at most this often
# (a heartbeat) so a long-lived blocked state stays visible without spamming
# the journal every tick.
_LIFECYCLE_BLOCK_HEARTBEAT_SECONDS = 1800.0
_FULL_EMNLP_GATE_DESCRIPTION = (
    "the L2 reviewer's full pipeline checklist (research → submission)"
)


def _legacy_final_submission_marker(text: str) -> bool:
    """Legacy backlog migration: recognize the ``final_submission`` scope from
    objective prose.

    New backlog items always carry the structured ``scope:final_submission``
    tag (see ``_planner_task_tags``). This prose recognizer exists ONLY so a
    daemon that resumes a backlog persisted by an older build — whose items may
    have the marker only in their objective text — still exempts a ``done``
    final-submission task from planner dedupe. It is a one-shot format bridge,
    not a runtime relevance/completion judgment.
    """
    normalized = _normalize_planner_text(text)
    return (
        "scope: final_submission" in normalized
        or "planner_scope: final_submission" in normalized
    )


def _entry_task_signature(entry: JournalEntry) -> tuple[str, str] | None:
    extra = getattr(entry, "extra", {}) or {}
    signature = extra.get("planner_task_signature")
    title = ""
    objective = ""
    if isinstance(signature, dict):
        title = str(signature.get("title", "") or "")
        objective = str(signature.get("objective", "") or "")
    elif isinstance(signature, (list, tuple)) and len(signature) >= 2:
        title = str(signature[0] or "")
        objective = str(signature[1] or "")
    else:
        title = str(extra.get("title") or entry.title or "")
        objective = str(extra.get("objective") or "")
    normalized_title = _normalize_planner_text(title)
    normalized_objective = _normalize_planner_text(objective)
    if not normalized_title and not normalized_objective:
        return None
    return normalized_title, normalized_objective


def _is_recent_no_progress_failure(entry: JournalEntry) -> bool:
    if entry.kind != "mission_failed":
        return False
    extra = getattr(entry, "extra", {}) or {}
    terminal_status = str(
        extra.get("terminal_status")
        or extra.get("status")
        or extra.get("failure_status")
        or ""
    ).strip().casefold()
    return terminal_status == _PLANNER_RECENT_FAILURE_STATUS


class _CostTrackingSink:
    """Wraps an ``EventSink`` to accumulate token counts.

    The mission engine emits ``round.main.completed`` and
    ``round.review.completed`` events that already carry per-call
    ``input_tokens`` / ``output_tokens`` (Phase-2 instrumentation). We
    fold them into running totals and forward every event downstream
    unchanged.
    """

    def __init__(
        self,
        downstream: EventSink,
        *,
        engineer_model: str,
        reviewer_model: str,
        on_phase_change: Any = None,  # Callable[[str, dict], None] | None
    ) -> None:
        self.downstream = downstream
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        self.engineer_input_tokens = 0
        self.engineer_output_tokens = 0
        self.reviewer_input_tokens = 0
        self.reviewer_output_tokens = 0
        self._on_phase_change = on_phase_change
        self._reviewer_notified = False
        self._engineer_round_count = 0
        self.engineer_cached_input_tokens = 0
        self.reviewer_cached_input_tokens = 0
        self._cumulative_usage_baselines: dict[
            tuple[str, str], tuple[int, int, int]
        ] = {}

    def handle_event(self, event: dict[str, Any]) -> None:
        try:
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "round.main.completed":
                in_tok, cached_tok, out_tok = self._usage_delta(
                    event,
                    layer="engineer",
                )
                self.engineer_input_tokens += in_tok
                self.engineer_cached_input_tokens += cached_tok
                self.engineer_output_tokens += out_tok
                self._engineer_round_count += 1
            elif kind == "round.review.started":
                if not self._reviewer_notified and self._on_phase_change:
                    self._reviewer_notified = True
                    try:
                        self._on_phase_change("reviewer", {
                            "round_index": event.get("round_index", 0),
                            "status": "started",
                            "engineer_rounds": self._engineer_round_count,
                        })
                    except Exception:  # noqa: BLE001
                        log.debug("phase change callback failed", exc_info=True)
            elif kind == "round.review.completed":
                in_tok, cached_tok, out_tok = self._usage_delta(
                    event,
                    layer="reviewer",
                )
                self.reviewer_input_tokens += in_tok
                self.reviewer_cached_input_tokens += cached_tok
                self.reviewer_output_tokens += out_tok
        except Exception:  # noqa: BLE001
            log.debug("cost-tracking sink ignored malformed event", exc_info=True)
        # Always forward.
        try:
            self.downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("downstream event sink raised; continuing")

    def handle_stream_line(self, stream: str, line: str) -> None:  # noqa: ARG002
        """Forward stream lines when the downstream sink supports them."""
        try:
            handler = getattr(self.downstream, "handle_stream_line", None)
            if handler is not None:
                handler(stream, line)
        except Exception:  # noqa: BLE001
            log.exception("downstream stream handler raised; continuing")

    def close(self) -> None:
        try:
            closer = getattr(self.downstream, "close", None)
            if closer is not None:
                closer()
        except Exception:  # noqa: BLE001
            log.exception("downstream close raised; continuing")

    def total_usd(self) -> float:
        return self.engineer_usd() + self.reviewer_usd()

    def engineer_usd(self) -> float:
        return usd_for_tokens(
            self.engineer_model,
            self.engineer_input_tokens,
            self.engineer_cached_input_tokens,
            self.engineer_output_tokens,
            price_lookup=price_for,
        )

    def reviewer_usd(self) -> float:
        return usd_for_tokens(
            self.reviewer_model,
            self.reviewer_input_tokens,
            self.reviewer_cached_input_tokens,
            self.reviewer_output_tokens,
            price_lookup=price_for,
        )

    def total_input_tokens(self) -> int:
        return self.engineer_input_tokens + self.reviewer_input_tokens

    def total_output_tokens(self) -> int:
        return self.engineer_output_tokens + self.reviewer_output_tokens

    def _usage_delta(
        self,
        event: dict[str, Any],
        *,
        layer: str,
    ) -> tuple[int, int, int]:
        raw = (
            int(event.get("input_tokens", 0) or 0),
            int(event.get("cached_input_tokens", 0) or 0),
            int(event.get("output_tokens", 0) or 0),
        )
        if str(event.get("usage_scope") or "delta").lower() != "cumulative":
            return raw

        session_id = str(
            event.get("session_id")
            or event.get("thread_id")
            or event.get("actor")
            or "__global__"
        )
        key = (layer, session_id)
        previous = self._cumulative_usage_baselines.get(key)
        self._cumulative_usage_baselines[key] = raw
        if previous is None:
            return raw
        delta = (
            raw[0] - previous[0],
            raw[1] - previous[1],
            raw[2] - previous[2],
        )
        if any(value < 0 for value in delta):
            log.debug(
                "cumulative usage decreased; treating current event as fresh delta "
                "(layer=%s, session_id=%s, previous=%s, current=%s)",
                layer,
                session_id,
                previous,
                raw,
            )
            return raw
        return delta


# ---------------------------------------------------------------------------
# Supervisor
# ---------------------------------------------------------------------------

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


# ----- thin protocol describing what we need from a MissionExecutor --------

class _MissionRunner(Protocol):
    """Structural type for the MissionExecutor we drive.

    We keep this loose so tests can substitute a fake without dragging
    ArgusBot in. Real callers pass an ``argus_skill.daemon.mission_executor.MissionExecutor``.
    """

    def execute(
        self,
        *,
        objective: str,
        sink: EventSink,
        preload_injects: list[str] | None = None,
        prelude_context: str = "",
        scope: str = "",
    ) -> Any:  # MissionOutcome
        raise NotImplementedError


class LifeSupervisor:
    """Cross-mission scheduler.

    Public API:

    - :meth:`run` — drive missions until backlog is exhausted, the
      iteration cap is hit, the budget is tripped, or ``stop_event``
      is set. Returns a summary dict (mission count, costs, statuses).

    - :meth:`tick` — process a single backlog item if available; useful
      for tests and CLI ``life next``.

    Memory wiring:

    - Before each mission, we render ``LifeMemory.render_prelude(...)``
      using the live objective and forward it as ``prelude_context``.
    - After each mission, we append a ``mission_complete`` /
      ``mission_failed`` journal entry so the next mission can recall it.
    """

    def __init__(
        self,
        *,
        memory: _MemoryView,
        runner: _MissionRunner,
        sink: EventSink,
        config: LifeSupervisorConfig | None = None,
        engineer_model: str = "gpt-5.5",
        reviewer_model: str = "gpt-5.5",
        planner_runner: Any | None = None,
        skill_store: Any | None = None,
    ) -> None:
        self.memory = memory
        self.runner = runner
        self.sink = sink
        self.config = config or LifeSupervisorConfig()
        self.engineer_model = engineer_model
        self.reviewer_model = reviewer_model
        # planner_runner: any RunnerBackend (codex / memory). When None
        # the iteration loop is effectively disabled — items still go
        # ``done`` after the first successful mission. Wired by the
        # life worker / REPL to the same backend the engineer uses.
        self.planner_runner = planner_runner
        # Optional role-scoped skill store for the planner mission matcher.
        # Threaded from the composition root (REPL / life worker). None keeps
        # the planner on fixed role context only (no planner skill pool today).
        self.skill_store = skill_store
        self._missions_started = 0
        self._planning_cycles = 0
        # Idle backoff state (await-external / repeated no-work planner cycles).
        # Persists across daemon outer-loop iterations (the supervisor instance
        # is reused) so backoff escalates while the project waits, and resets
        # the moment a real mission runs.
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0
        # Lifecycle-block log-hygiene state: suppress identical held-state
        # emits except on change or a slow heartbeat.
        self._last_lifecycle_block_sig: tuple[str, str] | None = None
        self._last_lifecycle_block_at = 0.0
        self._reap_orphans_on_startup()

    def _reap_orphans_on_startup(self) -> None:
        """Recover items left ``running`` by a crashed process.

        Items are reset to ``pending`` (up to 3 retries) so they resume
        automatically after a daemon restart. Items that keep crashing
        are marked ``failed`` to prevent poison-pill loops.
        """
        try:
            reaped = self.memory.backlog.reap_orphans()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: orphan reaper failed")
            return
        for it in reaped:
            requeued = it.status == "pending"
            if requeued:
                kind = "mission_requeued"
                title = f"recovered after restart: {it.title}"
                summary = (
                    f"item_id={it.id} "
                    f"retry={it.orphan_retries}/3 "
                    f"will resume automatically"
                )
            else:
                kind = "mission_orphaned"
                title = f"orphaned (max retries): {it.title}"
                summary = (
                    f"item_id={it.id} "
                    f"retries={it.orphan_retries} "
                    f"err={it.last_error}"
                )
            entry = JournalEntry.new(
                kind=kind,
                title=title,
                summary=summary,
                tags=list(it.tags) + ["life", "orphan"],
            )
            try:
                self.memory.journal.append(entry)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to journal orphan %s", it.id)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            self._emit({
                "type": "life.mission.orphaned",
                "item_id": it.id,
                "title": it.title,
                "started_ts": it.started_ts,
                "error": it.last_error,
            })

    @staticmethod
    def _safe_mode_enabled() -> bool:
        return os.environ.get("ARGUS_SKILL_SAFE_MODE", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    def _configured_worktree(self) -> Path | None:
        configured = getattr(self.config, "project_worktree", None)
        if configured is not None:
            return Path(configured).expanduser()
        memory_worktree = getattr(self.memory, "project_worktree", None)
        if memory_worktree is not None:
            return Path(memory_worktree).expanduser()
        return None

    def _project_workdir(self) -> Path:
        configured = self._configured_worktree()
        if configured is not None:
            return configured
        env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
        if env_workdir:
            return Path(env_workdir).expanduser()
        project_root = getattr(self.memory, "project_root", None)
        if project_root:
            return Path(project_root)
        project = getattr(self.memory, "project", None)
        if project is not None:
            root = getattr(project, "root", None)
            if root:
                return Path(root)
        root = getattr(self.memory, "root", None)
        if root:
            return Path(root)
        return Path.cwd()

    def _current_pipeline_stage(self) -> str | None:
        """Read current_stage from PIPELINE_STATE.json, or None if unavailable."""
        try:
            root = self._planner_workdir()
            state_path = root / "research" / "PIPELINE_STATE.json"
            if not state_path.exists():
                return None
            import json as _json
            data = _json.loads(state_path.read_text(encoding="utf-8"))
            return data.get("current_stage")
        except Exception:  # noqa: BLE001
            return None

    def _planner_workdir(self) -> Path:
        configured = self._configured_worktree()
        if configured is not None:
            return configured
        env_workdir = os.environ.get("ARGUS_SKILL_WORKDIR", "").strip()
        if env_workdir:
            return Path(env_workdir).expanduser()
        project_root = getattr(self.memory, "project_root", None)
        if project_root:
            return Path(project_root)
        project = getattr(self.memory, "project", None)
        if project is not None:
            root = getattr(project, "root", None)
            if root:
                return Path(root)
        root = getattr(self.memory, "root", None)
        if root:
            return Path(root)
        return Path.cwd()

    def _planner_config(self):
        from ..planner import PlannerConfig
        from ..tools.capability_vault import resolve_route_model

        safe_mode = self._safe_mode_enabled()
        return PlannerConfig(
            model=resolve_route_model("planner") or self.reviewer_model,
            working_dir=str(self._planner_workdir()),
            skip_git_repo_check=True,
            full_auto=safe_mode,
            dangerous_yolo=not safe_mode,
        )

    # ------------------------------------------------------------------
    # Public driving methods
    # ------------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        """Drive missions until a stop condition. Returns a summary."""
        results: list[dict[str, Any]] = []
        stopped_by: str = ""
        while True:
            # Hot-reload continuous config from provider (disk, etc.)
            self._reload_continuous_config()
            stop_reason = self._maybe_stop()
            if stop_reason:
                if stop_reason != "__silent_stop__":
                    self._emit_status(stop_reason)
                stopped_by = stop_reason
                break
            # Early auto-stop: if this is an EMNLP project and the gate
            # already passes, stop immediately — don't run any more ticks
            # or planner cycles.  This prevents the planner from inventing
            # new work (lint, refactor, etc.) after the paper is done.
            if (
                self.config.continuous
                and self.config.continuous_objective
                and self.config.full_emnlp_gate
                and self._journal_has_full_emnlp_gate_success()
            ):
                self._emit_status(
                    "auto-stop: EMNLP gate passes, project complete"
                )
                stopped_by = "project_done"
                break
            try:
                outcome = self.tick()
            except Exception as exc:  # noqa: BLE001
                err = f"{type(exc).__name__}: {exc}"
                log.exception("life supervisor: tick raised")
                recovered = self._fail_running_items_after_supervisor_error(err)
                self._emit({
                    "type": "life.supervisor.error",
                    "error": err,
                    "recovered_item_ids": recovered,
                })
                results.append({
                    "success": False,
                    "status": "supervisor_error",
                    "reason": err,
                    "recovered_item_ids": recovered,
                })
                stopped_by = "supervisor_error"
                break
            if outcome is None:
                # Backlog empty — continuous mode: ask planner for more
                if self.config.continuous and self.config.continuous_objective:
                    gate_reason = self._planner_cycle_gate_reason()
                    if gate_reason:
                        self._emit({
                            "type": "life.planner.deferred",
                            "reason": gate_reason,
                            "agent_layer": "planner",
                        })
                        self._emit_status(gate_reason)
                        stopped_by = gate_reason
                        break
                    # Auto-stop: if the EMNLP gate already passes, the
                    # project is done — don't ask the planner to invent
                    # more work.
                    if (
                        self.config.full_emnlp_gate
                        and self._journal_has_full_emnlp_gate_success()
                    ):
                        self._emit_status(
                            "planner: project done — EMNLP gate passes"
                        )
                        stopped_by = "project_done"
                        break
                    planned = self._plan_next_work()
                    if planned == "daemon_handoff":
                        stopped_by = "daemon_handoff"
                        break
                    if planned == "planner_retry":
                        stopped_by = "planner_retry"
                        break
                    if planned == _PLAN_AWAITING:
                        # Planner intentionally idled awaiting an external job.
                        # Return cleanly with a suggested backoff so the daemon
                        # outer loop sleeps (escalating) before re-checking,
                        # instead of make-work or a tight re-plan spin.
                        stopped_by = _PLAN_AWAITING
                        break
                    if planned is True:
                        continue  # new items in backlog, loop around
                    if planned is False:
                        self._emit_status("planner: project done")
                        stopped_by = "project_done"
                        break
                    stopped_by = "planner_error"
                    break
                # Non-continuous: sleep then re-check (so user-added
                # items via the file get picked up). Sleep is bounded
                # by the stop_event so a Ctrl-C shuts us down quickly.
                if self._wait_idle():
                    self._emit_status("stop requested while idle")
                    stopped_by = "stop_requested"
                    break
                # Re-check: if backlog still empty, exit cleanly so
                # `life run --once` semantics work in tests.
                if self.memory.backlog.next_pending() is None:
                    self._emit_status("backlog empty; exiting")
                    stopped_by = "backlog_empty"
                    break
                continue
            results.append(outcome)
            # A real mission ran: clear any accumulated no-work backoff.
            self._reset_idle_backoff()
            # Auth failure flagged by _run_one: propagate immediately
            if outcome.get("auth_failure"):
                stopped_by = "auth_failure"
                break
            post_mission_stop = self._post_mission_hook(outcome)
            if post_mission_stop:
                self._emit({
                    "type": "life.post_mission.stop",
                    "reason": post_mission_stop,
                    "item_id": outcome.get("item_id"),
                    "status": outcome.get("status"),
                })
                self._emit_status(post_mission_stop)
                stopped_by = post_mission_stop
                break
            # Stop conditions that ``tick`` signals via the result dict
            # (budget pause leaves the item PENDING on purpose so a
            # later supervisor run can retry — but for THIS run we must
            # not spin on the same blocked item).  ``lifecycle_block`` is
            # the same shape: the F5 gate leaves the item PENDING and
            # asks for human resume/archive, so we must break out instead
            # of re-ticking the same held item every loop (which would
            # busy-spin ``infer_observable_status`` at 100% CPU). The
            # daemon's outer loop re-enters after ``poll_interval``.
            if outcome.get("status") in {
                "budget_pause",
                "iteration_cap",
                "lifecycle_block",
            }:
                stopped_by = outcome.get("status", "")
                break
        return {
            "missions_started": self._missions_started,
            "planning_cycles": self._planning_cycles,
            "results": results,
            "stopped_by": stopped_by,
            "suggested_sleep": self._suggested_sleep_s,
        }

    def _fail_running_items_after_supervisor_error(self, error: str) -> list[str]:
        """Best-effort cleanup when an unexpected supervisor error escapes.

        ``_run_one`` normally finalizes its claimed item, but this guard
        prevents a bug outside that narrow try/except from leaving durable
        ``running`` rows forever.
        """
        try:
            items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog after error")
            return []

        recovered: list[str] = []
        for item in items:
            if getattr(item, "status", "") != "running":
                continue
            item_id = str(getattr(item, "id", "") or "")
            if not item_id:
                continue
            title = str(getattr(item, "title", "") or "running mission")
            objective = str(getattr(item, "objective", "") or "")
            failure_reason = f"supervisor error: {error}"
            try:
                self.memory.backlog.mark_failed(item_id, error=failure_reason)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to mark running item failed: %s", item_id)
                continue
            recovered.append(item_id)
            entry = JournalEntry.new(
                kind="mission_failed",
                title=title,
                summary=f"status=supervisor_error; rounds=0; exc={error}",
                tags=list(getattr(item, "tags", []) or []) + ["life"],
                extra={
                    "item_id": item_id,
                    "objective": objective,
                    "terminal_status": "supervisor_error",
                    "failure_reason": failure_reason,
                    "agent_layer": "supervisor",
                },
            )
            try:
                self.memory.journal.append(entry)
                self._inject_cumulative_cost(entry)
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to journal supervisor error")
            self._emit({
                "type": "life.mission.completed",
                "item_id": item_id,
                "success": False,
                "status": "supervisor_error",
                "rounds": 0,
                "cost_usd": 0.0,
                "journal_entry_id": entry.id,
            })
        return recovered

    def _planner_cycle_gate_reason(self) -> str:
        gate = self.config.planner_cycle_gate
        if gate is None:
            return ""
        try:
            reason = gate()
        except Exception:  # noqa: BLE001
            log.exception("planner cycle gate raised; continuing with planner")
            return ""
        return str(reason or "").strip()

    def _planner_runtime_context(self) -> str:
        provider = self.config.planner_runtime_context_provider
        if provider is None:
            return ""
        try:
            context = provider()
        except Exception:  # noqa: BLE001
            log.exception("planner runtime context provider raised; continuing")
            return ""
        return str(context or "").strip()

    def _planner_project_context(self) -> str:
        """Return cheap project-state context that keeps planner work grounded."""
        return self._planner_runtime_context()

    def _recent_no_progress_failures(self) -> dict[tuple[str, str], JournalEntry]:
        """Return recent failed task signatures quarantined from replanning."""
        try:
            # Read a wider tail and drop Signal-B failure-observation rows
            # (counting plumbing) so they can't push real no-progress
            # failures out of the quarantine window.
            recent_entries = [
                e
                for e in self.memory.journal.tail(_PLANNER_RECENT_HISTORY_WINDOW * 4)
                if getattr(e, "kind", "") != "self_evolve.failure_observation"
            ][-_PLANNER_RECENT_HISTORY_WINDOW:]
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to read recent journal for planner")
            return {}
        matches: dict[tuple[str, str], JournalEntry] = {}
        for entry in reversed(recent_entries):
            if not _is_recent_no_progress_failure(entry):
                continue
            signature = _entry_task_signature(entry)
            if signature is None or signature in matches:
                continue
            matches[signature] = entry
        return matches

    def _handle_planner_restart(self, reason: str) -> bool:
        handler = self.config.planner_restart_handler
        if handler is None:
            return False
        try:
            return bool(handler(reason))
        except Exception:  # noqa: BLE001
            log.exception("planner restart handler raised; continuing")
            return False

    def _post_mission_hook(self, outcome: dict[str, Any]) -> str:
        hook = self.config.post_mission_hook
        if hook is None:
            return ""
        try:
            return str(hook(outcome) or "").strip()
        except Exception:  # noqa: BLE001
            log.exception("post mission hook raised; continuing")
            return ""

    def tick(self) -> dict[str, Any] | None:
        """Process at most one backlog item. Returns its result dict or
        ``None`` if nothing was eligible to run."""
        item = self.memory.backlog.next_pending()
        if item is None:
            return None

        ok, reason = self.config.budget.can_start(
            item=item, journal=self.memory.journal
        )
        if not ok:
            # Don't fail the item — it'll be retried next supervisor
            # run when the daily cap rolls over. Just journal it and
            # signal the caller to exit cleanly.
            self._emit_status(f"budget block: {reason}")
            entry = JournalEntry.new(
                kind="budget_pause",
                title=f"paused before '{item.title}'",
                summary=reason,
                tags=["budget"],
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return {"status": "budget_pause", "item_id": item.id, "reason": reason}

        if not self.config.continuous and self._missions_started >= self.config.budget.max_missions:
            # Only narrate the cap when there's actually pending work
            # being held back. If the backlog is empty (or the user
            # asked for ``--once`` and we just ran their one mission),
            # this message is just noise.
            try:
                more_pending = self.memory.backlog.next_pending() is not None
            except Exception:  # noqa: BLE001
                more_pending = False
            if more_pending:
                self._emit_status(
                    f"max-missions cap reached ({self.config.budget.max_missions})"
                )
            return {"status": "iteration_cap", "item_id": item.id}

        # F5 project-lifecycle gate. Recompute observable status from the
        # project tree, overlay any persisted state (e.g. user quarantine),
        # then ask the policy engine if a transition is warranted. If the
        # resulting state is non-allocatable (quarantined/done/archived),
        # skip this tick — no token budget is spent and the user must
        # explicitly resume / archive.
        lifecycle_block = self._maybe_block_on_lifecycle(item)
        if lifecycle_block is not None:
            return lifecycle_block

        result = self._run_one(item)

        # Self-evolve hook (Signal A): scan the just-finished mission's
        # observable surface for missing-tool patterns. **Detection is
        # structural** (regex pattern grep — pure plumbing) but **the
        # mint decision is judgment** ("is this worth minting? was it
        # a typo? did we just work around it?"). Per skill 04, harness
        # only does the structural half. We write each detected signal
        # as a journal advisory; the reviewer / planner reads recent
        # advisories and decides whether to request a mint-skill
        # mission. This mirrors how F3 mediocrity_finding surfaces
        # facts to the reviewer without ruling.
        try:
            self._maybe_journal_self_evolve_advisory(item, result)
        except Exception:  # noqa: BLE001 — never let self-evolve crash tick
            log.exception("self-evolve advisory hook raised; tick continues")
        # Self-evolve hook (Signal B): scan for INFRA-FAILURE patterns that
        # recur across missions. Where Signal A flags a missing tool, Signal
        # B flags the same failure CLASS (CUDA OOM, vLLM engine-init, NCCL,
        # ...) recurring across N distinct missions — misconfigs of existing
        # tools that Signal A skips. Detection + counting is structural; the
        # mint decision stays with the reviewer/planner.
        try:
            self._maybe_journal_recurring_failure_advisory(item, result)
        except Exception:  # noqa: BLE001 — never let self-evolve crash tick
            log.exception("recurring-failure advisory hook raised; tick continues")
        return result

    # ------------------------------------------------------------------
    # Self-evolve: thin delegate to SelfEvolveAdvisor
    # ------------------------------------------------------------------
    # Logic lives in argus_skill/life/self_evolve_advisor.py per
    # nssmd/skills/06-keep-files-small.md. supervisor.py is already
    # ~1800 lines; the self-evolve concern (advisory journaling +
    # dedup + event tailing) belongs in its own module so it can be
    # discovered + tested + refactored independently.

    _MINT_SKILL_TAG = "mint-skill"  # back-compat: tests import this

    def _maybe_journal_self_evolve_advisory(
        self, item: BacklogItem, result: dict[str, Any] | None
    ) -> list[str]:
        """Surface missing-tool patterns as journal advisories.

        Thin delegate — see ``self_evolve_advisor.SelfEvolveAdvisor``
        for the logic. Kept here as a stable method on supervisor so
        existing tests + monkeypatches don't have to change.
        """
        from .self_evolve_advisor import SelfEvolveAdvisor
        return SelfEvolveAdvisor(
            memory=self.memory,
            on_cost=self._inject_cumulative_cost,
        ).maybe_journal_advisory(item, result)

    def _maybe_journal_recurring_failure_advisory(
        self, item: BacklogItem, result: dict[str, Any] | None
    ) -> list[str]:
        """Surface recurring infra-failure patterns as journal advisories.

        Thin delegate — see ``recurring_failure_advisor.RecurringFailureAdvisor``
        (Signal B). Kept as a stable supervisor method for tests/monkeypatch.
        """
        from .recurring_failure_advisor import RecurringFailureAdvisor
        return RecurringFailureAdvisor(
            memory=self.memory,
            on_cost=self._inject_cumulative_cost,
        ).maybe_journal_advisory(item, result)

    # ------------------------------------------------------------------
    # F5 project-lifecycle gate
    # ------------------------------------------------------------------

    def _lifecycle_root(self) -> Path:
        """Directory holding this project's ``lifecycle.json`` sidecar.

        Prefer the per-project telemetry/life dir so lifecycle state is
        isolated per project. Fall back to the global memory root when no
        telemetry dir is configured (non-daemon ``life run`` / tests), which
        preserves the historical single-file behavior.
        """
        tdir = getattr(self.config, "telemetry_dir", None)
        if tdir is not None:
            return Path(tdir)
        return Path(getattr(self.memory, "root", None) or ".")

    def _migrate_global_lifecycle_if_needed(self, per_root: Path) -> None:
        """One-time carry-over of the legacy GLOBAL lifecycle sidecar.

        Historically lifecycle.json lived under the global memory root and was
        (incorrectly) shared across projects. When a per-project dir is now in
        use and has no sidecar yet, copy the legacy global file in once, then
        retire the global file (rename to ``*.migrated``) so future projects
        start clean instead of inheriting a mis-keyed shared state. Best-effort
        and idempotent; only runs in the per-project (telemetry_dir) regime.
        """
        if getattr(self.config, "telemetry_dir", None) is None:
            return
        if getattr(self, "_lifecycle_migrated", False):
            return
        self._lifecycle_migrated = True
        try:
            per_file = _lifecycle_path(per_root)
            if per_file.exists():
                return
            global_root = Path(getattr(self.memory, "root", None) or ".")
            if global_root == per_root:
                return
            global_file = _lifecycle_path(global_root)
            if not global_file.exists():
                return
            per_root.mkdir(parents=True, exist_ok=True)
            data = global_file.read_text(encoding="utf-8")
            tmp = per_file.with_name(per_file.name + ".tmp")
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, per_file)
            try:
                global_file.replace(
                    global_file.with_name(global_file.name + ".migrated")
                )
            except OSError:
                log.warning(
                    "lifecycle: copied global sidecar to %s but could not "
                    "retire %s; future projects may inherit it",
                    per_file, global_file,
                )
            log.info(
                "lifecycle: migrated legacy global sidecar into per-project "
                "dir %s", per_file,
            )
        except Exception:  # noqa: BLE001
            log.exception(
                "lifecycle migration failed; continuing with fresh "
                "per-project state"
            )

    def _maybe_block_on_lifecycle(
        self, item: BacklogItem
    ) -> dict[str, Any] | None:
        """Run one F5 tick and decide whether to short-circuit dispatch.

        Returns ``None`` when the project is allocatable (supervisor
        proceeds to ``_run_one``). Returns a status dict when blocked —
        the daemon treats this like a budget pause.

        Fail-soft: any exception in the lifecycle path is logged and
        treated as "allocatable" so a corrupt sidecar can never wedge
        the supervisor.
        """
        try:
            memory_root = self._lifecycle_root()
            self._migrate_global_lifecycle_if_needed(memory_root)
            project_root = self._project_workdir()
            spent_usd, budget_usd = self._lifecycle_budget_snapshot()

            status = infer_observable_status(
                project_root,
                project_id=memory_root.name,
                budget_usd=budget_usd,
                spent_usd=spent_usd,
            )
            try:
                persisted = _lifecycle_load_persisted(memory_root)
            except LifecycleIOError as exc:
                log.warning(
                    "lifecycle sidecar at %s is malformed (%s); "
                    "treating project as fresh",
                    memory_root, exc,
                )
                persisted = {}
            status = apply_persisted_to_status(status, persisted)

            # EMNLP completion authority is the L2 reviewer's
            # ``final_submission`` certification — NOT the mere presence
            # of ``paper/main.pdf`` (which the agent compiles for format
            # preflight long before the draft is submission-ready). The
            # generic F5 rule ``submission_artifact_present -> DONE`` is
            # therefore premature for an uncertified full-EMNLP mission,
            # and DONE is terminal + non-allocatable, so it permanently
            # starves the project of tokens. Defer to the reviewer:
            #
            #   (a) repair an already-persisted bad DONE back to WRITING
            #       once (preserving history via append_event), and
            #   (b) suppress a fresh ``submission_artifact_present`` DONE
            #       transition *before* it is applied/journaled/persisted
            #       so we don't re-fire + spam the journal every tick.
            #
            # When the reviewer truly certifies, supervisor.run() auto-
            # stops via ``_journal_has_full_emnlp_gate_success`` instead.
            uncertified_full_emnlp = (
                self.config.full_emnlp_gate
                and not self._journal_has_full_emnlp_gate_success()
            )
            if (
                uncertified_full_emnlp
                and status.state == ProjectState.DONE
                and persisted.get("state") == ProjectState.DONE.value
            ):
                from datetime import datetime, timezone

                repair_event = LifecycleEvent(
                    at=datetime.now(timezone.utc),
                    from_state=ProjectState.DONE,
                    to_state=ProjectState.WRITING,
                    reason="full_emnlp_gate_not_certified",
                )
                status = apply_event(status, repair_event)
                try:
                    _lifecycle_append_event(
                        memory_root,
                        new_status=status,
                        event=repair_event,
                    )
                except OSError as exc:
                    log.warning(
                        "could not repair premature-DONE lifecycle "
                        "sidecar at %s: %s",
                        memory_root, exc,
                    )
                entry = JournalEntry.new(
                    kind="lifecycle_transition",
                    title="done → writing",
                    summary=(
                        "premature DONE from preflight main.pdf repaired; "
                        "EMNLP final_submission not yet reviewer-certified"
                    ),
                    tags=["lifecycle", ProjectState.WRITING.value],
                )
                self.memory.journal.append(entry)
                self._inject_cumulative_cost(entry)

            event = decide_next_state(status)
            if (
                uncertified_full_emnlp
                and event is not None
                and event.to_state == ProjectState.DONE
                and event.reason == "submission_artifact_present"
            ):
                # Suppress non-persistently: drop the event entirely so it
                # is never applied, journaled, or persisted. The project
                # stays WRITING (allocatable) until the reviewer certifies.
                event = None
            if event is not None:
                status = apply_event(status, event)
                try:
                    _lifecycle_append_event(
                        memory_root,
                        new_status=status,
                        event=event,
                    )
                except OSError as exc:
                    log.warning(
                        "could not persist lifecycle transition to %s: %s",
                        memory_root, exc,
                    )
                entry = JournalEntry.new(
                    kind="lifecycle_transition",
                    title=(
                        f"{event.from_state.value} → {event.to_state.value}"
                    ),
                    summary=event.reason,
                    tags=["lifecycle", event.to_state.value],
                )
                self.memory.journal.append(entry)
                self._inject_cumulative_cost(entry)

            # Advisory time signals (incubating_time / running_evidence_gap
            # / writing_idle) are PULLED on demand by --status / cockpit /
            # telegram digest, not PUSHED into the journal every tick.
            # See ``advisory_time_signals`` in project_lifecycle.py. The
            # harness must not spam the journal with non-event facts.

            if not is_token_allocatable(status):
                # Log hygiene: the held-item status/journal line is identical
                # every tick a project sits in the same non-allocatable state.
                # Emit + journal only when the (state, item) signature changes
                # or a heartbeat interval elapses — otherwise a long block used
                # to flood events.jsonl / activity.log with tens of thousands
                # of identical lines. Dispatch behavior is unchanged: we always
                # return the block dict.
                state_value = status.state.value
                sig = (state_value, item.id)
                now = time.monotonic()
                reason = (
                    f"project lifecycle is {state_value}; "
                    f"resume with --lifecycle-resume or archive with "
                    f"--lifecycle-archive"
                )
                last_sig = getattr(self, "_last_lifecycle_block_sig", None)
                last_at = getattr(self, "_last_lifecycle_block_at", 0.0)
                should_emit = (
                    sig != last_sig
                    or (now - last_at) >= _LIFECYCLE_BLOCK_HEARTBEAT_SECONDS
                )
                if should_emit:
                    self._last_lifecycle_block_sig = sig
                    self._last_lifecycle_block_at = now
                    self._emit_status(
                        f"lifecycle gate: project state={state_value}; "
                        f"backlog item {item.id!r} held"
                    )
                    entry = JournalEntry.new(
                        kind="lifecycle_block",
                        title=f"held '{item.title}' (state={state_value})",
                        summary=reason,
                        tags=["lifecycle", state_value],
                    )
                    self.memory.journal.append(entry)
                    self._inject_cumulative_cost(entry)
                return {
                    "status": "lifecycle_block",
                    "item_id": item.id,
                    "lifecycle_state": state_value,
                    "reason": reason,
                }
        except Exception:  # noqa: BLE001
            log.exception("lifecycle gate failed; allowing dispatch")
        return None

    def _lifecycle_budget_snapshot(self) -> tuple[float, float]:
        """Best-effort (spent_usd, total_budget_usd) for F5 budget gate.

        LifeBudget tracks per-mission and daily caps; we approximate the
        project's total budget as ``daily_cap * 30`` (a month of running)
        and spent as the journal-recorded cumulative cost. If either is
        unavailable, returns ``(0.0, 0.0)`` and the F5 budget gate stays
        dormant for that tick.
        """
        try:
            budget = getattr(self.config, "budget", None)
            if budget is None:
                return (0.0, 0.0)
            daily = float(getattr(budget, "daily_cap_usd", 0.0) or 0.0)
            total = daily * 30.0
            spent = 0.0
            try:
                spent = float(
                    sum(
                        float(getattr(e, "cost_usd", 0.0) or 0.0)
                        for e in self.memory.journal.all()
                    )
                )
            except Exception:  # noqa: BLE001
                spent = 0.0
            return (spent, total)
        except Exception:  # noqa: BLE001
            return (0.0, 0.0)

    # ------------------------------------------------------------------
    # One mission
    # ------------------------------------------------------------------

    def _run_one(self, item: BacklogItem) -> dict[str, Any]:
        prelude = self.memory.render_prelude(objective=item.objective)
        item_metadata = self._render_backlog_item_metadata(item)
        if item_metadata:
            prelude = item_metadata + "\n---\n\n" + prelude if prelude else item_metadata
        # Inject runtime context (backend, models, budget) so the agent
        # knows its own environment. Placed before operator nudges so
        # nudges can override if needed.
        rt = self.config.runtime_context
        if rt:
            prelude = rt + "\n---\n\n" + prelude if prelude else rt
        # Drain any pending operator nudges from the inbox bus and
        # splice them in front of the prelude as live operator
        # guidance. Each round in the engineer loop will see this as
        # `Operator message history`.
        nudges = self._drain_user_inbox()
        if nudges:
            prelude = (
                "## Operator messages (live nudges, most recent last)\n"
                + "\n".join(f"- {m}" for m in nudges)
                + "\n\n---\n\n"
                + prelude
            )
        # Atomic claim: flip pending → running in one rewrite. If the
        # head moved between the budget peek and now (concurrent writer
        # or user `/rm`), bail; the next tick will re-evaluate.
        claimed = self.memory.backlog.claim_next()
        if claimed is None or claimed.id != item.id:
            if claimed is not None:
                # Roll back so the next tick sees it again. running →
                # pending is a legal transition (only terminal states
                # are sealed).
                try:
                    self.memory.backlog.update(claimed.id, status="pending")
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: claim rollback failed")
            return {"status": "claim_lost", "item_id": item.id}
        item = claimed
        self._missions_started += 1

        self._emit({
            "type": "life.mission.started",
            "item_id": item.id,
            "title": item.title,
            "missions_started": self._missions_started,
        })
        # Notify: mission starting (engineer layer)
        try:
            start_entry = JournalEntry.new(
                kind="mission_started",
                title=item.title,
                summary=f"objective={item.objective[:200]}",
                tags=list(item.tags) + ["life"],
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "engineer",
                },
            )
            self.memory.journal.append(start_entry)
            self._inject_cumulative_cost(start_entry)
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(start_entry)
        except Exception:  # noqa: BLE001
            log.debug("mission_started notify failed; non-critical")

        # Phase-change callback: notifies Telegram when reviewer starts
        def _phase_cb(layer: str, info: dict[str, Any]) -> None:
            try:
                self._emit({
                    "type": "life.phase.started",
                    "item_id": item.id,
                    "agent_layer": layer,
                    "round_index": info.get("round_index", 0),
                })
                from .notify import dispatch_journal_entry
                entry = JournalEntry.new(
                    kind="phase_change",
                    title=item.title,
                    summary=f"round {info.get('round_index', '?')}: {layer} 开始评审",
                    tags=["life", "phase"],
                    extra={
                        "item_id": item.id,
                        "objective": item.objective,
                        "agent_layer": layer,
                        "engineer_rounds": info.get("engineer_rounds", 0),
                    },
                )
                # Don't journal phase changes — just notify.
                # Pass in-flight cost so cumulative includes current mission.
                self._inject_cumulative_cost(
                    entry, in_flight_usd=cost_sink.total_usd(),
                )
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.debug("phase_change notify failed; non-critical")

        cost_sink = _CostTrackingSink(
            self.sink,
            engineer_model=self.engineer_model,
            reviewer_model=self.reviewer_model,
            on_phase_change=_phase_cb,
        )

        telemetry_monitor: Any = None
        if self.config.telemetry_dir is not None:
            try:
                from .telemetry import MissionTelemetryMonitor
                telemetry_monitor = MissionTelemetryMonitor(
                    life_dir=self.config.telemetry_dir,
                    workdir=self._project_workdir(),
                    item_id=item.id,
                    title=item.title,
                    interval_seconds=self.config.telemetry_interval_seconds,
                    stop_event=self.config.stop_event,
                )
                telemetry_monitor.start()
            except Exception:  # noqa: BLE001
                log.exception("life supervisor: failed to start telemetry monitor")

        outcome: Any = None
        exc_str: str | None = None
        t0 = time.time()
        try:
            outcome = self.runner.execute(
                objective=item.objective,
                sink=cost_sink,
                prelude_context=prelude,
                scope=self._planner_scope_from_item(item),
            )
        except Exception as exc:  # noqa: BLE001
            exc_str = f"{type(exc).__name__}: {exc}"
            log.exception("life supervisor: mission raised")
        finally:
            if telemetry_monitor is not None:
                try:
                    telemetry_monitor.stop()
                except Exception:  # noqa: BLE001
                    log.exception("life supervisor: failed to stop telemetry monitor")
        elapsed = time.time() - t0

        success = bool(getattr(outcome, "success", False)) if outcome else False
        status = str(getattr(outcome, "status", "error") if outcome else "error")
        rounds = int(getattr(outcome, "rounds", 0) or 0)
        stop_reason = str(getattr(outcome, "stop_reason", "") or "")
        usd = cost_sink.total_usd()

        # Emit per-layer completion notifications with actual costs
        try:
            from .notify import dispatch_journal_entry
            eng_usd = cost_sink.engineer_usd()
            rev_usd = cost_sink.reviewer_usd()
            # L1 engineer completed
            eng_done = JournalEntry.new(
                kind="phase_change",
                title=item.title,
                summary=f"工程师完成: {rounds}轮, ${eng_usd:.4f}",
                tags=["life", "phase"],
                cost_usd=eng_usd,
                extra={
                    "item_id": item.id,
                    "objective": item.objective,
                    "agent_layer": "engineer",
                    "phase_status": "completed",
                    "rounds": rounds,
                    "input_tokens": cost_sink.engineer_input_tokens,
                    "output_tokens": cost_sink.engineer_output_tokens,
                },
            )
            self._inject_cumulative_cost(eng_done, in_flight_usd=usd)
            dispatch_journal_entry(eng_done)
            # L2 reviewer completed (only if reviewer was actually used)
            if cost_sink.reviewer_input_tokens > 0:
                rev_done = JournalEntry.new(
                    kind="phase_change",
                    title=item.title,
                    summary=f"审查员完成: ${rev_usd:.4f}",
                    tags=["life", "phase"],
                    cost_usd=rev_usd,
                    extra={
                        "item_id": item.id,
                        "objective": item.objective,
                        "agent_layer": "reviewer",
                        "phase_status": "completed",
                        "input_tokens": cost_sink.reviewer_input_tokens,
                        "output_tokens": cost_sink.reviewer_output_tokens,
                    },
                )
                self._inject_cumulative_cost(rev_done, in_flight_usd=usd)
                dispatch_journal_entry(rev_done)
        except Exception:  # noqa: BLE001
            log.debug("layer completion notify failed; non-critical")

        # Auth failure: the codex backend detected an expired/invalid
        # token. Stop this drain pass so we do not immediately continue
        # with stale credentials, but do not signal the daemon's global
        # stop_event. A 7x24 worker should stay alive so it can recover
        # after credentials are refreshed, and transient provider errors
        # should not kill the supervising process.
        auth_failure = bool(getattr(outcome, "auth_failure", False))
        if auth_failure:
            self._emit({
                "type": "life.auth_failure",
                "item_id": item.id,
                "text": (
                    "⚠️  codex authentication failed — run `codex login` "
                    "to refresh credentials if this persists; the daemon "
                    "will keep polling."
                ),
            })

        # The post-mission critic/polish iteration loop was removed. The L1
        # engineer does the work and the L2 reviewer verifies it; there is no
        # separate critic agent. ``iteration_outcome`` is retained as ``None``
        # so the downstream journal/event payloads stay schema-compatible.
        iteration_outcome: dict[str, Any] | None = None

        # Update backlog row.
        if iteration_outcome and iteration_outcome.get("requeued"):
            # Item is back to ``pending``; do not mark_done. The next
            # tick will pick it up and re-execute with the polished
            # objective.
            pass
        elif success:
            self.memory.backlog.mark_done(item.id)
        else:
            err = exc_str or stop_reason or "unspecified failure"
            self.memory.backlog.mark_failed(item.id, error=err)

        # Journal entry.
        if iteration_outcome and iteration_outcome.get("requeued"):
            kind = "mission_iterated"
        else:
            kind = "mission_complete" if success else "mission_failed"
        summary_parts = [
            f"status={status}",
            f"rounds={rounds}",
            f"elapsed={elapsed:.1f}s",
            f"tokens_in={cost_sink.total_input_tokens()}",
            f"tokens_out={cost_sink.total_output_tokens()}",
            f"cost_usd=${usd:.4f}",
        ]
        if iteration_outcome:
            summary_parts.append(
                f"iter={iteration_outcome.get('cycles_done', 0)}/{item.iteration_max_cycles}"
            )
            if iteration_outcome.get("requeued"):
                summary_parts.append(
                    f"improvements={iteration_outcome.get('improvement_count', 0)}"
                )
            elif iteration_outcome.get("stop_reason"):
                summary_parts.append(f"iter_stop={iteration_outcome['stop_reason']}")
        if stop_reason:
            summary_parts.append(f"reason={stop_reason}")
        if exc_str:
            summary_parts.append(f"exc={exc_str}")
        entry = JournalEntry.new(
            kind=kind,
            title=item.title,
            summary="; ".join(summary_parts),
            tags=list(item.tags) + ["life"],
            cost_usd=usd,
            extra={
                "item_id": item.id,
                "objective": item.objective,
                "planner_task_signature": {
                    "title": _normalize_planner_text(item.title),
                    "objective": _normalize_planner_text(item.objective),
                }
                if kind == "mission_failed"
                else {},
                "terminal_status": status if kind == "mission_failed" else "",
                "stop_reason": (stop_reason or err) if kind == "mission_failed" else "",
                "failure_reason": err if kind == "mission_failed" else "",
                "agent_layer": "planner" if iteration_outcome and iteration_outcome.get("requeued") else "engineer",
                "engineer_model": self.engineer_model,
                "reviewer_model": self.reviewer_model,
                "input_tokens": cost_sink.total_input_tokens(),
                "output_tokens": cost_sink.total_output_tokens(),
                "matched_skill": str(getattr(outcome, "matched_skill_name", "") or ""),
                "skill_distilled": bool(getattr(outcome, "skill_distilled", False)),
                "had_follow_up": bool(getattr(outcome, "had_follow_up", False)),
                "completion_summary": self._completion_evidence_from_outcome(outcome),
                "planner_report": (
                    getattr(outcome, "planner_report", {})
                    if isinstance(getattr(outcome, "planner_report", {}), dict)
                    else {}
                ),
                "final_submission_certified": bool(
                    kind == "mission_complete"
                    and self._planner_scope_from_item(item)
                    == _PLANNER_SCOPE_FINAL_SUBMISSION
                    and getattr(outcome, "final_submission_certified", False)
                ),
                "iteration": iteration_outcome or {},
            },
        )
        self.memory.journal.append(entry)
        self._inject_cumulative_cost(entry)
        try:
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(entry)
        except Exception:  # noqa: BLE001
            log.exception("notify dispatch failed; continuing")

        self._emit({
            "type": "life.mission.completed",
            "item_id": item.id,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
            "iteration": iteration_outcome or None,
        })

        return {
            "item_id": item.id,
            "title": item.title,
            "success": success,
            "status": status,
            "rounds": rounds,
            "cost_usd": usd,
            "journal_entry_id": entry.id,
            "iteration": iteration_outcome,
            "auth_failure": auth_failure,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _drain_user_inbox(self, *, max_messages: int = 10) -> list[str]:
        """Pull all pending operator nudges from the configured inbox.

        Returns up to ``max_messages`` lines (oldest-first). Empty list
        if no inbox is configured or nothing is pending. Any exception
        from the user-supplied callable is swallowed — a flaky bus
        must never break a mission.
        """
        cb = getattr(self.config, "user_inbox", None)
        if cb is None:
            return []
        out: list[str] = []
        for _ in range(max(1, int(max_messages))):
            try:
                msg = cb()
            except Exception:  # noqa: BLE001
                log.exception("user_inbox callable raised; ignoring")
                break
            if not msg:
                break
            text = str(msg).strip()
            if text:
                out.append(text)
        if out:
            self._emit({
                "type": "life.inbox.drained",
                "count": len(out),
                "messages": out,
            })
            # Opt #4: persistent absorption record. Earlier the inbox
            # drain only injected operator nudges into the engineer's
            # one-round prompt + emitted a transient event. That
            # gave the operator no way to confirm "did the daemon
            # actually see my notify?" hours later. Now we write a
            # ``inbox.injected`` journal entry per drain so the
            # operator (via --status / --watch / cockpit) can verify
            # absorption, and the feedback-parser skill (run later
            # by the engineer/planner agent) has a stable record to
            # iterate over for L1 polish / L2 mint decisions.
            try:
                summary = " | ".join(
                    (m if len(m) < 80 else m[:77] + "...") for m in out
                )
                entry = JournalEntry.new(
                    kind="inbox.injected",
                    title=f"{len(out)} operator message(s) injected",
                    summary=summary,
                    tags=["inbox", "feedback"],
                )
                self.memory.journal.append(entry)
                self._inject_cumulative_cost(entry)
            except Exception:  # noqa: BLE001
                log.exception(
                    "inbox journal-write failed; messages still injected to engineer"
                )
        return out

    def _maybe_stop(self) -> str:
        ev = self.config.stop_event
        if ev is not None and ev.is_set():
            return "stop_event signalled"
        # In continuous mode, max_missions is not a hard cap — the
        # planner generates new work indefinitely until it declares
        # the project done. Only daily budget is enforced.
        if not self.config.continuous:
            if self._missions_started >= self.config.budget.max_missions:
                # Suppress the cap message when there's no held-back work.
                # Treats "you asked for one mission, you got one" as silent
                # success rather than a noisy guardrail trip.
                try:
                    more_pending = self.memory.backlog.next_pending() is not None
                except Exception:  # noqa: BLE001
                    more_pending = False
                if more_pending:
                    return f"max-missions cap reached ({self.config.budget.max_missions})"
                return "__silent_stop__"
        if self.config.budget.remaining_today(self.memory.journal) <= 0:
            return "daily budget exhausted"
        return ""

    def _wait_idle(self) -> bool:
        """Sleep ``poll_interval_seconds`` honouring stop_event.

        Returns True if stop_event fired during the wait."""
        ev = self.config.stop_event
        if ev is None:
            time.sleep(self.config.poll_interval_seconds)
            return False
        return ev.wait(self.config.poll_interval_seconds)

    def _emit(self, event: dict[str, Any]) -> None:
        try:
            self.sink.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: event sink raised")

    def _emit_status(self, text: str) -> None:
        self._emit({"type": "life.status", "text": text})

    def _idle_backoff_seconds(self) -> float:
        """Exponential re-check sleep for consecutive no-work plan-cycles.

        ``_consecutive_idle_planner_cycles`` is incremented by the caller
        BEFORE calling this; cycle 1 → base, doubling each cycle, capped.
        """
        n = max(1, int(self._consecutive_idle_planner_cycles))
        return min(_IDLE_BACKOFF_CAP_SECONDS, _IDLE_BACKOFF_BASE_SECONDS * (2 ** (n - 1)))

    def _reset_idle_backoff(self) -> None:
        self._consecutive_idle_planner_cycles = 0
        self._suggested_sleep_s = 0.0

    def _enter_idle_backoff(self) -> float:
        """Register one more no-work plan-cycle and return the suggested sleep."""
        self._consecutive_idle_planner_cycles += 1
        self._suggested_sleep_s = self._idle_backoff_seconds()
        return self._suggested_sleep_s

    def _planner_task_tags(self, task: Any) -> list[str]:
        scope = self._normalize_planner_scope(getattr(task, "scope", ""))
        return ["planner", f"scope:{scope}"]

    @staticmethod
    def _normalize_planner_scope(scope: object) -> str:
        normalized = str(scope or _PLANNER_SCOPE_BOUNDED).strip().lower().replace("-", "_")
        if normalized == _PLANNER_SCOPE_FINAL_SUBMISSION:
            return _PLANNER_SCOPE_FINAL_SUBMISSION
        return _PLANNER_SCOPE_BOUNDED

    @staticmethod
    def _planner_scope_from_item(item: BacklogItem) -> str:
        for tag in item.tags:
            normalized = str(tag).strip().lower().replace("-", "_")
            if normalized in {
                f"scope:{_PLANNER_SCOPE_FINAL_SUBMISSION}",
                f"planner_scope:{_PLANNER_SCOPE_FINAL_SUBMISSION}",
            }:
                return _PLANNER_SCOPE_FINAL_SUBMISSION
            if normalized in {
                f"scope:{_PLANNER_SCOPE_BOUNDED}",
                f"planner_scope:{_PLANNER_SCOPE_BOUNDED}",
            }:
                return _PLANNER_SCOPE_BOUNDED
        return ""

    @classmethod
    def _item_is_final_submission(cls, item: BacklogItem) -> bool:
        """True when a backlog item is a project-final ``final_submission``
        task. Prefers the structured ``scope:final_submission`` tag; falls back
        to the legacy objective-prose marker only for items persisted before
        scope tagging existed (resumed-daemon compatibility)."""
        if cls._planner_scope_from_item(item) == _PLANNER_SCOPE_FINAL_SUBMISSION:
            return True
        return _legacy_final_submission_marker(getattr(item, "objective", "") or "")

    def _render_backlog_item_metadata(self, item: BacklogItem) -> str:
        scope = self._planner_scope_from_item(item)
        if not scope and not item.tags:
            return ""
        is_paper_long_horizon = self.config.paper_mission
        lines = ["## Backlog item metadata"]
        if scope:
            lines.append(f"- planner_scope: {scope}")
        if item.tags:
            lines.append("- tags: " + ", ".join(item.tags))
        if scope == _PLANNER_SCOPE_FINAL_SUBMISSION:
            lines.append(
                f"- final_submission_gate: {_FULL_EMNLP_GATE_DESCRIPTION} must be "
                "fully satisfied (every checklist item certified by the reviewer "
                "with concrete evidence) before this item can be marked done."
            )
        elif scope == _PLANNER_SCOPE_BOUNDED:
            if is_paper_long_horizon:
                lines.append(
                    "- paper_optimization_task: this is a bounded mission, but it is "
                    "part of a long-horizon paper/submission objective. First satisfy "
                    "the named acceptance criteria, then continue through adjacent "
                    "paper blockers while budget allows; do not mark done only because "
                    "one narrow check passed if the relevant stage checklist items "
                    "(manuscript, evidence, review, layout, figure/table, citation, "
                    "manifest, or assurance) are still unmet. Full-pipeline "
                    "certification is required only for `final_submission`, but fresh "
                    "concrete evidence for the items you touched is required here."
                )
            else:
                lines.append(
                    "- bounded_task: judge this item against its own acceptance criteria; "
                    "do not require the project-final EMNLP gate unless the objective "
                    "explicitly asks for it."
                )
        return "\n".join(lines)

    def _objective_with_item_scope_context(
        self,
        item: BacklogItem,
        objective: str,
    ) -> str:
        metadata = self._render_backlog_item_metadata(item)
        if not metadata:
            return objective
        return f"{metadata}\n\nOriginal operator objective:\n{objective.strip()}"

    @staticmethod
    def _completion_evidence_from_outcome(outcome: Any) -> str:
        for attr in ("final_message", "completion_summary_markdown", "stop_reason"):
            value = getattr(outcome, attr, "") or ""
            if value:
                return str(value)[:4000]
        return ""

    def _journal_has_full_emnlp_gate_success(self) -> bool:
        """Decide whether the project-final completion gate has passed.

        Source of truth (post-validator-retirement): the journal. A
        ``final_submission`` mission is certified complete only when the
        reviewer returns a full-pipeline completion verdict, which the
        supervisor records as a ``mission_complete`` journal entry carrying
        ``extra["final_submission_certified"] = True``. We no longer call the
        hardcoded ``validate_full_emnlp_readiness`` validator — the reviewer's
        checklist verdict is the single source of truth.

        Fail-closed: only an explicit certified entry counts. We scan the
        recent journal tail for such an entry.
        """
        try:
            entries = self.memory.journal.tail(50)
        except Exception:  # noqa: BLE001
            return False
        for entry in entries:
            if getattr(entry, "kind", "") != "mission_complete":
                continue
            extra = getattr(entry, "extra", {}) or {}
            if isinstance(extra, dict) and bool(
                extra.get("final_submission_certified")
            ):
                return True
        return False

    def _operator_only_external_blocker_wait_reason(self) -> str:
        """Return a waiting reason for an operator-only external blocker.

        This is intentionally *not* a benchmark validator. It only reads the
        lock file written by a prior bounded diagnostic mission and checks
        whether any declared external target has appeared. If none has, local
        engineering is exhausted and the planner should not spin by injecting
        another ``final_submission`` proof task.
        """
        project_root = self._project_workdir()
        lock_path = project_root / "diagnosis" / "operator_only_external_blocker_lock_20260605.json"
        if not lock_path.exists():
            return ""
        try:
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return ""
        if not isinstance(payload, dict):
            return ""
        if payload.get("local_engineer_action_required_before_mount") is not False:
            return ""
        required = payload.get("required_external_targets")
        if not isinstance(required, list) or not required:
            return ""
        present = [
            str(item)
            for item in required
            if isinstance(item, str) and (project_root / item).exists()
        ]
        if present:
            return ""
        missing_count = sum(1 for item in required if isinstance(item, str))
        owner = payload.get("next_owner") or "operator/data owner"
        verdict = payload.get("canonical_viability_verdict") or "external artifacts missing"
        return (
            f"operator-only external benchmark blocker: {verdict}; "
            f"{missing_count} required external target(s) still absent; "
            f"next owner is {owner}"
        )

    def _defer_project_done_for_operator_external_blocker(self, verdict: Any) -> Any:
        if not (
            getattr(verdict, "project_done", False)
            and self.config.full_emnlp_gate
            and not self._journal_has_full_emnlp_gate_success()
        ):
            return verdict
        wait_reason = self._operator_only_external_blocker_wait_reason()
        if not wait_reason:
            return verdict
        return replace(
            verdict,
            project_done=False,
            waiting=True,
            waiting_reason=wait_reason,
            reason=wait_reason,
            new_tasks=[],
        )

    # ------------------------------------------------------------------
    # Iteration loop
    # ------------------------------------------------------------------

    def _render_recent_journal_for_planner(self, item_id: str) -> str:
        """A tiny tail of journal entries for the current item, plain text."""
        try:
            entries = self.memory.journal.tail(6)
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            extra = getattr(e, "extra", None) or {}
            if isinstance(extra, dict) and extra.get("item_id") == item_id:
                lines.append(f"- {e.kind}: {e.summary}")
        return "\n".join(lines[-3:])

    def _inject_cumulative_cost(
        self, entry: Any, *, in_flight_usd: float = 0.0,
    ) -> None:
        """Stamp ``cumulative_cost_usd`` onto ``entry.extra``.

        ``in_flight_usd`` is an optional cost from the *current* mission
        that hasn't been journaled yet (e.g. during phase-change
        notifications that fire mid-execution).
        """
        try:
            cumul = self.memory.journal.total_cost_since(0) + max(0.0, in_flight_usd)
            extra = getattr(entry, "extra", None)
            if extra is None:
                entry.extra = {"cumulative_cost_usd": round(cumul, 2)}
            else:
                extra["cumulative_cost_usd"] = round(cumul, 2)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------
    # Hot-reload continuous config
    # ------------------------------------------------------------------

    def _reload_continuous_config(self) -> None:
        """Update ``self.config.continuous`` from the config provider.

        Called at the top of every ``run()`` iteration so that changes
        from the REPL (written to disk) take effect within seconds even
        when the supervisor is in a long continuous run.
        """
        provider = self.config.continuous_config_provider
        if provider is None:
            return
        try:
            enabled, objective = provider()
            self.config.continuous = enabled
            if objective:
                self.config.continuous_objective = objective
        except Exception:  # noqa: BLE001
            log.debug("continuous config provider raised; keeping current values")

    # ------------------------------------------------------------------
    # Planner — continuous improvement mode
    # ------------------------------------------------------------------

    def _plan_next_work(self) -> bool | None | str:
        """Call the planner to generate new backlog items.

        Returns ``True`` if new work was added (caller should loop),
        ``False`` if the planner declares the project done, and
        ``"daemon_handoff"`` if the planner asked the host to restart,
        and ``None`` when the planner fails and should be retried later.
        """
        if self.planner_runner is None:
            self._emit_status("planner error: no planner runner wired; retry later")
            entry = JournalEntry.new(
                kind="planner_error",
                title="planner unavailable",
                summary="no planner runner wired",
                tags=["life", "planner"],
                extra={"agent_layer": "planner", "error": "no planner runner wired"},
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return None

        self._planning_cycles += 1
        self._emit({
            "type": "life.planner.start",
            "cycle": self._planning_cycles,
            "objective": self.config.continuous_objective[:200],
        })

        journal_tail = self._render_journal_for_planner()
        remaining = self.config.budget.remaining_today(self.memory.journal)

        try:
            from ..planner import Planner

            planner = Planner(self.planner_runner, skill_store=self.skill_store)
            # Enable streaming so planner output flows through the event sink
            ctx = getattr(self.runner, "stream_to", None)
            stream_ctx = ctx(self.sink) if ctx else None
            if stream_ctx:
                stream_ctx.__enter__()
            try:
                verdict = planner.plan_next(
                    continuous_objective=self.config.continuous_objective,
                    journal_tail=journal_tail,
                    budget_remaining_usd=remaining,
                    planning_cycle=self._planning_cycles - 1,
                    runtime_change_summary=self._planner_project_context(),
                    config=self._planner_config(),
                )
            finally:
                if stream_ctx:
                    stream_ctx.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            log.exception("life supervisor: planner raised; retrying later")
            self._emit({
                "type": "life.planner.error",
                "cycle": self._planning_cycles,
                "error": f"{type(exc).__name__}: {exc}",
            })
            entry = JournalEntry.new(
                kind="planner_error",
                title=f"planner cycle #{self._planning_cycles}",
                summary=f"{type(exc).__name__}: {exc}",
                tags=["life", "planner"],
                extra={
                    "agent_layer": "planner",
                    "error": f"{type(exc).__name__}: {exc}",
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return None

        planner_cost_usd = usd_for_tokens(
            self.reviewer_model,
            verdict.input_tokens,
            verdict.cached_input_tokens,
            verdict.output_tokens,
            price_lookup=price_for,
        )

        if verdict.error:
            self._emit({
                "type": "life.planner.error",
                "cycle": self._planning_cycles,
                "error": verdict.error,
                "raw_text": verdict.raw_text,
            })
            self._emit_status(f"planner error: {verdict.error}; retry later")
            entry = JournalEntry.new(
                kind="planner_error",
                title=f"planner cycle #{self._planning_cycles}",
                summary=f"{verdict.error}: {verdict.reason}",
                tags=["life", "planner"],
                extra={
                    "agent_layer": "planner",
                    "error": verdict.error,
                    "reason": verdict.reason,
                    "raw_text": verdict.raw_text,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            # A planner error is a no-work outcome: back off before retrying so
            # a persistently-failing planner cannot spin every poll interval.
            self._enter_idle_backoff()
            return _PLAN_ERROR

        verdict = self._defer_project_done_for_operator_external_blocker(verdict)

        # First-class await-external: the planner intentionally idled because
        # the project is blocked on a live, nonterminal external job and there
        # is no new high-impact work. NOT an error, NOT make-work — record a
        # lightweight waiting entry and back off (escalating) before re-checking.
        if verdict.waiting:
            sleep_s = self._enter_idle_backoff()
            reason = verdict.waiting_reason or verdict.reason or "awaiting external job"
            self._emit({
                "type": "life.planner.waiting",
                "cycle": self._planning_cycles,
                "reason": reason,
                "consecutive_idle_cycles": self._consecutive_idle_planner_cycles,
                "suggested_sleep_s": sleep_s,
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
            })
            self._emit_status(f"awaiting external job: {reason}")
            entry = JournalEntry.new(
                kind="planner_waiting",
                title=f"planner cycle #{self._planning_cycles}",
                summary=f"awaiting external job; backoff {sleep_s:.0f}s :: {reason}",
                tags=["life", "planner", "awaiting"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "waiting": True,
                    "reason": reason,
                    "consecutive_idle_cycles": self._consecutive_idle_planner_cycles,
                    "suggested_sleep_s": sleep_s,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return _PLAN_AWAITING

        # The planner's tasks are trusted. Deterministic gate-repair is only
        # used as a fallback when the planner itself fails (verdict.error above).

        if (
            verdict.project_done
            and self.config.full_emnlp_gate
            and not self._journal_has_full_emnlp_gate_success()
        ):
            from ..planner import TaskSpec

            verdict = replace(
                verdict,
                project_done=False,
                reason=(
                    "full-pipeline final-submission readiness is required before "
                    "project_done; queueing final submission proof"
                ),
                new_tasks=[
                    TaskSpec(
                        title="Prove final submission readiness",
                        objective=(
                            "Project-final task. Scope: final_submission. "
                            "Complete and verify every item on the full research "
                            "pipeline checklist (research → run → analysis → draft → "
                            "submission). The reviewer will certify completion only "
                            "when EVERY checklist item is satisfied with concrete "
                            "evidence (command output, file contents, query rows). "
                            "Do not declare done based on a single stage, a pilot run, "
                            "or an underlength draft; inspect any unmet checklist item "
                            "and repair experiments, baselines, ablations, paper "
                            "contract, figures, citations, manifest, or submission "
                            "state as needed until the reviewer certifies the project."
                        ),
                        impact_score=5,
                        impact_area="requirement_gap",
                        evidence=(
                            "Planner attempted project_done without a journal entry "
                            "certifying full-pipeline final-submission readiness."
                        ),
                        scope=_PLANNER_SCOPE_FINAL_SUBMISSION,
                    )
                ],
            )

        if verdict.project_done and self.config.open_ended:
            self._emit({
                "type": "life.planner.verdict",
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
                "open_ended_objective": True,
            })
            self._emit_status(
                "planner: project done — continuing later for open-ended objective"
            )
            entry = JournalEntry.new(
                kind="planner_retry",
                title="planner suggests continuing",
                summary=(
                    f"project_done=true; continuing later for open-ended objective: "
                    f"{verdict.reason}"
                ),
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "open_ended_objective": True,
                    "restart_daemon": verdict.restart_daemon,
                    "restart_reason": verdict.restart_reason,
                    "reason": verdict.reason,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            return "planner_retry"

        if verdict.project_done:
            self._emit({
                "type": "life.planner.verdict",
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": len(verdict.new_tasks),
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
            })
            self._emit_status(
                f"planner: project done — {verdict.reason}"
            )
            entry = JournalEntry.new(
                kind="planner_done",
                title="planner declares project done",
                summary=verdict.reason,
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "restart_daemon": verdict.restart_daemon,
                    "restart_reason": verdict.restart_reason,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            if verdict.restart_daemon and self._handle_planner_restart(
                verdict.restart_reason
            ):
                self._emit_status("daemon_handoff")
                return "daemon_handoff"
            return False

        if verdict.restart_daemon and not verdict.new_tasks:
            restart_reason = verdict.restart_reason or verdict.reason
            self._emit({
                "type": "life.planner.verdict",
                "cycle": self._planning_cycles,
                "project_done": verdict.project_done,
                "reason": verdict.reason,
                "task_count": 0,
                "enqueued_tasks": 0,
                "skipped_duplicate_tasks": 0,
                "enqueued_titles": [],
                "skipped_duplicate_titles": [],
                "input_tokens": verdict.input_tokens,
                "cached_input_tokens": verdict.cached_input_tokens,
                "output_tokens": verdict.output_tokens,
                "cost_usd": planner_cost_usd,
                "restart_daemon": True,
                "restart_reason": restart_reason,
            })
            entry = JournalEntry.new(
                kind="planner_cycle",
                title=f"planner cycle #{self._planning_cycles}",
                summary=f"planner requested daemon restart: {restart_reason}",
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "objective": self.config.continuous_objective[:200],
                    "proposed_tasks": 0,
                    "enqueued_tasks": 0,
                    "skipped_duplicate_tasks": 0,
                    "enqueued_titles": [],
                    "skipped_duplicate_titles": [],
                    "restart_daemon": True,
                    "restart_reason": restart_reason,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            if self._handle_planner_restart(restart_reason):
                self._emit_status("daemon_handoff")
                return "daemon_handoff"
            self._emit_status("planner requested daemon restart but host did not restart")
            self._enter_idle_backoff()
            return _PLAN_ERROR

        if not verdict.new_tasks:
            self._emit({
                "type": "life.planner.error",
                "cycle": self._planning_cycles,
                "error": "planner produced no tasks",
                "raw_text": verdict.raw_text,
            })
            self._emit_status("planner error: produced no tasks; retry later")
            entry = JournalEntry.new(
                kind="planner_error",
                title=f"planner cycle #{self._planning_cycles}",
                summary=verdict.reason or "planner produced no tasks",
                tags=["life", "planner"],
                cost_usd=planner_cost_usd,
                extra={
                    "agent_layer": "planner",
                    "error": "planner produced no tasks",
                    "reason": verdict.reason,
                    "raw_text": verdict.raw_text,
                },
            )
            self.memory.journal.append(entry)
            self._inject_cumulative_cost(entry)
            try:
                from .notify import dispatch_journal_entry
                dispatch_journal_entry(entry)
            except Exception:  # noqa: BLE001
                log.exception("notify dispatch failed; continuing")
            # No tasks, no waiting flag, not done: a degenerate no-work cycle.
            # Back off so repeated empty plans cannot spin the daemon.
            self._enter_idle_backoff()
            return _PLAN_ERROR

        try:
            existing_items = self.memory.backlog.all()
        except Exception:  # noqa: BLE001
            log.exception("life supervisor: failed to inspect backlog before planning")
            existing_items = []

        seen_signatures: dict[tuple[str, str], BacklogItem] = {}
        for existing in existing_items:
            if existing.status not in _PLANNER_DEDUP_STATUSES:
                continue
            if (
                existing.status == "done"
                and self._item_is_final_submission(existing)
            ):
                continue
            signature = _planner_task_signature(existing.title, existing.objective)
            if existing.status in {"pending", "running"}:
                seen_signatures[signature] = existing
            elif signature not in seen_signatures:
                seen_signatures[signature] = existing

        recent_failures = self._recent_no_progress_failures()
        added_titles: list[str] = []
        skipped_duplicate_titles: list[str] = []
        skipped_recent_failure_titles: list[str] = []
        added_impact_scores: list[int] = []

        # Add new tasks to the backlog.
        for task in verdict.new_tasks:
            sanitized_title = _sanitize_planner_task_text(task.title)
            sanitized_objective = _sanitize_planner_task_text(task.objective)
            sanitized_evidence = _sanitize_planner_task_text(task.evidence)
            if (
                sanitized_title != task.title
                or sanitized_objective != task.objective
                or sanitized_evidence != task.evidence
            ):
                task = replace(
                    task,
                    title=sanitized_title,
                    objective=sanitized_objective,
                    evidence=sanitized_evidence,
                )
            signature = _planner_task_signature(task.title, task.objective)
            duplicate_item = seen_signatures.get(signature)
            if duplicate_item is not None:
                skipped_duplicate_titles.append(task.title)
                duplicate_reason = (
                    "duplicate completed task"
                    if duplicate_item.status == "done"
                    else "duplicate pending/running task"
                )
                self._emit({
                    "type": "life.planner.task_skipped",
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": duplicate_item.id,
                    "matched_status": duplicate_item.status,
                    "reason": duplicate_reason,
                })
                continue
            recent_failure = recent_failures.get(signature)
            if recent_failure is not None:
                skipped_recent_failure_titles.append(task.title)
                failure_extra = getattr(recent_failure, "extra", {}) or {}
                failure_signature = _entry_task_signature(recent_failure)
                self._emit({
                    "type": "life.planner.task_skipped",
                    "cycle": self._planning_cycles,
                    "title": task.title,
                    "objective": task.objective,
                    "impact_score": task.impact_score,
                    "impact_area": task.impact_area,
                    "evidence": task.evidence,
                    "matched_item_id": failure_extra.get("item_id"),
                    "matched_title": recent_failure.title,
                    "matched_status": failure_extra.get("terminal_status") or failure_extra.get("status"),
                    "matched_stop_reason": failure_extra.get("stop_reason") or failure_extra.get("failure_reason"),
                    "matched_signature": (
                        {
                            "title": failure_signature[0],
                            "objective": failure_signature[1],
                        }
                        if failure_signature is not None
                        else None
                    ),
                    "skip_category": "recent_no_progress_failure",
                    "reason": "recent no_progress failure",
                })
                continue
            item = BacklogItem.new(
                title=task.title,
                objective=task.objective,
                priority=100,
                tags=self._planner_task_tags(task),
                iterate=True,
                iteration_max_cycles=self._item_iteration_cycles(),
                iteration_budget_usd=self._item_iteration_budget(),
            )
            self.memory.backlog.add(item)
            seen_signatures[signature] = item
            added_titles.append(item.title)
            added_impact_scores.append(task.impact_score)
            self._emit({
                "type": "life.planner.task_added",
                "item_id": item.id,
                "title": item.title,
                "impact_score": task.impact_score,
                "impact_area": task.impact_area,
            })

        summary_parts = [
            f"proposed {len(verdict.new_tasks)} task(s)",
            (
                "enqueued "
                f"{len(added_titles)} task(s): "
                + (", ".join(added_titles) if added_titles else "(none)")
            ),
        ]
        if skipped_duplicate_titles:
            summary_parts.append(
                "skipped "
                f"{len(skipped_duplicate_titles)} duplicate(s): "
                + ", ".join(skipped_duplicate_titles)
            )
        if skipped_recent_failure_titles:
            summary_parts.append(
                "quarantined "
                f"{len(skipped_recent_failure_titles)} recent no_progress repeat(s): "
                + ", ".join(skipped_recent_failure_titles)
            )

        entry = JournalEntry.new(
            kind="planner_cycle",
            title=f"planner cycle #{self._planning_cycles}",
            summary="; ".join(summary_parts),
            tags=["life", "planner"],
            cost_usd=planner_cost_usd,
            extra={
                "agent_layer": "planner",
                "objective": self.config.continuous_objective[:200],
                "proposed_tasks": len(verdict.new_tasks),
                "enqueued_tasks": len(added_titles),
                "skipped_duplicate_tasks": len(skipped_duplicate_titles),
                "skipped_recent_failure_tasks": len(skipped_recent_failure_titles),
                "enqueued_titles": added_titles,
                "enqueued_impact_scores": added_impact_scores,
                "skipped_duplicate_titles": skipped_duplicate_titles,
                "skipped_recent_failure_titles": skipped_recent_failure_titles,
                "restart_daemon": verdict.restart_daemon,
                "restart_reason": verdict.restart_reason,
            },
        )
        self._emit({
            "type": "life.planner.verdict",
            "cycle": self._planning_cycles,
            "project_done": verdict.project_done,
            "reason": verdict.reason,
            "task_count": len(verdict.new_tasks),
            "enqueued_tasks": len(added_titles),
            "skipped_duplicate_tasks": len(skipped_duplicate_titles),
            "skipped_recent_failure_tasks": len(skipped_recent_failure_titles),
            "enqueued_titles": added_titles,
            "enqueued_impact_scores": added_impact_scores,
            "skipped_duplicate_titles": skipped_duplicate_titles,
            "skipped_recent_failure_titles": skipped_recent_failure_titles,
            "input_tokens": verdict.input_tokens,
            "cached_input_tokens": verdict.cached_input_tokens,
            "output_tokens": verdict.output_tokens,
            "cost_usd": planner_cost_usd,
            "restart_daemon": verdict.restart_daemon,
            "restart_reason": verdict.restart_reason,
        })
        self.memory.journal.append(entry)
        self._inject_cumulative_cost(entry)
        try:
            from .notify import dispatch_journal_entry
            dispatch_journal_entry(entry)
        except Exception:  # noqa: BLE001
            log.exception("notify dispatch failed; continuing")
        if verdict.restart_daemon and self._handle_planner_restart(
            verdict.restart_reason
        ):
            self._emit_status("daemon_handoff")
            return "daemon_handoff"
        # Real new work was queued: clear the no-work backoff so the next cycle
        # runs promptly.
        self._reset_idle_backoff()
        return True

    def _item_iteration_cycles(self) -> int:
        """Default iteration cycles for planner-generated tasks."""
        try:
            return max(1, int(self.config.planner_task_iteration_max_cycles))
        except (TypeError, ValueError):
            return 6

    def _item_iteration_budget(self) -> float:
        """Default iteration budget for planner-generated tasks."""
        try:
            return max(0.0, float(self.config.planner_task_iteration_budget_usd))
        except (TypeError, ValueError):
            return 30.0

    def _render_journal_for_planner(self) -> str:
        """Render recent journal entries for the planner's context."""
        try:
            # Read a larger tail and drop low-level Signal-B failure-
            # observation rows (counting plumbing) BEFORE truncating, so a
            # burst of observations can't crowd out real mission outcomes /
            # advisories. The recurring-failure *advisory* itself is kept.
            entries = [
                e
                for e in self.memory.journal.tail(80)
                if getattr(e, "kind", "") != "self_evolve.failure_observation"
            ][-20:]
        except Exception:  # noqa: BLE001
            return ""
        lines: list[str] = []
        for e in entries:
            from datetime import datetime
            ts = datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M")
            line = f"- [{ts}] {e.kind}: {e.title} — {e.summary}"
            extra = getattr(e, "extra", {}) or {}
            if isinstance(extra, dict):
                if extra.get("final_submission_certified"):
                    evidence = str(extra.get("completion_summary") or "").strip()
                    if evidence:
                        line += f" | final-submission evidence: {evidence[:500]}"
                if e.kind in ("mission_complete", "mission_failed"):
                    # Surface the L2 reviewer's own structured briefing so the
                    # planner attends to *what actually happened*, not just the
                    # `status=done` field. A mission can be marked done by being
                    # waved through a blocked/rollback/allowed-failure gate
                    # without resolving the underlying blocker; this report lets
                    # the planner avoid re-dispatching no-progress missions.
                    report = extra.get("planner_report")
                    if isinstance(report, dict) and report:
                        rendered = self._render_planner_report(report)
                        if rendered:
                            line += "\n" + rendered
            lines.append(line)
        return "\n".join(lines) or "(empty)"

    @staticmethod
    def _render_planner_report(report: dict) -> str:
        """Render the reviewer-authored planner briefing as plain lines.

        The reviewer authors a clean structured object; we only select and
        truncate its fields, never reformat free text or strip logs (the
        reviewer is instructed to emit clean content). Returns "" when the
        object carries no usable signal.
        """
        def _clean(value: object, limit: int) -> str:
            return str(value or "").strip()[:limit]

        forward = report.get("forward_progress")
        headline = _clean(report.get("headline"), 600)
        blocker = _clean(report.get("blocker"), 1200)
        recommended = _clean(report.get("recommended_next"), 1200)
        parts: list[str] = []
        if isinstance(forward, bool):
            parts.append(f"    reviewer→planner: forward_progress={forward}")
        if headline:
            parts.append(f"    headline: {headline}")
        if blocker:
            parts.append(f"    blocker: {blocker}")
        if recommended:
            parts.append(f"    recommended_next: {recommended}")
        evidence = report.get("evidence_files")
        if isinstance(evidence, list) and evidence:
            parts.append("    evidence_files the planner MUST open before replanning:")
            for entry in evidence[:8]:
                if not isinstance(entry, dict):
                    continue
                path = _clean(entry.get("path"), 400)
                if not path:
                    continue
                why = _clean(entry.get("why"), 600)
                parts.append(f"      - {path}" + (f"  — {why}" if why else ""))
        return "\n".join(parts)
