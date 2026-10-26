"""Apply the person's answer to a question card a required step raised.

``life.waypoint_cards`` raises two kinds of card; ``_apply_operator_answer``
hands them here before its ordinary resume:

- checkpoint (第 N 步完成了): **继续** approves the step's delivered tasks
  (``core.waypoints.approve_step``), closes the card and lets the next step
  start; **先改改** (or written changes) turns the card into that step's
  revision task, which resumes with their words like any answered task and is
  shown to them again when it is delivered.
- dispute (the Planner doubts a step): **keep** leaves the step, **change**
  takes the new words the card offered, **remove** turns the step back into
  an idea card (their words stay on the canvas); a written answer is their
  reading of the step and goes to the Planner as such. The card closes.

The Planner hears every decision through the inbox, and a changed step
through the route's new operator-context revision.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from ..core.mission_view import conversation_is_chinese
from ..core.waypoints import (
    CHANGE_OPTION,
    CHECKPOINT_DECISION,
    CONTINUE_OPTION,
    DISPUTE_DECISION,
    REMOVE_OPTION,
    approve_step,
    read_route,
    read_step_states,
)

log = logging.getLogger(__name__)

_YES = re.compile(r"^\s*(继续|可以|好的?|行|没问题|通过|ok(ay)?|yes|continue|go on|lgtm|looks good)", re.I)
_CHANGE = re.compile(r"改|调整|不对|不行|重做|change|fix|redo|instead", re.I)


def _approves(text: str) -> bool:
    """A written answer to "第 N 步完成了" that says go on and asks for no change."""
    return bool(_YES.match(text or "")) and not _CHANGE.search(text or "")


def _step_label(life_dir: Path, step_id: str, zh: bool) -> tuple[str, str]:
    step = next((row for row in read_route(life_dir) if row.id == step_id), None)
    if step is None:
        return ("这一步" if zh else "this step"), ""
    return step.label(zh), step.text


def _edit_step(life_dir: Path, step_id: str, **change: Any) -> bool:
    """Change the person's step card on their word; retried once on a concurrent write."""
    from . import canvas as store
    from .routes.canvas import sync_route, tell_open_maps

    for _attempt in range(2):
        current = store.read_canvas(life_dir)
        if not any(card["id"] == step_id for card in current["cards"]):
            return False
        try:
            canvas, _card = store.update_card(life_dir, current["revision"], step_id, **change)
        except store.CanvasConflict:
            continue
        tell_open_maps(life_dir, canvas, "card")
        sync_route(life_dir)
        return True
    return False


