"""Reviewer sub-agent: graded "done / continue / blocked" verdict.

Provenance: vendored from ``ArgusBot/agent_cli/reviewer.py``. The
substantive change is decoupling: the original took a ``AgentCliRunner``
directly; this version takes any ``RunnerBackend`` (see
``argus_skill.core.ports``) so it works with codex, claude-code, or the
in-memory test stub equally well.

Public surface kept identical: ``Reviewer.evaluate(...) -> ReviewDecision``,
``parse_decision_text(text) -> ReviewDecision | None``.
"""
from __future__ import annotations

import hashlib
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.models import ReviewDecision, RunnerOptions
from ..core.ports import RunnerBackend
from ..core.run_gateway import run_exec as gateway_run_exec
from ..core.stop_kinds import normalize_stop_kind
from ..core.task_contract import EFFECTIVE_TASK_CONTRACT
from ..skills.role_context import format_role_context, load_builtin_skill_text
from ._parsing import _find_decision_in_messages

log = logging.getLogger(__name__)

# F7 anti-rubber-stamp guard. Prepended to the per-round DELTA only when the
# reviewer is RESUMING its own thread (the full static rubric is already in the
# thread). Resuming saves tokens; it must NEVER become deference to the prior
# verdict. The role/rubric/decision-rules from earlier in the thread still bind,
# but THIS round's artifacts (below) are the only evidence — re-verify against
# them. This preserves reviewer independence under HARD CONSTRAINT 3.
_REEVALUATE_HEADER = (
    "## NEW ROUND — RE-EVALUATE INDEPENDENTLY (resumed reviewer)\n"
    "You are resuming your OWN thread ONLY to avoid re-sending the static rubric "
    "— NOT to defer to your previous verdict. The role, rubric, and decision "
    "rules from earlier in this thread still bind, but THIS round's artifacts "
    "below are the ONLY evidence: re-verify against them from scratch. Your prior "
    "verdict is not a prior and must never be rubber-stamped; judge this round on "
    "its own artifacts, summary, and log audit.\n\n"
)


@dataclass
class ReviewerConfig:
    model: str | None = None
    reasoning_effort: str | None = None
    extra_args: list[str] = field(default_factory=list)
    skip_git_repo_check: bool = False
    full_auto: bool = False
    dangerous_yolo: bool = False
    working_dir: str | None = None


SCHEMA_PATH = str(Path(__file__).with_name("reviewer_schema.json"))
RESEARCH_SCHEMA_PATH = str(Path(__file__).with_name("reviewer_research_schema.json"))
def _load_reviewer_engineer_handoff_skill() -> str:
    return load_builtin_skill_text("reviewer-engineer-handoff.md")


def _load_academic_paper_review_skill() -> str:
    return load_builtin_skill_text("academic-paper-peer-review-benchmark.md")


def _load_wiki_curator_skill_if_present(
    working_dir: str | Path | None = None,
) -> str | None:
    """Return wiki-curator skill text when the current project has a wiki.

    The adaptive reviewer matcher has empirically missed this skill for
    diagnostic/debugging objectives, so wiki-curator is fixed context whenever
    `.autors/*/wiki/` exists in the current project.
    """
    project_root = Path(working_dir).expanduser() if working_dir else Path.cwd()
    autors = project_root / ".autors"
    if not autors.exists():
        return None
    from ..wiki.bootstrap import is_initialized_wiki
    if not any(
        is_initialized_wiki(p / "wiki") for p in autors.iterdir() if p.is_dir()
    ):
        return None
    return load_builtin_skill_text("wiki-curator.md")


def _format_academic_paper_review_skill_block(*, include: bool) -> str:
    if not include:
        return ""
    skill = _load_academic_paper_review_skill()
    return (
        "Academic-paper peer review benchmark skill "
        "(apply only to near-complete academic paper scopes):\n"
        f"{skill}\n\n"
    )


def _verification_directive() -> str:
    """Trust-first verification stance for the reviewer prompt.

    Root-cause fix (operator directive 2026-06-26): the previous unconditional
    instruction told the reviewer to re-run the engineer's commands itself and
    use *its own* output as ground truth. On a trusted-scorer task that meant
    the reviewer re-ran the official scorer EVERY round to re-confirm a number
    the engineer had already obtained from that same frozen scorer — burning the
    whole round, adding zero value, and treating the engineer as a suspect even
    though it has no reward signal to game and does not control the scorer.

    The new stance (global, all verticals): TRUST an honest, internally
    consistent self-report; spend a verification command ONLY when the evidence
    is missing or self-contradictory (the cheap anti-fabrication floor that
    still stops a faked number). Reinvest the saved round in the two things the
    engineer cannot do for itself — judging the novelty/quality of the idea and
    giving high-altitude strategic direction.
    """
    return (
        "**Trust the engineer by default; verify only on doubt.** The engineer "
        "has no reward signal it could game, and on measured tasks its numbers "
        "come from a TRUSTED external scorer it does not control — so an honest, "
        "internally consistent self-report is the NORMAL case, not a suspect "
        "one. When the engineer's summary already SHOWS the verification output "
        "(the scorer's RESULT line, pytest output, a file listing) and it is "
        "internally consistent, TRUST IT — do NOT reflexively re-run the same "
        "commands just to re-confirm an honest result. That redundant re-run "
        "burns the entire round and is the #1 reviewer anti-pattern to avoid.\n"
        "**You have shell access** — spend a verification command ONLY when the "
        "evidence is actually MISSING from the summary, or the claimed result "
        "is self-contradictory / implausible / contradicted by an acceptance "
        "check. That cheap floor is what stops a fabricated number; it does NOT "
        "require re-running every honest one.\n"
        "**Reinvest the round in what the engineer cannot do for itself:** judge "
        "whether THIS round's idea was genuinely novel and well-motivated or a "
        "tired re-tweak of a direction that already lost, and give HIGH-ALTITUDE "
        "direction — your `next_action` should name a concrete, clearly-"
        "different next approach (a different SOTA technique, a different "
        "hardware feature, the specific profiled bottleneck to attack), not "
        "`re-run and paste the output`.\n\n"
    )






