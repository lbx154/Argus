"""Reviewer prompt operations, structured context, and complete prompt body."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...core.model_visible_text import (
    MODEL_INTEGRITY_BOUNDARY,
    REVIEW_EVIDENCE_RULE,
    sanitize_model_visible_text,
)
from ...core.role_handoff import OPERATOR_CHOICES_RULE
from ...core.workspace_snapshot import WorkspaceSnapshots
from ..task_contract import (
    EFFECTIVE_TASK_CONTRACT,
    EXPERIMENT_EVIDENCE_CONTRACT,
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
    "The role, standards, and decision rules still apply.\n\n"
)

# Acceptance settles effort, never truth. Once one round accepted a 6% score on
# a benchmark where the model publishes ~80%, this boundary forbade every later
# round from looking again, and the campaign reproduced its own broken baseline
# for ninety reviews without once returning `incorrect`.
_INCREMENTAL_REREVIEW_BOUNDARY = (
    "## Incremental re-review boundary\n"
    "The previous Reviewer judgment below is not fact. If current evidence shows "
    "your earlier judgment was wrong, say so and correct it. For final paper "
    "review, the separate selected-venue acceptance threshold still governs "
    "`done`: fixing the last edit alone is insufficient.\n"
    "A prior finding that the Engineer's account disputes with its own labeled, "
    "reproducible measurement is not settled: conflicting values for the same "
    "checkable fact are a dispute to resolve. Before repeating the request, "
    "explain what is wrong with the Engineer's output or withdraw the finding, "
    "and cite a number only when the label comes from the same output. Before "
    "`done`, independently check every value changed in response to a contested "
    "finding; the Engineer's report that it recalculated them is not that check.\n\n"
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
    include_role_context: bool = False,
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
        include_role_context=include_role_context,
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
        "failure nor causation without this mission's mutation command. "
        "Check which data split each reported metric was computed on.\n\n"
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
        "Preserve useful corrections, but do not hand off `→ done` when the required "
        "historical integrity is irrecoverable.\n\n"
    )


# The Reviewer starts from the diff instead of re-reading the workspace to find
# what moved. Past these bounds a file is listed by name and line counts only.
_DIFF_FILE_LINES = 60
_DIFF_TOTAL_LINES = 300
_DIFF_LINE_CHARS = 240
_DIFF_LISTED_FILES = 80


def _change_block(snapshots: WorkspaceSnapshots | None) -> str:
    """What the task changed in the workspace, from its baseline to this candidate."""
    changes = snapshots.changes("baseline", "candidate") if snapshots else None
    if changes is None:
        return ""
    block = (
        "\n## Workspace changes under review\n"
        "The workspace from the task's start to now, leaving out ignored files, "
        "`.argus`, environments, caches and nested repositories.\n"
    )
    if not changes:
        return block + "Nothing in it has changed.\n"
    budget, chosen = _DIFF_TOTAL_LINES, []
    for change in changes:
        lines = (change.added or 0) + (change.deleted or 0)
        if lines and budget > 0:
            chosen.append(change.path)
            budget -= min(lines, _DIFF_FILE_LINES)
    excerpt, shown, budget = [], {}, _DIFF_TOTAL_LINES
    for path, hunk in zip(chosen, snapshots.hunks("baseline", "candidate", chosen)):
        room = min(_DIFF_FILE_LINES, budget)
        if room > 0:
            excerpt += [f"--- {path}", *(line[:_DIFF_LINE_CHARS] for line in hunk[:room])]
            shown[path] = len(hunk) <= room
            budget -= min(room, len(hunk))
    rows = []
    for change in changes[:_DIFF_LISTED_FILES]:
        if change.sizes is not None:
            before, after = (
                "absent" if size is None else f"{size:,} bytes" for size in change.sizes
            )
            rows.append(f"- {change.path}: large file, {before} -> {after}, not diffed")
        elif change.added is None:
            rows.append(f"- {change.path}: binary")
        else:
            note = (
                "" if shown.get(change.path) or not change.added + change.deleted
                else ", excerpt cut" if change.path in shown else ", not excerpted"
            )
            rows.append(f"- {change.path}: +{change.added} -{change.deleted}{note}")
    rest = changes[_DIFF_LISTED_FILES:]
    if rest:
        rows.append(
            f"- and {len(rest)} more files: +{sum(change.added or 0 for change in rest)} "
            f"-{sum(change.deleted or 0 for change in rest)}"
        )
    block += (
        f"{len(changes)} files changed, +{sum(change.added or 0 for change in changes)} "
        f"-{sum(change.deleted or 0 for change in changes)}:\n" + "\n".join(rows) + "\n"
        "Start the review from this diff and open a file only to check a specific "
        "claim; do not re-explore the workspace. Read a file marked cut or not "
        "excerpted yourself if you need it.\n"
    )
    if excerpt:
        block += "\nDiff excerpt:\n" + "\n".join(excerpt) + "\n"
    return sanitize_model_visible_text(block)


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
    engineer_log_path: str = "",
    engineer_call_id: str = "",
    preselected_skill_block: str | None = None,
    working_dir: str | Path | None = None,
    vertical_state_root: str | Path | None = None,
    vertical: str = "",
    workflow_mode: str | None = None,
    snapshots: WorkspaceSnapshots | None = None,
    previous_findings: str = "",
) -> tuple[str, str]:
    """Render the complete Reviewer prompt as ``(static_preamble, round_delta)``.

    ``previous_findings`` is the host's carry-over of this Reviewer's own
    earlier judgments. When present it is the single copy of those verdicts,
    replacing the shorter ``prev_review_summary`` line beside it.
    """
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
            # The vertical's live facts (the research notes)
            # belong to the round delta, never to the fingerprinted static.
            include_role_context=operation == EVALUATE,
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
    research_context_block = prompt_context.role_context
    direct_workflow = (
        workflow_mode if workflow_mode is not None else resolve_workflow_mode(_proot)
    ) == "direct"
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
            scope=scope_normalized,
            stage=_stage,
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
            "identical at every profile. Judge directly and explain in the review. "
        )
        if _research_target_level is not None and not prose_final_review:
            verification_instruction += (
                "If the direction cannot reach the target, hand the work to the "
                "Planner: `→ planner`.\n"
                "Supply the research assessment through the review action tool "
                "using evidence you inspected; never append a data block to your reply. A survey is "
                "`literature_review` with `novelty_status` `known` or "
                "`not_applicable`. Use one listed value per field so the record "
                "stays comparable across campaigns; the tool argument summarizes your "
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
        "and produces no work; if the answer could change the claim or route, hand "
        "it to the Planner: `→ planner`.\n"
        if _research_target_level is not None
        else ""
    )
    # Live search-altitude facts (NO verdict) so the reviewer can SEE the
    # floor history when judging the round — i.e. distinguish "this
    # round advanced a declared structural line" from "Nth single-knob
    # nibble at a floor that has not moved in N attempts". Empty for
    # verticals that do not surface it.
    search_altitude_block = sanitize_model_visible_text(
        prompt_context.search_altitude
    )
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
            "rollback is necessary, hand the work to the Manager, who owns rollback: "
            "`→ manager`, with the earliest stage and evidence. Never edit "
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
    previous_findings = str(previous_findings or "").strip()
    shared_context_block = _format_engineer_shared_context(
        skill_used=active_skill_id,
        prev_review_summary="" if previous_findings else prev_review_summary,
    )
    shared_context_block = sanitize_model_visible_text(shared_context_block)
    incremental_review_block = ""
    if round_index > 1 and prev_review_summary.strip():
        incremental_review_block = _INCREMENTAL_REREVIEW_BOUNDARY
    previous_findings_block = (
        sanitize_model_visible_text(previous_findings) + "\n\n"
        if previous_findings else ""
    )
    # Prefer direct runtime and verifier evidence over the Engineer's summary
    # when callers provide it. Omit the block when no such evidence exists.
    evidence_block = (
        "\nRaw verification evidence:\n"
        + sanitize_model_visible_text(raw_evidence.rstrip())
        + "\n"
        if raw_evidence.strip()
        else ""
    )
    # Given only the Engineer's summary, reviewers missed evaluation on the
    # training split; given the artifacts, they caught it. The diff comes first,
    # then the recorded commands, and the account last.
    change_block = _change_block(snapshots)
    background_block = (
        "\n" + sanitize_model_visible_text(background_context.strip()) + "\n"
        if background_context.strip()
        else ""
    )
    # The shared Markdown checkpoint is the live handoff. ``prior_checkpoint``
    # remains accepted for callers that have not migrated to the file path.
    _ = prior_checkpoint
    _ = checkpoint_path
    checkpoint_block = ""
    # The Engineer's own account is primary. Keep the execution log as a
    # one-line fallback pointer for a concrete contradiction instead of asking
    # every Reviewer to reconstruct the round from grep recipes. Empty path
    # (memory backend / tests / unresolvable life_dir) omits the pointer.
    engineer_log_audit_block = _engineer_log_audit_block(
        engineer_log_path,
        engineer_call_id=engineer_call_id,
        round_index=round_index,
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
        "Hand off `→ done` when a direct task meets its requirements and decisive "
        "check, `→ planner` only to change the plan, and `→ engineer` naming the next "
        "repair for a material gap. Leave optional hardening advisory."
        if direct_workflow
        else (
            "`→ done` needs sufficient evidence, not exhaustive proof or every file. "
            "Only claim-essential evidence gaps warrant `→ engineer`; optional "
            "evidence and minor weaknesses are advice. A timeout or failed attempt "
            "proves no impossibility; a missed threshold describes only that run. "
            "Root-cause, dominant-stage, bottleneck, or replacement claims need code-path "
            "evidence plus profiling, timing, or a controlled comparison. Integrity is "
            "required, not scientific value. Ask only for operator-owned authority/facts. "
            "`→ done` closes a task; final-submission may close the project."
        )
    )
    decision_policy = (
        "## Submit your review\n"
        "Express the complete review naturally in the operator's language and submit "
        "it with the one native action, submit_review; its first sentence is what the "
        "operator reads: what was delivered and what you verified, in plain words. "
        "End it with one line naming "
        "who holds the work next: `→ done`, `→ engineer`, `→ planner`, `→ manager` or "
        "`→ operator: <one question>`. " + OPERATOR_CHOICES_RULE + " No JSON or other "
        "closing line is read. "
        "Before accepting, check each "
        "requirement and symptom the task (this increment) states against the "
        "deliverable; tests the Engineer wrote show only its reading of them. "
        "Submit all evidence/next steps unchanged in the review argument. "
        "Chat cannot replace the tool. A plan challenge names the failed assumption "
        "and an alternative; a commitment of the Manager or the operator goes to them. "
        "Give Engineer instructions in the review; neither "
        "read nor edit checkpoint or context records.\n\n"
    )
    if prose_final_review:
        decision_policy += (
            "Explain your actual current venue recommendation, not a hoped-for future "
            "rating. Hand off `→ done` only when no required repairs remain; "
            "otherwise `→ engineer`, even if the current rating is weak accept "
            "or better. Scientific repairs stay in this final review task.\n\n"
        )
        handoff_policy = (
            "The selected venue's acceptance standard governs completion. Continue "
            "the final review and revision loop while acceptance is unclear or "
            "feasible high-impact improvements remain. Speculative future work stays "
            "advisory; meaningful improvement after final review belongs in this task."
        )
    # Nothing here may vary with the mission. The static preamble is
    # fingerprinted, and a same-role session resumes only when the fingerprint
    # matches; when the objective and the Planner's guidance lived here, every
    # new mission rotated the fingerprint, forced a cold start, and re-sent the
    # full rubric — so those blocks ride in the delta below instead.
    #
    # What repeated lessons of this vertical have settled into. Bounded and
    # present only once the vertical has compiled principles; it changes only
    # when they are recompiled, so it belongs to the static text.
    from ...wiki.context import render_project_principles

    principles_block = render_project_principles(_proot)
    # Within the static text, what holds for every mission of the role comes
    # first, then what holds for the stage, and last the recalled Skill block,
    # which follows the mission's task: a fresh Reviewer on a new mission then
    # shares the longest possible prefix with the previous one.
    static = (
        EFFECTIVE_TASK_CONTRACT
        + "\n\n"
        + (shell_contract + "\n\n" if shell_contract else "")
        + MODEL_INTEGRITY_BOUNDARY
        + "\n\n"
        + _PRODUCT_ACCEPTANCE_DIRECTIVE
        + "\n\n## Reviewer role\n"
        "`→ done` meets this profile; check relevant uncertainty. "
        "Leave source/output/build files unchanged; use the vertical command for judgments. "
        "Use `→ engineer` for one material gap in scope, `→ planner` for a wrong target or "
        "scope change, and `→ manager` or `→ operator` only for obstacles the team cannot "
        "remove. Use primary sources "
        "for external claims; community code may ground implementation details. "
        "Stay within this profile; require no future-proofing. "
        "In `explore`/`develop`, require experimental or research feedback. "
        "Ground negatives, hedging, limitations and reruns in consequences; "
        "all claims share one evidence standard. "
        + REVIEW_EVIDENCE_RULE
        + "\n\n"
        + EXPERIMENT_EVIDENCE_CONTRACT + "\n\n"
        + "Check actual input changes, not rerun reasons; producer logs cannot replace external observation.\n\n"
        + RESEARCHER_VOICE + "\n\n"
        + decision_policy
        + ("" if _requires_engineering_audit else _verification_directive())
        + verification_instruction
        + wiki_curator_skill_block
        + direct_memory_edit_block
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
        + matched_review_skill_block
        + ("\n\n" + sanitize_model_visible_text(principles_block) if principles_block else "")
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
        + f"{engineer_log_audit_block}"
        + (f"Round: {round_index}/{round_max}\n" if round_max > 0 else f"Round: {round_index}\n")
        + f"Session ID: {session_id or 'none'}\n"
        + f"{shared_context_block}"
        + f"{incremental_review_block}"
        + previous_findings_block
        + f"{background_block}"
        + change_block
        + f"{evidence_block}"
        + f"Main agent fatal error: {error_text}\n\n"
        + "## Engineer's account of this round\n"
        + engineer_account
        + "\n\n"
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
            "shared_context": (
                shared_context_block + incremental_review_block + previous_findings_block
            ),
            "main_summary": main_summary,
            "raw_evidence": evidence_block,
            "workspace_changes": change_block,
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