def apply_waypoint_answer(
    mem: Any, item: Any, answer: str, *, decision_option: str, decision_id: str,
    decision_note: str, manager_reply: str, decided_by: str,
) -> dict[str, Any] | None:
    """Resolve a step's card; ``None`` lets the ordinary answer resume the task."""
    card = item.operator_decision or {}
    kind = card.get("decision_kind")
    if kind not in {CHECKPOINT_DECISION, DISPUTE_DECISION}:
        return None
    life_dir = Path(mem.project_root)
    step_id = str(card.get("waypoint_id") or "")
    option = decision_option.strip().lower()
    typed = option in {"", "custom"}
    zh = conversation_is_chinese(answer, item.pending_question)
    label, words = _step_label(life_dir, step_id, zh)
    if kind == CHECKPOINT_DECISION:
        if not (option == CONTINUE_OPTION or (typed and _approves(answer))):
            # 先改改: this card becomes the step's revision task and resumes with their words.
            mem.backlog.update(item.id, waypoint_id=step_id)
            item.waypoint_id = step_id
            return None
        states = read_step_states(life_dir, mem.backlog.history())
        entry = next((row for row in states if row.step.id == step_id), None)
        approve_step(life_dir, step_id, entry.done_task_ids if entry else ())
        reply = manager_reply if manager_reply and manager_reply.strip() else (
            f"好，{label}通过了，接着往下做。" if zh else f"Good — {label} is approved; the team goes on."
        )
        note = f"The operator approved {label} «{words}»; the next step may start."
        return _close(mem, item, life_dir, option=CONTINUE_OPTION, decision=f"approved {label}",
                      reply=reply, note=note, decision_id=decision_id, decision_note=decision_note,
                      answer=answer, decided_by=decided_by)
    # A doubted step: the person's reading of their own requirement decides.
    if option == REMOVE_OPTION:
        _edit_step(life_dir, step_id, kind="idea")
        reply = f"{label}去掉了；卡片改回了想法，原话还在。" if zh else f"{label} is removed; the card is an idea again."
        note = f"The operator removed {label} «{words}» from the required route."
    elif option == CHANGE_OPTION:
        chosen = next((row for row in card.get("options") or () if isinstance(row, dict) and row.get("id") == CHANGE_OPTION), {})
        new_words = " ".join(str(chosen.get("description") or decision_note or "").split())
        if new_words:
            _edit_step(life_dir, step_id, text=new_words)
        reply = f"{label}改成了“{new_words}”。" if zh else f"{label} now reads “{new_words}”."
        note = f"The operator changed {label} to «{new_words}»."
    else:
        kept = option != "custom" and not typed
        reply = (f"好，{label}照原话保留。" if zh else f"{label} stays as written.") if kept else (
            "收到，按你的意思做这一步。" if zh else "Got it — the step is done your way.")
        note = (f"The operator keeps {label} «{words}» as written; plan it."
                if kept else f"About {label} «{words}», the operator answered: {answer}. "
                "This is their reading of their own requirement; plan the step that way.")
    reply = manager_reply.strip() if manager_reply and manager_reply.strip() else reply
    return _close(mem, item, life_dir, option=option or "custom", decision=note, reply=reply, note=note,
                  decision_id=decision_id, decision_note=decision_note, answer=answer, decided_by=decided_by)


def _close(
    mem: Any, item: Any, life_dir: Path, *, option: str, decision: str, reply: str, note: str,
    decision_id: str, decision_note: str, answer: str, decided_by: str,
) -> dict[str, Any]:
    from ..apps._inbox import queue_inbox_message
    from ..core.event_catalog import EventType
    from ..life.event_log import JsonlEventSink

    question = str(item.pending_question or "")
    closed = mem.backlog.stop_for_operator_decision(
        item.id, note=decision_note or answer, decision_id=decision_id, selected_option=option,
        manager_decision=decision, reply=reply, decided_by=decided_by, status="done",
    )
    if closed is None:
        return {"error": "question is no longer pending", "answered_item_id": item.id}
    try:
        queue_inbox_message(life_dir, f"[OPERATOR ROUTE DECISION] {note}", source="manager.answer")
    except Exception:  # noqa: BLE001 - the route and the backlog already carry the decision
        log.warning("Could not tell the Planner about a required-step answer", exc_info=True)
    chosen = next((str(row.get("label") or "") for row in (item.operator_decision or {}).get("options") or ()
                   if isinstance(row, dict) and row.get("id") == option), "")
    JsonlEventSink(None, life_dir=life_dir).append({
        "type": EventType.LIFE_OPERATOR_QUESTION_ANSWERED, "item_id": item.id, "question": question,
        "manager_decision": decision, **({"option": chosen} if chosen else {}),
        "decision_id": decision_id, "decided_by": decided_by,
        "waypoint_id": str((item.operator_decision or {}).get("waypoint_id") or ""),
    })
    return {
        "answered_item_id": item.id, "answer_intent": True, "resolved": True,
        "application_status": "accepted",
        "resolution_id": str(closed.operator_decision.get("resolution_id") or ""),
        "resume_requested": True, "reply": reply, "manager_decision": decision,
        "item": closed.to_jsonable(),
    }


__all__ = ["apply_waypoint_answer"]
