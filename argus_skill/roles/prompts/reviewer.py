"""Reviewer prompt operations, structured context, and complete prompt body."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .types import ChecklistMode, RoleName, RolePromptRequest

EVALUATE = "evaluate"

OPERATIONS = frozenset({EVALUATE})

_REEVALUATE_HEADER = (
    "## NEW ROUND — RE-EVALUATE INDEPENDENTLY (resumed reviewer)\n"
    "You are resuming your OWN thread ONLY to avoid re-sending the static rubric "
    "— NOT to defer to your previous verdict. The role, rubric, and decision "
    "rules from earlier in this thread still bind, but THIS round's artifacts "
    "below are the ONLY evidence: re-verify against them from scratch. Your prior "
    "verdict is not a prior and must never be rubber-stamped; judge this round on "
    "its own artifacts, summary, and log audit.\n\n"
)

_MAX_SHARED_CTX_CHARS = 100_000_000


def evaluate_request(
    project_root: Path | str,
    *,
    scope: str = "",
    stage: str | None = None,
    vertical: str | None = None,
    checklist_mode: ChecklistMode = ChecklistMode.AUTO,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.REVIEWER,
        operation=EVALUATE,
        project_root=project_root,
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

    return any(
        is_initialized_wiki(p / "wiki") for p in autors.iterdir() if p.is_dir()
    )


def _load_wiki_curator_skill_if_present(
    working_dir: str | Path | None = None,
) -> str | None:
    """Compatibility helper returning the compact wiki-curator contract."""
    if not _project_has_wiki(working_dir):
        return None
    return (
        "knowledge curator: directly edit durable concepts, principles, facts, "
        "hypotheses, relationships, and conflicts grounded in real evidence; "
        "do not return page operations in the verdict."
    )


def _direct_memory_edit_block(
    skill_store: Any,
    working_dir: str | Path | None,
) -> str:
    project_store = getattr(skill_store, "project", None)
    skill_dir_value = getattr(project_store, "skills_dir", None)
    if skill_dir_value is None:
        skill_dir_value = getattr(skill_store, "skills_dir", None)
    skill_dir = (
        Path(skill_dir_value).expanduser().resolve()
        if skill_dir_value is not None
        else None
    )
    project_root = (
        Path(working_dir).expanduser().resolve()
        if working_dir
        else Path.cwd().resolve()
    )
    wiki_roots: list[Path] = []
    try:
        from ...wiki.auto_hooks import discover_wikis

        wiki_roots = [path.resolve() for path in discover_wikis(project_root)]
    except Exception:  # noqa: BLE001
        wiki_roots = []
    if skill_dir is None and not wiki_roots:
        return ""
    skill_line = str(skill_dir) if skill_dir is not None else "none"
    skill_block = (
        "## Direct reusable-memory maintenance\n"
        "You are an executable Reviewer with file and shell tools. If this round "
        "contains durable, reusable learning, edit the project memory directly "
        "BEFORE your final verdict. Do not describe a proposed edit in final JSON.\n"
        f"Project skill directory (project layer only): {skill_line}\n"
        "Skill rules: inspect the existing Markdown first; create or edit only in "
        "the project skill directory; preserve valid frontmatter; increment "
        "`version` on an update; never modify a skill with `protected: true`; never "
        "write the shared/global skill layer.\n"
        "If there is no durable reusable procedure, make no Skill edit.\n\n"
    )
    if not wiki_roots:
        return skill_block
    from ...wiki.context import render_knowledge_wiki_block

    return skill_block + render_knowledge_wiki_block(
        project_root,
        role="Reviewer",
    )


def _format_academic_paper_review_skill_block(*, include: bool) -> str:
    if not include:
        return ""
    return (
        "## Near-complete paper review\n"
        "Be a skeptical program-committee reviewer: require a clear contribution, "
        "credible comparisons, sufficient evidence/statistics, accurate citations, "
        "readable writing, and clean figures/layout. `done` requires the applicable "
        "final checklist with no critical blocker; do not reward polish without "
        "substantive evidence. Rebuild the manuscript and inspect the generated "
        "artifact: reject undefined citations, bibliography warnings, significant "
        "overfull boxes or clipped pages, and missing PDF title/author metadata. "
        "Render the relevant pages when layout matters.\n\n"
    )


def _verification_directive() -> str:
    """Compact trust-first verification stance."""
    return (
        "Trust consistent shown results. Re-open raw material only for a missing, "
        "stale, contradictory, or implausible material fact; otherwise judge the "
        "work and its next step. An empty git diff proves nothing for an untracked "
        "or outside-repository artifact: check tracking first, then use direct "
        "content, hashes, or another scoped observation.\n\n"
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
    """Reviewer prompt section for auditing the engineer's execution log."""
    path = (engineer_log_path or "").strip()
    if not path:
        return ""
    call_id = (engineer_call_id or "").strip()
    if compact:
        scope = (
            f"current engineer call id `{call_id}`"
            if call_id
            else "the current engineer round"
        )
        return (
            "## Engineer execution log (on-demand)\n"
            f"Log: `{path}`; scope: {scope}. Do not read or grep it routinely. "
            "Previously certified process evidence remains valid. Inspect this log "
            "only for a concrete contradiction, implausible result, missing material "
            "provenance, or suspected shortcut; otherwise spend the review judging "
            "the result and next research decision.\n\n"
        )
    if call_id:

        def shell_quote(value: str) -> str:
            return "'" + value.replace("'", "'\"'\"'") + "'"

        current_call_rows = (
            f"{shell_quote(sys.executable)} -I -m "
            "argus_skill.tools.event_log_query "
            f"--log {shell_quote(path)} --call-id {shell_quote(call_id)}"
        )
        query_block = (
            f"Current engineer call id: `{call_id}`. Scope every audit command "
            "to this id so prior rounds and this Reviewer's own prompt cannot "
            "pollute the evidence. The query parses top-level JSON fields and "
            "reads rolled logs in chronological order:\n"
            f"    {current_call_rows}\n"
        )
        log_row_description = (
            "The call-scoped raw `agent.io.*` rows record the commands, tool "
            "results, and assistant messages produced by this invocation."
        )
    else:
        query_block = (
            "No exact call id is available. Do not scan the whole project history "
            "unless a concrete concern cannot be resolved from current artifacts.\n"
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
            "burn the round re-deriving an honest number. Inspect the log ONLY when "
            "the engineer pasted NO RESULT line, the number is implausible / "
            "self-contradictory, or the score jumped suspiciously. Otherwise "
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
        f"{log_row_description}\n"
        f"{query_block}\n"
        "Result-traceability (does the final artifact match the checklist?) tells "
        "you the OUTCOME is real. This log tells you the PROCESS was honest — the "
        "two are different, and an artifact can match the checklist while the "
        "process may still contradict the claim.\n\n"
        f"{when_clause}\n"
        "Choose any further inspection yourself from the concrete concern and the "
        "actual event fields; do not classify the process by a preset keyword list. "
        "If the process matches the claim, judge the result as usual. This audit "
        "supplements result traceability and never changes frozen measurements.\n\n"
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
        if len(prev) > _MAX_SHARED_CTX_CHARS:
            prev = prev[:_MAX_SHARED_CTX_CHARS].rstrip() + "..."
        indented = "\n".join("    " + line for line in prev.splitlines())
        parts.append("- previous_review_summary:\n" + indented)
    return "\n".join(parts) + "\n\n"


def render_reviewer_prompt(
    owner: Any,
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
) -> tuple[str, str]:
    """Render the complete Reviewer prompt as ``(static_preamble, round_delta)``."""
    from ...core.project import resolve_project_root
    from ...core.research_contract import resolve_research_target_level
    from ...engineer.checkpoint import shared_checkpoint_instructions
    from ...skills.vertical_select import _persisted_vertical
    from ...verticals.research.stages import CANONICAL_STAGE_ORDER
    from ..task_contract import EFFECTIVE_TASK_CONTRACT
    from .registry import resolve_role_prompt

    error_text = main_error or "none"
    # Role-mission matcher (same primitive engineer/planner use). It
    # surfaces ADAPTIVE reviewer skills (stage-specific review playbooks)
    # plus cross-role engineer references on top of the fixed
    # role/handoff/academic blocks above. The three fixed reviewer skills
    # are excluded by ReviewerMission so the matcher never re-injects what
    # is already hard-wired into this prompt.
    _proot = resolve_project_root(working_dir)
    scope_normalized = (scope or "").strip().lower().replace("-", "_")
    prompt_context = resolve_role_prompt(
        evaluate_request(_proot, scope=scope_normalized)
    )
    _persisted = _persisted_vertical(_proot)
    persisted_prompt_context = (
        resolve_role_prompt(
            evaluate_request(
                _proot,
                vertical=_persisted,
                checklist_mode=ChecklistMode.NONE,
            )
        )
        if _persisted is not None
        else None
    )
    _requires_engineering_audit = bool(
        persisted_prompt_context is not None
        and persisted_prompt_context.requires_independent_review
    )
    matched_review_skill_block = ""
    if preselected_skill_block is not None:
        if preselected_skill_block.strip():
            matched_review_skill_block = (
                "Preselected mission skill context from the single matcher pass "
                "(apply what is relevant; follow any on-demand read instruction "
                "inside):\n"
                f"{preselected_skill_block.strip()}\n\n"
            )
    elif owner.skill_store is not None:
        from ...skills.venue_profiles import venue_excluded_skill_files

        review_match = owner.mission.match(
            objective,
            extra_exclude=venue_excluded_skill_files(_proot),
        )
        if review_match.block:
            matched_review_skill_block = (
                "Matched reviewer skill(s) for this objective "
                "(read first; apply the relevant one(s)):\n"
                f"{review_match.block}\n\n"
            )

    stage = prompt_context.stage
    _measured = (
        not _requires_engineering_audit
        and os.environ.get("ARGUS_SKILL_MEASURED_MODE", "").strip().lower()
        in ("1", "true", "yes", "on")
    )
    # Vertical-native prompt framing: resolve the active vertical and let it
    # supply the top-of-prompt role banner. The rollback / final-submission
    # framing below applies ONLY to a paper vertical (completion_gate ==
    # "full_paper"); for any other vertical (e.g. speedrun) those blocks are
    # suppressed and the vertical's banner is prepended so the reviewer judges
    # only that vertical's metric instead of paper-pipeline artifacts.
    _full_paper = prompt_context.full_paper
    optimize_banner = prompt_context.role_banner
    if (
        prompt_context.requires_independent_review
        and not _requires_engineering_audit
    ):
        optimize_banner = ""
    research_target_instruction = ""
    _research_target_level = resolve_research_target_level(_proot)
    if _research_target_level is not None:
        research_target_instruction = (
            f"Project target: `{_research_target_level}`. Judge correctness, novelty, "
            "significance, fidelity, evidence, and limitations directly. Explain the "
            "judgment in `reason`; do not encode it in extra fields. For project-level "
            "`publishable` or `doctoral` completion, require a verified original result "
            "at the requested significance. If the current direction cannot meet that "
            "bar, return `replan_requested`.\n\n"
        )
    # Live search-altitude facts (NO verdict) so the reviewer can SEE the
    # floor history when judging forward_progress — i.e. distinguish "this
    # round advanced a declared structural line" from "Nth single-knob
    # nibble at a floor that has not moved in N attempts". Empty for
    # verticals that do not surface it.
    search_altitude_block = prompt_context.search_altitude
    # Structured scope only. The planner threads scope=final_submission as
    # a backlog tag all the way here; we no longer sniff the objective
    # prose for "scope: final_submission" markers. Normalize the same way
    # the planner does (lower + hyphen→underscore) so callers that pass
    # "final-submission" still match.
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
    else:
        stage_checklist = prompt_context.stage_checklist

    # Academic peer-review benchmark skill: advisory rubric for reviewing
    # a near-complete manuscript. Gate it on the structured stage/scope
    # signal — final_submission, or the paper-writing stages (review /
    # submission) — instead of keyword-sniffing the objective/evidence
    # for tokens like "main.pdf". `draft` is excluded so mid-production
    # drafting isn't held to final peer-review standards prematurely.
    paper_review_skill_block = _format_academic_paper_review_skill_block(
        include=(
            is_final_submission
            or (_full_paper and stage in {"review", "submission"})
        ),
    )
    wiki_curator_text = _load_wiki_curator_skill_if_present(working_dir)
    wiki_curator_skill_block = (
        "## Wiki curator (fixed when a wiki exists)\n"
        f"{wiki_curator_text}\n\n"
        if wiki_curator_text
        else ""
    )
    direct_memory_edit_block = (
        _direct_memory_edit_block(owner.skill_store, working_dir)
        if owner.memory_maintenance_enabled
        else ""
    )

    venv_skill_block = (
        "## Dependency rule\n"
        "A missing project package is repairable: tell the Engineer to install "
        "it with `./.venv/bin/pip`; never modify the Argus framework venv."
    )

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
        "## Upstream defects\n"
        f"Current stage: `{stage}`. Earlier stages: {earlier_stages}.\n"
        "If earlier-stage evidence is broken and this mission cannot repair it "
        "within its own scope, return `replan_requested` (never `continue`) and "
        "name the earliest broken stage and concrete evidence in `reason`; the "
        "Manager owns rollback. "
        "Never edit `research/PIPELINE_STATE.json`."
    )
    operator_text = (
        "\n".join(f"- {line}" for line in operator_messages)
        if operator_messages
        else "- none"
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
            "it. If nothing else can proceed, return `continue` and state what "
            "evidence the next round should wait for.\n"
        )
    # ``prior_checkpoint`` is accepted only for source compatibility with
    # older callers. The live handoff is the ordinary Markdown file that the
    # Engineer already edited and the Reviewer must now edit directly.
    _ = prior_checkpoint
    checkpoint_block = shared_checkpoint_instructions(
        Path(checkpoint_path) if checkpoint_path else None,
        role="reviewer",
    )
    if checkpoint_block:
        checkpoint_block += "\n\n"
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
        compact=not bool((main_error or "").strip()),
    )
    # Final-submission completion contract. This block replaces the
    # retired hardcoded EMNLP validators: instead of the supervisor
    # running ``validate_full_paper_readiness`` and friends, the reviewer
    # is the single source of truth for whether the *whole project* is
    # ready to submit. It only fires for final_submission missions.
    final_submission_block = ""
    if is_final_submission:
        final_submission_block = (
            "## Final paper review\n"
            "Read the current manuscript, rendered PDF, and claim-critical sources "
            "as an independent venue reviewer. Use `done` only when the research "
            "objective and selected venue bar are genuinely met; otherwise return "
            "`continue` with the few highest-leverage scientific or writing changes. "
            "Do not require or manufacture an assurance memo, reviewer-question "
            "bundle, or other certification packet.\n\n"
        )
    if not _full_paper:
        # non-paper vertical: no paper stages to roll back to, and no
        # final-submission certification — judge only the vertical's metric.
        rollback_block = ""
        final_submission_block = ""
    # Byte-stable static policy; every fresh Reviewer receives it in full.
    static = (
        optimize_banner
        + research_target_instruction
        + EFFECTIVE_TASK_CONTRACT
        + "\n\n## Reviewer role\n"
        "Judge the objective against real evidence and its checklist. Bounded "
        "work may finish before the project; final-submission work may not. Use "
        "`done` for verified completion, `continue` for agent-fixable gaps, and "
        "`blocked` only for operator/external dependencies.\n\n"
        + (
            ""
            if _requires_engineering_audit
            else _verification_directive()
        )
        + "## Output protocol\n"
        "Reason and use tools normally, and write your review however is "
        "clearest. End the final message with these lines; only they are read, "
        "and REASON/NEXT_ACTION may run over several lines:\n"
        "STATUS=done|continue|blocked|replan_requested\n"
        "REASON=<the verdict rationale>\n"
        "NEXT_ACTION=<the Engineer instruction; empty for done>\n"
        "OPERATOR_QUESTION=<operator-only blocker, or none>\n"
        "Edit CHECKPOINT.md first as instructed.\n\n"
        + paper_review_skill_block
        + wiki_curator_skill_block
        + direct_memory_edit_block
        + matched_review_skill_block
        + stage_checklist
        + "\n\n"
        + final_submission_block
        + rollback_block
        + "\n\n"
        + venv_skill_block
        + "\n\n## Final handoff fields\n"
        "Return only STATUS, REASON, NEXT_ACTION and OPERATOR_QUESTION. "
        "`reason` is the only verdict rationale; `next_action` is the only Engineer "
        "instruction and is empty for `done`.\n"
        "- Put measured surprises, open questions, and alternative directions "
        "in CHECKPOINT.md once, not in extra fields.\n"
        "- Every valid measured result must identify the strongest supported "
        "finding in `reason`. Preserve clean negative, null, "
        "boundary, and diagnostic evidence, but integrity is a hard constraint, "
        "not scientific value by itself. Do not automatically turn an honest result "
        "into a paper or project completion. First audit implementation adequacy, "
        "construct fidelity, and plausible repairs. An agent-designed weak proxy is "
        "not evidence about the claimed online agent or system. "
        "Recommend publication work only when the result supports a standalone, "
        "venue-relevant thesis beyond 'we tried and it failed'; otherwise return "
        "`replan_requested`. "
        "There is no fixed retry count: judge further engineering by the diagnosed "
        "cause, expected information gain, and remaining resources.\n"
        "- `operator_question` is only for an operator-only blocker.\n\n"
        "Decision rules:\n"
        "- `done` requires concrete evidence and exact adherence to material "
        "operator constraints. A generic acknowledgment is never enough.\n"
        "- Default to `continue` whenever the agent's claims are not backed by "
        "shown/checkable evidence; once sufficient evidence is present, do not "
        "burn another round re-running it.\n"
        "- On `continue`, name the missing outcome/evidence and the specific "
        "NEXT work or unexplored direction; leave implementation freedom unless "
        "a deterministic failure identifies the repair.\n"
        "- `continue` is ONLY for a repair that remains inside the current "
        "mission objective, acceptance check, non-goals, stage, and resource "
        "contract. If the next work needs a new/separate/scoped mission, a "
        "replacement plan, or any change to those boundaries, return "
        "`replan_requested` instead and cite the relevant files. Reviewer reports the "
        "defect but never authorizes scope expansion.\n"
        "- `blocked` is only for credentials, inaccessible resources, or a "
        "decision/specification only the operator can provide.\n"
        "- New measured evidence or a measured failed mechanism can be forward "
        "progress; setup, bookkeeping, repeated re-scoring, and near-identical "
        "unproductive tweaks are not. A smoke run proves wiring, not final "
        "evidence. Do not declare a method dead from a misconfigured run.\n"
        "- Final-submission `done` means you independently judge the whole project "
        "ready; bounded scope uses only its objective and relevant stage evidence.\n\n"
        + objective_block
        + "Operator messages:\n"
        f"{operator_text}\n\n"
        "Planner guidance:\n"
        f"{planner_review_instruction or 'none'}\n\n"
    )
    # Per-round DELTA — everything that changes round to round. Fresh
    # Reviewers receive this after the full static rubric every time.
    delta = (
        (_REEVALUATE_HEADER if resumed else "")
        + search_altitude_block
        + f"{checkpoint_block}"
        + f"{escalate_block}"
        + f"{engineer_log_audit_block}"
        + (
            f"Round: {round_index}/{round_max}\n"
            if round_max > 0
            else f"Round: {round_index}\n"
        )
        + f"Session ID: {session_id or 'none'}\n"
        + f"{shared_context_block}"
        + f"{background_block}"
        + f"Main agent fatal error: {error_text}\n\n"
        + "Main agent last summary:\n"
        + f"{main_summary}\n\n"
        + f"{evidence_block}"
    )
    objective_context = (
        f"{objective_block}{operator_text}\n"
        f"{planner_review_instruction or 'none'}"
    )
    owner._last_prompt_block_stats = _prompt_block_stats(
        {
            "static_total": static,
            "delta_total": delta,
            "stage_checklist": stage_checklist,
            "matched_skill": matched_review_skill_block,
            "direct_memory": direct_memory_edit_block,
            "wiki_curator": wiki_curator_skill_block,
            "paper_review": paper_review_skill_block,
            "research_target": research_target_instruction,
            "final_submission": final_submission_block,
            "objective_context": objective_context,
            "checkpoint": checkpoint_block,
            "execution_log_audit": engineer_log_audit_block,
            "background": background_block,
            "shared_context": shared_context_block,
            "main_summary": main_summary,
            "raw_evidence": evidence_block,
        }
    )
    return static, delta


def assemble_reviewer_prompt(static: str, delta: str) -> str:
    """Form the exact prompt sent to a fresh Reviewer session."""
    return static + delta


__all__ = [
    "EVALUATE",
    "OPERATIONS",
    "assemble_reviewer_prompt",
    "evaluate_request",
    "render_reviewer_prompt",
]
