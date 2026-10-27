"""Question cards a required step (必经步骤) raises with the person.

Two kinds, both parked backlog rows answered like any operator question:

- a **checkpoint**: a step marked "到这步先停下来给我看" delivered its work, so
  the project parks on "第 N 步完成了：…  继续 / 先改改" until the person
  answers (``core.waypoints`` holds every other task meanwhile);
- a **dispute**: the Planner believes a step is wrong or impossible and asks
  whether to keep, change or remove it. The reading of the person's
  requirement is theirs, so a doubted step is asked about, never skipped.

The answers are applied in ``webapi.waypoint_answers``.
"""

from __future__ import annotations

import logging
from functools import partial
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from ..core.mission_view import uses_cjk
from ..core.waypoints import (
    CHANGE_OPTION,
    CHECKPOINT_DECISION,
    CHECKPOINT_TAG,
    CONTINUE_OPTION,
    DISPUTE_DECISION,
    KEEP_OPTION,
    REMOVE_OPTION,
    REVISE_OPTION,
    StepState,
    due_checkpoints,
    read_step_states,
)

log = logging.getLogger(__name__)


def _words(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def checkpoint_question(entry: StepState) -> tuple[str, str, list[dict[str, str]]]:
    """The card's title, question and two answers, in the language of the person's words."""
    step = entry.step
    zh = uses_cjk(step.text) or not step.text
    words = _words(step.text, 120)
    if zh:
        label = step.label(True)
        return (
            f"{label}完成了：{_words(step.text, 40)}",
            f"{label}完成了：{words}。看一下结果，可以就继续；想改的话写下要改什么。",
            [
                {"id": CONTINUE_OPTION, "label": "继续", "description": "这一步可以了，开始后面的步骤"},
                {"id": REVISE_OPTION, "label": "先改改", "description": "写下要改的地方，改完再给你看"},
            ],
        )
    label = step.label(False).capitalize()
    return (
        f"{label} is done: {_words(step.text, 40)}",
        f"{label} is done: {words}. Look at the result: continue if it is right, or say what to change.",
        [
            {"id": CONTINUE_OPTION, "label": "Continue", "description": "This step is right; go on"},
            {"id": REVISE_OPTION, "label": "Change it first", "description": "Say what to change; you see it again after"},
        ],
    )


def raise_due_checkpoints(backlog: Any, emit: Callable[[dict[str, Any]], Any]) -> int:
    """Park the project on a question for every checkpoint step whose work was just delivered.

    One card per step: a step with a waiting card is not asked again. Returns
    how many cards were raised.
    """
    from .memory import BacklogItem
    from .supervisor.pending_notify import ask_operator

    life_dir = Path(backlog.path).parent
    rows = backlog.history()
    titles = {row.id: row.title for row in rows}
    states = read_step_states(life_dir, rows)
    raised = 0
    for entry in due_checkpoints(states):
        title, question, options = checkpoint_question(entry)
        step = entry.step
        zh = uses_cjk(step.text) or not step.text
        why = (
            f"你给{step.label(True)}设了“到这步先停下来给我看”，它的结果已经交付。"
            if zh else f"You asked the team to pause after {step.label(False)}; its work is delivered."
        )
        item = backlog.add(BacklogItem.new(
            title=title,
            objective=(
                f"Revise the operator's required {step.label(False)} «{step.text}» as their "
                f"feedback below asks. Its delivered work is in tasks {', '.join(entry.done_task_ids)}; "
                "change only what the feedback asks and keep the rest."
            ),
            tags=[CHECKPOINT_TAG, "operator_decision", "scope:bounded"],
            iterate=False,
            acceptance_check=(
                f"The result follows the operator's feedback on {step.label(False)} and still "
                f"fulfils «{step.text}»."
            ),
        ))
        ask_operator(
            life_dir, item, question, reason=why, layer="planner",
            save=partial(backlog.update, item.id), emit=emit, options=options,
            evidence=[
                {"label": titles.get(task_id) or task_id, "summary": "这一步交付的结果" if zh else "Delivered for this step"}
                for task_id in entry.done_task_ids[:5]
            ],
            decision_kind=CHECKPOINT_DECISION, waypoint_id=step.id,
        )
        raised += 1
    return raised


def dispute_card(
    route: Iterable[StepState], step_id: str, question: str, options: Iterable[Mapping[str, Any]],
    *, asks_only: bool,
) -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """The answers and card fields for the Planner's question about a step.

    The person always gets keep / remove, and change when the Planner proposed
    new words (in that option's description); the Planner's own wording of
    those answers is kept. ``asks_only`` says the card's row was made only to
    ask, so answering closes it; otherwise the row is a task the Planner was
    replanning, which resumes with the answer.
    """
    entry = next((row for row in route if row.step.id == step_id), None)
    zh = uses_cjk(question) or (entry is not None and uses_cjk(entry.step.text))
    offered = {str(option.get("id") or "").strip().lower(): dict(option) for option in options}
    defaults = {
        KEEP_OPTION: ("保留这一步", "照原话做") if zh else ("Keep the step", "Do it as written"),
        CHANGE_OPTION: ("改成这样", "") if zh else ("Change it to this", ""),
        REMOVE_OPTION: ("去掉这一步", "卡片改回想法，原话还在") if zh else ("Remove the step", "The card becomes an idea again"),
    }
    out: list[dict[str, Any]] = []
    for option_id, (label, description) in defaults.items():
        mine = offered.get(option_id)
        if option_id == CHANGE_OPTION and not (mine and str(mine.get("description") or "").strip()):
            continue
        out.append({
            "id": option_id,
            "label": str((mine or {}).get("label") or label),
            "description": str((mine or {}).get("description") or description),
        })
    fields = {"decision_kind": DISPUTE_DECISION, "waypoint_id": step_id, "asks_only": asks_only}
    if entry is not None:
        fields["waypoint_number"] = entry.step.number
    return tuple(out), fields


__all__ = ["checkpoint_question", "dispute_card", "raise_due_checkpoints"]
