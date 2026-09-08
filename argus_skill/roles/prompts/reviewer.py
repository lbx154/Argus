"""Reviewer prompt operations, structured context, and complete prompt body."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ...core.model_visible_text import (
    MODEL_INTEGRITY_BOUNDARY,
    sanitize_model_visible_text,
)
from ...core.role_decision import decision_footer_instruction
from ..task_contract import (
    EFFECTIVE_TASK_CONTRACT,
    native_shell_summary,
)
from .types import ChecklistMode, RoleName, RolePromptRequest
from .voice import RESEARCHER_VOICE

EVALUATE = "evaluate"
SCIENCE_LOSS_CHECK = "science_loss_check"
COLD_READ = "cold_read"

OPERATIONS = frozenset({EVALUATE, SCIENCE_LOSS_CHECK, COLD_READ})

_REEVALUATE_HEADER = (
    "## NEW ROUND — RE-EVALUATE INDEPENDENTLY (resumed reviewer)\n"
    "You are resuming your own thread to retain the review standards. Reach a fresh "
    "judgment on this round's changes; do not merely repeat your previous conclusion. "
    "Previously confirmed evidence remains settled context. Recheck only changed "
    "inputs, stale or contradictory evidence, and gaps the prior review left open. "
    "The role, standards, and decision rules still apply.\n\n"
)

# Acceptance settles effort, never truth. Once one round accepted a 6% score on
# a benchmark where the model publishes ~80%, this boundary forbade every later
# round from looking again, and the campaign reproduced its own broken baseline
# for ninety reviews without once returning `incorrect`.
_INCREMENTAL_REREVIEW_BOUNDARY = (
    "## Incremental re-review boundary\n"
    "The previous Reviewer judgment below is settled context for this mission. "
    "Inspect the prior `next_action`, the current Engineer summary, the "
    "files changed to satisfy that action, and the relevant "
    "checks. Do not restart repository research, reopen established findings, or "
    "repeat unchanged online/source checks. Repeat a broader check only when "
    "the current changes affected its input, the previous judgment explicitly left "
    "it unresolved, or a named contradiction/security/authority issue requires "
    "it. Prior agreement never establishes the truth of a number the paper will "
    "stand on when nothing outside this harness has confirmed it. A favorable "
    "review is not evidence that it is true, and a harness reproduces its own broken "
    "baseline every round it is asked. If the requested change now holds and no "
    "such contradiction exists, close the local repair; do not invent a new unrelated "
    "repair round. For final paper review, the separate selected-venue acceptance "
    "threshold still governs `done`: fixing the last edit alone is insufficient.\n\n"
)

# The Reviewer is the only role that can open the plan-challenge channel, and
# `reconsider` is the single token that opens it. Until this block existed the
# word appeared nowhere in any prompt while the example offered an invalid
# `keep`, so the channel stayed shut and a campaign could close round after
# locally correct round without anything ever questioning the plan itself.
# Keep these values in step with ``argus_skill.reviewer._parsing``.
_PLAN_SIGNAL_VOCABULARY = (
    "`plan_signal` is `continue` or, if evidence lowers expected value, `reconsider`. "
    "Add evidence-backed `plan_challenge` and `authority_impact`: `technical` for "
    "working choices and team plans; `manager_contract`/`operator` for their "
    "commitments only. Without a known `plan_alternative`, Manager uses `revise`.\n"
)


def evaluate_request(
    project_root: Path | str,
    *,
    altitude_root: Path | str | None = None,
    scope: str = "",
    stage: str | None = None,
    vertical: str | None = None,
    checklist_mode: ChecklistMode = ChecklistMode.AUTO,
    operation: str = EVALUATE,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.REVIEWER,
        operation=operation,
        project_root=project_root,
        # The facts are about the work, which lives in the worktree, not in
        # state/projects/<session>/ where the stage is read from.
        altitude_root=altitude_root,
        vertical=vertical,
        stage=stage,
        scope=scope,
        checklist_mode=checklist_mode,
        include_search_altitude=True,
    )


def _project_has_wiki(
    working_dir: str | Path | None = None,
) -> bool:
    project_root = Path(working_dir).expanduser() if working_dir else Path.cwd()
    autors = project_root / ".autors"
    if not autors.exists():
        return False
    from ...wiki.bootstrap import is_initialized_wiki

    return any(is_initialized_wiki(p / "wiki") for p in autors.iterdir() if p.is_dir())


def _load_wiki_curator_skill_if_present(
    working_dir: str | Path | None = None,
) -> str | None:
    """Compatibility helper returning the compact wiki-curator contract."""
    if not _project_has_wiki(working_dir):
        return None
    return (
        "knowledge curator: directly edit durable concepts, principles, facts, "
        "hypotheses, relationships, and conflicts grounded in real evidence; "
        "do not return page operations in the decision."
    )


def _verification_directive() -> str:
    """Compact trust-first verification stance."""
    return (
        "Trust consistent evidence; recheck gaps, stale evidence, contradictions, or "
        "implausibility only. Read beyond git diff. Identity drift proves neither "
        "failure nor causation without this mission's mutation command.\n\n"
    )


_PRODUCT_ACCEPTANCE_DIRECTIVE = (
    "Test claimed UI/API/CLI/service flows at a safe public entry point, beyond units. "
    "Internal exploration needs its feedback experiment; check libraries decisively. "
    "Web UI: inspect desktop/mobile rendering and interactions in Chromium; use $ARGUS_SKILL_PYTHON for Playwright. "
    "Report untested claims/trials. Never cause external or irreversible effects.\n"
)


def _audit_integrity_directive(context: str) -> str:
    lowered = str(context or "").lower()
    if not any(
        marker in lowered
        for marker in (
            "audit",
            "ledger",
            "append-only",
            "command_log",
            "process_trace",
            "审计",
            "账本",
            "只追加",
            "命令日志",
        )
    ):
        return ""
    return (
        "## Integrity of the historical record\n"
        "Treat an operator mutation freeze or append-only requirement as a hard temporal "
        "boundary. When continuity of the record matters, compare directive order with file-write, "
        "install, and command events. A later archive, correction, or successful rerun "
        "cannot make an overwritten or reconstructed record contemporaneous. Do not credit any "
        "fact attributed to the objective unless the cited objective text states it, and "
        "do not treat a summarized command log as the missing byte-faithful command. "
        "Preserve useful corrections, but return `continue`, `replan_requested`, or "
        "`blocked` when the required historical integrity is irrecoverable.\n\n"
    )


def _prompt_block_stats(blocks: Mapping[str, str]) -> dict[str, dict[str, int]]:
    stats: dict[str, dict[str, int]] = {}
    for name, text in blocks.items():
        rendered = str(text or "")
        byte_count = len(rendered.encode("utf-8"))
        stats[str(name)] = {
            "chars": len(rendered),
            "bytes": byte_count,
            "estimated_tokens": (byte_count + 3) // 4,
        }
    return stats


def _engineer_log_audit_block(
    engineer_log_path: str,
    *,
    engineer_call_id: str = "",
    round_index: int,
    measured: bool,  # noqa: ARG001 — kept for call-site symmetry
    compact: bool = False,
) -> str:
    """One-line fallback pointer when the Engineer's own account looks wrong."""
    path = (engineer_log_path or "").strip()
    if not path:
        return ""
    call_id = (engineer_call_id or "").strip()
    scope = f" (current call `{call_id}`)" if call_id else ""
    return (
        f"The event log is at `{path}`{scope} if the Engineer's account seems wrong.\n\n"
    )


