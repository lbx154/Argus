"""Reviewer sub-agent: graded "done / continue / blocked" verdict.

Provenance: vendored from ``ArgusBot/agent_cli/reviewer.py``. The
substantive change is decoupling: the original took a ``AgentCliRunner``
directly; this version takes any ``RunnerBackend`` (see
``argus_skill.core.ports``) so it works with any supported agent CLI or the
in-memory test stub equally well.

Public surface kept identical: ``Reviewer.evaluate(...) -> ReviewDecision``,
``parse_decision_text(text) -> ReviewDecision | None``.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import ReviewDecision, RunnerOptions
from ..core.ports import RunnerBackend
from ..core.role_decision import latest_role_decision
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.stop_kinds import normalize_stop_kind
from ._parsing import _find_decision_in_messages, decision_from_payload

log = logging.getLogger(__name__)


@dataclass
class ReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    active_vertical: str = ""
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
    sandbox_mode: str | None = None
    isolate_workdir: bool = False
    working_dir: str | None = None
    vertical_state_root: str | None = None


def _load_wiki_curator_skill_if_present(
    working_dir: str | Path | None = None,
) -> str | None:
    """Compatibility wrapper for the prompt-module helper."""
    from ..roles.prompts.reviewer import _load_wiki_curator_skill_if_present

    return _load_wiki_curator_skill_if_present(working_dir)


def _verification_directive() -> str:
    """Compatibility wrapper for the prompt-module helper."""
    from ..roles.prompts.reviewer import _verification_directive

    return _verification_directive()


def _engineer_log_audit_block(
    engineer_log_path: str,
    *,
    engineer_call_id: str = "",
    round_index: int,
    measured: bool,
    compact: bool = False,
) -> str:
    """Compatibility wrapper for the prompt-module helper."""
    from ..roles.prompts.reviewer import _engineer_log_audit_block

    return _engineer_log_audit_block(
        engineer_log_path,
        engineer_call_id=engineer_call_id,
        round_index=round_index,
        measured=measured,
        compact=compact,
    )


class Reviewer:
    """One independent verdict per round, with optional same-role resume."""

    def __init__(
        self,
        runner: RunnerBackend,
        *,
        skill_store: Any | None = None,
        memory_maintenance_enabled: bool = True,
    ) -> None:
        self.runner = runner
        # The Reviewer speaks normally and ends with named verdict lines. JSON
        # remains parser-only backward compatibility for already-running old
        # sessions; no backend receives an output schema.
        self._last_prompt_block_stats: dict[str, dict[str, int]] = {}
        # Optional agent-native library roots. The Reviewer searches and reads
        # relevant Markdown itself; the runtime never injects Skill bodies.
        self.skill_store = skill_store
        self.memory_maintenance_enabled = memory_maintenance_enabled
        from ..skills.missions import ReviewerMission
        self.mission = ReviewerMission(skill_store)

    def evaluate(
        self,
        *,
        objective: str,
        original_objective: str | None = None,
        operator_messages: list[str] | None = None,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        config: ReviewerConfig,
        round_max: int = 0,
        planner_review_instruction: str = "",
        active_skill_id: str | None = None,
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        checkpoint_path: str = "",
        background_context: str = "",
        escalate_hint: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        preselected_skill_block: str | None = None,
        resume_thread_id: str | None = None,
        prior_static_fingerprint: str = "",
    ) -> ReviewDecision:
        # Resolve the Reviewer's own library contract once for both the prompt
        # fallback and a backend-native loader.
        review_libraries = self.mission.libraries()
        if preselected_skill_block is None:
            preselected_skill_block = review_libraries.block
        native_skill_paths = [
            str(path) for path in getattr(review_libraries, "native_paths", [])
        ]
        # Split the prompt into a byte-stable STATIC preamble and per-round DELTA.
        # A matching same-role session receives only the new delta.
        common = dict(
            objective=objective,
            original_objective=original_objective or objective,
            operator_messages=operator_messages or [],
            planner_review_instruction=planner_review_instruction,
            round_index=round_index,
            round_max=round_max,
            session_id=session_id,
            main_summary=main_summary,
            main_error=main_error,
            active_skill_id=active_skill_id,
            prev_review_summary=prev_review_summary,
            raw_evidence=raw_evidence,
            scope=scope,
            prior_checkpoint=prior_checkpoint,
            checkpoint_path=checkpoint_path,
            background_context=background_context,
            escalate_hint=escalate_hint,
            engineer_log_path=engineer_log_path,
            engineer_call_id=engineer_call_id,
            preselected_skill_block=preselected_skill_block,
            working_dir=config.working_dir,
            vertical_state_root=config.vertical_state_root,
            vertical=config.active_vertical,
        )
        static, delta_base = self._render(resumed=False, **common)
        prompt_block_stats = {
            name: dict(stats)
            for name, stats in self._last_prompt_block_stats.items()
        }
        fingerprint_input = bytearray(static.encode("utf-8"))
        new_fp = hashlib.sha256(fingerprint_input).hexdigest()
        resume = (
            resume_thread_id
            if resume_thread_id and prior_static_fingerprint == new_fp
            else None
        )
        from ..roles.prompts.reviewer import (
            _REEVALUATE_HEADER,
            assemble_reviewer_prompt,
        )

        prompt = assemble_reviewer_prompt(
            "" if resume else static,
            (_REEVALUATE_HEADER + delta_base) if resume else delta_base,
        )
        try:
            result = gateway_run_exec(
                self.runner,
                prompt=prompt,
                resume_thread_id=resume,
                options=RunnerOptions(
                    model=config.model,
                    reasoning_effort=config.reasoning_effort,
                    # Reviewer is an independent read-only judge. It must never
                    # repair evidence, curate Wiki pages, or edit checkpoints.
                    dangerous_yolo=False,
                    full_auto=False,
                    sandbox_mode="read-only",
                    isolate_workdir=False,
                    skip_git_repo_check=config.skip_git_repo_check,
                    extra_args=list(config.extra_args) if config.extra_args else None,
                    skill_paths=native_skill_paths,
                    working_dir=config.working_dir,
                    # Search is available for the rare turn that proposes a
                    # skill; ordinary review turns need not invoke it.
                    live_search=True,
                ),
                run_label="reviewer",
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"Reviewer runner raised {type(exc).__name__}: {exc}"
            log.exception("reviewer runner raised")
            return ReviewDecision(
                status="blocked",
                reason=msg,
                next_action="Resolve the reviewer runner failure before retrying.",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
            )
        rev_in = int(getattr(result, "input_tokens", 0) or 0)
        rev_cached = int(getattr(result, "cached_input_tokens", 0) or 0)
        rev_out = int(getattr(result, "output_tokens", 0) or 0)
        rev_reasoning_output_tokens = int(
            getattr(result, "reasoning_output_tokens", 0) or 0
        )
        # Copilot premium-request delta for this reviewer turn (0.0 off copilot).
        # copilot 下本轮 reviewer 的高级请求增量（非 copilot 时为 0.0）。
        rev_premium = float(getattr(result, "premium_requests", 0.0) or 0.0)
        # Preserve transport metadata for observability and an opt-in same-role
        # continuation.
        rev_tid = getattr(result, "thread_id", None)
        fatal = str(getattr(result, "fatal_error", "") or "").strip()
        backend_stop_kind = (
            normalize_stop_kind(getattr(result, "stop_kind", None))
            or "backend_unavailable"
        )
        if fatal or result.exit_code != 0:
            reason = (
                "Reviewer backend returned no complete verdict "
                f"(exit={result.exit_code}"
                + (f", fatal_error={fatal}" if fatal else "")
                + ")."
            )
            return ReviewDecision(
                status="blocked",
                reason=reason,
                next_action=(
                    "Reviewer backend ended before a complete verdict — do NOT "
                    "treat partial output as evidence about the engineer's work."
                ),
                backend_unavailable=True,
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
                reasoning_output_tokens=rev_reasoning_output_tokens,
                premium_requests=rev_premium,
                thread_id=rev_tid,
                static_fingerprint=new_fp,
                backend_fatal_error=fatal,
                backend_exit_code=result.exit_code,
                backend_stop_kind=backend_stop_kind,
            )
        process_decision = latest_role_decision(result, "reviewer")
        # A recorded decision is already structured; reading it directly keeps
        # the runtime from serialising its own payload back to JSON text and
        # re-parsing that. `decision_messages` stays the evidence quoted back to
        # the operator when nothing parses.
        decision_messages = (
            [json.dumps(process_decision, ensure_ascii=True)]
            if process_decision is not None
            else result.agent_messages
        )
        if not decision_messages:
            return ReviewDecision(
                status="continue",
                reason=f"Reviewer returned empty output. exit={result.exit_code}",
                next_action="Continue implementation and provide concrete completed work.",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
                reasoning_output_tokens=rev_reasoning_output_tokens,
                premium_requests=rev_premium,
                thread_id=rev_tid,
                static_fingerprint=new_fp,
            )
        parsed = (
            decision_from_payload(process_decision)
            if process_decision is not None
            else _find_decision_in_messages(decision_messages)
        )
        if parsed is None:
            from ._parsing import describe_unparsed_verdict

            return ReviewDecision(
                status="continue",
                reason=describe_unparsed_verdict(decision_messages),
                next_action=(
                    "Continue implementation and record a valid Reviewer decision "
                    "event on the next review."
                ),
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
                reasoning_output_tokens=rev_reasoning_output_tokens,
                premium_requests=rev_premium,
                thread_id=rev_tid,
                static_fingerprint=new_fp,
            )
        # Phase-2 instrumentation: cost-tracking sinks (e.g. LifeSupervisor's
        # _CostTrackingSink) read these fields off ``round.review.completed``
        # events. If we don't propagate them every iteration budget enforcement
        # silently breaks and the journal shows ``cost_usd=$0.0000``.
        parsed.input_tokens = rev_in
        parsed.cached_input_tokens = rev_cached
        parsed.output_tokens = rev_out
        parsed.reasoning_output_tokens = rev_reasoning_output_tokens
        parsed.premium_requests = rev_premium
        parsed.prompt_block_stats = prompt_block_stats
        parsed.thread_id = rev_tid
        parsed.static_fingerprint = new_fp
        parsed.session_resumed = bool(resume)
        if (
            parsed.status == "done"
            and bool(
                getattr(
                    self.runner,
                    "tool_activity_observation_supported",
                    False,
                )
            )
            and not bool(getattr(result, "tool_activity_observed", False))
        ):
            parsed.status = "continue"
            parsed.reason = (
                "Reviewer proposed completion without independently inspecting or "
                "executing a check."
            )
            parsed.next_action = (
                "Provide a directly runnable acceptance path and concrete evidence "
                "so Reviewer can independently exercise the result."
            )
        # The L2 reviewer's verdict is authoritative — the harness must not
        # second-guess its scientific judgment from structured result labels or
        # keyword heuristics on the engineer's summary.
        # If a generic role-acknowledgment turn slips through, that is a
        # reviewer-prompt concern (the reviewer is told to demand concrete
        # evidence and verify when it is missing/contradictory), not a harness
        # post-filter.
        return parsed

    def _render(
        self,
        *,
        resumed: bool = False,
        objective: str,
        original_objective: str = "",
        operator_messages: list[str],
        planner_review_instruction: str,
        round_index: int,
        session_id: str | None,
        main_summary: str,
        main_error: str | None,
        round_max: int = 0,
        active_skill_id: str | None = None,
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        checkpoint_path: str = "",
        background_context: str = "",
        escalate_hint: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        preselected_skill_block: str | None = None,
        working_dir: str | Path | None = None,
        vertical_state_root: str | Path | None = None,
        vertical: str = "",
    ) -> tuple[str, str]:
        """F7: render the reviewer prompt as ``(static_preamble, round_delta)``.

        ``static_preamble`` is a byte-stable role/rubric prefix suitable for
        provider caching and same-role resume. Fresh calls receive both parts;
        resumed calls receive only the round delta.
        """
        from ..roles.prompts.reviewer import render_reviewer_prompt

        return render_reviewer_prompt(
            self,
            resumed=resumed,
            objective=objective,
            original_objective=original_objective,
            operator_messages=operator_messages,
            planner_review_instruction=planner_review_instruction,
            round_index=round_index,
            session_id=session_id,
            main_summary=main_summary,
            main_error=main_error,
            round_max=round_max,
            active_skill_id=active_skill_id,
            prev_review_summary=prev_review_summary,
            raw_evidence=raw_evidence,
            scope=scope,
            prior_checkpoint=prior_checkpoint,
            checkpoint_path=checkpoint_path,
            background_context=background_context,
            escalate_hint=escalate_hint,
            engineer_log_path=engineer_log_path,
            engineer_call_id=engineer_call_id,
            preselected_skill_block=preselected_skill_block,
            working_dir=working_dir,
            vertical_state_root=vertical_state_root,
            vertical=vertical,
        )

    def _build_prompt(self, **kwargs: Any) -> str:
        """Full reviewer prompt (static + round-1 delta). Kept for the unit tests
        and any non-resuming caller; ``evaluate`` uses ``_render`` directly."""
        static, delta = self._render(resumed=False, **kwargs)
        from ..roles.prompts.reviewer import assemble_reviewer_prompt

        return assemble_reviewer_prompt(static, delta)

    def _build_static_preamble(self, **kwargs: Any) -> str:
        """The byte-stable static preamble alone (for the fingerprint + resume)."""
        static, _ = self._render(resumed=False, **kwargs)
        return static

    def _build_round_delta(self, *, resumed: bool, **kwargs: Any) -> str:
        """This round's delta alone; ``resumed`` prepends the RE-EVALUATE header."""
        _, delta = self._render(resumed=resumed, **kwargs)
        return delta

    @property
    def last_prompt_block_stats(self) -> dict[str, dict[str, int]]:
        return {
            name: dict(stats)
            for name, stats in self._last_prompt_block_stats.items()
        }
