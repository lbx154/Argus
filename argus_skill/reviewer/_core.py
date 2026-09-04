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
from contextlib import ExitStack
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


def _parallel_final_review_passes(
    runner: RunnerBackend,
    config: "ReviewerConfig",
) -> ReviewDecision | None:
    """Run the three initial final-paper inspections on one immutable draft."""
    workdir = Path(config.artifact_root or config.working_dir or ".").resolve()
    state_root = Path(config.vertical_state_root or workdir).resolve()
    vertical = str(config.active_vertical or "").strip().lower()
    if not vertical:
        from ..skills.vertical_select import resolve_vertical

        vertical = resolve_vertical(state_root)
    if vertical != "research":
        return None
    from ..core.pipeline_state import read_pipeline_state
    from ..skills.stage_machine import current_stage

    verdict = str(read_pipeline_state(state_root).get("current_verdict") or "")
    if (
        current_stage(state_root) != "review"
        or verdict
        not in {
            "",
            "continue",
            "in_progress",
            "mapped_stage_requires_current_review",
        }
    ):
        return None
    comparison_mode = bool(config.narrative_snapshot_root)
    comparison = None
    if comparison_mode:
        from ..core.manuscript_narrative_runtime import snapshot_after_edit

        try:
            comparison = snapshot_after_edit(
                workdir,
                config.narrative_snapshot_root or "",
            )
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            return ReviewDecision(
                status="continue",
                reason=f"ScientificLoss shadow pass unavailable: {exc}",
                next_action=(
                    "Restore a readable immutable pre-edit snapshot, then rerun the "
                    "semantic-loss comparison."
                ),
            )
        from ..core.manuscript_narrative_runtime import rendered_pdf_freshness

        pdf_is_current, pdf_status = rendered_pdf_freshness(workdir)
        if not pdf_is_current:
            return ReviewDecision(
                status="continue",
                reason=f"ColdRead shadow pass unavailable: {pdf_status}",
                next_action="Compile the current manuscript before the PDF-only cold read.",
            )

    common = (
        "Read the current paper in read-only mode. Do not edit files. Start from "
        "paper/main.tex and its rendered output, then follow only direct references "
        "needed for this assigned pass. Return a concise pass/fail assessment with "
        "specific blocking findings and repairs."
    )
    if comparison is None:
        # Compatibility for direct callers that have not entered the narrative-edit
        # operation. Production Review missions always provide an immutable baseline.
        prompts = {
            "Scientific": (
                common
                + " Check the complete thesis, novelty, claim-to-code fidelity, positive "
                "controls, strongest same-information baselines, evidence, citations, and "
                "whether every necessary experiment and section is present."
            ),
            "Visual": (
                common
                + " Inspect every rendered page and every included figure and table at "
                "publication scale. Reject visible overlap, clipping, overflow, connector "
                "penetration, wrong arrows, unreadable labels, malformed tables, misleading "
                "plots, abnormal whitespace, broken float placement, or inconsistent "
                "typography."
            ),
            "Language": (
                common
                + " Check academic language and argument flow. Identify exact revisions for "
                "confident, precise prose without defensive boilerplate, experiment "
                "chronology, internal workflow language, repeated caveats, or integrity "
                "self-praise."
            ),
        }
    else:
        from ..roles.prompts import ChecklistMode, resolve_role_prompt
        from ..roles.prompts.reviewer import (
            COLD_READ,
            SCIENCE_LOSS_CHECK,
            evaluate_request,
        )

        science_policy = resolve_role_prompt(
            evaluate_request(
                state_root,
                altitude_root=workdir,
                vertical="research",
                stage="review",
                checklist_mode=ChecklistMode.NONE,
                operation=SCIENCE_LOSS_CHECK,
            )
        ).role_banner
        cold_policy = resolve_role_prompt(
            evaluate_request(
                state_root,
                altitude_root=workdir,
                vertical="research",
                stage="review",
                checklist_mode=ChecklistMode.NONE,
                operation=COLD_READ,
            )
        ).role_banner
        prompts = {
            "ScientificLoss": (
                science_policy
                + "\n\nCompare these immutable trees:\n"
                f"- before: `{comparison.before_paper}` "
                f"(sha256 {comparison.before_sha256})\n"
                f"- after: `{comparison.after_paper}` "
                f"(sha256 {comparison.after_sha256})\n"
                "Use the live project only for a direct claim-critical dispute. Return "
                "pass/fail and identify every loss by fact, prior carrier, and required "
                "replacement carrier."
            ),
            "Visual": (
                common
                + " Inspect every rendered page and every included figure and table at "
                "publication scale. Reject visible overlap, clipping, overflow, connector "
                "penetration, wrong arrows, unreadable labels, malformed tables, misleading "
                "plots, abnormal whitespace, broken float placement, or inconsistent "
                "typography."
            ),
            "ColdRead": (
                cold_policy
                + "\n\nOpen `paper/main.pdf` and return a concise pass/fail cold-read "
                "assessment with page-locatable findings."
            ),
        }

    import threading
    from concurrent.futures import ThreadPoolExecutor

    fork = getattr(runner, "fork", None)
    if not callable(fork):
        return ReviewDecision(
            status="blocked",
            reason="The reviewer backend cannot create independent parallel calls.",
            next_action="Use a reviewer backend that supports independent calls.",
            backend_unavailable=True,
            backend_stop_kind="backend_unavailable",
        )
    from ..adapters.agent_cli_backend import AgentCliBackend

    if isinstance(runner, AgentCliBackend):
        interrupt_lock = threading.Lock()
        interrupt_reason = ""
        source_interrupt = runner._default_interrupt_reason_provider

        def shared_interrupt() -> str | None:
            nonlocal interrupt_reason
            with interrupt_lock:
                if not interrupt_reason and source_interrupt is not None:
                    interrupt_reason = str(source_interrupt() or "")
                return interrupt_reason or None

        pass_runners = {
            label: fork(interrupt_reason_provider=shared_interrupt)
            for label in prompts
        }
    else:
        pass_runners = {label: fork() for label in prompts}
    if (
        any(backend is runner for backend in pass_runners.values())
        or len({id(backend) for backend in pass_runners.values()}) != len(prompts)
    ):
        return ReviewDecision(
            status="blocked",
            reason="The reviewer backend returned shared instances for parallel calls.",
            next_action="Use a reviewer backend that supports independent calls.",
            backend_unavailable=True,
            backend_stop_kind="backend_unavailable",
        )

    workspace_stack = ExitStack()
    working_dirs = {label: workdir for label in prompts}
    if comparison is not None:
        from ..core.manuscript_narrative_runtime import isolated_pdf_workspace

        working_dirs["ColdRead"] = workspace_stack.enter_context(
            isolated_pdf_workspace(workdir)
        )

    def inspect(label: str) -> Any:
        return gateway_run_exec(
            pass_runners[label],
            prompt=prompts[label],
            options=RunnerOptions(
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                dangerous_yolo=False,
                full_auto=False,
                sandbox_mode="read-only",
                force_safe_mode=True,
                skip_git_repo_check=config.skip_git_repo_check,
                extra_args=list(config.extra_args) if config.extra_args else None,
                working_dir=str(working_dirs[label]),
            ),
            run_label=f"reviewer-{label.lower()}",
        )

    created_runners = [
        backend
        for backend in pass_runners.values()
        if backend is not runner
    ]
    try:
        with ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="argus-final-review",
        ) as pool:
            futures = {
                label: pool.submit(inspect, label)
                for label in prompts
            }
            results = {}
            errors = {}
            for label in prompts:
                try:
                    results[label] = futures[label].result()
                except Exception as exc:  # provider boundary
                    errors[label] = exc
    finally:
        for backend in created_runners:
            close = getattr(backend, "close_acp_clients", None)
            if callable(close):
                close()
        workspace_stack.close()

    usage = {
        field: sum(int(getattr(result, field, 0) or 0) for result in results.values())
        for field in (
            "input_tokens",
            "cached_input_tokens",
            "output_tokens",
            "reasoning_output_tokens",
        )
    }
    premium_requests = sum(
        float(getattr(result, "premium_requests", 0.0) or 0.0)
        for result in results.values()
    )
    stop_priority = {
        "operator_abort": 0,
        "operator_pause": 1,
        "daemon_shutdown": 2,
        "budget_exhausted": 3,
        "provider_cooldown": 4,
        "provider_fence": 5,
        "permanent_error": 6,
        "transient_error": 7,
        "backend_unavailable": 8,
    }
    failures: list[tuple[int, str, str, str, int | None, str]] = []
    for label, error in errors.items():
        stop_kind = normalize_stop_kind(getattr(error, "stop_kind", None))
        if stop_kind is None and getattr(error, "login_required", False):
            stop_kind = "permanent_error"
        stop_kind = stop_kind or "backend_unavailable"
        failures.append((
            stop_priority[stop_kind],
            label,
            stop_kind,
            str(error),
            None,
            f"{label} review provider raised {type(error).__name__}: {error}",
        ))
    for label, result in results.items():
        fatal = str(getattr(result, "fatal_error", "") or "").strip()
        exit_code = int(getattr(result, "exit_code", 0) or 0)
        if not fatal and exit_code == 0:
            continue
        stop_kind = (
            normalize_stop_kind(getattr(result, "stop_kind", None))
            or "backend_unavailable"
        )
        failures.append((
            stop_priority[stop_kind],
            label,
            stop_kind,
            fatal,
            exit_code,
            (
                f"{label} review failed before producing an assessment"
                + (f": {fatal}" if fatal else f" (exit={exit_code})")
            ),
        ))
    if failures:
        _, _, stop_kind, fatal, exit_code, reason = min(failures)
        return ReviewDecision(
            status="blocked",
            reason=reason,
            next_action="Resolve the failed review provider call before retrying.",
            backend_unavailable=True,
            backend_fatal_error=fatal,
            backend_exit_code=exit_code,
            backend_stop_kind=stop_kind,
            premium_requests=premium_requests,
            **usage,
        )
    findings = {
        label: "\n".join(results[label].agent_messages or []).strip()
        for label in prompts
    }
    empty = next((label for label, text in findings.items() if not text), "")
    if empty:
        return ReviewDecision(
            status="blocked",
            reason=f"{empty} review returned no assessment",
            next_action="Retry the missing read-only review pass.",
            backend_unavailable=True,
            backend_stop_kind="backend_unavailable",
            premium_requests=premium_requests,
            **usage,
        )
    return ReviewDecision(
        status="continue",
        reason="\n\n".join(f"{label}: {findings[label]}" for label in prompts),
        next_action=(
            "Apply the scientific, visual, and reader-facing findings to the paper, "
            "recompile it, then request the integrated final review."
        ),
        premium_requests=premium_requests,
        **usage,
    )


