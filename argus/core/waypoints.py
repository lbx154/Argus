"""必经步骤 (waypoints): steps a person laid on the canvas that Argus must walk.

A step is a card of kind ``step`` in the project's canvas record
(``<life_dir>/canvas.json``, written by ``webapi.canvas``). A chain of 先后
(``order``) links from one step to another puts them in order, also when it
passes through cards that are not steps (an idea a step was turned back into
keeps the steps around it in order); numbered steps are the chained ones,
①②③ in the order the links give, and a step no 先后 chain joins to another
step may come at any time. A step may carry ``checkpoint``: the
team finishes it, then waits for the person's OK before the next step.

What binds the team:

- The route (the steps, their order and words) is one standing operator
  directive (:data:`ROUTE_SOURCE`), so every role reads it and any change bumps
  the operator-context revision, which makes the Planner plan again
  (:func:`sync_route_directive`).
- A task that works on a step carries its ``waypoint_id``. A task tied to a
  later step is not claimed until every earlier step is complete, and while a
  checkpoint waits for the person nothing else starts (:func:`held_item_ids`).
- The project is not done while a step is open (:func:`open_steps_blocker`).

A step's status is read from the tasks tied to it and the approvals the
person gave at checkpoints (``<life_dir>/waypoints.json``); nothing here calls
a model. Rows are read by duck typing (``id``, ``status``, ``waypoint_id``,
``pending_question``, ``operator_decision``), so this kernel module never
imports the backlog.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .atomic_replace import write_atomic

log = logging.getLogger(__name__)

CANVAS_FILENAME = "canvas.json"
STATE_FILENAME = "waypoints.json"
STEP_KIND = "step"
ROUTE_SOURCE = "operator.canvas.route"
#: Decision kinds of the two question cards a step can raise.
CHECKPOINT_DECISION = "waypoint_checkpoint"
DISPUTE_DECISION = "waypoint_dispute"
CHECKPOINT_TAG = "waypoint_checkpoint"
#: Option ids of the checkpoint card and of a disputed step's card.
CONTINUE_OPTION = "continue"
REVISE_OPTION = "revise"
KEEP_OPTION = "keep"
CHANGE_OPTION = "change"
REMOVE_OPTION = "remove"

STATUSES = ("todo", "running", "review", "done", "question")
_TERMINAL = frozenset({"done", "failed", "aborted", "skipped", "superseded"})
_STATE_LOCK = threading.Lock()
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


@dataclass(frozen=True)
class Step:
    id: str
    text: str
    checkpoint: bool
    #: Place along the 先后 chain, from 1; ``None`` when the step may come any time.
    number: int | None
    #: Steps that come directly before this one.
    after: tuple[str, ...]
    #: Files a person linked to the step with 依据.
    materials: tuple[str, ...] = ()
    created_ts: float = 0.0

    def label(self, zh: bool = True) -> str:
        if self.number is None:
            return "任意顺序的一步" if zh else "an any-order step"
        return f"第 {self.number} 步" if zh else f"step {self.number}"

    def mark(self) -> str:
        if self.number is None:
            return "·"
        return _CIRCLED[self.number - 1] if self.number <= len(_CIRCLED) else f"({self.number})"


@dataclass(frozen=True)
class StepState:
    step: Step
    status: str
    task_ids: tuple[str, ...] = ()
    open_task_ids: tuple[str, ...] = ()
    done_task_ids: tuple[str, ...] = ()
    #: The question card waiting on the person about this step, if any.
    question_item_id: str = ""

    @property
    def complete(self) -> bool:
        return self.status == "done"

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.step.id, "number": self.step.number, "text": self.step.text,
            "checkpoint": self.step.checkpoint, "after": list(self.step.after),
            "status": self.status, "task_ids": list(self.task_ids),
            "question_item_id": self.question_item_id,
        }


# ---------------------------------------------------------------------------
# The route a person laid
# ---------------------------------------------------------------------------

def read_canvas_file(life_dir: Path | str) -> dict[str, Any]:
    """The canvas record as stored, or an empty one; never raises."""
    try:
        raw = json.loads((Path(life_dir) / CANVAS_FILENAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"cards": [], "links": []}
    return raw if isinstance(raw, dict) else {"cards": [], "links": []}


def route_steps(canvas: Mapping[str, Any]) -> list[Step]:
    """The steps on ``canvas``: numbered ones in chain order, then any-order ones.

    Numbers follow the 先后 links between steps, also through a card that is
    not a step (a topological order, earlier cards first where the links leave
    a choice). A link that would close a cycle is ignored, so a person's
    mistake never wedges the route.
    """
    cards = [
        card for card in canvas.get("cards") or ()
        if isinstance(card, dict) and card.get("kind") == STEP_KIND and card.get("id")
    ]
    by_id = {str(card["id"]): card for card in cards}
    created = {key: float(card.get("created_ts") or 0.0) for key, card in by_id.items()}
    files = {
        str(card["id"]): str((card.get("attachment") or {}).get("relative_path")
                             or (card.get("attachment") or {}).get("name") or "")
        for card in canvas.get("cards") or ()
        if isinstance(card, dict) and card.get("kind") == "file" and card.get("id")
    }
    after: dict[str, list[str]] = {key: [] for key in by_id}
    reach: dict[str, set[str]] = {key: {key} for key in by_id}  # key -> steps reachable from it
    links = sorted(
        (link for link in canvas.get("links") or () if isinstance(link, dict)),
        key=lambda link: float(link.get("created_ts") or 0.0),
    )
    order_into: dict[str, list[str]] = {}
    for link in links:
        if link.get("kind") == "order":
            order_into.setdefault(str(link.get("target") or ""), []).append(str(link.get("source") or ""))

    def steps_before(card_id: str) -> list[str]:
        """The step an order link leaves, or the steps before the card it leaves:
        a step turned back into an idea keeps the steps around it in order."""
        found, stack, seen = [], [card_id], set()
        while stack:
            key = stack.pop()
            if key in seen:
                continue
            seen.add(key)
            if key in by_id:
                found.append(key)
            else:
                stack.extend(order_into.get(key, ()))
        return found

    chained: set[str] = set()
    materials: dict[str, list[str]] = {key: [] for key in by_id}
    for link in links:
        source, target, kind = str(link.get("source") or ""), str(link.get("target") or ""), link.get("kind")
        if kind == "basis":
            # 依据 either way between a file card and a step.
            for file_id, step_id in ((source, target), (target, source)):
                if file_id in files and step_id in by_id and files[file_id] and files[file_id] not in materials[step_id]:
                    materials[step_id].append(files[file_id])
            continue
        if kind != "order" or target not in by_id:
            continue
        for source in steps_before(source):
            if source == target or source in reach[target] or source in after[target]:
                continue  # closes a cycle, or repeats a link
            after[target].append(source)
            chained.update((source, target))
            for key, reachable in reach.items():
                if source in reachable:
                    reachable |= reach[target]
    numbers: dict[str, int] = {}
    waiting = {key: set(after[key]) for key in chained}
    while waiting:
        ready = sorted((key for key, deps in waiting.items() if not deps), key=lambda key: (created[key], key))
        if not ready:  # unreachable: cycles were refused above
            break
        head = ready[0]
        numbers[head] = len(numbers) + 1
        del waiting[head]
        for deps in waiting.values():
            deps.discard(head)
    steps = [
        Step(
            id=key, text=" ".join(str(card.get("text") or "").split()),
            checkpoint=bool(card.get("checkpoint")), number=numbers.get(key),
            after=tuple(sorted(after[key], key=lambda k: numbers.get(k, 0))),
            materials=tuple(materials[key]), created_ts=created[key],
        )
        for key, card in by_id.items()
    ]
    steps.sort(key=lambda step: (step.number is None, step.number or 0, step.created_ts, step.id))
    return steps


def read_route(life_dir: Path | str) -> list[Step]:
    return route_steps(read_canvas_file(life_dir))


def ancestors(steps: Iterable[Step]) -> dict[str, set[str]]:
    """Every step that must be complete before each step may start."""
    direct = {step.id: set(step.after) for step in steps}
    out: dict[str, set[str]] = {}

    def walk(key: str, seen: set[str]) -> set[str]:
        if key in out:
            return out[key]
        found: set[str] = set()
        for parent in direct.get(key, ()):
            if parent in seen:
                continue
            found.add(parent)
            found |= walk(parent, seen | {parent})
        out[key] = found
        return found

    for key in direct:
        walk(key, {key})
    return out


# ---------------------------------------------------------------------------
# Approvals the person gave at checkpoints
# ---------------------------------------------------------------------------

def _state_path(life_dir: Path | str) -> Path:
    return Path(life_dir) / STATE_FILENAME


def read_state(life_dir: Path | str) -> dict[str, Any]:
    try:
        raw = json.loads(_state_path(life_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"approved": {}}
    approved = raw.get("approved") if isinstance(raw, dict) else None
    return {"approved": {str(k): [str(v) for v in values] for k, values in (approved or {}).items()
                         if isinstance(values, list)}}


@contextmanager
def _state_locked(life_dir: Path | str) -> Iterator[None]:
    import portalocker

    path = _state_path(life_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _STATE_LOCK, (path.parent / f"{path.name}.lock").open("a+b") as handle:
        portalocker.lock(handle, portalocker.LOCK_EX)
        try:
            yield
        finally:
            portalocker.unlock(handle)


def approve_step(life_dir: Path | str, step_id: str, task_ids: Iterable[str]) -> None:
    """Record that the person approved ``step_id`` as delivered by ``task_ids``.

    The approval covers exactly those tasks: a step reworked afterwards waits
    for the person's OK again.
    """
    with _state_locked(life_dir):
        state = read_state(life_dir)
        covered = set(state["approved"].get(step_id, ())) | {str(task) for task in task_ids}
        state["approved"][step_id] = sorted(covered)
        state["updated_ts"] = time.time()
        path = _state_path(life_dir)
        write_atomic(path, json.dumps(state, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Where each step stands
# ---------------------------------------------------------------------------

def _row(item: Any, name: str, default: Any = "") -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _card(item: Any) -> Mapping[str, Any]:
    card = _row(item, "operator_decision", None)
    return card if isinstance(card, Mapping) else {}


def _waiting_question(item: Any) -> bool:
    return bool(str(_row(item, "pending_question", "") or "").strip()) and \
        str(_row(item, "status", "") or "") not in _TERMINAL


def step_states(
    steps: Iterable[Step], items: Iterable[Any], state: Mapping[str, Any] | None = None,
) -> list[StepState]:
    """Each step's status from the tasks tied to it, in route order.

    ``todo`` 未开始 · ``running`` 进行中 · ``review`` 等你看 (a checkpoint step
    whose work is delivered and not yet approved) · ``done`` 已完成 ·
    ``question`` 有疑问 (a question about the step waits on the person).
    """
    steps = list(steps)
    rows = list({str(_row(item, "id")): item for item in items}.values())
    approved = (state or {}).get("approved") or {}
    out: list[StepState] = []
    for step in steps:
        tied = [item for item in rows if str(_row(item, "waypoint_id", "") or "") == step.id]
        open_rows = [item for item in tied if str(_row(item, "status")) not in _TERMINAL]
        done = [str(_row(item, "id")) for item in tied if str(_row(item, "status")) == "done"]
        questions = [
            item for item in rows
            if _waiting_question(item) and str(_card(item).get("waypoint_id") or "") == step.id
        ]
        dispute = next((item for item in questions if _card(item).get("decision_kind") != CHECKPOINT_DECISION), None)
        checkpoint = next((item for item in questions if _card(item).get("decision_kind") == CHECKPOINT_DECISION), None)
        needs_ok = step.checkpoint and bool(done) and not set(done) <= set(approved.get(step.id, ()))
        if dispute is not None:
            status = "question"
        elif checkpoint is not None:
            status = "review"
        elif open_rows:
            started = done or any(str(_row(item, "status")) != "pending" for item in open_rows)
            status = "running" if started else "todo"
        elif done:
            status = "review" if needs_ok else "done"
        else:
            status = "todo"
        question = dispute if dispute is not None else checkpoint
        out.append(StepState(
            step=step, status=status,
            task_ids=tuple(str(_row(item, "id")) for item in tied),
            open_task_ids=tuple(str(_row(item, "id")) for item in open_rows),
            done_task_ids=tuple(done),
            question_item_id=str(_row(question, "id")) if question is not None else "",
        ))
    return out


def read_step_states(life_dir: Path | str, items: Iterable[Any]) -> list[StepState]:
    steps = read_route(life_dir)
    return step_states(steps, items, read_state(life_dir)) if steps else []


def held_item_ids(life_dir: Path | str, items: Iterable[Any], history: Iterable[Any] = ()) -> set[str]:
    """Pending rows the route does not let start yet.

    A task tied to a step waits until every step before it is complete. While
    a checkpoint step waits for the person's OK, the project parks: only work
    on that step itself may start. A task of a step no longer on the canvas is
    an ordinary task.
    """
    steps = read_route(life_dir)
    if not steps:
        return set()
    live = list(items)
    states = step_states(steps, [*history, *live], read_state(life_dir))
    complete = {entry.step.id for entry in states if entry.complete}
    waiting_ok = {entry.step.id for entry in states if entry.status == "review"}
    before = ancestors(steps)
    held: set[str] = set()
    for item in live:
        if str(_row(item, "status")) != "pending":
            continue
        step_id = str(_row(item, "waypoint_id", "") or "")
        if waiting_ok and step_id not in waiting_ok:
            held.add(str(_row(item, "id")))
        elif step_id in before and not before[step_id] <= complete:
            held.add(str(_row(item, "id")))
    return held


def due_checkpoints(states: Iterable[StepState]) -> list[StepState]:
    """Checkpoint steps whose work is delivered with no card asking the person yet."""
    return [entry for entry in states
            if entry.status == "review" and entry.step.checkpoint and not entry.question_item_id]


def scope_to_step(objective: str, step: Step) -> tuple[str, str]:
    """The objective and acceptance check of a task that carries out ``step`` only."""
    return (
        f"{objective.rstrip()}\n\n[REQUIRED STEP] This task carries out only the operator's "
        f"{step.label(False)} «{step.text}». The later steps are planned as their own tasks after "
        "it; do not start them here.",
        f"It fulfils the operator's required {step.label(False)}: «{step.text}».",
    )


def open_steps(states: Iterable[StepState]) -> list[StepState]:
    return [entry for entry in states if not entry.complete]


# ---------------------------------------------------------------------------
# What the roles read
# ---------------------------------------------------------------------------

def route_directive_text(steps: Iterable[Step]) -> str:
    """The standing operator directive that carries the route; "" with no steps.

    Only what the person decided is here (words, order, checkpoints, files),
    never progress, so the revision moves only when the route does.
    """
    steps = list(steps)
    if not steps:
        return ""
    numbers = {step.id: step.number for step in steps}
    lines = [
        "必经步骤 — the operator's required route (binding requirements in their own words, "
        "not suggestions). Plan tasks that fulfil every step; set each such task's "
        "waypoint_id to its step id (a step may need several tasks, a task fulfils at most "
        "one step). Numbered steps go in order: plan and start a later step only after the "
        "earlier ones are done. You may add tasks between steps; never drop, merge away, "
        "reorder or reinterpret a step. If a step looks wrong or impossible, ask the operator "
        "(to=operator, about_waypoint=<step id>, options keep / change / remove) instead of "
        "skipping it. The project is not done while a step is open. A task tied to a step is "
        "reviewed against that step's words. The [ids] are for waypoint_id only: in titles and "
        "anything the operator reads, name a step by its number and words (第 2 步「…」).",
    ]
    for step in steps:
        order = f"{step.number}." if step.number is not None else "- (any order)"
        extras = []
        if step.after:
            extras.append("after " + ", ".join(
                f"step {numbers.get(parent)}" if numbers.get(parent) else parent for parent in step.after
            ))
        if step.checkpoint:
            extras.append("checkpoint: once its work is delivered the host itself pauses the project "
                          "and asks the operator; do not ask for that review yourself or hold the task for it")
        if step.materials:
            extras.append("materials: " + ", ".join(step.materials))
        suffix = f" ({'; '.join(extras)})" if extras else ""
        lines.append(f"{order} [{step.id}] {step.text}{suffix}")
    return "\n".join(lines)


_STATUS_WORDS = {
    "todo": ("未开始", "not started"),
    "running": ("进行中", "in progress"),
    "review": ("等你看", "waiting for the operator's OK"),
    "done": ("已完成", "done"),
    "question": ("有疑问", "a question to the operator waits"),
}


def status_word(status: str, zh: bool) -> str:
    words = _STATUS_WORDS.get(status, (status, status))
    return words[0] if zh else words[1]


def route_status_block(states: Iterable[StepState]) -> str:
    """Where each step stands, for the Planner's turn; "" with no steps."""
    states = list(states)
    if not states:
        return ""
    lines = ["## 必经步骤 — where the operator's route stands now"]
    for entry in states:
        step = entry.step
        tasks = f"; tasks {', '.join(entry.task_ids)}" if entry.task_ids else "; no task yet"
        lines.append(
            f"- {step.mark()} [{step.id}] {step.text[:200]} — {status_word(entry.status, False)}{tasks}"
        )
    review = [entry for entry in states if entry.status == "review"]
    if review:
        lines.append(
            "The project is parked until the operator answers the checkpoint on "
            + ", ".join(entry.step.label(False) for entry in review)
            + ". Plan nothing that needs that answer; the host has already asked."
        )
    return "\n".join(lines)