def _format_engineer_shared_context(
    *,
    skill_used: str | None,
    prev_review_summary: str,
) -> str:
    """Render the read-only shared context block injected into reviewer prompts."""
    skill = (skill_used or "").strip()
    prev = (prev_review_summary or "").strip()
    if not skill and not prev:
        return ""
    parts = ["Shared read-only context (do NOT modify; advisory only):"]
    if skill:
        parts.append(f"- skill_used: {skill}")
    if prev:
        indented = "\n".join("    " + line for line in prev.splitlines())
        parts.append("- previous_review_summary:\n" + indented)
    return "\n".join(parts) + "\n\n"


def render_reviewer_prompt(
    owner: Any,
    *,
    operation: str = EVALUATE,
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
    """Render the complete Reviewer prompt as ``(static_preamble, round_delta)``."""
    from ...core.project import resolve_project_root
    from ...core.research_contract import (
        RESULT_FIELD_CHOICES,
        resolve_research_target_level,
    )
    from ...skills.vertical_select import (
        _persisted_vertical,
        resolve_workflow_mode,
    )
    from .registry import resolve_role_prompt

    error_text = sanitize_model_visible_text(main_error or "none")
    engineer_account = sanitize_model_visible_text(main_summary)
    if len(engineer_account) > 6000:
        omitted_chars = len(engineer_account) - 5900
        engineer_account = (
            engineer_account[:4000]
            + f"\n…[middle omitted {omitted_chars} characters]…\n"
            + engineer_account[-1900:]
        )
    # Reviewer receives Skill-library paths and searches independently; no
    # Skill body is selected or injected by the runtime.
    _proot = resolve_project_root(vertical_state_root or working_dir)
    try:
        from ...core.manuscript_snapshot import (
            manuscript_review_artifact_statuses,
        )

        stale_review_facts = [
            fact
            for fact in manuscript_review_artifact_statuses(_proot)
            if fact["status"] != "current"
        ]
        review_validity_block = ""
        if stale_review_facts:
            review_validity_block = (
                "## Manuscript-bound review validity (mechanical facts)\n"
                + "\n".join(
                    f"- {fact['path']}: {fact['message']}"
                    for fact in sorted(
                        stale_review_facts,
                        key=lambda item: (str(item["path"]), str(item["message"])),
                    )
                )
                + "\nTreat these records as stale facts, never as passed/certified. "
                "Do not request a new model review merely because they are stale; "
                "judge the next action under the current plan.\n\n"
            )
    except Exception:  # noqa: BLE001 - optional paper facts never break review
        review_validity_block = ""
    scope_normalized = (scope or "").strip().lower().replace("-", "_")
    _persisted = _persisted_vertical(_proot)
    explicit_vertical = str(vertical or "").strip()
    routed_vertical = explicit_vertical or _persisted
    prompt_context = resolve_role_prompt(
        evaluate_request(
            _proot,
            altitude_root=resolve_project_root(working_dir) if working_dir else None,
            scope=scope_normalized,
            vertical=routed_vertical,
            # Suppressed only when this project has no pipeline state to read a
            # stage from — not merely because the caller named the vertical.
            #
            # The two used to be the same condition, on the reasoning that a
            # caller passing ``vertical`` explicitly is one running outside a
            # project (``argus_maintenance`` in a bare directory), where asking
            # for a stage checklist is actively harmful: ``current_stage``
            # returns the vertical's first stage as a FALLBACK rather than
            # reporting that it found nothing, and an unresolved stage renders
            # as "Configuration error: this required checklist is not loaded.
            # Do not mark the stage complete" — a blocker manufactured out of a
            # missing file.
            #
            # But the daemon passes ``vertical_override`` for a real campaign
            # that does have pipeline state, so the proxy misfires there and the
            # Reviewer silently loses its stage checklist: for a math project in
            # ``solve`` that is ~2k characters of the acceptance criteria it is
            # supposed to be judging against, while the Engineer's own prompt
            # still carries them. ``_persisted`` is non-None exactly when
            # ``.argus/PIPELINE_STATE.json`` records a vertical, which is the
            # condition actually being asked about.
            checklist_mode=(
                ChecklistMode.NONE
                if explicit_vertical and not _persisted
                else ChecklistMode.AUTO
            ),
            operation=operation,
        )
    )
    persisted_prompt_context = (
        resolve_role_prompt(
            evaluate_request(
                _proot,
                vertical=routed_vertical,
                checklist_mode=ChecklistMode.NONE,
                operation=operation,
            )
        )
        if routed_vertical is not None
        else None
    )
    _requires_engineering_audit = bool(
        persisted_prompt_context is not None
        and persisted_prompt_context.requires_independent_review
    )
    matched_review_skill_block = ""
    if preselected_skill_block is not None:
        if preselected_skill_block.strip():
            matched_review_skill_block = preselected_skill_block.strip() + "\n\n"
    elif owner.skill_store is not None:
        review_libraries = owner.mission.libraries()
        if review_libraries.block:
            matched_review_skill_block = review_libraries.block + "\n\n"
    stage = prompt_context.stage
    from ...core.venue_review import requires_venue_review

    prose_final_review = requires_venue_review(
        vertical=routed_vertical or "", stage=stage,
        scope=scope_normalized, operation=operation,
    )
    research_context_block = ""
    if prompt_context.vertical and operation == EVALUATE:
        from ...verticals._base import load_vertical_contract

        context_provider = load_vertical_contract(
            prompt_context.vertical, project_root=_proot
        ).role_prompt_context
        if context_provider is not None:
            research_context_block = context_provider(
                role="reviewer",
                operation=operation,
                stage=stage,
                scope=scope_normalized,
                project_root=(
                    resolve_project_root(working_dir) if working_dir else _proot
                ),
            )
    direct_workflow = resolve_workflow_mode(_proot) == "direct"
    _measured = not _requires_engineering_audit and os.environ.get(
        "ARGUS_SKILL_MEASURED_MODE", ""
    ).strip().lower() in ("1", "true", "yes", "on")
    # Vertical-owned policy arrives through the prompt catalog; this module
    # contributes only role-wide review behavior.
    optimize_banner = prompt_context.role_banner
    if prompt_context.requires_independent_review and not _requires_engineering_audit:
        optimize_banner = ""
    verification_instruction = ""
    _research_target_level = resolve_research_target_level(_proot)
    if (
        _research_target_level is not None
        or prompt_context.verification_stage_profiles
    ):
        # Two separate things, previously one sentence: `research_target_level`
        # says what finishing the PROJECT means, and the verification profile
        # says what THIS round has to show. Conflating them made every early
        # probe get judged against publication readiness.
        from ...core.verification_policy import policy_line, resolve_policy
        from ...skills.stage_machine import current_stage

        try:
            _stage = current_stage(_proot)
        except Exception:  # noqa: BLE001 - stage is advisory here
            _stage = ""
        _policy = resolve_policy(
            _proot,
            scope=scope_normalized,
            stage=_stage,
            vertical=_persisted_vertical(_proot),
            target_level=_research_target_level,
            stage_profiles=prompt_context.verification_stage_profiles,
        )
        verification_instruction = (
            (
                f"Project target `{_research_target_level}` defines project "
                "completion, not this round's bar. "
            )
            if _research_target_level is not None
            else (
                "The active vertical owns when verification becomes final; "
                "judge this mission at its current stage, not at the last "
                "stage's completion standard. "
            )
        ) + (
            f"This round: {policy_line(_policy)}. The integrity floor is "
            "identical at every profile. Judge directly and explain in `reason`. "
        )
        if _research_target_level is not None and not prose_final_review:
            verification_instruction += (
                "If the direction cannot reach the target, return "
                "`replan_requested`.\n"
                "End with `RESEARCH_RESULT=<JSON>` over evidence you inspected. "
                "`evidence` and `limitations` are JSON string arrays; a survey is "
                "`literature_review` with `novelty_status` `known` or "
                "`not_applicable`. Use one listed value per field so the record "
                "stays comparable across campaigns; the block summarizes your "
                "judgment and never replaces it:\n"
                + "".join(
                    f"{_field}: {' '.join(_choices)}\n"
                    for _field, _choices in RESULT_FIELD_CHOICES
                )
                + "\n"
            )
    surprise_judgment_block = (
        "What observed result or pattern most changed your belief — including a "
        "positive surprise — and what is the cheapest observation that would "
        "distinguish a new scientific explanation from an artifact? `none` is valid "
        "and produces no work; if the answer could change the claim or route, use "
        "ordinary `reconsider` to ask Planner to consider it.\n"
        if _research_target_level is not None
        else ""
    )
    # Live search-altitude facts (NO verdict) so the reviewer can SEE the
    # floor history when judging forward_progress — i.e. distinguish "this
    # round advanced a declared structural line" from "Nth single-knob
    # nibble at a floor that has not moved in N attempts". Empty for
    # verticals that do not surface it.
    search_altitude_block = sanitize_model_visible_text(
        prompt_context.search_altitude
    )
    if _measured:
        stage_checklist = (
            "## MEASURED-BENCHMARK MODE — TRUST the scorer, judge the IDEA\n"
            "The scorer is trusted and frozen. The Engineer has no reward signal "
            "and does not control the scorer, so normally trust the pasted RESULT "
            "(correct + cand_ms/score). Ask whether this round's measured score "
            "beat the Engineer's previous best.\n"
            "Do not rerun the scorer to confirm an honest, consistent number: "
            "the Engineer checks correctness by running it every round, and "
            "repeating that measurement adds nothing. Check only if no RESULT "
            "was pasted or it contradicts itself. Otherwise, spend the round "
            "reaching a judgment and choosing the next direction.\n"
            "- Use `continue` if the score improved (preserve it and explore the next "
            "mechanism) or a clearly different mechanism remains untried. First "
            "ask whether this mechanism was new or another adjustment to a "
            "direction that already failed. `next_action` must name a concrete new "
            "direction (a different SOTA/library approach, a hardware technique, "
            "the profiled bottleneck). Seek different mechanisms; never ask for "
            "another small adjustment to a failed direction or a result already shown.\n"
            "- Use `blocked` only at a real plateau (several rounds without improvement "
            "and distinct mechanisms exhausted) or when only the operator can help. "
            "When a choice requires the operator (direction, budget, which task, GPU, "
            "or a yes/no), also set `operator_question`: one plain-language question "
            "in the operator's language (Chinese here), answerable in a sentence "
            "— no jargon/JSON/template names.\n"
            "- Use `done` rarely here, only at or above the known ceiling.\n"
            "Ignore GROUND_TRUTH/marker/status/provenance files (the harness "
            "ignores them) and file tidiness; the scorer's number is the only "
            "evidence. A round that measured a real number, even a worse one, made "
            "progress by ruling out a mechanism. This takes precedence over the "
            "general rules below about evidence and repeated measurements."
        )
    else:
        stage_checklist = prompt_context.stage_checklist
    if direct_workflow:
        stage_checklist = ""

    wiki_curator_skill_block = ""
    direct_memory_edit_block = ""

    venv_skill_block = (
        "## Dependencies\n"
        "Have Engineer install missing packages with `./.venv/bin/pip`; "
        "leave Argus's venv unchanged."
    )

    # Upstream-evidence defect REPORT. When the reviewer notices that an
    # upstream stage's evidence is missing or unreliable while working a
    # later stage, the correct move is to REPORT it so the Manager can roll
    # the stage back — the reviewer does NOT edit the pipeline state machine
    # itself (stage authority is the Manager's). The instruction lives here
    # (not in the individual checklist items) so it applies uniformly.
    stage_order = prompt_context.stage_order
    stage_idx = stage_order.index(stage) if stage in stage_order else 0
    earlier_stages = ", ".join(stage_order[:stage_idx]) or "(none)"
    if prompt_context.vertical == "research":
        rollback_block = (
            "## Upstream defects\n"
            f"Current stage: `{stage}`. Earlier stages: {earlier_stages}.\n"
            "Research moves forward only. Name repairs here in `next_action` for earlier "
            "method, experiment, or paper defects affecting this work. Never "
            "request rollback, reopen idea selection, or edit `.argus/PIPELINE_STATE.json`."
        )
    else:
        rollback_block = (
            "## Upstream defects\n"
            f"Current stage: `{stage}`. Earlier stages: {earlier_stages}.\n"
            "Rollback only when a concrete earlier defect makes the current result "
            "unusable. Optional outputs or those not needed for the claim are advisory. If "
            "rollback is necessary, return `replan_requested` with the earliest "
            "stage and evidence; Manager owns rollback. Never edit "
            "`.argus/PIPELINE_STATE.json`."
        )
    operator_text = (
        "\n".join(f"- {line}" for line in operator_messages) if operator_messages else "- none"
    )
    original_text = (original_objective or objective).strip()
    current_text = objective.strip()
    if original_text == current_text:
        objective_block = f"Task objective:\n{current_text}\n\n"
    else:
        objective_block = (
            f"Original operator request:\n{original_text}\n\n"
            f"Current mission objective:\n{current_text}\n\n"
        )
    # The Reviewer is the one role whose verdict closes work, so it is the role
    # that most needs the operator's stated bar in front of it. Without this it
    # judges the mission text, which describes the increment rather than what
    # the operator agreed counts as done.
    from ...core.project_contract import contract_briefing, load_contract_for_cwd

    _contract_block = contract_briefing(
        load_contract_for_cwd(_proot),
        authoritative_objective=original_objective,
    )
    if _contract_block:
        objective_block += _contract_block + "\n\n"
    shared_context_block = _format_engineer_shared_context(
        skill_used=active_skill_id,
        prev_review_summary=prev_review_summary,
    )
    shared_context_block = sanitize_model_visible_text(shared_context_block)
    incremental_review_block = ""
    if round_index > 1 and prev_review_summary.strip():
        incremental_review_block = _INCREMENTAL_REREVIEW_BOUNDARY
    # Prefer direct runtime and verifier evidence over the Engineer's summary
    # when callers provide it. Omit the block when no such evidence exists.
    evidence_block = (
        "\nRaw verification evidence:\n"
        + sanitize_model_visible_text(raw_evidence.rstrip())
        + "\n"
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
            "\n"
            + sanitize_model_visible_text(background_context.strip())
            + "\n\n"
            "Reviewer note on the above: these are SUPERVISED subagents with "
            "their own independent supervisor, so their autonomous progress is "
            "NOT by itself the engineer's forward progress. If the engineer only "
            "re-polled a healthy self-watched subagent this round, steer "
            "`next_action` to advance independent work that does not depend on "
            "it. If nothing else can proceed, return `continue` and state what "
            "evidence the next round should wait for.\n"
        )
    # The shared Markdown checkpoint is the live handoff. ``prior_checkpoint``
    # remains accepted for callers that have not migrated to the file path.
    _ = prior_checkpoint
    _ = checkpoint_path
    checkpoint_block = ""
    # Anti-livelock rule supplied at the soft round boundary.
    escalate_block = ""
    if escalate_hint:
        escalate_block = (
            f"## Escalation directive (operator harness — IMPORTANT)\n{escalate_hint}\n\n"
        )
    # The Engineer's own account is primary. Keep the execution log as a
    # one-line fallback pointer for a concrete contradiction instead of asking
    # every Reviewer to reconstruct the round from grep recipes. Empty path
    # (memory backend / tests / unresolvable life_dir) omits the pointer.
    engineer_log_audit_block = _engineer_log_audit_block(
        engineer_log_path,
        engineer_call_id=engineer_call_id,
        round_index=round_index,
        measured=_measured,
        compact=not bool((main_error or "").strip()),
    )
    engineer_log_audit_block = sanitize_model_visible_text(
        engineer_log_audit_block
    )
    if direct_workflow:
        rollback_block = ""
    # Byte-stable static policy; every fresh Reviewer receives it in full.
    shell_contract = native_shell_summary()
    audit_integrity_block = _audit_integrity_directive(
        "\n".join(
            (
                objective,
                original_objective,
                planner_review_instruction,
                *operator_messages,
            )
        )
    )
    handoff_policy = (
        "Use `done` when a direct task meets its requirements and decisive check. "
        "Use `replan_requested` only to change the plan; `plan_signal` advises and "
        "cannot override `status`. For a material gap, use `continue` and name the "
        "next task. Leave optional hardening advisory."
        if direct_workflow
        else (
            "`done` needs sufficient evidence, not exhaustive proof or every file. "
            "Only claim-essential evidence gaps warrant `continue`; optional "
            "evidence and minor weaknesses are advice. A timeout or failed attempt "
            "proves no impossibility; a missed threshold describes only that run. "
            "Root-cause, dominant-stage, bottleneck, or replacement claims need code-path "
            "evidence plus profiling, timing, or a controlled comparison. Integrity is "
            "required, not scientific value. Ask only for authority or facts the operator "
            "owns. `done` closes a task; at final-submission, possibly the project."
        )
    )
    decision_policy = (
        "## Decision\n"
        "REASON, NEXT_ACTION, and OPERATOR_QUESTION are human-facing. Use the "
        "operator's language. State evidence and consequence plainly; ask questions "
        "answerable in one sentence. Omit internal values and template names. "
        "Separate options (`id::label::description`) with semicolons."
        + (
            " Include `research_result` from inspected evidence."
            if _research_target_level is not None
            else ""
        )
        + "\n"
        + decision_footer_instruction(
            "STATUS=done\n"
            "REASON=requested outcome is materially complete\n"
            "NEXT_ACTION=\n"
            "FORWARD_PROGRESS=true\n"
            "PLAN_SIGNAL=continue"
        )
        + "\nPlan change:\n"
        "STATUS=replan_requested\n"
        "PLAN_CHALLENGE=failed assumption\n"
        "AUTHORITY_IMPACT=technical"
        + "\nFor operator choices only, add "
        "`OPERATOR_QUESTION=...` and "
        "`OPERATOR_OPTIONS=a::Use A::What choosing A does; "
        "b::Use B::What choosing B does`.\n"
        + "\nJudge forward_progress toward the operator's goal, which even a sound "
        "repair may leave unchanged.\n"
        + _PLAN_SIGNAL_VOCABULARY
        + "Give Engineer instructions only in next_action; neither read nor edit "
        "checkpoint or context records.\n\n"
    )
    if prose_final_review:
        decision_policy = (
            "Write your final review naturally in the operator's language. No JSON, "
            "fixed fields, named closing lines, or review template is required. "
            "Explain the actual venue recommendation, the evidence behind it, and "
            "any further changes Engineer should make. The host handles control flow "
            "internally and passes your complete feedback to Engineer.\n\n"
        )
        handoff_policy = (
            "The selected venue's acceptance standard governs completion. Continue "
            "the final review and revision loop while acceptance is unclear or "
            "feasible high-impact improvements remain. Speculative future work stays "
            "advisory; meaningful improvement after final review belongs in this task."
        )
    # Other review operations retain their existing minimal control protocol.
    # Keep the requested footer smaller than the compatibility parser. Legacy
    # FRONTIER_*, NEXT_DECISION_POINT, REGRESSION_ENVELOPE,
    # CHECKPOINT_RECOMMENDED, and SESSION_SIGNAL lines remain readable, but the
    # Reviewer is not asked to fill them in. The fields below each feed round
    # settlement, operator routing, research certification, or plan adjudication.
    #
    # Nothing here may vary with the mission. The static preamble is
    # fingerprinted, and a same-role session resumes only when the fingerprint
    # matches; when the objective and the Planner's guidance lived here, every
    # new mission rotated the fingerprint, forced a cold start, and re-sent the
    # full rubric — so those blocks ride in the delta below instead.
    static = (
        EFFECTIVE_TASK_CONTRACT
        + "\n\n"
        + (shell_contract + "\n\n" if shell_contract else "")
        + MODEL_INTEGRITY_BOUNDARY
        + "\n\n"
        + _PRODUCT_ACCEPTANCE_DIRECTIVE
        + "\n\n## Reviewer role\n"
        "`done` means the outcome meets this verification profile. "
        "Check essential uncertainty proportionately. Leave sources, outputs, and "
        "builds unchanged; you may record your judgment with the vertical's command. "
        "Use `continue` for one material gap in scope, `replan_requested` for a wrong target or "
        "scope change, and `blocked` only for external obstacles. Use primary sources "
        "for external claims; community code may ground implementation details. "
        "Stay within this profile; require no future-proofing. "
        "In `explore`/`develop`, require experimental or research feedback. "
        "Negative results, hedging, limitations, and reruns need grounded "
        "consequences; positive and negative claims share one evidence standard.\n\n"
        + RESEARCHER_VOICE + "\n\n"
        + decision_policy
        + ("" if _requires_engineering_audit else _verification_directive())
        + verification_instruction
        + wiki_curator_skill_block
        + direct_memory_edit_block
        + matched_review_skill_block
        + stage_checklist
        + "\n\n"
        + rollback_block
        + "\n\n"
        + surprise_judgment_block
        + venv_skill_block
        + "\n\n## Completion\n"
        + handoff_policy
        + "\n\n"
        + (optimize_banner + "\n\n" if optimize_banner else "")
    )
    # Per-round DELTA — everything that varies with the mission or the round.
    # Fresh Reviewers receive this after the full static rubric every time.
    # The objective, the Planner's guidance, and the record-integrity note its
    # text can call for are necessary context every round; they open the delta
    # so a resumed session still reads the mission before the round's facts.
    delta = (
        (_REEVALUATE_HEADER if resumed else "")
        + objective_block
        + "Planner guidance:\n"
        f"{planner_review_instruction or 'none'}\n\n"
        + audit_integrity_block
        + research_context_block
        + ("\n\n" if research_context_block else "")
        + review_validity_block
        + search_altitude_block
        + f"{checkpoint_block}"
        + f"{escalate_block}"
        + f"{engineer_log_audit_block}"
        + (f"Round: {round_index}/{round_max}\n" if round_max > 0 else f"Round: {round_index}\n")
        + f"Session ID: {session_id or 'none'}\n"
        + f"{shared_context_block}"
        + f"{incremental_review_block}"
        + f"{background_block}"
        + f"Main agent fatal error: {error_text}\n\n"
        + "## Engineer's account of this round\n"
        + engineer_account
        + "\n\n"
        + f"{evidence_block}"
        # OperatorContext is intentionally the final live-facts block: this
        # preserves the static cache prefix and improves steering recency.
        + "Operator messages:\n"
        + operator_text
    )
    objective_context = f"{objective_block}{operator_text}\n{planner_review_instruction or 'none'}"
    owner._last_prompt_block_stats = _prompt_block_stats(
        {
            "static_total": static,
            "delta_total": delta,
            "stage_checklist": stage_checklist,
            "matched_skill": matched_review_skill_block,
            "direct_memory": direct_memory_edit_block,
            "wiki_curator": wiki_curator_skill_block,
            "research_target": verification_instruction,
            "surprise_judgment": surprise_judgment_block,
            "manuscript_review_validity": review_validity_block,
            "research_context": research_context_block,
            "objective_context": objective_context,
            "checkpoint": checkpoint_block,
            "execution_log_audit": engineer_log_audit_block,
            "background": background_block,
            "shared_context": shared_context_block + incremental_review_block,
            "main_summary": main_summary,
            "raw_evidence": evidence_block,
        }
    )
    return static, delta


def assemble_reviewer_prompt(static: str, delta: str) -> str:
    """Form the exact prompt sent to a fresh Reviewer session."""
    return static + delta


__all__ = [
    "COLD_READ",
    "EVALUATE",
    "OPERATIONS",
    "SCIENCE_LOSS_CHECK",
    "assemble_reviewer_prompt",
    "evaluate_request",
    "render_reviewer_prompt",
]