def _persist_research_review(
    decision: ReviewDecision,
    config: "ReviewerConfig",
) -> None:
    """Overwrite the sole research review artifact at the Review stage."""
    workdir = Path(config.working_dir or ".").expanduser().resolve()
    artifact_root = Path(
        config.artifact_root or workdir
    ).expanduser().resolve()
    state_root = Path(config.vertical_state_root or workdir).expanduser().resolve()
    vertical = str(config.active_vertical or "").strip().lower()
    if not vertical:
        from ..skills.vertical_select import resolve_vertical

        vertical = resolve_vertical(state_root)
    if vertical != "research":
        return
    from ..skills.stage_machine import current_stage

    if current_stage(state_root) != "review":
        return
    report = decision.planner_report if isinstance(decision.planner_report, dict) else {}
    accept_case = str(
        report.get("accept_case")
        or report.get("strongest_accept_case")
        or decision.reason
        or ""
    ).strip()
    challenge = str(
        report.get("challenge")
        or report.get("plan_challenge")
        or ("" if decision.status == "done" else decision.reason)
        or ""
    ).strip()
    text = (
        "# Authoritative review\n\n"
        f"**Verdict:** {decision.status}\n\n"
        "## Scientific, visual, and language assessment\n"
        f"{decision.reason or 'Not assessed.'}\n\n"
        "## Strongest accept case\n"
        f"{accept_case or 'No accept case was established.'}\n\n"
        "## Reject-level issues\n"
        f"{challenge or 'None.'}\n\n"
        "## Next action\n"
        f"{decision.next_action or 'None.'}\n"
    )
    path = artifact_root / "paper" / "REVIEW.md"
    from ..manager.source_writeback import atomic_write

    atomic_write(path, text)

    from ..core.pipeline_state import read_pipeline_state, write_pipeline_state

    payload = read_pipeline_state(state_root)
    payload["current_verdict"] = str(decision.status)
    payload["next_action"] = str(decision.next_action or "none")
    write_pipeline_state(state_root, payload)


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
    artifact_root: str | None = None
    vertical_state_root: str | None = None
    narrative_snapshot_root: str | None = None


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
        operation: str = "evaluate",
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
        reviewed_manuscript_snapshot = None
        try:
            from ..core.manuscript_snapshot import manuscript_snapshot

            candidate_snapshot = manuscript_snapshot(
                config.artifact_root or config.working_dir
            )
            if candidate_snapshot["sha256"]:
                reviewed_manuscript_snapshot = candidate_snapshot
        except Exception:  # noqa: BLE001 - non-paper reviews have no manuscript
            pass
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
        static, delta_base = self._render(
            operation=operation,
            resumed=False,
            **common,
        )
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
                status="blocked",
                reason=(
                    "Reviewer backend returned empty output; this says nothing "
                    "about the Engineer's work."
                ),
                next_action="Retry Reviewer; do not manufacture an Engineer gap.",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
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
                status="blocked",
                reason=(
                    describe_unparsed_verdict(decision_messages)
                    + " This is a Reviewer/backend failure, not evidence that "
                    "implementation is incomplete."
                ),
                next_action="Retry Reviewer; do not manufacture an Engineer gap.",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
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
        parsed.manuscript_snapshot = reviewed_manuscript_snapshot
        # The L2 reviewer's verdict is authoritative — the harness must not
        # second-guess its scientific judgment from structured result labels or
        # keyword heuristics on the engineer's summary.
        # If a generic role-acknowledgment turn slips through, that is a
        # reviewer-prompt concern (the reviewer is told to demand concrete
        # evidence and verify when it is missing/contradictory), not a harness
        # post-filter.
        _persist_research_review(parsed, config)
        return parsed

    def _render(
        self,
        *,
        operation: str = "evaluate",
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
            operation=operation,
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
