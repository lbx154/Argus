"""SupervisedEngineer: round-loop wrapper around an engineer call.

This is the heart of the argus-skill v0.1 integration:

  * Each round, run the engineer with the current task prompt
    (initial task + optional skill block + optional reviewer next_action
    from prior round).
  * Accept an explicit, decisively verified Engineer self-review waiver for a
    bounded task; otherwise call the Reviewer for a structured verdict.
  * If ``done``, stop. If ``continue``, capture ``next_action`` and loop.
    If ``blocked``, stop and surface the reason.

Provenance: the round-loop control flow is adapted from
``ArgusBot/agent_cli/core/engine.py`` (LoopEngine), simplified to the
single-agent case — argus-skill does not have ArgusBot's planner /
explore subagent; the skill block plays a similar "what to do" role for
the engineer in front of you.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable

from ..core.models import (
    LoopOutcome,
    LoopStatus,
    RoundRecord,
    RunnerOptions,
    RunnerResult,
)
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.secret_guard import (
    known_secret_values,
    redact_secrets_record,
)
from ..reviewer import Reviewer, ReviewerConfig
from .background_subagents import (
    emit_subagent_cost_events,
)
from .checkpoint import ensure_shared_checkpoint
from .self_review import (
    EngineerCompletionDecision,
    EngineerSkillMaintenanceOutcome,
)

log = logging.getLogger(__name__)
# ``round_config``/``round_waits`` hold the config dataclasses and the
# session-tail helpers respectively (mechanical extraction to keep this
# module under the maintainability line-count target); re-exported here as
# plain module attributes for historical/test module-attribute access (e.g.
# ``runner_module._is_codex_compaction_line``, direct
# ``from argus_skill.engineer.runner import EngineerConfig``).
from .round_config import (
    EngineerConfig,
    SupervisedConfig,
    _engineer_live_search,
    parse_continue_work_request,
)
from .round_execution import RoundExecutionMixin
from .round_prompt import RoundPromptMixin
from .round_reviewer import RoundReviewerMixin
from .round_self_review import RoundSelfReviewMixin
from .round_settlement import RoundSettlementMixin

# The following ``round_signals`` re-exports are not called internally by
# this module anymore (their logic now lives in the phase mixins above), but
# they are kept importable here — as plain module attributes — for
# historical/test module-attribute access (e.g. ``runner_module._plan_signal_event``,
# direct ``from argus_skill.engineer.runner import _apply_round_secret_guard``).
from .round_signals import (
    _apply_round_secret_guard,  # noqa: F401
    _next_decision_stall_streak,  # noqa: F401
    _normalize_dynamic_plan_mode,  # noqa: F401
    _pause_decision_clock,  # noqa: F401
    _plan_signal_event,  # noqa: F401
    _promote_scope_change_to_replan,  # noqa: F401
    _review_event_payload,  # noqa: F401
    _run_background_wait,  # noqa: F401
    _run_external_work_wait,  # noqa: F401
)
from .round_state import RoundLoopState
from .round_stop_signals import (
    backend_failure_review_decision,
    daemon_stop_review_decision,
    external_pause_review_decision,
    fatal_error_looks_like_backend_failure,
    fatal_error_looks_like_compaction_thrash,
    fatal_error_looks_like_daemon_stop_request,
    fatal_error_looks_like_effective_progress_timeout,
    fatal_error_looks_like_model_configuration,
    fatal_error_looks_like_operator_abort_request,
    fatal_error_looks_like_recoverable_reconnect,
    model_configuration_review_decision,
    operator_abort_review_decision,
    runner_result_is_backend_failure,
    should_clear_thread_id_after_outcome,
)
from .round_waits import (  # noqa: F401 -- re-exported for tests
    RoundWaitsMixin,
    _codex_sessions_root,
    _is_codex_compaction_line,
    _is_effective_codex_session_line,
    _is_project_progress_ignored_dir,
    _session_cwd_matches,
    _SessionReadResult,
)

# ``_EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS``, ``_PROJECT_PROGRESS_
# MAX_FILES`` and ``_PROJECT_PROGRESS_SCAN_BUDGET_SECONDS`` stay here (rather
# than moving with the other config/session constants) because they are read
# directly by ``_EffectiveProgressWatchdog`` below, which a test monkeypatches
# via ``runner_module._PROJECT_PROGRESS_MAX_FILES``.
_EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS = 120.0
_PROJECT_PROGRESS_MAX_FILES = 5000
_PROJECT_PROGRESS_SCAN_BUDGET_SECONDS = 0.35


class _EffectiveProgressWatchdog:
    """Detect live-but-stale Codex turns.

    Codex can keep a subprocess alive by writing token-count heartbeats
    after the last real tool/message event. The lower-level idle watcher
    sees those heartbeats as stdout activity, so this watchdog looks at
    semantic progress instead.
    """

    def __init__(
        self,
        *,
        workdir: Path,
        timeout_seconds: int,
        check_interval_seconds: float,
        warning_seconds: int = 0,
        stalled_seconds: int = 0,
        on_event: Callable[[dict], None] | None = None,
        run_label: str | None = None,
        compaction_limit: int = 0,
        now: float | None = None,
    ) -> None:
        self.workdir = Path(workdir).expanduser().resolve()
        self.timeout_seconds = max(0, int(timeout_seconds or 0))
        self.warning_seconds = max(0, int(warning_seconds or 0))
        self.stalled_seconds = max(0, int(stalled_seconds or 0))
        self.check_interval_seconds = max(1.0, float(check_interval_seconds or 1.0))
        self.on_event = on_event
        self.run_label = run_label
        self.compaction_limit = max(0, int(compaction_limit or 0))
        self.started_at = time.time() if now is None else float(now)
        self.last_effective_progress_at = self.started_at
        self._last_check_at = 0.0
        self._interrupt_reason: str | None = None
        self._interrupted_event_sent = False
        self._warning_event_sent = False
        self._stalled_event_sent = False
        self._compaction_thrash_event_sent = False
        self._compaction_count = 0
        self._last_waiting_event_at = 0.0
        self._project_signature = self._latest_project_signature()
        self._session_root = _codex_sessions_root()
        self._session_offsets = self._initial_session_offsets()
        self._relevant_sessions: set[Path] = set()

    def interrupt_reason(self) -> str | None:
        if self.timeout_seconds <= 0:
            return None
        if self._interrupt_reason:
            return self._interrupt_reason
        now = time.monotonic()
        if now - self._last_check_at < self.check_interval_seconds:
            return None
        self._last_check_at = now
        try:
            self._refresh_effective_progress()
        except Exception:  # noqa: BLE001 - watchdog must never crash a runner
            log.debug("effective progress watchdog check failed", exc_info=True)
            return None

        if (
            self.compaction_limit
            and self._compaction_count >= self.compaction_limit
        ):
            self._interrupt_reason = (
                "compaction thrash: codex auto-compaction amnesia loop — "
                f"{self._compaction_count} compactions within one round "
                f"(limit {self.compaction_limit}); stopping this turn so the "
                "next fresh session can continue from CHECKPOINT.md"
            )
            self._emit_compaction_thrash_event()
            return self._interrupt_reason

        idle_seconds = time.time() - self.last_effective_progress_at
        self._emit_staged_events(idle_seconds)
        if idle_seconds < self.timeout_seconds:
            self._emit_waiting_event(idle_seconds)
            return None

        self._interrupt_reason = (
            "effective progress timeout: no non-token Codex session events "
            f"or project file changes for {int(idle_seconds)}s "
            f"(limit {self.timeout_seconds}s)"
        )
        self._emit_interrupted_event(idle_seconds)
        return self._interrupt_reason

    def current_interrupt_reason(self) -> str | None:
        return self._interrupt_reason

    def compaction_count(self) -> int:
        """Number of codex auto-compactions observed this round (F5 hedge):
        the loop forces a full STATIC re-send next round when this is > 0."""
        return int(self._compaction_count)

    def _refresh_effective_progress(self) -> None:
        if self._project_changed():
            self._mark_effective_progress()
        if self._session_progressed():
            self._mark_effective_progress()

    def _mark_effective_progress(self) -> None:
        self.last_effective_progress_at = time.time()
        self._last_waiting_event_at = 0.0
        self._warning_event_sent = False
        self._stalled_event_sent = False

    def _emit_staged_events(self, idle_seconds: float) -> None:
        if self.on_event is None:
            return
        stages: list[tuple[str, int, dict[str, object]]] = []
        if (
            self.warning_seconds > 0
            and idle_seconds >= self.warning_seconds
            and not self._warning_event_sent
        ):
            self._warning_event_sent = True
            stages.append(
                (
                    "round.watchdog.no_progress_warning",
                    self.warning_seconds,
                    {"operator_alert": True},
                )
            )
        if (
            self.stalled_seconds > 0
            and idle_seconds >= self.stalled_seconds
            and not self._stalled_event_sent
        ):
            self._stalled_event_sent = True
            stages.append(
                (
                    "round.watchdog.likely_stalled",
                    self.stalled_seconds,
                    {"operator_alert": True, "likely_blocked": True},
                )
            )
        for event_type, threshold, extra in stages:
            try:
                self.on_event(
                    {
                        "type": event_type,
                        "run_label": self.run_label,
                        "idle_seconds": round(idle_seconds, 1),
                        "threshold_seconds": threshold,
                        "timeout_seconds": self.timeout_seconds,
                        "round_started_at": self.started_at,
                        "last_effective_progress_at": self.last_effective_progress_at,
                        "relevant_session_count": len(self._relevant_sessions),
                        "project_signature": self._project_signature,
                        "compaction_count": int(self._compaction_count),
                        **extra,
                    }
                )
            except Exception:  # noqa: BLE001
                log.debug("effective progress watchdog stage event failed", exc_info=True)

    def _emit_interrupted_event(self, idle_seconds: float) -> None:
        if self._interrupted_event_sent or self.on_event is None:
            return
        self._interrupted_event_sent = True
        try:
            self.on_event({
                "type": "round.watchdog.effective_progress_timeout",
                "run_label": self.run_label,
                "idle_seconds": round(idle_seconds, 1),
                "limit_seconds": self.timeout_seconds,
                "operator_alert": True,
                "will_retry_fresh_session": True,
                "text": self._interrupt_reason,
            })
        except Exception:  # noqa: BLE001
            log.debug("effective progress watchdog event failed", exc_info=True)

    def _emit_compaction_thrash_event(self) -> None:
        if self._compaction_thrash_event_sent or self.on_event is None:
            return
        self._compaction_thrash_event_sent = True
        try:
            self.on_event({
                "type": "round.watchdog.compaction_thrash",
                "run_label": self.run_label,
                "compaction_count": int(self._compaction_count),
                "limit": int(self.compaction_limit),
                "text": self._interrupt_reason,
            })
        except Exception:  # noqa: BLE001
            log.debug("compaction thrash watchdog event failed", exc_info=True)

    def _emit_waiting_event(self, idle_seconds: float) -> None:
        if self.on_event is None:
            return
        if idle_seconds < _EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS:
            return
        now = time.time()
        if (
            self._last_waiting_event_at
            and now - self._last_waiting_event_at
            < _EFFECTIVE_PROGRESS_WAITING_EVENT_INTERVAL_SECONDS
        ):
            return
        self._last_waiting_event_at = now
        try:
            self.on_event({
                "type": "round.watchdog.waiting",
                "run_label": self.run_label,
                "idle_seconds": round(idle_seconds, 1),
                "limit_seconds": self.timeout_seconds,
                "text": (
                    "engineer turn is still alive but has no non-token Codex "
                    f"session events or project file changes for {int(idle_seconds)}s "
                    f"(limit {self.timeout_seconds}s)"
                ),
            })
        except Exception:  # noqa: BLE001
            log.debug("effective progress watchdog waiting event failed", exc_info=True)

    def _project_changed(self) -> bool:
        signature = self._latest_project_signature()
        if signature is None:
            return False
        previous = self._project_signature
        self._project_signature = signature
        if previous is None:
            return signature[0] >= self.started_at - 1.0
        return signature != previous and signature[0] >= self.started_at - 1.0

    def _latest_project_signature(self) -> tuple[float, int, str] | None:
        started = time.monotonic()
        newest: tuple[float, int, str] | None = None
        scanned = 0
        try:
            walker = os.walk(self.workdir, topdown=True)
        except OSError:
            return None
        for dirpath, dirnames, filenames in walker:
            dirnames[:] = [
                name for name in dirnames
                if not _is_project_progress_ignored_dir(Path(dirpath), name)
            ]
            for filename in filenames:
                if scanned >= _PROJECT_PROGRESS_MAX_FILES:
                    return newest
                if time.monotonic() - started > _PROJECT_PROGRESS_SCAN_BUDGET_SECONDS:
                    return newest
                path = Path(dirpath) / filename
                scanned += 1
                try:
                    stat = path.stat()
                except OSError:
                    continue
                candidate = (float(stat.st_mtime), int(stat.st_size), str(path))
                if newest is None or candidate > newest:
                    newest = candidate
        return newest

    def _initial_session_offsets(self) -> dict[Path, int]:
        offsets: dict[Path, int] = {}
        root = self._session_root
        if root is None:
            return offsets
        try:
            paths = list(root.rglob("*.jsonl"))
        except OSError:
            return offsets
        for path in paths:
            try:
                offsets[path] = path.stat().st_size
            except OSError:
                continue
        return offsets

    def _session_progressed(self) -> bool:
        root = self._session_root
        if root is None:
            return False
        progressed = False
        try:
            paths = list(root.rglob("*.jsonl"))
        except OSError:
            return False
        for path in paths:
            try:
                stat = path.stat()
            except OSError:
                continue
            previous_offset = self._session_offsets.get(path)
            if previous_offset is None:
                previous_offset = 0
            if stat.st_size < previous_offset:
                previous_offset = 0
            if stat.st_size == previous_offset:
                self._session_offsets[path] = stat.st_size
                continue
            if not self._session_relevant(path):
                self._session_offsets[path] = stat.st_size
                continue
            result = self._read_effective_session_events(path, previous_offset)
            if result.progressed:
                progressed = True
            if result.compactions:
                self._compaction_count += result.compactions
            # Advance only past fully newline-terminated lines so a partially
            # written trailing line is re-read (and its eventual ``compacted``
            # event is never silently skipped) on the next poll.
            self._session_offsets[path] = previous_offset + result.consumed_bytes
        return progressed

    def _session_relevant(self, path: Path) -> bool:
        if path in self._relevant_sessions:
            return True
        if _session_cwd_matches(path, self.workdir):
            self._relevant_sessions.add(path)
            return True
        return False

    def _read_effective_session_events(
        self, path: Path, offset: int
    ) -> _SessionReadResult:
        try:
            with path.open("rb") as fh:
                fh.seek(max(0, offset))
                data = fh.read()
        except OSError:
            return _SessionReadResult(progressed=False, compactions=0, consumed_bytes=0)
        last_newline = data.rfind(b"\n")
        if last_newline < 0:
            # No complete line yet; leave the offset untouched.
            return _SessionReadResult(progressed=False, compactions=0, consumed_bytes=0)
        complete = data[: last_newline + 1]
        progressed = False
        compactions = 0
        for raw in complete.splitlines():
            line = raw.decode("utf-8", errors="replace")
            if _is_codex_compaction_line(line):
                compactions += 1
                continue
            if _is_effective_codex_session_line(line):
                progressed = True
        return _SessionReadResult(
            progressed=progressed,
            compactions=compactions,
            consumed_bytes=len(complete),
        )



class SupervisedEngineer(
    RoundPromptMixin,
    RoundExecutionMixin,
    RoundWaitsMixin,
    RoundSelfReviewMixin,
    RoundReviewerMixin,
    RoundSettlementMixin,
):
    """Run the Engineer with self-verification or Reviewer-gated retries.

    Stateless across calls. Construct once with backends, call ``run``
    per task.
    """

    def __init__(
        self,
        *,
        engineer_runner: RunnerBackend,
        reviewer: Reviewer,
        engineer_config: EngineerConfig,
        reviewer_config: ReviewerConfig,
    ) -> None:
        self.engineer_runner = engineer_runner
        self.reviewer = reviewer
        self.engineer_config = engineer_config
        self.reviewer_config = reviewer_config

    def run(
        self,
        *,
        objective: str,
        original_objective: str | None = None,
        engineer_prompt_builder: Callable[[str | None, bool], str],
        supervised_config: SupervisedConfig,
        workdir: Path,
        on_event: Callable[[dict], None] | None = None,
        seed_thread_id: str | None = None,
        scope: str = "",
        prepare_review_context: Callable[[], None] | None = None,
        review_completed_hook: Callable[[RoundRecord], None] | None = None,
        continue_adaptor: Callable[[list[RoundRecord]], str] | None = None,
        reviewer_skill_block: str | None = None,
        engineer_skill_maintenance: Callable[
            [EngineerCompletionDecision, str | None, str],
            EngineerSkillMaintenanceOutcome,
        ] | None = None,
    ) -> tuple[LoopStatus, list[RoundRecord], str, str, str | None]:
        """Run the supervised loop.

        ``engineer_prompt_builder(next_action, include_static)`` is called once
        per round. Round 1 receives the static task/skill contract; continuation
        rounds default to a compact Reviewer delta plus CHECKPOINT.md. Engineer
        and Reviewer both start fresh provider sessions every round; raw model
        threads are never carried across a round or mission boundary.

        Returns ``(status, rounds, final_message, reason, last_thread_id)``.

        This orchestrates the round loop's phase mixins in order — prompt
        assembly, engineer-turn execution, non-review stop shortcircuits,
        agent-driven background/external waits, progress/self-review
        bookkeeping, Reviewer invocation/retry, and round settlement — and
        interprets each phase's ``RoundControl`` verdict: ``return`` ends the
        mission with a terminal result, ``continue_loop`` immediately starts
        the next round, and falling through (``proceed``) lets the round
        continue to the next phase. Completion authority is explicit rather
        than inferred: an allowed `review=skip` control produces an Engineer
        self-review verdict, while `review=required` and mandatory-review tasks
        invoke the independent Reviewer. Every phase only forwards or records
        the selected verdict source; it never overrides that source.
        """
        if on_event is not None:
            raw_on_event = on_event

            def _redacted_on_event(event: dict) -> None:
                raw_on_event(
                    redact_secrets_record(
                        event,
                        known_values=known_secret_values(),
                    )
                )

            on_event = _redacted_on_event
        state = RoundLoopState()
        # ``seed_thread_id`` is intentionally ignored: autonomous role calls are
        # one turn per provider session. The checkpoint file is the baton.
        _ = seed_thread_id
        checkpoint_path = ensure_shared_checkpoint(supervised_config.checkpoint_path)
        control_scope = str(
            supervised_config.session_id or uuid.uuid4().hex
        )

        for round_index in range(1, supervised_config.max_rounds + 1):
            if on_event:
                try:
                    emit_subagent_cost_events(workdir, on_event)
                except Exception:  # noqa: BLE001
                    log.debug("subagent cost scan ignored an error", exc_info=True)
            engineer_prompt, control_path = self._assemble_round_prompt(
                round_index=round_index,
                supervised_config=supervised_config,
                engineer_prompt_builder=engineer_prompt_builder,
                reviewer_next_action=state.reviewer_next_action,
                checkpoint_path=checkpoint_path,
                control_scope=control_scope,
                workdir=workdir,
                on_event=on_event,
            )

            outcome = self._run_engineer_turn(
                round_index=round_index,
                engineer_prompt=engineer_prompt,
                workdir=workdir,
                supervised_config=supervised_config,
                checkpoint_path=checkpoint_path,
                control_path=control_path,
                on_event=on_event,
                state=state,
            )

            control = self._handle_stop_kind_shortcircuit(
                round_index=round_index,
                supervised_config=supervised_config,
                outcome=outcome,
                state=state,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue

            control = self._handle_agent_driven_wait(
                round_index=round_index,
                supervised_config=supervised_config,
                raw_engineer_message=outcome.raw_engineer_message,
                workdir=workdir,
                state=state,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue

            control = self._handle_progress_and_self_review(
                round_index=round_index,
                supervised_config=supervised_config,
                outcome=outcome,
                state=state,
                engineer_skill_maintenance=engineer_skill_maintenance,
                review_completed_hook=review_completed_hook,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue

            control = self._invoke_reviewer_with_retry(
                objective=objective,
                original_objective=original_objective,
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                scope=scope,
                checkpoint_path=checkpoint_path,
                reviewer_skill_block=reviewer_skill_block,
                outcome=outcome,
                state=state,
                prepare_review_context=prepare_review_context,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue
            review = control.payload

            control = self._settle_round(
                review=review,
                round_index=round_index,
                supervised_config=supervised_config,
                workdir=workdir,
                outcome=outcome,
                state=state,
                review_completed_hook=review_completed_hook,
                continue_adaptor=continue_adaptor,
                on_event=on_event,
            )
            if control.action == "return":
                return control.terminal
            if control.action == "continue_loop":
                continue
            # else "proceed": fall through to the next round.

        return (
            "max_rounds",
            state.rounds,
            state.last_engineer_message,
            f"Hit max_rounds={supervised_config.max_rounds} without reviewer-confirmed completion.",
            None,
        )

    def _run_engineer(
        self,
        *,
        prompt: str,
        workdir: Path,
        run_label: str,
        resume_thread_id: str | None = None,
        reasoning_effort: str | None = None,
        supervised_config: SupervisedConfig | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> tuple[RunnerResult, int]:
        effective_progress_provider: Callable[[], str | None] | None = None
        effective_progress_watchdog: _EffectiveProgressWatchdog | None = None
        hard_idle_seconds: int | None = None
        if supervised_config is not None:
            timeout_seconds = int(
                supervised_config.effective_progress_timeout_seconds or 0
            )
            hard_idle_seconds = int(supervised_config.runner_hard_idle_seconds or 0)
            if timeout_seconds > 0:
                effective_progress_watchdog = _EffectiveProgressWatchdog(
                    workdir=workdir,
                    timeout_seconds=timeout_seconds,
                    check_interval_seconds=(
                        supervised_config.effective_progress_check_interval_seconds
                    ),
                    warning_seconds=(
                        supervised_config.effective_progress_warning_seconds
                    ),
                    stalled_seconds=(
                        supervised_config.effective_progress_stalled_seconds
                    ),
                    on_event=on_event,
                    run_label=run_label,
                    compaction_limit=supervised_config.round_compaction_limit,
                )
                effective_progress_provider = effective_progress_watchdog.interrupt_reason
        try:
            result = gateway_run_exec(
                self.engineer_runner,
                prompt=prompt,
                options=RunnerOptions(
                    model=self.engineer_config.model,
                    reasoning_effort=(
                        reasoning_effort
                        if reasoning_effort is not None
                        else self.engineer_config.reasoning_effort
                    ),
                    extra_args=self.engineer_config.extra_args,
                    full_auto=self.engineer_config.full_auto,
                    skip_git_repo_check=self.engineer_config.skip_git_repo_check,
                    dangerous_yolo=self.engineer_config.dangerous_yolo,
                    sandbox_mode=self.engineer_config.sandbox_mode,
                    isolate_workdir=self.engineer_config.isolate_workdir,
                    working_dir=str(workdir),
                    live_search=_engineer_live_search(
                        workdir, self.engineer_config.live_search_stages
                    ),
                    external_interrupt_reason_provider=effective_progress_provider,
                    watchdog_hard_idle_seconds=hard_idle_seconds,
                ),
                run_label=run_label,
                resume_thread_id=resume_thread_id,
            )
            # F5: per-round compaction count (0 when no watchdog) — the loop forces
            # a full STATIC re-send next round when this is > 0 (anti-amnesia hedge).
            _compactions = (
                effective_progress_watchdog.compaction_count()
                if effective_progress_watchdog is not None
                else 0
            )
            if (
                effective_progress_watchdog is not None
                and effective_progress_watchdog.current_interrupt_reason()
                and not getattr(result, "fatal_error", None)
            ):
                return replace(
                    result,
                    exit_code=(
                        int(getattr(result, "exit_code", 0) or 0)
                        if int(getattr(result, "exit_code", 0) or 0) != 0
                        else -1
                    ),
                    fatal_error=effective_progress_watchdog.current_interrupt_reason(),
                ), _compactions
            return result, _compactions
        except Exception as exc:  # noqa: BLE001
            msg = f"engineer runner raised {type(exc).__name__}: {exc}"
            log.exception("engineer runner raised during %s", run_label)
            return RunnerResult(
                exit_code=-1,
                fatal_error=msg,
                stderr_lines=[msg],
                stop_kind="backend_unavailable",
            ), 0


__all__ = [
    "EngineerConfig",
    "SupervisedConfig",
    "SupervisedEngineer",
    "LoopOutcome",
    "backend_failure_review_decision",
    "external_pause_review_decision",
    "model_configuration_review_decision",
    "daemon_stop_review_decision",
    "operator_abort_review_decision",
    "fatal_error_looks_like_backend_failure",
    "runner_result_is_backend_failure",
    "fatal_error_looks_like_model_configuration",
    "fatal_error_looks_like_daemon_stop_request",
    "fatal_error_looks_like_operator_abort_request",
    "fatal_error_looks_like_effective_progress_timeout",
    "fatal_error_looks_like_compaction_thrash",
    "fatal_error_looks_like_recoverable_reconnect",
    "parse_continue_work_request",
    "should_clear_thread_id_after_outcome",
]
