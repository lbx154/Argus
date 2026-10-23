"""Manager prompt operations and structured context requests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from ...core.model_visible_text import (
    MODEL_INTEGRITY_BOUNDARY,
    sanitize_model_visible_text,
)
from ...core.role_reply import decision_footer_instruction
from ..task_contract import EXPERIMENT_EVIDENCE_CONTRACT, argus_command
from .types import ChecklistMode, RoleName, RolePromptRequest
from .voice import RESEARCHER_VOICE

STAGE_DECISION = "stage_decision"
GROUNDED_VERTICAL_DECISION = "grounded_vertical_decision"
RESEARCH_TARGET = "research_target"
PLAN_PREVIEW = "plan_preview"
SKILL_PLACEMENT_BATCH = "skill_placement_batch"
LIVE_VIEW = "live_view"
PENDING_QUESTION = "pending_question"

_COMPOSABLE_WORKFLOW_ROUTING = (
    "For a composable vertical, use workflow_profile=custom and "
    "workflow_stages=<requested stage names> when no preset "
    "matches exactly. Host adds mandatory companions and keeps canonical order. "
    "Select creation stages only when creating/changing those outputs; existing "
    "inputs still need evidence. Explain requested, added and excluded work. "
    "If requirements conflict with exclusions, clarify before dispatch.\n\n"
)

_RESEARCH_DELIVERABLE_ROUTING = (
    "For a supplied idea or hypothesis requested as a full paper, choose staged "
    "research with direction locked. Locked needs a concrete supplied mechanism "
    "or hypothesis; a field/venue alone (e.g. ICLR + multi-agent communication) is broad. "
    "For proposing, comparing, or reviewing ideas "
    "without implementation, experiments, or a paper, choose direct research: broad "
    "for discovery, locked for one supplied idea. Target level sets quality and "
    "never expands an idea-only request into a paper. Research figures, plots, "
    "diagrams, Figure 1, sections, drafts, and manuscript revisions are `research`. "
    "If only that part is requested and a full campaign excluded, choose `direct`; "
    "produce that part without adding a manuscript, experiments, or literature review. "
    "Set start_stage to `idea` for proposing, comparing, or reviewing ideas or surveys; "
    "`experiment` for bounded empirical studies or implementations with real runs "
    "and no manuscript; `paper` for figures, drafts, sections, or revisions.\n\n"
    "Optional start_stage applies only to workflow_mode=direct: "
    "choose a stage of that vertical, or leave empty for its first stage. Staged "
    "work always begins at its first stage.\n\n"
)

_MIN_PLAN_STEPS = 3
_MAX_PLAN_STEPS = 8

OPERATIONS = frozenset(
    {
        STAGE_DECISION,
        GROUNDED_VERTICAL_DECISION,
        RESEARCH_TARGET,
        PLAN_PREVIEW,
        SKILL_PLACEMENT_BATCH,
        LIVE_VIEW,
        PENDING_QUESTION,
    }
)


def current_date_line(now: datetime | None = None) -> str:
    """One line that anchors the Manager in calendar time.

    Reply prompts are otherwise date-free, so the model dates "the latest"
    work by its training cutoff and presents last year's papers as new.
    """
    moment = (now or datetime.now()).astimezone()
    return (
        f"Current date: {moment:%Y-%m-%d} ({moment:%a}), timezone {moment:%Z}. "
        "Your training knowledge may predate this; do not call anything the "
        "latest or current unless a source checked now says so.\n\n"
    )

MANAGER_RULES = Path(__file__).resolve().parents[2] / "builtin_skills" / "manager"
#: Each rule file, and when the Manager reads it.
_RULES = (
    ("team-handoff", "before deciding to hand work to the team, and when reporting the team's result"),
    ("asking-the-operator", "before asking the operator anything"),
    ("settings-and-control", "when the operator changes a setting, pauses, aborts or steers work, grants something or sets a lasting rule"),
    ("replying", "before your first substantive reply, and before delivering a web page"),
    ("research-and-sources", "when learning, surveying, analysing data, writing, or saving a report"),
    ("workspace-and-view", "when choosing what the right-side view shows"),
)


def build_manager_prompt(message: str, *, workspace: Path | str, state_root: Path | str) -> str:
    """The Manager's one page, sent once per conversation: who it is, where things are, which rule to read when."""
    from ...core.role_config import runner_backend_label

    view = argus_command("argus.tools.manager_live_view", "--workspace", workspace, "--state-dir", state_root)
    rules = "\n".join(f"- {name}: {when}" for name, when in _RULES)
    return (
        f"You are Argus Manager, the operator's one point of contact, in one {runner_backend_label()} "
        "session that keeps this conversation. Answer the operator yourself, with tools when the "
        "answer depends on files, state or the web, and finish and check small work yourself. "
        "When the work is big, hand it to the team: hand_off(to=team) with a ticket. When only the "
        "operator can decide, hand_off(to=operator) with a question and options. For settings, "
        "control, grants and lasting rules, hand_off(to=manager). Otherwise call nothing and reply.\n"
        "Write in the operator's language for a non-technical reader: conclusion first, plain words, short.\n\n"
        f"{current_date_line()}"
        "## Where things are\n"
        f"- Workspace, where project outputs live: {workspace}\n"
        f"- Argus state: {state_root} (CHECKPOINT.md; backlog.jsonl; events.jsonl, the team log)\n"
        f"- Right-side view tool: `{view}`\n\n"
        f"## Rules: read {MANAGER_RULES}/<topic>/SKILL.md before doing what it covers\n"
        f"{rules}\n\n"
        f"Message:\n{message.strip()}"
    )


