"""Manager prompt operations and structured context requests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..task_contract import argus_command
from .voice import RESEARCHER_VOICE

STAGE_DECISION = "stage_decision"
GROUNDED_VERTICAL_DECISION = "grounded_vertical_decision"
PLAN_PREVIEW = "plan_preview"
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
        PLAN_PREVIEW,
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
    ("asking-the-operator", "before asking the operator anything, and when a paused task's question is listed with the message"),
    ("workflow-setup", "when the operator chooses to set up a reusable workflow, and until that setup is settled"),
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


def build_prompt_completion_prompt(
    draft: str, anchor: str, *, project_context: str = "", recent_turns: str = "",
) -> str:
    """Ask for the likely rest of the line the operator is typing.

    Fixed rules first and the draft last, so the provider reuses the cached
    prefix across the many short calls one message produces.
    """
    prompt = (
        "The operator has paused while typing a message to the Manager of their research team. "
        "Predict the words they will most likely type next to finish the sentence they are writing; "
        "the composer shows them as gray text the operator may accept.\n\n"
        "Rules:\n"
        "1. Finish only the current sentence, on one line: at most about 20 words or 30 Chinese characters.\n"
        "2. Write in the draft's language and tone. Do not answer, rewrite or correct the draft, "
        "and add no request the operator has not made.\n"
        "3. Use names and paths from the context only when the draft is clearly heading to them; "
        "never invent numbers, names or paths.\n"
        '4. If the sentence already reads as finished, or you cannot tell where it is going, reply {"text": ""}.\n\n'
        'Reply with JSON only: {"text": "..."}. The text starts with the exact characters under '
        '"Starts with" (the end of the draft), then continues it; finish a cut-off word without a space.\n\n'
    )
    if project_context.strip():
        prompt += f"## Project context\n{project_context.strip()}\n\n"
    if recent_turns.strip():
        prompt += f"## Latest conversation\n{recent_turns.strip()}\n\n"
    return (prompt + "## Draft (a JSON string)\n" + json.dumps(draft, ensure_ascii=False)
            + "\n\n## Starts with\n" + json.dumps(anchor, ensure_ascii=False))


__all__ = [
    "GROUNDED_VERTICAL_DECISION",
    "LIVE_VIEW",
    "OPERATIONS",
    "PENDING_QUESTION",
    "PLAN_PREVIEW",
    "STAGE_DECISION",
    "build_manager_prompt",
    "build_plan_prompt",
    "build_prompt_completion_prompt",
    "build_prompt_rewrite_prompt",
]