def route_digest_lines(states: Iterable[StepState]) -> list[str]:
    """The Manager's lines on the route, by number and the person's words."""
    lines = []
    for entry in states:
        step = entry.step
        check = "，到这步先停下来给人看" if step.checkpoint else ""
        lines.append(f"- 必经步骤 {step.mark()} {step.id}: {step.text[:200]}（{status_word(entry.status, True)}{check}）")
    return lines


def open_steps_blocker(life_dir: Path | str, items: Iterable[Any]) -> str:
    """Why the project cannot be done yet because of the route; "" when nothing blocks."""
    try:
        states = read_step_states(life_dir, items)
    except Exception:  # noqa: BLE001 - a damaged record must not invent a blocker
        log.exception("waypoint status read failed")
        return ""
    return route_blocker(states)


def route_blocker(states: Iterable[StepState]) -> str:
    """The completion blocker for steps that are not complete; "" when all are."""
    pending = open_steps(states)
    if not pending:
        return ""
    named = "; ".join(
        f"{entry.step.label(False)} [{entry.step.id}] «{entry.step.text[:120]}» is "
        f"{status_word(entry.status, False)}"
        for entry in pending[:5]
    )
    return (
        f"the operator's required steps are not all done: {named}. Plan tasks for them, or "
        "ask the operator whether to keep, change or remove a step you doubt; never skip one"
    )


def sync_route_directive(life_dir: Path | str) -> int:
    """Make the operator-context route match the canvas; returns the context revision.

    A change in a step's words, order, checkpoint or materials replaces the one
    route directive, which bumps the revision the Planner watches.
    """
    from .operator_context import OperatorContextStore

    text = route_directive_text(read_route(life_dir))
    return OperatorContextStore(life_dir).replace_standing_directive(ROUTE_SOURCE, text)


__all__ = [
    "CANVAS_FILENAME", "CHANGE_OPTION", "CHECKPOINT_DECISION", "CHECKPOINT_TAG", "CONTINUE_OPTION",
    "DISPUTE_DECISION", "KEEP_OPTION", "REMOVE_OPTION", "REVISE_OPTION", "ROUTE_SOURCE",
    "STATE_FILENAME", "STATUSES", "STEP_KIND", "Step", "StepState", "ancestors", "approve_step",
    "due_checkpoints", "held_item_ids", "scope_to_step", "open_steps", "open_steps_blocker", "read_canvas_file",
    "read_route", "read_state", "read_step_states", "route_blocker", "route_digest_lines", "route_directive_text",
    "route_status_block", "route_steps", "status_word", "step_states", "sync_route_directive",
]