def build_pending_question_prompt(item: Any, answer: str) -> str:
    question = str(getattr(item, "pending_question", "") or "").strip()
    # The answers the card offers, numbered as the operator was shown them.
    card = getattr(item, "operator_decision", None) or {}
    offered = "".join(
        f"\n{number}. {option.get('label')}"
        for number, option in enumerate(card.get("options") or (), 1)
    )
    return (
        "You are the Manager resolving a question that only the operator can answer "
        "for an existing mission. Interpret the response in the context of why work paused. "
        f"\n\n{RESEARCHER_VOICE}\n\n"
        "REPLY must use the operator's language and plain language: one question, "
        "why it is needed, and what happens next; never return a bare internal status. "
        "End your reply with these lines; DECISION and REPLY may run over "
        "several lines:\n"
        "IS_ANSWER=true|false\n"
        "RESOLVED=true|false\n"
        "DECISION=<explicit role-clean instruction for Planner/Engineer>\n"
        "REPLY=<one concise clarification question, when not resolved>\n"
        "Set IS_ANSWER=false when the message is unrelated "
        "chat, status, configuration, or control rather than an attempted answer; "
        "in that case also set RESOLVED=false and leave DECISION and REPLY empty. "
        "Set RESOLVED=true only when the response supplies enough authority or "
        "information for the team to continue. DECISION must then be an explicit, "
        "role-clean instruction for Planner/Engineer. The latest operator response "
        "is binding wherever it conflicts with inherited mission details. When it "
        "changes a method, scope, tool, or requirement for completion, explicitly name "
        "the inherited constraint that is superseded instead of trying to satisfy "
        "both. If it is unrelated or insufficient, set resolved=false, keep "
        "decision empty, and use reply to ask one concise clarification question.\n\n"
        f"Blocked item id: {item.id}\n"
        f"Blocked mission title: {item.title}\n"
        f"Blocked mission objective:\n{item.objective}\n\n"
        f"Reviewer question:\n{question}\n\n"
        + (f"Answers offered, by number:{offered}\n\n" if offered else "")
        + f"Operator response:\n{answer.strip()}"
    )


def build_research_target_prompt(
    task: str,
    *,
    supported_levels: Sequence[str] = (
        "exploratory",
        "publishable",
        "doctoral",
    ),
) -> str:
    """Ask the Manager for a success bar when research routing is fixed."""
    return (
        "You are the Manager guiding research toward a stated goal. The operator has "
        "already fixed the vertical; do not revisit routing. Decide only the "
        "requested research success bar from the task below. Judge what outcome "
        "the operator requires, not the problem's apparent difficulty.\n\n"
        "- exploratory: a well-defined investigation, known result, finite computation, "
        "domain-specific local verification, or decision-relevant negative finding "
        "may satisfy the task. Honest reporting alone is not scientific value.\n"
        "- publishable: success requires a correctness-verified result with a "
        "nontrivial technical core, verified originality, "
        "formal/causal grounding, and field-level significance.\n"
        "- doctoral: success explicitly requires doctoral/thesis-level original "
        "research. Reports, literature review, finite checks, and local verification "
        "alone are not success.\n"
        "Do not choose exploratory merely because it makes an honest negative report "
        "easy to close. A request to develop a submission-quality paper, find a "
        "publishable method, or continue autonomous research requires at least the "
        "publishable bar unless the operator explicitly asks only for a limited "
        "investigation.\n\n"
        "Task:\n"
        f"{(task or '').strip()}\n\n"
        "Allowed levels for this vertical: "
        f"{', '.join(supported_levels)}.\n\n"
        + RESEARCHER_VOICE + "\n\n"
        + decision_footer_instruction(
            "RESEARCH_TARGET_LEVEL=publishable\n"
            "RATIONALE=brief reason tied to the requested success bar"
        )
        + "\n"
    )