def _engineer_log_audit_block(
    engineer_log_path: str,
    *,
    engineer_call_id: str = "",
    round_index: int,
    measured: bool,  # noqa: ARG001 — round_index kept for call-site symmetry with the other audit blocks
) -> str:
    """Reviewer prompt section for auditing the engineer's EXECUTION LOG.

    The reviewer normally sees ONLY the engineer's 4000-char final summary, so it
    cannot tell HOW the result was reached. This block
    points the reviewer at the mission's execution log (the per-project
    ``<life_dir>/events.jsonl``) and gives concrete grep recipes so it can audit
    PROCESS correctness: did the engineer hardcode the expected answer, skip a
    required step, use a cheat method (``use_attach``, fabricated metrics, a
    bypassed evaluator), or run commands that contradict the method it claims in
    the checklist?

    Back-compat contract: returns ``""`` when ``engineer_log_path`` is empty
    (memory backend / tests / unresolvable life_dir) — the prompt is then
    byte-for-byte identical to before this feature existed. The section is
    SUPPLEMENTARY to result-traceability, never a replacement.

    ``measured``: in MEASURED-BENCHMARK mode the reviewer is told to TRUST the
    frozen scorer and not re-run honest results. To avoid an incentive
    contradiction we soften this to a RED-FLAG-ONLY audit there (spend a grep
    only when the pasted RESULT is missing/implausible), and keep the full
    "audit by default when the evidence can't be independently verified" stance
    for paper/research mode.
    """
    path = (engineer_log_path or "").strip()
    if not path:
        return ""
    call_id = (engineer_call_id or "").strip()
    progress_filter = '\'"type": "engineer.progress"\''
    if call_id:
        def shell_quote(value: str) -> str:
            return "'" + value.replace("'", "'\"'\"'") + "'"

        current_call_rows = (
            f"{shell_quote(sys.executable)} -I -m "
            "argus_skill.tools.event_log_query "
            f"--log {shell_quote(path)} --call-id {shell_quote(call_id)}"
        )
        audit_scope = (
            f"Current engineer call id: `{call_id}`. Scope every audit command "
            "to this id so prior rounds and this Reviewer's own prompt cannot "
            "pollute the evidence. The query parses top-level JSON fields and "
            "reads rolled logs in chronological order.\n"
        )
        progress_recipe = f"{current_call_rows} | tail -60"
        cheat_recipe = (
            f"{current_call_rows} | grep -nE 'use_attach|set_pose|teleport|hardcod|"
            "HARDCODE|TODO|FIXME|mock|monkeypatch|fake|dummy|placeholder|"
            "return 0\\.9|assert True|--skip|xfail'"
        )
        evaluator_recipe = (
            f"{current_call_rows} | grep -nE "
            "'pytest|check_success|scorer|evaluate|benchmark|metric'"
        )
        log_row_description = (
            "The call-scoped raw `agent.io.*` rows record the commands, tool "
            "results, and assistant messages produced by this invocation."
        )
    else:
        audit_scope = ""
        progress_recipe = f"grep {progress_filter} '{path}' | tail -60"
        cheat_recipe = (
            "grep -nE 'use_attach|set_pose|teleport|hardcod|HARDCODE|TODO|FIXME|"
            "mock|monkeypatch|fake|dummy|placeholder|return 0\\.9|assert True|"
            f"--skip|xfail' '{path}'"
        )
        evaluator_recipe = (
            "grep -nE 'pytest|check_success|scorer|evaluate|benchmark|metric' "
            f"'{path}'"
        )
        log_row_description = (
            "Each `engineer.progress` event's `text` field is what the engineer "
            "actually DID this round — a shell command it ran, a tool call, or a "
            "reasoning beat."
        )
    if measured:
        when_clause = (
            "MEASURED-BENCHMARK mode is active, so this is a RED-FLAG-ONLY check: "
            "you already TRUST the frozen scorer's pasted RESULT line and must NOT "
            "burn the round re-deriving an honest number. Grep the log ONLY when "
            "the engineer pasted NO RESULT line, the number is implausible / "
            "self-contradictory, or the score jumped suspiciously — then confirm "
            "the scorer was actually invoked and not bypassed/hardcoded. Otherwise "
            "skip this section.\n"
        )
    else:
        when_clause = (
            "Decide WHEN to dig: you do not need to read the log every round, but "
            "you SHOULD when the artifact is suspicious, the result is "
            "surprisingly good, a checklist item cannot be independently verified "
            "from the produced files, or the summary is thin on HOW the work was "
            "done. When the engineer's own summary already shows the verification "
            "output and it is internally consistent, a quick log skim is enough.\n"
        )
    return (
        "## Engineer execution-log audit (process correctness — SUPPLEMENTARY)\n"
        "This round's engineer EXECUTION LOG is on disk at:\n"
        f"  {path}\n"
        "It is the per-project event log (NOT in the git work-tree). "
        f"{log_row_description} You have shell access; you can grep it.\n"
        f"{audit_scope}\n"
        "Result-traceability (does the final artifact match the checklist?) tells "
        "you the OUTCOME is real. This log tells you the PROCESS was honest — the "
        "two are different, and an artifact can match the checklist while the "
        "process that produced it was faked. Use this to catch what the summary "
        "hides.\n\n"
        f"{when_clause}\n"
        "Grep recipes (substitute the path above):\n"
        "- See what the engineer ran this round (newest last):\n"
        f"    {progress_recipe}\n"
        "- Hunt for cheats / shortcuts that mask a real failure:\n"
        f"    {cheat_recipe}\n"
        "- Check the claimed evaluator/scorer was actually invoked (not bypassed "
        "or replaced by an inline constant):\n"
        f"    {evaluator_recipe}\n\n"
        "Red flags → even if the artifact traces to the checklist, return "
        "`continue` (or `blocked` if it needs the operator) and NAME the process "
        "defect in `reason` / `next_action`:\n"
        "- (a) HARDCODED the expected value/answer instead of computing it (e.g. "
        "writing the gold number straight into the output, an `assert True`, a "
        "constant return where a measurement belongs).\n"
        "- (b) SKIPPED a required step and wrote the result directly (the "
        "checklist says 'run X then measure', but no X command appears in the "
        "log).\n"
        "- (c) Used a WRONG or CHEATING method — a physics/sim override "
        "(`use_attach`, forced pose), a fabricated metric, or a bypassed/replaced "
        "real evaluator — to make a failing task look passed.\n"
        "- (d) Ran commands that CONTRADICT the method the checklist/summary "
        "claims (the prose says one approach; the log shows another).\n\n"
        "If the log is clean and the process matches the claim, say so briefly and "
        "judge on the result as usual — do NOT manufacture a process objection "
        "where there is none. This audit SUPPLEMENTS result-traceability; it does "
        "not replace it, and it never changes the frozen outcome/metric/verifier.\n\n"
    )


