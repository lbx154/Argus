"""Park a stage question the Manager put to the operator on the task it came from."""
from __future__ import annotations

from functools import partial
from pathlib import Path

from ..core.handoff import HandOff


def park_stage_question_on_task(root: Path | str, handoff: HandOff) -> bool:
    """Put the Manager's ask on a stage question to the operator as its task's question card.

    The task the stage question came from parks on it, as a task the team
    paused on a question does: the operator's answer goes through ``answer``,
    lands in that task's objective and acceptance, and resumes the same task
    for the Engineer and Reviewer. The stage question is then theirs, not the
    Manager's, so it leaves PIPELINE_STATE. False when there is no such task
    or it no longer waits.
    """
    from ..core.pipeline_state import read_pipeline_state, write_pipeline_state
    from ..life.event_log import JsonlEventSink
    from ..life.memory import LifeMemory
    from ..life.supervisor.pending_notify import ask_operator
    from ._session_ops import manager_pipeline_lock

    root = Path(root)
    question = handoff.question.strip()
    if handoff.to != "operator" or not question:
        return False
    with manager_pipeline_lock(root):
        state = read_pipeline_state(root)
        task_id = str(state.get("stage_question_task") or "")
        if not task_id or not state.get("stage_question"):
            return False
        backlog = LifeMemory.open(root).backlog
        item = next((row for row in backlog.active() if row.id == task_id), None)
        if item is None or item.status in {"done", "failed", "skipped", "superseded"}:
            return False
        ask_operator(
            root, item, question, reason=handoff.summary or question, layer="manager",
            save=partial(backlog.update, item.id, last_error=handoff.summary or question),
            emit=JsonlEventSink(None, life_dir=root).append,
            options=handoff.options,
        )
        state.pop("stage_question", None)
        state.pop("stage_question_task", None)
        write_pipeline_state(root, state)
    return True