def build_plan_prompt(
    objective: str,
    *,
    role_banner: str = "",
    allow_repository_inspection: bool = False,
) -> str:
    """Render the prompt asking the model for a preview plan."""
    obj = (objective or "").strip()
    first_rule = (
        "1. Inspect the repository with tools as needed to ground the plan, but "
        "do NOT implement the fix or modify production files. The tool "
        "working directory is already the repository root; use focused "
        "relative-path reads/searches and never search the filesystem root."
        if allow_repository_inspection
        else (
            "1. Do NOT do the work. Do NOT run any shell command, inspect the "
            "repo, or write code. This is an outline only."
        )
    )
    prompt = (
        "You are the planning front-end of an autonomous coding/research agent. "
        "The operator wants to PREVIEW a plan BEFORE any work begins. "
        f"Produce an ordered plan ({_MIN_PLAN_STEPS}-{_MAX_PLAN_STEPS} steps) of how "
        "you WOULD approach the objective.\n\n"
        "Hard rules:\n"
        f"{first_rule}\n"
        "2. Each step is one concrete action with an imperative title.\n"
        f"3. Keep it to {_MIN_PLAN_STEPS}-{_MAX_PLAN_STEPS} steps, but include enough detail "
        "for the operator to understand the approach.\n"
        "\n"
        "## Objective\n"
        f"{obj}\n\n"
        "## Your answer\n"
        f"{RESEARCHER_VOICE}\n\n"
        "Answer as a numbered list, one step per line, each as "
        "`<imperative title> — <what/why>`:\n"
        "1. <imperative title> — <what/why>\n"
        "2. ...\n"
        "Then, if anything is worth flagging, one line:\n"
        "NOTES=<caveat or assumption>; <another>\n"
    )
    banner = str(role_banner or "").strip()
    if not banner:
        return prompt
    return f"## Active vertical role\n{banner}\n\n{prompt}"


