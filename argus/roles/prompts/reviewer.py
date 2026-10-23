"""Reviewer prompt operations, structured context, and complete prompt body."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from ...core.model_visible_text import sanitize_model_visible_text
from ...core.workspace_snapshot import WorkspaceSnapshots
from ..task_contract import native_shell_summary
from .rules import rules_list
from .types import ChecklistMode, RoleName, RolePromptRequest

EVALUATE = "evaluate"
SCIENCE_LOSS_CHECK = "science_loss_check"
COLD_READ = "cold_read"

OPERATIONS = frozenset({EVALUATE, SCIENCE_LOSS_CHECK, COLD_READ})

_REEVALUATE_HEADER = (
    "## NEW ROUND — RE-EVALUATE INDEPENDENTLY (resumed reviewer)\n"
    "Reach a fresh judgment on this round's changes; read re-review before reusing "
    "your previous conclusion.\n\n"
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
    from ...core.research_contract import resolve_research_target_level
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
    search_altitude_block = sanitize_model_visible_text(prompt_context.search_altitude)
    direct_workflow = (
        workflow_mode if workflow_mode is not None else resolve_workflow_mode(_proot)
    ) == "direct"
    # Vertical-owned policy arrives through the prompt catalog; this module
    # contributes only role-wide review behavior.
    optimize_banner = prompt_context.role_banner
    if prompt_context.requires_independent_review and not _requires_engineering_audit:
        optimize_banner = ""
    if prose_final_review:
        mode = "Final paper review"
    elif direct_workflow:
        mode = "Direct work"
    else:
        mode = "Staged work"
    deciding = f"before you hand off; this review follows its '{mode}' section"
    target_block = ""
    _research_target_level = resolve_research_target_level(_proot)
    if (
        _research_target_level is not None
        or prompt_context.verification_stage_profiles
    ):
        # `research_target_level` says what finishing the PROJECT means; the
        # verification profile says what THIS round has to show.
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
        target_block = f"## This round's bar\n{policy_line(_policy)}."
        if _research_target_level is None:
            target_block += (
                " The active vertical owns when verification becomes final; judge "
                "this task at its current stage."
            )
        else:
            target_block += f" Project research target: `{_research_target_level}`."
            if not prose_final_review:
                deciding += " and its 'Research target' section"
    stage_checklist = "" if direct_workflow else prompt_context.stage_checklist
    stage_order = prompt_context.stage_order
    stage_line = ""
    if not direct_workflow:
        stage_idx = stage_order.index(stage) if stage in stage_order else 0
        earlier = ", ".join(stage_order[:stage_idx]) or "(none)"
        stage_line = f"Current stage: `{stage}`. Earlier stages: {earlier}."
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
    # The Reviewer's verdict closes work, so it needs the operator's stated bar,
    # not only the mission text, which describes the increment.
    from ...core.project_contract import contract_briefing, load_contract_for_cwd

    _contract_block = contract_briefing(
        load_contract_for_cwd(_proot),
        authoritative_objective=original_objective,
    )
    if _contract_block:
        objective_block += _contract_block + "\n\n"
    previous_findings = str(previous_findings or "").strip()
    shared_context_block = sanitize_model_visible_text(_format_engineer_shared_context(
        skill_used=active_skill_id,
        prev_review_summary="" if previous_findings else prev_review_summary,
    ))
    if round_index > 1 and prev_review_summary.strip():
        shared_context_block += "Your earlier judgment follows; read re-review before reusing it.\n\n"
    previous_findings_block = (
        sanitize_model_visible_text(previous_findings) + "\n\n"
        if previous_findings else ""
    )
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
    engineer_log_audit_block = sanitize_model_visible_text(_engineer_log_audit_block(
        engineer_log_path,
        engineer_call_id=engineer_call_id,
        round_index=round_index,
    ))
    from ...core.role_config import runner_backend_label
    from ...wiki.context import render_project_principles

    rules = rules_list("reviewer", (
        ("argus-reviewer-role.md", "before you judge: what the review weighs and how to write the Engineer's next step"),
        ("checking-the-work/SKILL.md", "before you accept or reject evidence, rerun a check, or test a product"),
        ("deciding/SKILL.md", deciding),
        ("re-review/SKILL.md", "when an earlier judgment of yours is shown, or the Engineer disputes a finding"),
        ("record-integrity/SKILL.md", "when the task involves an audit log, a ledger, or an append-only or frozen record"),
    ))
    page = (
        f"You are the Argus Reviewer, in one {runner_backend_label()} session. Judge "
        "independently whether the Engineer's work below holds. Read and run checks in the "
        "working directory, which holds the work; change no source, output or build file.\n\n"
        "## Handing off\n"
        "End your turn with hand_off. Its summary is what the operator reads: only this "
        "work's result, in their language and plain words (e.g. \"char_count is delivered; "
        "its default count, ignored whitespace and package export were checked\"). Its note "
        "carries the complete review, evidence and next steps for the next holder.\n\n"
        + rules
    )
    # Nothing here may vary with the mission: the static preamble is
    # fingerprinted, and a same-role session resumes only when it matches. What
    # holds for every mission of the role comes first, then the stage, and last
    # the recalled Skill block, so fresh Reviewers share the longest prefix.
    principles_block = render_project_principles(_proot)
    static = "\n\n".join(
        block for block in (
            page,
            native_shell_summary(),
            target_block,
            stage_checklist.strip(),
            stage_line,
            optimize_banner.strip(),
            matched_review_skill_block.strip(),
            sanitize_model_visible_text(principles_block) if principles_block else "",
        ) if block
    ) + "\n\n"
    # Per-round DELTA: everything that varies with the mission or the round.
    delta = (
        (_REEVALUATE_HEADER if resumed else "")
        + objective_block
        + "Planner guidance:\n"
        f"{planner_review_instruction or 'none'}\n\n"
        + research_context_block
        + ("\n\n" if research_context_block else "")
        + review_validity_block
        + search_altitude_block
        + engineer_log_audit_block
        + (f"Round: {round_index}/{round_max}\n" if round_max > 0 else f"Round: {round_index}\n")
        + f"Session ID: {session_id or 'none'}\n"
        + shared_context_block
        + previous_findings_block
        + background_block
        + change_block
        + evidence_block
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
            "research_target": target_block,
            "manuscript_review_validity": review_validity_block,
            "research_context": research_context_block,
            "objective_context": objective_context,
            "execution_log_audit": engineer_log_audit_block,
            "background": background_block,
            "shared_context": shared_context_block + previous_findings_block,
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
