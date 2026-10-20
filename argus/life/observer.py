"""The Observer: the person who used to sit beside the screen.

Someone glancing at the team sees at once what no role sees about itself: a
Planner that wakes every few minutes only to say it is still waiting, the same
role called again and again with nothing new, a long stretch where nobody
moves, money going out faster than anything changes, a course drifting off
the task. Whether the work itself is right is the Reviewer's to judge; the
Observer only watches whether the work flows.

The Observer is that person as a role, on the same backends as the others.
Whenever the Engineer, Reviewer or Planner ends a turn, and once whenever the
team goes still, it reads how the task has flowed so far from ``events.jsonl``,
with the Engineer's access and only the tools reading needs, in a fresh
session each time. When all is well it
says nothing and leaves no record of its own. A remark is one ``life.observer.note`` event for the
Manager, who hears it through its supervision and decides what to do; with no
Manager, or once the task is delivered, the operator gets it as an alert.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType
from ..core.models import READING_ROLE_UNUSED_COPILOT_TOOLS, RunnerOptions
from ..core.run_gateway import run_exec

LOG = logging.getLogger(__name__)

RUN_LABEL = "observer"
#: The roles whose turns the Observer looks at.
WATCHED_ROLES = ("engineer", "planner", "reviewer")
MODEL_KNOB = "ARGUS_SKILL_OBSERVER_MODEL"
#: Earlier remarks shown with each look, so that none is made twice.
_REMEMBERED = 12


def _model(reviewer_model: str) -> str:
    """The Observer's own model when one is set, else the Reviewer's."""
    from ..core.knobs import resolve_knob

    own = resolve_knob(MODEL_KNOB, "auto").value
    return reviewer_model if own.lower() in {"", "auto"} else own


def _ended_turn(event: dict[str, Any]) -> dict[str, Any] | None:
    """The turn this event ends: the role, its task and when, or None."""
    role = str(event.get("role") or "")
    if event.get("type") != EventType.ROLE_SESSION_TURN or role not in WATCHED_ROLES:
        return None
    return {"role": role, "item_id": str(event.get("item_id") or ""), "ts": float(event.get("ts") or time.time())}


def _went_still(event: dict[str, Any]) -> dict[str, Any] | None:
    """The Planner's skipped turn as one to look at, or None.

    A skipped Planner call is the last event a still team writes; without this
    look nobody would see that nothing moves.
    """
    if event.get("type") != EventType.LIFE_PLANNER_IDLE or event.get("model_call_skipped") is not True:
        return None
    return {"role": "planner", "item_id": "", "ts": float(event.get("ts") or time.time()), "still": True}


def _earlier_remarks(root: Path) -> list[str]:
    """The Observer's latest remarks, oldest first, read back from the event log."""
    from .memory import _read_jsonl_tail_history

    rows = _read_jsonl_tail_history(
        root / "events.jsonl", _REMEMBERED,
        raw_markers=(EventType.LIFE_OBSERVER_NOTE.value.encode(),),
        predicate=lambda row: row.get("type") == EventType.LIFE_OBSERVER_NOTE,
    )
    return [str(row.get("text") or "") for row in rows]


def remarks(reply: str) -> list[str]:
    """The ``→ manager: <remark>`` line of a reply; the last one counts."""
    from ..core.role_handoff import baton_lines

    return [text for to, text in baton_lines(reply) if to == "manager" and text][-1:]


def look(backend: Any, *, root: Path, turns: list[dict[str, Any]], model: str) -> list[str]:
    """One look at the flow up to the turns that just ended: a remark worth making, if any.

    Each look is a fresh session: the turns, where the events are, and the
    remarks already made are all it needs. Raises when the call fails; the
    caller then skips these turns.
    """
    from ..core.mission_view import load_mission_view
    from ..roles.prompts.observer import build_observer_prompt

    said = _earlier_remarks(root)
    prompt = build_observer_prompt(
        root=root, turns=turns, said=said, chinese=load_mission_view(root).get("language") == "zh",
    )
    result = run_exec(
        backend, prompt=prompt, run_label=RUN_LABEL,
        options=RunnerOptions(
            model=model or None, reasoning_effort="low",
            working_dir=str(root), skip_git_repo_check=True,
            # The Engineer's access: full outside safe mode, full-auto inside it.
            full_auto=True,
            # It only reads the event log: tools for writing, agents and
            # GitHub/web search stay out of each fresh session's requests.
            lean_tool_surface=True,
            excluded_tools=(*READING_ROLE_UNUSED_COPILOT_TOOLS, "apply_patch"),
            disabled_mcp_servers=("github-mcp-server",),
        ),
    )
    if int(getattr(result, "exit_code", 0) or 0) != 0 or getattr(result, "fatal_error", None):
        raise RuntimeError(f"the Observer's call ended with exit code {getattr(result, 'exit_code', '?')}")
    reply = "\n".join(getattr(result, "agent_messages", None) or [])
    return [remark for remark in remarks(reply) if remark not in said]