def build_prompt_rewrite_prompt(
    draft: str,
    *,
    role_banner: str = "",
    project_context: str = "",
    operator_context: str = "",
) -> str:
    """Render the prompt asking the Manager to rewrite an operator's draft.

    Operators type short, under-specified requests ("优化一下 kernel", "写个
    paper"). Handing that verbatim to the team wastes rounds on guessing what
    was meant. The Manager — which already owns front-door judgment — restates
    the request as a brief the team can act on.

    The Manager is expected to use its own judgment about what the task needs,
    including metrics, thresholds, baselines and scope limits the operator never
    mentioned. The constraint is not "never propose" — it is "never decide
    silently": anything the operator did not ask for is raised back to them as a
    concrete, answerable question instead of being baked into the rewrite.
    """
    body = (draft or "").strip()
    context = (project_context or "").strip()
    prompt = (
        "You are the Manager (front door) of an autonomous engineering/research "
        "team. The operator typed a short request and asked you to REWRITE it "
        "into a brief your team can execute, BEFORE anything is dispatched.\n\n"
        "Your job is to make the request ACTIONABLE. A bare restatement of the "
        "operator's words is a failed rewrite: the team would have to guess the "
        "same things the operator left implicit. Organise the request so it "
        "states, in the operator's own terms:\n"
        "- the outcome wanted and the concrete work it requires;\n"
        "- the subject/scope, grounded in the real project below when given "
        "(actual paths, files, components) rather than left abstract;\n"
        "- what would count as done, derived from what the operator asked for.\n\n"
        "Use your own judgment about what this task actually needs. If it needs "
        "a success metric, a threshold, a baseline, a scope limit, a deadline or "
        "a tool that the operator never mentioned, you SHOULD raise it — ask the "
        "operator in `questions`, with your suggested value, so they can simply "
        "approve it. Proposing is expected; deciding for them is not.\n\n"
        "Hard rules:\n"
        "1. Do NOT do the work, run commands, inspect the repo, or write code. "
        "This is a rewrite only.\n"
        "2. The REWRITE itself carries only what the operator asked for (plus "
        "their implicit intent made explicit). Anything you are proposing rather "
        "than restating — a number, threshold, baseline, deadline, tool or "
        "narrowed scope they never expressed — belongs in `questions`, not in "
        "`rewritten`. The operator must never discover a requirement they did "
        "not agree to.\n"
        "3. Preserve every concrete detail the operator DID give (names, "
        "numbers, paths, hardware, file names) verbatim.\n"
        "4. Write the rewrite AND the questions in the SAME language the "
        "operator used.\n"
        "5. Return the draft essentially unchanged ONLY when it is already a "
        "well-formed brief. 'Vague but short' is not a reason to leave it "
        "alone — that is exactly what you are here to fix.\n"
        "6. If the core goal itself is genuinely unknowable (you cannot tell "
        "what outcome is wanted at all), still produce the best faithful brief "
        "you can and put the unknowns in `questions`.\n"
        "7. Keep `questions` worth answering: each one should change how the "
        "work is done. Prefer a concrete proposal the operator can accept or "
        'correct ("cover the public API, target ~80% line coverage — ok?") '
        'over an open prompt ("what coverage do you want?").\n\n'
        "Keep it compact — a short paragraph or a few bullet lines a teammate "
        "can act on, not a specification document.\n\n"
        f"{RESEARCHER_VOICE}\n\n"
    )
    if context:
        prompt += f"## Project context (advisory, may be empty)\n{context}\n\n"
    prompt += (
        "## Operator's draft\n"
        f"{body}\n\n"
        "## Your answer\n"
        "State your answer on these lines. REWRITTEN may run over several "
        "lines; the two lists are separated by semicolons:\n"
        "REWRITTEN=<the rewritten request>\n"
        "CHANGES=<what you made explicit and why>; <another>\n"
        "QUESTIONS=<what you propose or could not infer, kept out of the "
        "rewrite until the operator answers>; <another>\n"
    )
    if operator_context.strip():
        prompt += "\n" + operator_context.strip()
    banner = str(role_banner or "").strip()
    if not banner:
        return prompt
    return f"## Active vertical role\n{banner}\n\n{prompt}"


def build_skill_placements_prompt(
    *,
    skills: Sequence[dict[str, str]],
    candidate_verticals: Sequence[str],
) -> str:
    candidates = sorted(
        value for value in candidate_verticals if isinstance(value, str) and value
    )
    return (
        "You are the Manager tidying several project-distilled skills after a "
        "mission. Classify every row independently.\n\n"
        "Placement policy: global = cross-domain; vertical = only one named "
        "candidate vertical; stay = project-specific or uncertain. Prefer stay.\n\n"
        f"Candidate verticals: {', '.join(candidates) or '(none)'}\n\n"
        "Skills to classify (input data):\n"
        f"{json.dumps(list(skills), ensure_ascii=False)}\n\n"
        f"{RESEARCHER_VOICE}\n\n"
        "State one block per input skill, in this shape, exactly one row per "
        "input:\n"
        "CANDIDATE_ID=<exact input candidate_id>\n"
        "PLACEMENT=global|vertical|stay\n"
        "VERTICAL=<name from the candidate list, or empty>\n"
        "WHY=<clear explanation>"
    )


def assemble_manager_prompt(
    prompt: str,
    *,
    role_banner: str = "",
    role_skill_block: str = "",
    role_context: str = "",
) -> str:
    """Wrap the decision prompt in the Manager's vertical policy and Skills.

    The vertical banner holds for every decision of the campaign, so it leads
    and extends the cacheable prefix; the vertical's live facts
    (``role_context``) and the Skill block, whose recalled bodies follow the
    decision at hand, close the prompt.
    """
    context = sanitize_model_visible_text(str(role_banner or "").strip())
    with_vertical = (
        f"## Active vertical Manager skill\n{context}\n\n{prompt}"
        if context
        else prompt
    )
    live = sanitize_model_visible_text(str(role_context or "").strip())
    with_live = f"{with_vertical}\n\n{live}" if live else with_vertical
    with_skill = (
        f"{with_live}\n\n{role_skill_block}"
        if role_skill_block
        else with_live
    )
    return MODEL_INTEGRITY_BOUNDARY + "\n\n" + with_skill