class Reviewer:
    """One reviewer call per round. Stateless across rounds."""

    def __init__(self, runner: RunnerBackend, *, skill_store: Any | None = None) -> None:
        self.runner = runner
        self.schema_path = SCHEMA_PATH
        # Optional: when wired, the reviewer runs the same role-mission skill
        # matcher every other role uses, surfacing adaptive reviewer skills
        # (e.g. stage-specific review playbooks) plus cross-role engineer
        # references on top of the fixed role/handoff context. ``None`` keeps
        # the legacy fixed-context-only behaviour.
        self.skill_store = skill_store
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
        planner_review_instruction: str = "",
        active_skill_id: str | None = None,
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        background_context: str = "",
        escalate_hint: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        resume_thread_id: str | None = None,
        prior_static_fingerprint: str = "",
    ) -> ReviewDecision:
        schema_path = self.schema_path
        research_target_level = None
        structured_result_required = False
        try:
            from ..core.research_contract import resolve_research_target_level
            from ..skills.harness_overlay import resolve_project_root
            from ..skills.vertical_select import resolve_vertical

            root = resolve_project_root(config.working_dir)
            research_target_level = resolve_research_target_level(root)
            structured_result_required = (
                research_target_level is not None or resolve_vertical(root) == "math"
            )
            if structured_result_required and schema_path == SCHEMA_PATH:
                schema_path = RESEARCH_SCHEMA_PATH
        except Exception:  # noqa: BLE001 — default schema remains safe
            pass
        # Defense-in-depth (root-cause guard for the 2026-06-25 incident): if the
        # reviewer output-schema file is unavailable, codex aborts with exit 1
        # ("Failed to read output schema file ...") and the round renders NO
        # verdict. Detect it up front and fail loud as a backend-unavailable
        # block, instead of building a prompt and handing codex a path it cannot
        # read. This catches a moved schema / a stale import-time path held by a
        # long-lived daemon whose on-disk tree moved underneath it.
        schema_contract = b""
        try:
            if schema_path:
                schema_contract = Path(schema_path).read_bytes()
        except OSError as exc:
            reason = (
                "Reviewer output-schema file is unavailable at "
                f"{schema_path} ({type(exc).__name__}: {exc}); the reviewer backend "
                "cannot start. This is "
                "an environment/packaging fault (e.g. the schema was moved or a "
                "running process holds a stale import-time path), not a verdict."
            )
            return ReviewDecision(
                status="blocked",
                reason=reason,
                next_action=(
                    "Restore the reviewer schema at that path, or restart the "
                    "daemon on code whose schema path matches disk; do not treat "
                    "this as evidence about the engineer's work."
                ),
                round_summary_markdown=f"# Review Summary\n\n- {reason}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
                backend_unavailable=True,
                backend_stop_kind="backend_unavailable",
            )
        # F7: split the prompt into a byte-stable STATIC preamble (the ~50KB
        # role/rubric/decision-rules + mission anchors) and a per-round DELTA
        # (this round's summary/log-audit/altitude). When the reviewer can
        # resume its OWN codex thread from last round AND neither the static
        # preamble nor output schema has changed, send ONLY the delta — both
        # contracts are already in the thread. Any static/schema drift flips the
        # fingerprint and forces a full re-send (anti-staleness guard).
        common = dict(
            objective=objective,
            original_objective=original_objective or objective,
            operator_messages=operator_messages or [],
            planner_review_instruction=planner_review_instruction,
            round_index=round_index,
            session_id=session_id,
            main_summary=main_summary,
            main_error=main_error,
            active_skill_id=active_skill_id,
            prev_review_summary=prev_review_summary,
            raw_evidence=raw_evidence,
            scope=scope,
            prior_checkpoint=prior_checkpoint,
            background_context=background_context,
            escalate_hint=escalate_hint,
            engineer_log_path=engineer_log_path,
            engineer_call_id=engineer_call_id,
            working_dir=config.working_dir,
        )
        static, delta_base = self._render(resumed=False, **common)
        fingerprint_input = bytearray(static.encode("utf-8"))
        if schema_path:
            fingerprint_input.extend(b"\0output-schema\0")
            fingerprint_input.extend(schema_contract)
        new_fp = hashlib.sha256(fingerprint_input).hexdigest()
        resume = (
            resume_thread_id
            if (resume_thread_id and new_fp == prior_static_fingerprint)
            else None
        )
        # ``delta_base`` was rendered resumed=False (no RE-EVALUATE header); when
        # we ARE resuming, prepend the anti-rubber-stamp header — identical to
        # ``_render(resumed=True)[1]`` but without a second (matcher-firing) render.
        delta = (_REEVALUATE_HEADER + delta_base) if resume else delta_base
        prompt = delta if resume else static + delta
        try:
            result = gateway_run_exec(
                self.runner,
                prompt=prompt,
                resume_thread_id=resume,
                options=RunnerOptions(
                    model=config.model,
                    reasoning_effort=config.reasoning_effort,
                    dangerous_yolo=config.dangerous_yolo,
                    full_auto=config.full_auto,
                    skip_git_repo_check=config.skip_git_repo_check,
                    extra_args=list(config.extra_args) if config.extra_args else None,
                    output_schema_path=schema_path,
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
                round_summary_markdown=f"# Review Summary\n\n- {msg}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
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
        # F7: the thread this turn ran on + the static fingerprint we just sent.
        # Set on EVERY result-bearing return path so the supervised loop can
        # resume this thread next round (and detect static drift). The earlier
        # schema-missing / runner-raised returns have no ``result`` → thread_id
        # stays None (the loop must start a fresh reviewer session there).
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
                round_summary_markdown=f"# Review Summary\n\n- {reason}\n",
                completion_summary_markdown="",
                failure_cause="environmental",
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
        if not result.agent_messages:
            return ReviewDecision(
                status="continue",
                reason=f"Reviewer returned empty output. exit={result.exit_code}",
                next_action="Continue implementation and provide concrete completed work.",
                round_summary_markdown="# Review Summary\n\n- Reviewer returned empty output.\n",
                input_tokens=rev_in,
                cached_input_tokens=rev_cached,
                output_tokens=rev_out,
                reasoning_output_tokens=rev_reasoning_output_tokens,
                premium_requests=rev_premium,
                thread_id=rev_tid,
                static_fingerprint=new_fp,
            )
        parsed = _find_decision_in_messages(
            result.agent_messages,
            allow_research_pause=structured_result_required,
        )
        if parsed is None:
            return ReviewDecision(
                status="continue",
                reason="Reviewer output was not valid JSON.",
                next_action="Continue implementation and include clear completion evidence.",
                round_summary_markdown="# Review Summary\n\n- Reviewer output was not valid JSON.\n",
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
        # F7: carry the thread + static fingerprint so the loop resumes this
        # reviewer thread next round and detects mid-mission static drift.
        parsed.thread_id = rev_tid
        parsed.static_fingerprint = new_fp
        if research_target_level is not None and parsed.status == "done":
            from ..core.research_contract import (
                research_completion_issue,
                research_pause_status,
            )

            issue = research_completion_issue(
                parsed.research_result,
                research_target_level=research_target_level,
                scope=scope,
            )
            if issue:
                parsed.status = research_pause_status(parsed.research_result)
                parsed.achievement = None
                parsed.reason = (
                    "Research completion gate held for "
                    f"{research_target_level} target: {issue}. {parsed.reason}"
                )[:5000]
                parsed.next_action = (
                    "Preserve this cycle's evidence and resume or re-plan toward "
                    "a structured result whose correctness, novelty, and significance "
                    "satisfy the persisted research target "
                    f"({research_target_level}; {issue})."
                )[:1500]
        # The L2 reviewer's verdict is authoritative — the harness must not
        # second-guess it with keyword heuristics on the engineer's summary.
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
        active_skill_id: str | None = None,
        prev_review_summary: str = "",
        raw_evidence: str = "",
        scope: str = "",
        prior_checkpoint: dict[str, Any] | None = None,
        background_context: str = "",
        escalate_hint: str = "",
        engineer_log_path: str = "",
        engineer_call_id: str = "",
        working_dir: str | Path | None = None,
    ) -> tuple[str, str]:
        """F7: render the reviewer prompt as ``(static_preamble, round_delta)``.

        ``static_preamble`` is a byte-stable prefix (role/rubric/decision-rules +
        immutable mission anchors) suitable for prefix-cache reuse AND for codex
        thread resume — it is identical across rounds of a mission unless the
        stage/objective/vertical drift. ``round_delta`` is this round's evidence
        (altitude, checkpoint, escalation, log-audit, summary). When
        ``resumed`` the delta is prefixed with the anti-rubber-stamp RE-EVALUATE
        header. Callers concatenate ``static + delta`` for a full send, or send
        ``delta`` alone when resuming a thread that already holds the static.
        """
        error_text = main_error or "none"
        reviewer_role_context = format_role_context(
            "Argus reviewer role skill",
            "argus-reviewer-role.md",
        )
        handoff_skill = _load_reviewer_engineer_handoff_skill()
        # Role-mission matcher (same primitive engineer/planner use). It
        # surfaces ADAPTIVE reviewer skills (stage-specific review playbooks)
        # plus cross-role engineer references on top of the fixed
        # role/handoff/academic blocks above. The three fixed reviewer skills
        # are excluded by ReviewerMission so the matcher never re-injects what
        # is already hard-wired into this prompt.
        from ..skills.harness_overlay import resolve_project_root
        from ..skills.vertical_select import resolve_vertical
        from ..verticals._base import (
            load_vertical,
            vertical_completion_gate,
            vertical_role_banner,
            vertical_search_altitude,
            vertical_workflow_mode,
        )

        _proot = resolve_project_root(working_dir)
        _active_vertical = resolve_vertical(_proot)
        _vmod = load_vertical(_active_vertical, project_root=_proot)
        _direct_workflow = vertical_workflow_mode(_vmod) == "direct"
        matched_review_skill_block = ""
        if self.skill_store is not None and not _direct_workflow:
            from ..skills.venue_profiles import venue_excluded_skill_files

            review_match = self.mission.match(
                objective,
                extra_exclude=venue_excluded_skill_files(_proot),
            )
            if review_match.block:
                matched_review_skill_block = (
                    "Matched reviewer skill(s) for this objective "
                    "(read first; apply the relevant one(s)):\n"
                    f"{review_match.block}\n\n"
                )
        from ..skills.stage_checklists import (
            CANONICAL_STAGE_ORDER,
            current_stage,
            format_full_pipeline_checklist,
            format_stage_checklist,
        )
        stage = current_stage(_proot)
        import os as _os
        _measured = _os.environ.get("ARGUS_SKILL_MEASURED_MODE", "").strip().lower() in ("1", "true", "yes", "on")
        # Vertical-native prompt framing: resolve the active vertical and let it
        # supply the top-of-prompt role banner. The rollback / final-submission
        # framing below applies ONLY to a paper vertical (completion_gate ==
        # "full_paper"); for any other vertical (e.g. speedrun) those blocks are
        # suppressed and the vertical's banner is prepended so the reviewer judges
        # only that vertical's metric instead of paper-pipeline artifacts.
        _full_paper = vertical_completion_gate(_vmod) == "full_paper"
        optimize_banner = vertical_role_banner(_vmod, "reviewer")
        research_result_instruction = ""
        from ..core.research_contract import resolve_research_target_level

        _research_target_level = resolve_research_target_level(_proot)
        if _research_target_level is not None:
            _bounded_research_contract = (
                "This is a structured `bounded` backlog item. `done` certifies "
                "only this item's explicit objective and acceptance criteria; it "
                "does NOT certify the persisted project-level research target. "
                "Still emit an honest `research_result`, but do not require "
                "verified novelty, publishable significance, or an original "
                "terminal theorem unless this bounded objective explicitly asks "
                "for them. A verification probe may therefore finish with a "
                "correctly classified novelty-unverified result.\n"
                if (scope or "").strip().lower().replace("-", "_") == "bounded"
                else ""
            )
            research_result_instruction = (
                "For this targeted research mission, `research_result` is REQUIRED "
                "on every verdict. Judge result_class, correctness_status, "
                "novelty_status, significance_status, and any domain-specific "
                "fidelity field independently; use concrete evidence and limitations.\n"
                f"The Manager-persisted `research_target_level` is "
                f"`{_research_target_level}`. {_bounded_research_contract}"
                "For non-bounded completion, use exactly this success bar; do not "
                "downgrade it because a report is polished or a bounded cycle ended. "
                "For `publishable` or `doctoral`, `done` requires "
                "correctness_status=verified, novelty_status=verified_new, "
                "significance_status publishable or "
                "doctoral, and an original terminal result (complete solution, "
                "verified new result/theorem, improved bound, new infinite family, "
                "new reduction, or exact counterexample). Literature review, known "
                "results, finite verification, local Lean verification, "
                "novelty-unverified work, and honest/structured failure reports are "
                "artifacts, not mission success. When the current cycle should end "
                "without that result, use `research_incomplete`, "
                "`paused_no_breakthrough`, or `exhausted_current_methods`; these "
                "preserve evidence and permit a future resume. For `exploratory`, "
                "an independently verified honest failure report may be `done`.\n\n"
            )
        # Live search-altitude facts (NO verdict) so the reviewer can SEE the
        # floor history when judging forward_progress — i.e. distinguish "this
        # round advanced a declared structural line" from "Nth single-knob
        # nibble at a floor that has not moved in N attempts". Empty for
        # verticals that do not surface it.
        search_altitude_block = vertical_search_altitude(_vmod, _proot)
        # Structured scope only. The planner threads scope=final_submission as
        # a backlog tag all the way here; we no longer sniff the objective
        # prose for "scope: final_submission" markers. Normalize the same way
        # the planner does (lower + hyphen→underscore) so callers that pass
        # "final-submission" still match.
        scope_normalized = (scope or "").strip().lower().replace("-", "_")
        is_final_submission = scope_normalized == "final_submission"
        if _measured:
            stage_checklist = (
                "## MEASURED-BENCHMARK MODE — TRUST the scorer, judge the IDEA\n"
                "Trusted, FROZEN scorer; the engineer has NO reward signal and does "
                "not control it, so its pasted RESULT (correct + cand_ms/score) is "
                "the honest norm. Your verdict turns on ONE thing: did this round's "
                "MEASURED score beat the engineer's previous best?\n"
                "Do NOT re-run the scorer yourself to re-confirm an honest, "
                "self-consistent number — the engineer self-supervises correctness "
                "by running it every round, so re-measuring burns the round for zero "
                "value. Spend a check ONLY if NO RESULT was pasted or it is "
                "self-contradictory. Otherwise: JUDGMENT + DIRECTION, not "
                "re-measurement.\n"
                "- `continue` if the score improved (lock it in, explore the NEXT "
                "mechanism) OR a clearly-different mechanism is still untried. First "
                "judge: was this mechanism genuinely novel or a re-tweak of a "
                "direction that already lost? `next_action` MUST name a CONCRETE new "
                "direction (a different SOTA/library approach, a hardware technique, "
                "the profiled bottleneck) — push mechanism diversity; never ask to "
                "re-tweak a losing direction or re-paste a shown result.\n"
                "- `blocked` ONLY on a real plateau (several rounds, no improvement, "
                "distinct mechanisms exhausted) or an operator-only blocker. When "
                "only the OPERATOR can unblock (route, budget, which task, GPU, a "
                "yes/no), ALSO set `operator_question`: ONE plain-language question "
                "in the operator's language (Chinese here), answerable in a sentence "
                "— no jargon/JSON/template names.\n"
                "- `done` is rare here — only at/above the known ceiling.\n"
                "Ignore GROUND_TRUTH/gate/marker/status/provenance files (the harness "
                "ignores them) and artifact hygiene — the scorer's number is the only "
                "evidence. A round that MEASURED a real number, even a worse one, made "
                "progress by ruling out a mechanism. This OVERRIDES the generic "
                "demand-evidence / re-run rules below."
            )
        elif is_final_submission or stage == "submission":
            stage_checklist = format_full_pipeline_checklist(role="reviewer", project_root=_proot)
        else:
            stage_checklist = format_stage_checklist(stage, role="reviewer", project_root=_proot)

        # Academic peer-review benchmark skill: advisory rubric for reviewing
        # a near-complete manuscript. Gate it on the structured stage/scope
        # signal — final_submission, or the paper-writing stages (review /
        # submission) — instead of keyword-sniffing the objective/evidence
        # for tokens like "main.pdf". `draft` is excluded so mid-production
        # drafting isn't held to final peer-review standards prematurely.
        paper_review_skill_block = _format_academic_paper_review_skill_block(
            include=is_final_submission or stage in {"review", "submission"},
        )
        wiki_curator_text = _load_wiki_curator_skill_if_present(working_dir)
        wiki_curator_skill_block = (
            "## Wiki curator (fixed when a wiki exists -- run as part of this verdict)\n\n"
            f"{wiki_curator_text}\n\n"
            if wiki_curator_text
            else ""
        )

        # Always-on project-venv reminder for the reviewer too: a round
        # summary that says "I skipped X because the package is missing"
        # is never acceptable — the engineer must `./.venv/bin/pip install`
        # and retry. Inject the canonical skill body so both roles read
        # the same source of truth.
        venv_skill_block = ""
        try:
            from ..skills.builtins import iter_builtin_skill_texts
            for fname, body in iter_builtin_skill_texts():
                if fname == "project-venv-package-management.md":
                    venv_skill_block = (
                        "## Project venv (any missing package is the engineer's job to install)\n"
                        + body
                    )
                    break
        except Exception:  # noqa: BLE001
            pass

        # Upstream-evidence defect REPORT. When the reviewer notices that an
        # upstream stage's evidence is missing or unreliable while working a
        # later stage, the correct move is to REPORT it so the Manager can roll
        # the stage back — the reviewer does NOT edit the pipeline state machine
        # itself (stage authority is the Manager's). The instruction lives here
        # (not in the individual checklist items) so it applies uniformly.
        stage_idx = (
            CANONICAL_STAGE_ORDER.index(stage)
            if stage in CANONICAL_STAGE_ORDER
            else 0
        )
        earlier_stages = ", ".join(CANONICAL_STAGE_ORDER[:stage_idx]) or "(none)"
        rollback_block = (
            "## Upstream-evidence defects (REPORT — the Manager owns rollback)\n"
            f"Current stage: `{stage}`. Earlier stages: {earlier_stages}.\n"
            "If an EARLIER stage's evidence is missing/stale/unreliable (e.g. in "
            "`run` the `benchmark` evaluator is a stub; in `draft` "
            "`research/INFRA_CHOICE.md` was never locked; in `analysis` the "
            "`run.score_variance` rows are all identical), do NOT patch it from the "
            "current stage and do NOT edit the pipeline state machine — stage "
            "transitions are the Manager's authority. Reply `continue` and name the "
            "earliest broken stage + why in `reason` AND `planner_report.blocker` "
            "(e.g. \"`benchmark` returns a constant — recommend rollback to "
            "`benchmark`\"). Never call `rollback_stage` or write "
            "`research/PIPELINE_STATE.json`."
        )
        # Checklist-feedback channel. The PLANNER owns the per-stage checklist
        # (it authors/edits it via checklist_ops). The reviewer is FEEDBACK-ONLY:
        # if the checklist ITSELF is wrong for this task, it reports rather than
        # working around or silently honoring a broken item.
        checklist_feedback_block = (
            "## Checklist is Planner-owned — give FEEDBACK, do NOT edit it\n"
            "The per-stage checklist above is the Planner's. If it is ITSELF wrong "
            "(an item irrelevant/over-strict/under-specified/mis-stated, or an "
            "obvious missing gate), do NOT ignore, work around, or edit it (never "
            "write `research/CHECKLISTS.json`). Emit the optional "
            "`checklist_feedback` object (`stage`, one-line `summary`, per-item "
            "`{id, problem, suggested_fix}`) — the Planner applies it next cycle. "
            "Still judge THIS round's artifacts against the checklist as written; "
            "feedback fixes FUTURE rounds, it is not a reason to pass/fail now."
        )
        operator_text = (
            "\n".join(f"- {line}" for line in operator_messages)
            if operator_messages
            else "- none"
        )
        shared_context_block = _format_engineer_shared_context(
            skill_used=active_skill_id,
            prev_review_summary=prev_review_summary,
        )
        # v12 phase-4: when callers (e.g. harbor_adapter) collect richer
        # post-round evidence (engineer self-report verbatim, runtime probe,
        # official verifier output with "ground truth, trust this" framing),
        # they pass it as ``raw_evidence`` so the reviewer has the strongest
        # signal grounded in actual container state, not just the engineer's
        # prose. Empty string → legacy v3 behaviour.
        evidence_block = (
            f"\nRaw verification evidence:\n{raw_evidence.rstrip()}\n"
            if raw_evidence.strip()
            else ""
        )
        # Background-subagent context (rendered by the engineer/runner from the
        # live ``.argus_subagents`` registry). Present only when this mission has
        # in-flight subagents. A SUPERVISED subagent advancing on its own is NOT
        # by itself the engineer's forward progress, so we steer next_action away
        # from "poll again" toward independent work (or an explicit cadence
        # yield) without forcing a forward_progress value.
        background_block = ""
        if background_context.strip():
            background_block = (
                f"\n{background_context.strip()}\n\n"
                "Reviewer note on the above: these are SUPERVISED subagents with "
                "their own independent supervisor, so their autonomous progress is "
                "NOT by itself the engineer's forward progress. If the engineer only "
                "re-polled a healthy self-watched subagent this round, steer "
                "`next_action` to advance independent work that does not depend on "
                "it — or, if nothing else can proceed, to yield with "
                "`WAIT_FOR_SUBAGENT: <task_id>` — rather than prescribing another "
                "poll.\n"
            )
        # Curated working-memory: show the reviewer the checkpoint it authored
        # last round so it can do deliberate CRUD (add/update/delete) rather
        # than re-deriving memory from scratch. The engineer's HANDOFF proposal
        # arrives inside ``main_summary``; the reviewer validates it against
        # checks/artifacts and emits the next canonical checkpoint.
        from ..engineer.checkpoint import CheckpointState as _CheckpointState
        prior_cp = _CheckpointState.from_dict(prior_checkpoint or {})
        if prior_cp.is_empty():
            prior_checkpoint_block = (
                "## Curated working memory (checkpoint)\n"
                "No prior checkpoint yet — author the first one from the "
                "engineer's HANDOFF and the verified facts of this round.\n\n"
            )
        else:
            _cp_done = "\n".join(f"  - {d}" for d in prior_cp.done) or "  - (none)"
            _cp_fail = (
                "\n".join(f"  - {d}" for d in prior_cp.tried_and_failed)
                or "  - (none)"
            )
            _cp_maturing = (
                "\n".join(f"  - {d}" for d in prior_cp.maturing)
                or "  - (none)"
            )
            _cp_facts = (
                "\n".join(f"  - {d}" for d in prior_cp.env_facts)
                or "  - (none)"
            )
            _al = prior_cp.active_line if isinstance(prior_cp.active_line, dict) else {}
            _cp_active = (
                f"{_al.get('desc', '')} | code saved at "
                f"{_al.get('branch_or_path', '?')} | developed "
                f"{_al.get('rounds_active', 0)} round(s) | next: {_al.get('note', '')}"
                if _al else "(none yet)"
            )
            prior_checkpoint_block = (
                "## Curated working memory (checkpoint) — PRIOR\n"
                f"goal: {prior_cp.goal or '(unset)'}\n"
                f"done:\n{_cp_done}\n"
                f"tried_and_failed:\n{_cp_fail}\n"
                f"maturing (tried, not yet succeeding — NOT dead ends):\n{_cp_maturing}\n"
                "active_line (a bold/structural line being matured ABOVE the floor "
                "— BUILD ON IT; do NOT restore the floor while it is alive): "
                f"{_cp_active}\n"
                f"open_blocker: {prior_cp.open_blocker or '(none)'}\n"
                f"next_step: {prior_cp.next_step or '(none)'}\n"
                f"env_facts:\n{_cp_facts}\n\n"
            )
        # Anti-livelock escalation directive (supplied by the round loop once a
        # mission passes the soft round limit): tell the reviewer to escalate an
        # unresolvable EXTERNAL blocker to `blocked` instead of looping `continue`.
        escalate_block = ""
        if escalate_hint:
            escalate_block = (
                "## Escalation directive (operator harness — IMPORTANT)\n"
                f"{escalate_hint}\n\n"
            )
        # Engineer execution-log audit (process correctness). The reviewer runs
        # in the project work-tree and only receives the engineer's final
        # summary, so it cannot otherwise SEE how a result was produced. When the
        # supervisor threads the absolute path to this mission's execution log
        # (``<life_dir>/events.jsonl``), give the reviewer grep recipes to audit
        # PROCESS correctness — not just whether the artifact matches the
        # checklist, but whether the engineer reached it honestly. Empty path
        # (memory backend / tests / unresolvable life_dir) → block omitted, prompt
        # byte-for-byte unchanged (back-compat).
        engineer_log_audit_block = _engineer_log_audit_block(
            engineer_log_path,
            engineer_call_id=engineer_call_id,
            round_index=round_index,
            measured=_measured,
        )
        # Final-submission completion contract. This block replaces the
        # retired hardcoded EMNLP validators: instead of the supervisor
        # running ``validate_full_paper_readiness`` and friends, the reviewer
        # is the single source of truth for whether the *whole project* is
        # ready to submit. It only fires for final_submission missions.
        final_submission_block = ""
        if is_final_submission:
            final_submission_block = (
                "## FINAL SUBMISSION CONTRACT (scope = final_submission)\n"
                "You are certifying the ENTIRE pipeline is complete and ready to "
                "submit — the single source of truth, no separate validator. You "
                "MUST: (1) set `scope` = `final_submission`; (2) populate `checklist` "
                "with ONE entry per full-pipeline item, each `{item, satisfied "
                "(true/false), evidence}` where evidence is concrete proof YOU "
                "verified (command output, file contents, query rows) and is "
                "non-empty for every `satisfied: true`; (3) choose `status: done` "
                "ONLY when EVERY item is `satisfied: true` with evidence, else "
                "`continue` and list every unmet item + exact repair steps in "
                "`next_action`; (4) run the Academic-Paper Peer Review Benchmark and "
                "put its `### Simulated peer-review benchmark` block (Decision, "
                "Overall /10, the eight dimension scores) in `round_summary_markdown` "
                "— `done` is allowed ONLY on `Decision: Accept` with `Overall >= 6`. "
                "A `Reject` (Overall <= 5), including a borderline one and REGARDLESS "
                "of any 'diagnostic'/'null-result'/'bounded-scope' self-label, is NOT "
                "done → `continue` with the dimension scores + strongest reject "
                "reason + concrete repairs in `next_action`; when unsure, reject. "
                "Do not certify on the engineer's word — re-run the verification "
                "commands yourself and cite your own output.\n\n"
            )
        if not _full_paper:
            # non-paper vertical: no paper stages to roll back to, and no
            # final-submission certification — judge only the vertical's metric.
            rollback_block = ""
            final_submission_block = ""
        from ..skills.ground_truth import ground_truth_mandate

        # F7: STATIC preamble — byte-stable across rounds (prefix-cache + thread
        # resume). ``search_altitude_block`` and the per-round checkpoint/
        # escalation/log-audit blocks were REORDERED out of here into the delta.
        static = (
            ground_truth_mandate(
                "reviewer",
                workflow_mode=vertical_workflow_mode(_vmod),
            )
            + optimize_banner
            + research_result_instruction
            + EFFECTIVE_TASK_CONTRACT
            + "\n\n"
            + "You are the reviewer sub-agent for an argus-skill autoloop run.\n"
            "Decide whether the objective is fully complete.\n\n"
            + _verification_directive()
            + "**Never mark `done` on a generic role acknowledgment** (the engineer\n"
            "merely says it will take ownership) without concrete execution\n"
            "evidence — command output, file diffs, or query results — you verified.\n\n"
            "## How to respond — talk normally, structure ONLY the final handoff\n"
            "Work like a senior reviewer thinking out loud: verify and reason in\n"
            "NATURAL LANGUAGE — talk normally, run whatever checks you need. Do NOT\n"
            "format your intermediate messages as JSON. ONLY your FINAL message is "
            "the structured handoff: a SINGLE JSON object matching the schema below,\n"
            "emitted last with nothing after it and no markdown fences — the harness\n"
            "reads only that final JSON.\n\n"
            f"{reviewer_role_context}"
            "Reviewer-to-engineer handoff skill:\n"
            f"{handoff_skill}\n\n"
            f"{paper_review_skill_block}"
            f"{wiki_curator_skill_block}"
            f"{matched_review_skill_block}"
            f"{stage_checklist}\n\n"
            f"{final_submission_block}"
            f"{rollback_block}\n\n"
            f"{checklist_feedback_block}\n\n"
            f"{venv_skill_block}\n\n"
            "**Length constraints:** be thorough in `round_summary_markdown` (brief\n"
            "bullets, not essays); `next_action` must carry ALL specific issues,\n"
            "failure details, and repair steps — do NOT summarize away critical\n"
            "information.\n\n"
            "Required keys of the FINAL handoff JSON object (the strict schema\n"
            "enforces presence — your job is to fill each one WELL):\n"
            "- status, reason, next_action, round_summary_markdown,\n"
            "  completion_summary_markdown\n"
            "- achievement{title, goal, metric_id, summary, evidence[]} OR null —\n"
            "  certify only a project-level research achievement you independently\n"
            "  verified on a `done` verdict; ordinary completion MUST use null\n"
            "- planner_report{forward_progress, headline, blocker, recommended_next,\n"
            "  evidence_files[{path, why}]} — the ONLY thing the planner reads\n"
            "- step_back{supported_by_results, surprises, new_questions[],\n"
            "  alt_directions[{direction, why, cheap_to_test}]} OR null — null ONLY\n"
            "  when the round produced no measured result (see STEP-BACK below)\n"
            "- checkpoint{goal, done[], tried_and_failed[], maturing[],\n"
            "  active_line{desc, branch_or_path, rounds_active, note}, open_blocker,\n"
            "  next_step, env_facts[]} — the engineer's ENTIRE next-round memory\n"
            "- scope (`bounded`|`final_submission`) + checklist[{item, satisfied,\n"
            "  evidence}] — REQUIRED when scope is final_submission\n"
            "- operator_question — ONLY on an operator-only `blocked` (ONE plain\n"
            "  question in the operator's language, ≤500 chars)\n"
            "- checklist_feedback{stage, summary, items[{id, problem, suggested_fix}]}\n"
            "  — ONLY when the checklist ITSELF is wrong (advisory to the Planner)\n"
            "- failure_cause — on a non-`done` verdict (see SKILL-EVOLUTION below)\n"
            "- skill_ops[{op, name, content, why}] — omit/[] when no skill change\n"
            "- wiki_ops[{op, id, card_type, title, status, body, evidence, why}] —\n"
            "  omit/[] when no wiki change (see WIKI MEMORY below)\n\n"
            "Planner report rules (the ONLY thing the planner reads — a clean\n"
            "structured briefing, not raw logs):\n"
            "- `forward_progress` (bool): TRUE only if the mission moved the project\n"
            "  closer to the operator goal. FALSE for an allowed blocked/rollback/\n"
            "  not-launched/gate escape path, or a rename/refresh while the real\n"
            "  blocker remains — even if `status` is `done`.\n"
            "  For OPTIMIZATION / SPEEDRUN / METRIC missions: TRUE on any NEW\n"
            "  verifier-measured data point that advances the active line —\n"
            "  INCLUDING a structural/optimizer/architecture/precision change that\n"
            "  REGRESSED vs best but was honestly measured and reverted (it ruled out\n"
            "  a real direction; the global-best floor is never lost, so a\n"
            "  measured-and-reverted bold experiment is GOOD process). Also TRUE for\n"
            "  a genuine MEASURED DIAGNOSIS (step profile, train-vs-val read,\n"
            "  within-attempt ablation) even with no new scored candidate.\n"
            "  FALSE for: no new measured evidence (crash/NaN/no-op/rename/refresh/\n"
            "  escape); a stalled single-knob nibble that neither beat the floor nor\n"
            "  advanced a declared structural line; or RE-SCORING an existing attempt\n"
            "  to re-confirm a KNOWN floor with no new mechanism (the best is already\n"
            "  fixed — reproducing it is wasted budget; do not reward run-to-run\n"
            "  reproducibility). When the Search-altitude facts show the floor\n"
            "  unchanged across many attempts AND deltas within noise (~0.001-0.002)\n"
            "  AND this candidate only RE-COMBINES tried levers, it is the\n"
            "  stalled-nibble case → FALSE (do not score TRUE just because it was\n"
            "  'measured').\n"
            "- `headline`: one or two plain sentences on what changed or was proven\n"
            "  (no ANSI, banners, or command dumps).\n"
            "- `blocker`: the single most important unresolved blocker + root cause\n"
            "  + owning stage if known (e.g. `plan-level method defect: all\n"
            "  conditions emit identical outputs; owning stage = plan`). Empty if\n"
            "  nothing blocks.\n"
            "- `recommended_next`: the concrete next focus (e.g. `pivot the method` /\n"
            "  `roll back to plan and redesign condition separation`), or empty if\n"
            "  done. Never recommend re-running an equivalent task that leaves the\n"
            "  blocker in place.\n"
            "- `evidence_files`: [{path, why}] (≤8) the planner must OPEN to diagnose\n"
            "  — REQUIRED for a failed/no-progress/surprising run, pointing at real\n"
            "  evidence (run dir `status.json`/`progress.jsonl`, supervisor handoff,\n"
            "  the training/eval SOURCE script, the data-provenance file, reward/\n"
            "  metric diagnostics, any `*_NO_GO.md`), not just a generated summary.\n"
            "  `why` = what the planner learns from it. Empty only when nothing on\n"
            "  disk would help.\n"
            "- RUN-HEALTH: a mechanical health-gate / `*_NO_GO.md` / `state=failed`\n"
            "  from a METRIC THRESHOLD (one tail step's `clipped_ratio`, a\n"
            "  short-window dip) is ADVISORY, not the verdict. Judge from the metric\n"
            "  TREND + supervisor handoff; do NOT set FALSE or relaunch SOLELY\n"
            "  because a mechanical gate tripped — only a real failure (crash/OOM/\n"
            "  NaN/timeout/collapsing trend) justifies that.\n"
            "- GRADUATION: a smoke/micro-run (tiny `max_steps`, `num_generations=2`,\n"
            "  a few rows) only proves WIRING, never paper evidence. Once it runs, the\n"
            "  next mission must EITHER launch the real pilot/full run OR diagnose a\n"
            "  NAMED root-cause hypothesis — another equivalent micro-smoke with only\n"
            "  a threshold/flag tweak is FALSE unless it tests a named hypothesis.\n"
            "- NO-GO ATTRIBUTION: never falsify an IDEA on a misconfigured run. When\n"
            "  an RL/post-training method underperforms a baseline, confirm the run\n"
            "  was FAIR first (read its manifest + rollout diagnostics; use\n"
            "  `rl-training-collapse-diagnosis` as the AUTHORITY on collapse\n"
            "  signatures + sane-regime thresholds, do not re-derive them). Label\n"
            "  `misconfigured_run` (truncated rollouts, zero reward variance,\n"
            "  sub-RL-scale knobs, a health-gate gamed by terse/`answer_only`\n"
            "  rollouts) → roll back and re-run the correction, idea NOT dead;\n"
            "  `method_failure` ONLY after one corrected sane-regime run still loses;\n"
            "  or `infeasible_under_budget`. Do not demand endless reruns once a fair\n"
            "  run exists or the sane regime is unreachable in budget.\n\n"
            "STEP-BACK REFLECTION (emit `step_back`; the anti-plan-lock-in guard —\n"
            "load-bearing, not optional):\n"
            "- WHEN: fill it on ANY round with a MEASURED result (a metric, score,\n"
            "  eval output, or a read of a results/log file). `null` ONLY when the\n"
            "  round produced no measured result (pure wiring/setup/waiting).\n"
            "- INDEPENDENCE (the whole point): author it AS A FRESH domain expert\n"
            "  seeing ONLY these numbers + the plan, with NO commitment to the\n"
            "  current direction — EVEN IF status=done, forward_progress=true, and\n"
            "  the result looks fine. A clean, on-plan result is the MOST important\n"
            "  time to step back. If you are writing 'nothing surprising, continue',\n"
            "  look harder — a real result almost always raises a new question.\n"
            "- `supported_by_results`: does the result ACTUALLY support the plan's\n"
            "  claim? `yes`/`partial`/`no` — judge the claim, not whether it ran.\n"
            "- `surprises`: what is anomalous/underexplored or quietly contradicts an\n"
            "  assumption the plan rests on, even if the headline looks good. Empty\n"
            "  only if, after genuinely looking, nothing qualifies.\n"
            "- `new_questions`: 2-3 sharp questions a skeptic would ask of these\n"
            "  numbers (capability vs prompt formatting? holds on the harder split?).\n"
            "- `alt_directions`: 1-3 CONCRETE next experiments worth a branch, each\n"
            "  {direction, why, cheap_to_test}; prefer cheap_to_test=true. The\n"
            "  planner triages each; empty should be rare on a real result.\n"
            "- REFLECTION, not a verdict change: never alters status/forward_progress\n"
            "  and never edits the plan; do not relitigate the FROZEN outcome/metric/\n"
            "  verifier — widen the search, don't move the goalposts.\n\n"
            "- SKILL-EVOLUTION SIGNAL (you decide, not a status heuristic): on any\n"
            "  non-`done` verdict set `failure_cause` = `skill_gap` |\n"
            "  `execution_mistake` | `ambiguous_objective` | `environmental` |\n"
            "  `method_failure` | `unknown`. `skill_gap` = a FIXABLE knowledge/config\n"
            "  gap a future mission could avoid (the `misconfigured_run` cases:\n"
            "  underpowered/wrong hyperparameters, truncated rollouts, an\n"
            "  `answer_only`-gamed reward/length gate, wrong base vs instruct, a\n"
            "  missing method step). `method_failure` = the idea is genuinely dead\n"
            "  after a fair, sane-regime run (no reusable fix → no lesson). For\n"
            "  skill_gap does NOT require a separate lesson paragraph; explain the\n"
            "  concrete repair directly in `reason` / `next_action`.\n\n"
            "- SKILL MEMORY (you own the library; emit `skill_ops` on any verdict):\n"
            "  * create — a NEW capability the engineer lacked: op=create,\n"
            "    content=the full skill markdown, written for a FAMILY of tasks\n"
            "    (title a CAPABILITY, use <placeholders>, never hardcode this\n"
            "    mission's paths/ids/numbers); include When-to-use / When-NOT /\n"
            "    How-to-solve (+pitfalls). Before emitting create/update, use\n"
            "    live web search to check current primary sources; if that search\n"
            "    cannot ground the lesson, omit the skill_op.\n"
            "  * update — fold a lesson into a matched skill: op=update, name,\n"
            "    content=the FULL revised markdown.\n"
            "  * archive/delete — retire a matched skill you found WRONG/harmful/\n"
            "    mis-scoped: op=archive, name, why=one clause. Your direct authority.\n"
            "  create/update become active, versioned project-layer skills immediately;\n"
            "  use later task trajectories to update or archive them when warranted.\n"
            "  Most rounds need NO\n"
            "  skill_ops; never propose one for a one-off environmental blocker.\n\n"
            "- WIKI MEMORY (you own the project idea-wiki too; emit `wiki_ops` on\n"
            "  any verdict — omit/[] when this project has no initialized wiki, or\n"
            "  this round warrants no wiki change):\n"
            "  * create_page/update_page — record a durable technique/conflict/\n"
            "    pattern: op, id (a stable slug), card_type (technique|conflict|\n"
            "    pattern), title, status (scratch|candidate|stable — new pages start\n"
            "    scratch), body=the page markdown, evidence=[{source_id, quote,\n"
            "    locator}] citing an already-ingested wiki source VERBATIM — a\n"
            "    fabricated quote is mechanically rejected regardless of your\n"
            "    judgment, so never paraphrase a cited quote.\n"
            "  * retire_page — tombstone a page you found wrong/superseded: op,\n"
            "    id, why=one clause. Never a hard delete; always reversible.\n"
            "  Prior missions are mechanically captured as immutable RunCards under\n"
            "  `sources/runs/`. Inspect the latest relevant RunCard before deciding\n"
            "  whether a durable technique/conflict/pattern page is warranted; it is\n"
            "  valid evidence for a later mission's wiki_ops.\n"
            "  Most rounds need NO wiki_ops. This is a SEPARATE library from\n"
            "  skill_ops above: skills teach the ENGINEER how to act; wiki pages\n"
            "  record what the PROJECT learned (durable facts/techniques worth\n"
            "  finding again). A single round may propose both when warranted.\n\n"
            "Checkpoint rules (you are the MEMORY AUDITOR; this object IS the\n"
            "engineer's entire working memory next round — the raw session is\n"
            "dropped, so a fresh engineer sees ONLY this):\n"
            "- Author it from the PRIOR checkpoint above + the engineer's `HANDOFF:`\n"
            "  block. The engineer PROPOSES; you VALIDATE — never copy a claim you\n"
            "  cannot back with evidence/artifacts.\n"
            "- Curated memory, NOT a log; hard-capped (done ≤ 8, tried_and_failed ≤\n"
            "  6, maturing ≤ 5, short strings). The cap forces you to FORGET: keep\n"
            "  only what changes the next session; deletion is correct (ground truth\n"
            "  stays on disk and is re-summonable).\n"
            "- `goal`: the mission's end goal in one line (carry it forward).\n"
            "- `done`: only VERIFIED accomplishments, each with its proof\n"
            "  (command/file). Failed verification means that objective is NOT done.\n"
            "- `tried_and_failed`: GENUINE dead ends + the reason (prevents a\n"
            "  Sisyphus loop); keep the ones tied to the current blocker. Do NOT dump\n"
            "  a promising approach here on its FIRST failure — that belongs in\n"
            "  `maturing`.\n"
            "- `maturing`: tried, not-yet-succeeding, NOT dead — each with the\n"
            "  SPECIFIC next refinement. A new approach often underperforms a tuned\n"
            "  baseline until refined, so carrying it forward is what lets it be\n"
            "  developed over rounds instead of re-discovered later. It is YOUR\n"
            "  judgment when a fair window has passed → demote to `tried_and_failed`.\n"
            "- `active_line`: the ONE bold/structural direction being matured on a\n"
            "  retained branch that may sit ABOVE the global-best floor —\n"
            "  {desc, branch_or_path (where its train.py is saved so the next\n"
            "  engineer checks it out and BUILDS ON it), rounds_active (increment\n"
            "  each round), note (the next refinement)}. THE DEFAULT, INVERTED: when\n"
            "  this round's measured-but-unpromoted candidate is within run-to-run\n"
            "  noise (~0.001-0.002) of the floor OR advances a declared structural\n"
            "  direction, you OPEN/CONTINUE an active_line from it (save its train.py\n"
            "  to a named branch, carry it here) — you do NOT discard it and snap the\n"
            "  next candidate back to the floor. Snap back to the global-best floor\n"
            "  ONLY when the active line had a FAIR window and is dead (demote +\n"
            "  clear active_line), or for a deliberate fresh regime change. The floor\n"
            "  is never lost (recoverable on disk), so an above-floor line risks\n"
            "  nothing.\n"
            "- `open_blocker`: the single most important unresolved blocker + root\n"
            "  cause; keep it until resolved (move to `done`) or replaced by a more\n"
            "  specific one.\n"
            "- `next_step`: the most useful next action. If an active_line is alive,\n"
            "  next_step MUST be to CONTINUE developing it from its saved branch (the\n"
            "  named refinement) — NOT 'restore the global-best floor and tweak it',\n"
            "  the greedy rut the active_line exists to break.\n"
            "- `env_facts`: durable environment/infra facts the successor must NOT\n"
            "  re-derive (paths, access endpoints, versions, what's ephemeral vs\n"
            "  persistent); carry prior ones forward, add any newly established, drop\n"
            "  the least load-bearing to stay within the cap.\n"
            "- Carry forward load-bearing prior items the engineer did not mention —\n"
            "  PRESERVE valuable memory across the session boundary, dropping only\n"
            "  what is genuinely low-value.\n\n"
            "Decision rules:\n"
            "- Set `progress_class`; Do not add a separate explanation: `decision` for a\n"
            "  candidate/gate/NO-GO conclusion (including negative results),\n"
            "  `evidence` for fresh experiment/source/proof evidence, `setup_only`\n"
            "  for scaffolding without executed evidence, `artifact_sync_only` for\n"
            "  bookkeeping-only work, or `none`. Reuse reason/planner_report.\n"
            "1) `done` ONLY when the summary shows CONCRETE EVIDENCE of success —\n"
            "   real command output, test results, file contents, query results. A\n"
            "   bare `I implemented X` / `verified Y exists` without the command +\n"
            "   output is NOT evidence.\n"
            "1a) Symmetric stop rule: if the summary DOES include verbatim output\n"
            "   that directly satisfies the operator request, choose `done` — do NOT\n"
            "   demand another round whose only purpose is to re-run and re-print the\n"
            "   same output (that wastes rounds and tokens).\n"
            "2) Default to `continue` whenever the agent's claims are not backed by\n"
            "   concrete artifacts in the summary — the agent has no ground-truth\n"
            "   signal, so your job is to demand evidence. But once the evidence is\n"
            "   in front of you (rule 1a), stop.\n"
            "3) On `continue`, state the missing outcome/evidence and any hard\n"
            "   constraints, then leave implementation and tool choice to the\n"
            "   Engineer. Be step-by-step only when a deterministic failed check\n"
            "   already identifies an exact repair. If evidence is missing, ask for\n"
            "   the specific verification command; when honest evidence is already\n"
            "   in hand, do NOT re-request it — point to the specific NEXT work or "
            "unexplored direction instead.\n"
            "4) `blocked` ONLY when user input is strictly required for ANY further\n"
            "   progress (missing credentials, a spec only the user can clarify,\n"
            "   hardware the agent cannot reach). A failing test / runtime error /\n"
            "   wrong output the agent could fix itself is `continue`, not `blocked`.\n"
            "   When in doubt, prefer `continue`.\n"
            "5) `round_summary_markdown`: this round's completed work, evidence\n"
            "   shown, and remaining gaps.\n"
            "6) Non-`done`: `completion_summary_markdown` is a short placeholder.\n"
            "7) `done`: `completion_summary_markdown` must quote the concrete\n"
            "   evidence (command + output). No evidence → not done.\n"
            "8) Spec adherence: when the request gives CONCRETE STRUCTURAL\n"
            "   CONSTRAINTS — exact file paths, module/package names, framework\n"
            "   (e.g. `pytest` vs `unittest`), API signatures, return-type contracts,\n"
            "   test count, directory layout — the artifacts MUST match unless the\n"
            "   agent justified the deviation in its summary AND it is materially\n"
            "   equivalent. Any unjustified structural deviation → `continue` naming\n"
            "   it in `next_action`. Functional correctness alone is NOT enough when\n"
            "   the operator gave a precise structural contract (downstream tooling\n"
            "   relies on the exact paths/frameworks).\n"
            "9) Final-submission scope (ONLY when metadata says `planner_scope:\n"
            "   final_submission` or `Task scope: final_submission`): `done` only if\n"
            "   every full-pipeline-checklist item is satisfied (research →\n"
            "   submission). A passing single-stage checklist, a pilot run, or an\n"
            "   underlength draft is NOT enough. For positive paper objectives, do\n"
            "   not accept a negative-result pivot or a baseline-only win: the\n"
            "   contribution needs a structured X-Y-Z-W `paper_contribution` claim\n"
            "   that beats the strongest nontrivial baseline on the declared metric\n"
            "   with statistical support. For `bounded`/absent scope, do not require\n"
            "   the full pipeline checklist — judge by the task's own acceptance\n"
            "   criteria + the relevant per-stage items.\n\n"
            "Original operator request (root substantive anchor):\n"
            f"{(original_objective or objective).strip()}\n\n"
            "Current mission objective (may include planner/prelude context):\n"
            f"{objective}\n\n"
            "Operator message history (source of truth for user instructions):\n"
            f"{operator_text}\n\n"
            "Planner guidance for this review:\n"
            f"{planner_review_instruction or 'none'}\n\n"
        )
        # F7: per-round DELTA — everything that changes round to round. When the
        # reviewer resumes its own thread the static above is already in-context,
        # so ONLY this delta is re-sent. The RE-EVALUATE header (resumed only) is
        # the anti-rubber-stamp guard; ``search_altitude_block`` and the
        # checkpoint/escalation/log-audit blocks live here (reordered out of the
        # static prefix) precisely because they vary per round.
        delta = (
            (_REEVALUATE_HEADER if resumed else "")
            + search_altitude_block
            + f"{prior_checkpoint_block}"
            + f"{escalate_block}"
            + f"{engineer_log_audit_block}"
            + f"Round: {round_index}\n"
            f"Session ID: {session_id or 'none'}\n"
            f"{shared_context_block}"
            f"{background_block}"
            f"Main agent fatal error: {error_text}\n\n"
            "Main agent last summary:\n"
            f"{main_summary}\n\n"
            f"{evidence_block}"
        )
        return static, delta

    def _build_prompt(self, **kwargs: Any) -> str:
        """Full reviewer prompt (static + round-1 delta). Kept for the unit tests
        and any non-resuming caller; ``evaluate`` uses ``_render`` directly."""
        static, delta = self._render(resumed=False, **kwargs)
        return static + delta

    def _build_static_preamble(self, **kwargs: Any) -> str:
        """The byte-stable static preamble alone (for the fingerprint + resume)."""
        static, _ = self._render(resumed=False, **kwargs)
        return static

    def _build_round_delta(self, *, resumed: bool, **kwargs: Any) -> str:
        """This round's delta alone; ``resumed`` prepends the RE-EVALUATE header."""
        _, delta = self._render(resumed=resumed, **kwargs)
        return delta


_MAX_SHARED_CTX_CHARS = 100_000_000  # effectively no cap: reviewer must see the FULL engineer reasoning/prev-review to audit honesty


def _format_engineer_shared_context(
    *,
    skill_used: str | None,
    prev_review_summary: str,
) -> str:
    """Render the read-only shared context block injected into reviewer prompts.

    Keep this renderer stable because the same block is consumed across
    engineer/reviewer round boundaries.

    The engineer's final message is rendered exactly once, under "Main agent
    last summary"; its full reasoning/process is available to the reviewer via
    the ``engineer_log_path`` audit block. We therefore do NOT echo a separate
    ``engineer_reasoning_summary`` here — the sole caller fed it the same string
    as ``main_summary``, so it only duplicated input tokens every reviewer round.
    """
    skill = (skill_used or "").strip()
    prev = (prev_review_summary or "").strip()
    if not skill and not prev:
        return ""
    parts = ["Shared read-only context (do NOT modify; advisory only):"]
    if skill:
        parts.append(f"- skill_used: {skill}")
    if prev:
        if len(prev) > _MAX_SHARED_CTX_CHARS:
            prev = prev[:_MAX_SHARED_CTX_CHARS].rstrip() + "..."
        indented = "\n".join("    " + line for line in prev.splitlines())
        parts.append("- previous_review_summary:\n" + indented)
    return "\n".join(parts) + "\n\n"