@dataclass
class _Watch:
    """One project's Observer: the turns waiting for a look, and the look under way."""

    waiting: list[dict[str, Any]] = field(default_factory=list)
    looking: threading.Thread | None = None
    # Looked at since the team last went still; a turn that ends clears it.
    still: bool = False


#: One Observer per project, however many supervisors run its missions.
_WATCHES: dict[str, _Watch] = {}
_GUARD = threading.Lock()


def settle(stop: threading.Event) -> None:
    """Wait until the Observer has looked at every turn that has ended, or ``stop`` is set."""
    with _GUARD:
        looks = [watch.looking for watch in _WATCHES.values() if watch.looking is not None]
    for looking in looks:
        while looking.is_alive() and not stop.is_set():
            looking.join(1)


class ObserverSink:
    """Every event the team emits passes here; a turn another role ends wakes the Observer.

    One look runs at a time per project, on its own thread, with no lock held
    while the model works; turns that end meanwhile wait for the next look. A
    look that fails is skipped.
    """

    def __init__(self, sink: Any, supervisor: Any) -> None:
        self.inner = sink
        self.supervisor = supervisor

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def handle_event(self, event: dict[str, Any]) -> Any:
        result = self.inner.handle_event(event)
        if result is not False:
            try:
                self._wake(event)
            except Exception:  # noqa: BLE001 - watching never holds up the work
                LOG.exception("could not hand the Observer the turn that ended")
        return result

    def _wake(self, event: dict[str, Any]) -> None:
        backend = getattr(self.supervisor.runner, "reviewer_backend", None)
        turn = _ended_turn(event)
        still = None if turn else _went_still(event)
        if backend is None or not (turn or still):
            return
        if still and any(item.status == "running" for item in self.supervisor.memory.backlog.active()):
            return  # a role is still at work: the team is not still
        root = Path(self.supervisor._project_state_root()).resolve()
        with _GUARD:
            watch = _WATCHES.setdefault(str(root), _Watch())
            if still and watch.still:
                return  # looked at once already; the next turn that ends re-arms it
            watch.still = bool(still)
            watch.waiting.append(turn or still)
            if watch.looking is not None and watch.looking.is_alive():
                return
            watch.looking = threading.Thread(
                target=self._watch, args=(backend, root, watch), name="argus-observer", daemon=True,
            )
            watch.looking.start()

    def _watch(self, backend: Any, root: Path, watch: _Watch) -> None:
        while True:
            with _GUARD:
                turns, watch.waiting = watch.waiting, []
                if not turns:
                    watch.looking = None
                    return
            self._look(backend, root, turns)

    def _look(self, backend: Any, root: Path, turns: list[dict[str, Any]]) -> None:
        quiet = None
        try:
            fork = backend.fork
            # A background role publishes nothing into the foreground's live feed.
            quiet = fork(event_callback=None) if "event_callback" in signature(fork).parameters else fork()
            configure_usage = getattr(quiet, "set_usage_context", None)
            if callable(configure_usage):
                configure_usage(
                    project_root=root,
                    global_root=root.parent.parent if root.parent.name == "projects" else None,
                )
            heard = look(quiet, root=root, turns=turns, model=_model(self.supervisor.reviewer_model))
        except Exception:  # noqa: BLE001 - a failed look is skipped, not retried
            LOG.warning("the Observer could not look at the turns that ended", exc_info=True)
            return
        finally:
            close = getattr(quiet, "close_acp_clients", None)
            if callable(close):
                close()
        for text in heard:
            try:
                self._say(root, turns, text)
            except Exception:  # noqa: BLE001 - an unsaid remark is not recorded as said
                LOG.warning("the Observer's remark was not delivered", exc_info=True)

    def _say(self, root: Path, turns: list[dict[str, Any]], text: str) -> None:
        """One remark, as one event: for the Manager, else an alert for the operator."""
        from .memory import _read_jsonl_tail_history

        tasks = {turn["item_id"] for turn in turns if turn["item_id"]}
        delivered = tasks and _read_jsonl_tail_history(
            root / "events.jsonl", 1,
            raw_markers=(EventType.LIFE_MISSION_COMPLETED.value.encode(),),
            predicate=lambda row: row.get("type") == EventType.LIFE_MISSION_COMPLETED
            and row.get("item_id") in tasks,
        )
        event: dict[str, Any] = {"type": EventType.LIFE_OBSERVER_NOTE, "agent_layer": "observer", "text": text}
        if self.supervisor.manager is None or delivered:
            event.update(to="operator", operator_alert=True)
        else:
            event["to"] = "manager"
        # Through the team's own sink, where a remark to the Manager reaches its supervision.
        self.supervisor._emit(event)


__all__ = ["ObserverSink", "RUN_LABEL", "look", "remarks", "settle"]