def _advisory_planner(planner_verdict: Any) -> str:
    if planner_verdict is None:
        return "(none)"
    for attr in ("reason", "headline"):
        value = getattr(planner_verdict, attr, None)
        if value:
            return str(value)
    if isinstance(planner_verdict, dict):
        return str(
            planner_verdict.get("reason") or planner_verdict.get("headline") or planner_verdict
        )
    return str(planner_verdict)


def build_stage_decision_prompt(
    *,
    current_stage: str,
    next_stage: str,
    later_stages: Sequence[str] = (),
    earlier_stages: Sequence[str],
    checklist_md: str,
    review: Any,
    planner_verdict: Any = None,
    open_ended: bool = False,
    continuous_objective: str = "",
    allow_rollback: bool = True,
    allow_early_completion: bool = False,
) -> str:
    """Build the Manager's stage-transition prompt with a concrete target example."""
    # Normalize a stray string to one stage instead of iterating over its characters.
    stages = [earlier_stages] if isinstance(earlier_stages, str) else list(earlier_stages)
    earlier = ", ".join(f"`{stage}`" for stage in stages if str(stage).strip()) or (
        "(none — already first)"
    )
    advance_target = f"`{next_stage}`" if next_stage else "(none — already the final stage)"
    legal_advance = (
        ", ".join(f"`{stage}`" for stage in later_stages if str(stage).strip())
        or advance_target
    )
    status = str(getattr(review, "status", "") or "")
    reason = str(getattr(review, "reason", "") or "")
    review_source = str(getattr(review, "review_source", "reviewer") or "reviewer").strip()
    source_instructions = ""
    if review_source == "engineer_self_review":
        source_instructions = (
            "The Engineer used the self-review allowed for small tasks. No "
            "Reviewer assessment is therefore expected. The waiver "
            "itself is not evidence: inspect CHECKPOINT.md and the project outputs "
            "against every applicable requirement for the current stage. You "
            "may advance when that evidence genuinely satisfies the stage; hold "
            "otherwise. A final-submission or explicitly independent-review task "
            "still requires a real Reviewer assessment.\n"
        )

    open_ended_block = ""
    if open_ended:
        if allow_rollback:
            open_ended_block = (
                "## Open-ended research objective\n"
                "This is an open-ended campaign. Completing the final-stage checkpoint "
                "does not complete the operator objective by itself. If the original "
                "objective remains unresolved and the Planner identifies further "
                "high-impact work that belongs to an earlier stage, roll back to the "
                "earliest stage needed for that work. Hold only when no legal work can "
                "run yet; do not mark the campaign complete merely because a report or "
                "review file exists.\n\n"
            )
        else:
            open_ended_block = (
                "## Open-ended research objective\n"
                "This is an open-ended forward-only campaign. Completing a checkpoint "
                "does not complete the operator objective by itself. Keep the current "
                "stage and schedule any remaining repair work there; never move to an "
                "earlier stage.\n\n"
            )
    objective_block = (
        "## Operator objective\n"
        f"{continuous_objective.strip()}\n\n"
        if continuous_objective.strip()
        else ""
    )
    objective_block += EXPERIMENT_EVIDENCE_CONTRACT + "\n\n"

    harness_control = getattr(review, "harness_control", None)
    mission_scope_change = bool(
        isinstance(harness_control, dict)
        and harness_control.get("mission_scope_change_required") is True
    )
    mission_scope_block = ""
    if mission_scope_change:
        rollback_sentence = (
            "roll back only when earlier-stage evidence is genuinely broken; "
            if allow_rollback
            else "never move this sequence of stages backward; "
        )
        mission_scope_block = (
            "## Mission-scope arbitration\n"
            "The Reviewer found that the proposed next work cannot legally run "
            "as another Engineer round within the current mission's agreed scope. "
            "Reviewer advice is not authorization. Hold the current stage when "
            "the repair belongs in this stage so Planner can replace the mission; "
            f"{rollback_sentence}"
            "Advance only when independent review finds every current-stage requirement met. "
            "Do not rewrite implementation details yourself.\n\n"
        )

    rollback_rule = (
        "- move=rollback only when evidence from an earlier stage is broken; name the "
        "earliest affected stage.\n"
        if allow_rollback
        else (
            "- This vertical is forward-only. Keep the current stage and schedule "
            "repairs there; never roll back.\n"
        )
    )
    rollback_targets = (
        f"Rollback targets (earlier stages): {earlier}\n\n"
        if allow_rollback
        else "Rollback targets: none (forward-only vertical)\n\n"
    )
    completion_rule = (
        "- to=done when the independently reviewed direct objective is fully "
        "satisfied. Do not advance merely because later stages exist; they are "
        "outside this direct request. Open-ended campaigns never complete "
        "automatically.\n"
        if allow_early_completion
        else (
            "- to=done only at the final stage of a finite objective. Open-ended "
            "campaigns never complete automatically.\n"
        )
    )
    return (
        "Decide how the work should proceed from the evidence below, and record it "
        "with hand_off. Reviewer and Planner advise; you decide: to=team with "
        "move=advance (on to a later stage), move=hold (stay and keep working) or "
        "move=rollback (back to an earlier stage), or to=done (finish the objective). "
        "You only judge here: you change no files and run no builds or tests, since "
        "the work and its checks are the team's.\n\n"
        "## Your decision\n"
        "- Advance only when concrete evidence supports the stage's requirements.\n"
        "- Hold when work remains or evidence is unclear, including when Reviewer asks "
        "for replanning inside this stage.\n"
        + rollback_rule
        + completion_rule
        + "- A weak proxy or one failed attempt is not completion. Do not repeat the "
        "Reviewer's checks without a contradiction. When unsure, hold.\n"
        "- Judge the science, not the bookkeeping. A missing or outdated note for the "
        "next stage, review file, template detail, or file marker is repair work for the "
        "next round; it is never by itself a reason to hold a stage whose work "
        "the Reviewer found sound.\n"
        "- Evidence scale is part of the science. The stated requirements may appear met by a "
        "handful of items or one model; when the objective's claim is broader than what "
        "was measured, hold for the wider evidence rather than advance on a narrow win.\n\n"
        + RESEARCHER_VOICE + "\n\n"
        # The objective and the stage's requirements hold across the
        # campaign's decisions; the wait and scope arbitration, the evidence
        # and the Planner note belong to this one decision, so they close the
        # prompt and the provider can reuse the cached prefix.
        f"{objective_block}"
        f"{open_ended_block}"
        f"Current stage: `{current_stage}`\n"
        f"Advance targets (later stages): {legal_advance}\n"
        f"{rollback_targets}"
        "## What the current stage requires\n"
        f"{checklist_md}\n\n"
        f"{mission_scope_block}"
        "## Latest completion evidence\n"
        f"source: {review_source}\n"
        f"status: {status}\n"
        f"reason: {reason}\n"
        f"{source_instructions}\n"
        "## Planner note (advisory)\n"
        f"{_advisory_planner(planner_verdict)}\n\n"
    )


def stage_decision_request(
    project_root: Path | str,
    *,
    stage: str,
) -> RolePromptRequest:
    return RolePromptRequest(
        role=RoleName.MANAGER,
        operation=STAGE_DECISION,
        project_root=project_root,
        stage=stage,
        checklist_mode=ChecklistMode.STAGE,
        # Preserve the existing stage-decision framing, which asks for the
        # Planner view of the current checklist.
        checklist_role=RoleName.PLANNER,
        # The vertical's live facts (research notes) close the prompt.
        include_role_context=True,
    )


__all__ = [
    "GROUNDED_VERTICAL_DECISION",
    "LIVE_VIEW",
    "OPERATIONS",
    "PENDING_QUESTION",
    "PLAN_PREVIEW",
    "RESEARCH_TARGET",
    "SKILL_PLACEMENT_BATCH",
    "STAGE_DECISION",
    "assemble_manager_prompt",
    "build_manager_prompt",
    "build_pending_question_prompt",
    "build_plan_prompt",
    "build_prompt_rewrite_prompt",
    "build_research_target_prompt",
    "build_skill_placements_prompt",
    "build_stage_decision_prompt",
    "stage_decision_request",
]
