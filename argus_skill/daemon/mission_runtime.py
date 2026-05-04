"""``MissionDaemon`` — wraps ArgusBot ``LoopEngine`` for true 7×24 unattended ops.

Why this exists
---------------

The vanilla ``Daemon`` (``argus_skill.daemon.runtime.Daemon``) is a queue
dispatcher: it pops ``/run <task>`` commands and runs ``SkillLoop.run()``
once per task. That works for short request/response Telegram-style
operation but it is not a 7×24 supervisor.

ArgusBot ships ``codex_autoloop.core.engine.LoopEngine``, a stateful
multi-round mission engine: rounds 1..N (default 50), reviewer judging
each round, planner proposing follow-up sub-objectives when reviewer
says ``done`` and ``plan_mode == "auto"``, persistent operator
messages, stall watchdog, etc. ``MissionDaemon`` plugs argus-skill's
skill pipeline into ``LoopEngine.runner`` (via ``SkillLoopRunner``) so
argus-skill inherits all of that machinery.

Design
------

  * One blocking ``LoopEngine.run()`` call per daemon process — runs in
    a worker thread.
  * Operator commands route through ``LoopStateStore``:
      - ``/inject``  → ``state_store.request_inject(text)`` (mid-round
        external interrupt — engine.py:165-252).
      - ``/stop``    → ``state_store.request_stop()`` + daemon shutdown.
      - ``/skip``    → ``state_store.request_inject("[operator] /skip
        — abandon current approach and propose next")`` (closest to
        ArgusBot semantics).
      - ``/review <criteria>``  → ``state_store.request_review_criteria(text)``.
      - ``/plan <direction>``   → ``state_store.request_plan_direction(text)``.
      - ``/mode auto|off|record`` → ``state_store.request_plan_mode(text)``.
      - ``/status`` → emit a short summary.
      - ``/verbose`` ``/quiet`` → forward to sinks.
      - ``/help`` → mission-mode help text.
  * Events from ``LoopEngine`` flow to argus-skill's
    ``CompositeEventSink`` unchanged (the protocols are compatible).
  * When ``LoopEngine.run()`` returns, daemon emits ``mission.completed``
    and exits cleanly. The systemd unit's ``Restart=on-failure`` policy
    only restarts on non-zero exit; mission-completed exits 0 so the
    operator must explicitly start a new mission.

Backward compat with the queue ``Daemon`` is preserved at the CLI
level by ``daemon_app.cmd_daemon`` switching on ``--mission-file``.

Provenance: new code. ``LoopEngine`` / ``LoopStateStore`` / ``Reviewer``
/ ``Planner`` are imported from ArgusBot — no source vendoring.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..telegram.notifier import format_event_message

from ..adapters.skill_loop_runner import (
    EngineerCallConfig,
    SkillLoopRunner,
    SkillLoopRunnerConfig,
)
from ..core.ports import ControlCommand, EventSink
from ..scientist.distiller import Distiller, DistillerConfig
from ..skills.store import SkillStore
from .bus import write_status

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration (loaded from mission.json)
# ---------------------------------------------------------------------------

@dataclass
class MissionConfig:
    """The subset of ``mission.json`` MissionDaemon needs.

    Built via ``MissionConfig.from_json_file``.
    """
    mission_id: str
    objective: str
    workdir: str
    check_commands: list[str]
    max_rounds: int
    plan_mode: str  # "auto" | "off" | "record"
    main_model: str
    reviewer_model: str
    plan_model: str
    main_reasoning_effort: str
    reviewer_reasoning_effort: str
    plan_reasoning_effort: str

    @classmethod
    def from_json_file(cls, path: Path) -> "MissionConfig":
        payload = json.loads(Path(path).read_text())
        return cls(
            mission_id=payload["mission_id"],
            objective=payload["objective"],
            workdir=payload.get("workdir") or os.getcwd(),
            check_commands=list(payload.get("check_commands") or []),
            max_rounds=int(payload.get("max_rounds", 50)),
            plan_mode=payload.get("plan_mode", "off"),
            main_model=payload.get("main_model", "gpt-5.4-mini"),
            reviewer_model=payload.get("reviewer_model", "gpt-5.4-mini"),
            plan_model=payload.get("plan_model", "gpt-5.4"),
            main_reasoning_effort=payload.get("main_reasoning_effort", "medium"),
            reviewer_reasoning_effort=payload.get("reviewer_reasoning_effort", "medium"),
            plan_reasoning_effort=payload.get("plan_reasoning_effort", "high"),
        )


@dataclass
class MissionDaemonConfig:
    state_dir: str = ".argus-skill"
    skills_dir: str = "skills"
    status_refresh_seconds: int = 5


# ---------------------------------------------------------------------------
# Lazy ArgusBot imports
# ---------------------------------------------------------------------------

def _import_argusbot():
    try:
        from codex_autoloop.codex_runner import CodexRunner  # type: ignore
        from codex_autoloop.core.engine import LoopConfig, LoopEngine
        from codex_autoloop.core.state_store import LoopStateStore
        from codex_autoloop.planner import Planner, PlannerConfig
        from codex_autoloop.reviewer import Reviewer, ReviewerConfig
    except ImportError as exc:  # pragma: no cover — environmental
        raise ImportError(
            "MissionDaemon requires ArgusBot to be importable. "
            "Install with `pip install -e /path/to/ArgusBot`."
        ) from exc
    return {
        "CodexRunner": CodexRunner,
        "LoopConfig": LoopConfig,
        "LoopEngine": LoopEngine,
        "LoopStateStore": LoopStateStore,
        "Reviewer": Reviewer,
        "ReviewerConfig": ReviewerConfig,
        "Planner": Planner,
        "PlannerConfig": PlannerConfig,
    }


# ---------------------------------------------------------------------------
# Mission state-dir layout helpers
# ---------------------------------------------------------------------------

def _mission_loop_state_paths(state_dir: str | Path, mission_id: str) -> dict:
    """Map LoopStateStore artifact paths under state-dir/missions/<id>/loop_state."""
    base = Path(state_dir) / "missions" / mission_id / "loop_state"
    base.mkdir(parents=True, exist_ok=True)
    return {
        "state_file": str(base / "state.json"),
        "operator_messages_file": str(base / "operator_messages.txt"),
        "plan_overview_file": str(base / "plan_overview.md"),
        "review_summaries_dir": str(base / "review_summaries"),
        "final_report_file": str(base / "final_report.md"),
        "pptx_report_file": str(base / "final_report.pptx"),
        "main_prompt_file": str(base / "main_prompts.md"),
    }


# ---------------------------------------------------------------------------
# MissionDaemon
# ---------------------------------------------------------------------------

class MissionDaemon:
    """Hosts a single LoopEngine.run() invocation as a 7×24 supervisor.

    Construct, then ``start()``, then feed ``ControlCommand``s via
    ``handle_command``. ``wait()`` blocks until the mission completes
    or ``/stop`` is received. ``stop()`` is idempotent.
    """

    def __init__(
        self,
        *,
        mission: MissionConfig,
        sinks: EventSink,
        engineer_backend: Any,  # argus-skill RunnerBackend (e.g. CodexRunnerBackend)
        codex_runner: Any,  # ArgusBot CodexRunner (for reviewer/planner/reports)
        config: MissionDaemonConfig | None = None,
    ) -> None:
        self.mission = mission
        self.sinks = sinks
        self.engineer_backend = engineer_backend
        self.codex_runner = codex_runner
        self.config = config or MissionDaemonConfig()

        self._argus = _import_argusbot()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._status_thread: threading.Thread | None = None
        self._started_at: str = ""
        self._mission_status = "idle"  # idle | running | done | error
        self._mission_result: dict | None = None

        # Rich runtime state (populated by _track_event so /status and the
        # status.json writer can render it without re-querying LoopEngine).
        # phase: ready | engineering | checks | review | planning | idle
        self._current_phase: str = "ready"
        self._current_round: int = 0
        self._max_rounds: int = mission.max_rounds
        self._last_review: dict[str, Any] | None = None
        self._last_plan: dict[str, Any] | None = None
        self._last_main_summary: str = ""
        self._recent_events: deque[dict[str, Any]] = deque(maxlen=12)

        # Build the LoopEngine + supporting machinery up-front so we can
        # surface configuration errors before the worker thread starts.
        self.skill_store = SkillStore(
            Path(self.config.skills_dir),
            runner=self.engineer_backend,
            matcher_model=self.mission.main_model,
            matcher_reasoning_effort=self.mission.main_reasoning_effort,
        )
        self.distiller = Distiller(self.engineer_backend)
        self.skill_loop_runner = SkillLoopRunner(
            config=SkillLoopRunnerConfig(
                mission_objective=self.mission.objective,
                workdir=Path(self.mission.workdir),
                engineer=EngineerCallConfig(
                    model=self.mission.main_model,
                    reasoning_effort=self.mission.main_reasoning_effort,
                ),
                distiller=DistillerConfig(
                    model=self.mission.plan_model,  # scientist usually = plan model (big model)
                    reasoning_effort=self.mission.plan_reasoning_effort,
                ),
                distill_on_miss=True,
            ),
            skill_store=self.skill_store,
            distiller=self.distiller,
            engineer_runner=self.engineer_backend,
            fallback_runner=self.codex_runner,
            on_event=self._emit,
        )
        loop_paths = _mission_loop_state_paths(self.config.state_dir, self.mission.mission_id)
        self.state_store = self._argus["LoopStateStore"](
            objective=self.mission.objective,
            check_commands=list(self.mission.check_commands),
            plan_mode=self.mission.plan_mode,  # type: ignore[arg-type]
            **loop_paths,
        )
        self.reviewer = self._argus["Reviewer"](runner=self.codex_runner)
        # Planner is only constructed when plan_mode in {auto, record} — saves
        # a tiny bit of init for plan_mode=off missions.
        if self.mission.plan_mode in ("auto", "record"):
            self.planner = self._argus["Planner"](runner=self.codex_runner)
        else:
            self.planner = None

        self.loop_config = self._argus["LoopConfig"](
            objective=self.mission.objective,
            max_rounds=self.mission.max_rounds,
            check_commands=list(self.mission.check_commands),
            main_model=self.mission.main_model,
            main_reasoning_effort=self.mission.main_reasoning_effort,
            reviewer_model=self.mission.reviewer_model,
            reviewer_reasoning_effort=self.mission.reviewer_reasoning_effort,
            plan_mode=self.mission.plan_mode,  # type: ignore[arg-type]
            plan_model=self.mission.plan_model,
            plan_reasoning_effort=self.mission.plan_reasoning_effort,
            full_auto=True,
            skip_git_repo_check=True,
        )
        self.loop_engine = self._argus["LoopEngine"](
            runner=self.skill_loop_runner,
            reviewer=self.reviewer,
            planner=self.planner,
            config=self.loop_config,
            state_store=self.state_store,
            event_sink=_StateTrackingEventSink(self, self.sinks),
        )

    # --- lifecycle --------------------------------------------------------

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._lock:
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._mission_status = "running"
        self._stop_event.clear()
        self._worker = threading.Thread(target=self._run_mission, daemon=True)
        self._status_thread = threading.Thread(target=self._run_status_writer, daemon=True)
        self._worker.start()
        self._status_thread.start()
        self._emit({
            "type": "mission.started",
            "text": f"mission {self.mission.mission_id} started ({self.mission.plan_mode}, max_rounds={self.mission.max_rounds})",
        })
        self._write_status()

    def stop(self) -> None:
        """Idempotent stop: requests LoopStateStore.stop + sets thread event.

        LoopEngine polls ``state_store.is_stop_requested()`` between
        rounds; the worker thread exits when the engine returns. The
        in-flight engineer call is also interrupted because we wired
        ``state_store.consume_interrupt_reason`` into the codex
        watchdog at SkillLoopRunner construction time (via
        ``LoopEngine``'s built-in plumbing).
        """
        with self._lock:
            if self._stop_event.is_set():
                return
        self._emit({"type": "daemon.stopping", "text": "shutdown requested"})
        try:
            self.state_store.request_stop(source="mission-daemon")
        except Exception:  # noqa: BLE001
            log.exception("state_store.request_stop raised")
        self._stop_event.set()

    def wait(self) -> None:
        if self._worker is not None:
            self._worker.join()

    # --- command intake ---------------------------------------------------

    def handle_command(self, command: ControlCommand) -> None:
        kind = command.kind
        text = (command.text or "").strip()

        if kind in ("run", "inject"):
            # In mission mode there's no separate /run — a /run during a
            # running mission is treated as an operator inject (raise
            # External interrupt: ... in the engineer).
            if not text:
                self._emit({"type": "command.error",
                            "text": f"/{kind} needs text"})
                return
            self.state_store.request_inject(text, source="operator")
            ack = (
                f"noted ({len(text)} chars) — will be raised as External "
                "interrupt in the engineer's next poll"
            )
            self._emit({"type": "command.ack", "text": ack})
        elif kind == "skip":
            self.state_store.request_inject(
                "[operator] /skip — abandon the current approach and propose a different one",
                source="operator",
            )
            self._emit({"type": "command.ack", "text": "skipping current approach"})
        elif kind == "stop":
            self.stop()
        elif kind == "review":
            if not text:
                self._emit({"type": "command.error",
                            "text": "/review needs criteria text"})
                return
            self.state_store.request_review_criteria(text, source="operator")
            self._emit({"type": "command.ack",
                        "text": f"review criteria set ({len(text)} chars)"})
        elif kind == "plan":
            if not text:
                self._emit({"type": "command.error",
                            "text": "/plan needs direction text"})
                return
            self.state_store.request_plan_direction(text, source="operator")
            self._emit({"type": "command.ack",
                        "text": f"plan direction set ({len(text)} chars)"})
        elif kind == "mode":
            normalized = text.lower()
            if normalized not in ("auto", "off", "record"):
                self._emit({"type": "command.error",
                            "text": "/mode requires auto|off|record"})
                return
            applied = self.state_store.request_plan_mode(normalized, source="operator")
            self._emit({"type": "command.ack",
                        "text": f"plan_mode → {applied}"})
        elif kind == "status":
            self._emit({"type": "status.report", "text": self._render_status_short()})
        elif kind == "verbose":
            self._set_sinks_verbose(True)
            self._emit({"type": "command.ack",
                        "text": "verbose mode on — internal events will appear"})
        elif kind == "quiet":
            self._set_sinks_verbose(False)
            self._emit({"type": "command.ack",
                        "text": "quiet mode on — only essential events"})
        elif kind == "help":
            self._emit({"type": "help", "text": self._help_text()})
        else:
            self._emit({"type": "command.unknown", "text": f"unknown command: {kind}"})

    # --- worker -----------------------------------------------------------

    def _run_mission(self) -> None:
        try:
            result = self.loop_engine.run()
            with self._lock:
                self._mission_status = "done" if result.success else "error"
                self._mission_result = {
                    "success": result.success,
                    "stop_reason": result.stop_reason,
                    "session_id": result.session_id,
                    "rounds": len(result.rounds),
                }
            self._emit({
                "type": "mission.completed",
                "text": (
                    f"mission {self.mission.mission_id}: "
                    f"success={result.success} rounds={len(result.rounds)} "
                    f"reason={(result.stop_reason or '')[:200]}"
                ),
            })
        except Exception as exc:  # noqa: BLE001
            log.exception("LoopEngine.run raised")
            with self._lock:
                self._mission_status = "error"
                self._mission_result = {
                    "success": False,
                    "exception": f"{type(exc).__name__}: {exc}",
                }
            self._emit({
                "type": "mission.error",
                "text": f"mission errored: {type(exc).__name__}: {str(exc)[:200]}",
            })
        finally:
            self._stop_event.set()
            self._write_status()

    def _run_status_writer(self) -> None:
        while not self._stop_event.is_set():
            self._write_status()
            self._stop_event.wait(self.config.status_refresh_seconds)
        self._write_status()

    def _write_status(self) -> None:
        try:
            with self._lock:
                payload = {
                    "daemon_running": not self._stop_event.is_set(),
                    "daemon_pid": os.getpid(),
                    "started_at": self._started_at,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "mode": "mission",
                    "mission_id": self.mission.mission_id,
                    "mission_objective": self.mission.objective[:200],
                    "mission_status": self._mission_status,
                    "mission_result": self._mission_result,
                    "plan_mode": self._effective_plan_mode_locked(),
                    # Rich runtime state — for /status, dashboards, debugging.
                    "current_phase": self._current_phase,
                    "current_round": self._current_round,
                    "max_rounds": self._max_rounds,
                    "last_review": self._last_review,
                    "last_plan": self._last_plan,
                    "last_main_summary": self._last_main_summary[:1500],
                    "recent_events": list(self._recent_events),
                }
            write_status(
                str(Path(self.config.state_dir) / "status.json"),
                payload,
            )
        except Exception:  # noqa: BLE001
            log.exception("status write failed")

    # --- helpers ----------------------------------------------------------

    def _emit(self, event: dict) -> None:
        # Track first (in-process state); even if the user sink raises, our
        # internal bookkeeping stays accurate.
        try:
            self._track_event(event)
        except Exception:  # noqa: BLE001
            log.exception("state tracker raised (event will still ship)")
        try:
            self.sinks.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("sink.handle_event raised")

    def _track_event(self, event: dict[str, Any]) -> None:
        """Update in-memory mission state from a single LoopEngine/runner event.

        Called both from ``self._emit`` (mission-level events emitted by this
        class) and via ``_StateTrackingEventSink`` (events emitted by
        LoopEngine itself). Idempotent — re-receiving the same event simply
        re-applies the assignments.
        """
        kind = str(event.get("type", ""))
        with self._lock:
            self._note_recent(event)
            if kind == "loop.started":
                mr = event.get("max_rounds")
                if isinstance(mr, int) and mr > 0:
                    self._max_rounds = mr
                self._current_phase = "ready"
            elif kind == "round.started":
                ri = event.get("round_index")
                if isinstance(ri, int):
                    self._current_round = ri
                self._current_phase = "engineering"
            elif kind == "round.main.completed":
                last = (event.get("last_message") or "").strip()
                if last:
                    self._last_main_summary = last
                self._current_phase = "checks"
            elif kind == "round.checks.completed":
                self._current_phase = "review"
            elif kind == "round.review.completed":
                ri = event.get("round_index") or self._current_round
                status = str(event.get("status") or "")
                self._last_review = {
                    "round": ri,
                    "status": status,
                    "reason": (event.get("reason") or "")[:600],
                    "next_action": (event.get("next_action") or "")[:300],
                }
                self._current_phase = "planning" if status == "done" else "engineering"
            elif kind == "plan.completed":
                ri = event.get("round_index") or self._current_round
                self._last_plan = {
                    "round": ri,
                    "plan_mode": event.get("plan_mode"),
                    "follow_up_required": event.get("follow_up_required"),
                    "main_instruction": (event.get("main_instruction") or "")[:600],
                    "next_explore": (event.get("next_explore") or "")[:300],
                    "review_instruction": (event.get("review_instruction") or "")[:300],
                }
                self._current_phase = "engineering"
            elif kind == "loop.completed":
                self._current_phase = "idle"
            elif kind == "mission.started":
                self._current_phase = "ready"
            elif kind in ("mission.completed", "mission.error"):
                self._current_phase = "idle"

    def _note_recent(self, event: dict[str, Any]) -> None:
        """Append a sanitized summary of ``event`` to the recent ring buffer.

        We store only what's needed to show a one-line history in /status —
        the full event continues to ship through the user sinks unmodified.
        """
        kind = str(event.get("type", ""))
        if not kind or kind in ("status.report", "help"):
            return  # don't pollute history with our own status echoes
        try:
            short = format_event_message(event).split("\n", 1)[0]
        except Exception:  # noqa: BLE001
            short = kind
        self._recent_events.append({
            "ts": event.get("ts") or datetime.now(timezone.utc).isoformat(),
            "type": kind,
            "round_index": event.get("round_index"),
            "short": short[:240],
        })

    def _effective_plan_mode(self) -> str:
        """plan_mode honouring runtime ``/mode`` updates (rubber-duck #2)."""
        with self._lock:
            return self._effective_plan_mode_locked()

    def _effective_plan_mode_locked(self) -> str:
        try:
            getter = getattr(self.state_store, "current_plan_mode", None)
            if callable(getter):
                return str(getter())
        except Exception:  # noqa: BLE001
            log.debug("state_store.current_plan_mode raised", exc_info=True)
        return self.mission.plan_mode

    def _set_sinks_verbose(self, verbose: bool) -> None:
        setter = getattr(self.sinks, "set_verbose", None)
        if callable(setter):
            try:
                setter(verbose)
            except Exception:  # noqa: BLE001
                pass

    def _render_status_short(self) -> str:
        with self._lock:
            return _render_mission_status(
                mission_id=self.mission.mission_id,
                mission_status=self._mission_status,
                phase=self._current_phase,
                current_round=self._current_round,
                max_rounds=self._max_rounds,
                objective=self.mission.objective,
                plan_mode=self._effective_plan_mode_locked(),
                last_review=self._last_review,
                last_plan=self._last_plan,
                last_main_summary=self._last_main_summary,
                recent_events=list(self._recent_events),
            )

    @staticmethod
    def _help_text() -> str:
        return (
            "Mission mode (LoopEngine-driven 7×24):\n"
            "/inject <text>       — raise External interrupt: <text> mid-round\n"
            "/skip                — abandon current approach + ask for different one\n"
            "/review <criteria>   — set/append criteria the reviewer should grade against\n"
            "/plan <direction>    — guide the planner's next follow-up\n"
            "/mode auto|off|record — switch plan mode (auto = unattended chaining)\n"
            "/show prompt|plan|review|all  — peek at LoopStateStore artifacts\n"
            "/status              — round / phase / last-verdict / recent events\n"
            "/verbose, /quiet     — toggle event verbosity\n"
            "/stop                — terminate the mission\n"
            "/help                — this help\n"
            "Plain text without '/' is buffered as an inject."
        )


# ---------------------------------------------------------------------------
# Helpers (sinks + status rendering)
# ---------------------------------------------------------------------------

class _StateTrackingEventSink:
    """Wraps the user-facing event sink with an in-process state tracker.

    LoopEngine is given THIS as its ``event_sink`` so every emitted event
    flows through ``MissionDaemon._track_event`` (updating round/phase/
    last_review/last_plan/recent_events) BEFORE being forwarded to the
    real downstream sinks (Telegram, JSONL outbox, console, …).

    Per rubber-duck feedback #4: tracking lives inside the daemon's
    bookkeeping path, not as another peer sink, so even if the user-sink
    chain raises (it is best-effort), tracking stays correct.
    """

    def __init__(self, daemon: "MissionDaemon", downstream: EventSink) -> None:
        self._daemon = daemon
        self._downstream = downstream

    def handle_event(self, event: dict) -> None:
        try:
            self._daemon._track_event(event)
        except Exception:  # noqa: BLE001
            log.exception("state tracker raised (event still ships)")
        try:
            self._downstream.handle_event(event)
        except Exception:  # noqa: BLE001
            log.exception("downstream sink raised")

    def set_verbose(self, verbose: bool) -> None:
        setter = getattr(self._downstream, "set_verbose", None)
        if callable(setter):
            try:
                setter(verbose)
            except Exception:  # noqa: BLE001
                pass


_REVIEW_STATUS_ICONS = {
    "done": "✅",
    "continue": "↻",
    "blocked": "⛔",
    "no_progress": "🚫",
}


def _render_mission_status(
    *,
    mission_id: str,
    mission_status: str,
    phase: str,
    current_round: int,
    max_rounds: int,
    objective: str,
    plan_mode: str,
    last_review: dict[str, Any] | None,
    last_plan: dict[str, Any] | None,
    last_main_summary: str,
    recent_events: list[dict[str, Any]],
) -> str:
    """Multi-line snapshot for ``/status``.

    Pure function (no daemon state, no I/O) so it's trivial to unit-test.
    """
    head = f"📊 mission {mission_id}   {mission_status}"
    if max_rounds:
        head += f"   round {current_round}/{max_rounds}"
    head += f"   phase={phase}"
    lines: list[str] = [head]
    obj_preview = (objective or "").strip()
    if len(obj_preview) > 140:
        obj_preview = obj_preview[:140].rstrip() + "…"
    lines.append(f"   objective: {obj_preview}")
    lines.append(f"   plan_mode: {plan_mode}")

    if last_review:
        status = str(last_review.get("status", "?"))
        icon = _REVIEW_STATUS_ICONS.get(status, "•")
        line = f"   last review (round {last_review.get('round', '?')}): {icon} {status}"
        reason = (last_review.get("reason") or "").strip()
        if reason:
            line += f" — {reason[:200]}"
        lines.append(line)
        next_action = (last_review.get("next_action") or "").strip()
        if status != "done" and next_action:
            lines.append(f"     ↳ next: {next_action[:200]}")
    else:
        lines.append("   last review: (none yet)")

    if last_plan:
        main_inst = (last_plan.get("main_instruction") or "").strip()
        round_idx = last_plan.get("round", "?")
        if main_inst:
            lines.append(f"   last plan (round {round_idx}): {main_inst[:240]}")
        else:
            lines.append(f"   last plan (round {round_idx}): (no follow-up)")

    last_main_summary = (last_main_summary or "").strip()
    if last_main_summary:
        preview = last_main_summary[:240]
        if len(last_main_summary) > 240:
            preview = preview.rstrip() + "…"
        lines.append(f"   last main: {preview}")

    if recent_events:
        lines.append("   recent:")
        for entry in recent_events[-6:]:
            ts = (entry.get("ts") or "")
            # Prefer HH:MM:SS slice from ISO-8601 timestamps.
            if "T" in ts:
                ts = ts.split("T", 1)[1][:8]
            else:
                ts = ts[:8]
            short = (entry.get("short") or entry.get("type") or "").strip()
            lines.append(f"     {ts} {short}")
    return "\n".join(lines)


__all__ = [
    "MissionConfig",
    "MissionDaemon",
    "MissionDaemonConfig",
]
