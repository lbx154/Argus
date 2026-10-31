"""The Observer: the person who used to sit beside the screen.

Someone glancing at the team sees at once what no role sees about itself: a
Planner that wakes every few minutes only to say it is still waiting, the same
role called again and again with nothing new, a long stretch where nobody
moves, money going out faster than anything changes, a course drifting off
the task. Whether the work itself is right is the Reviewer's to judge; the
Observer only watches whether the work flows.

The Observer is that person as a role, on the same backends as the others.
Whenever the Engineer, Reviewer or Planner ends a turn, it reads how the task
has flowed so far from ``events.jsonl``, with the Engineer's access and only
the tools reading needs. Within a task it resumes its session and reads only
the events added since its last look; the session ends with the task, and
planning, which belongs to no task, is looked at afresh each time. A turn
nobody took, such as a Planner call held after repeated failures, is not looked
at, nor the turn that ends the work (a handoff to done, or the Planner declaring
the project done): after it nothing is left to flow. Each look ends with the
``observe`` form; when all is well it leaves the remark out and no record of
its own. A remark is one ``life.observer.note`` event, naming its task and the
event times it rests on, for the Manager, who
hears it through its supervision and decides what to do; with no Manager, the
operator gets it as an alert.
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
from ..core.handoff import HandOffActions
from ..core.models import READING_ROLE_UNUSED_COPILOT_TOOLS, RunnerOptions
from ..core.role_actions import role_actions
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
    return reviewer_model if own.lower() in {"", "auto", "inherit", "default"} else own


def _ended_turn(event: dict[str, Any]) -> dict[str, Any] | None:
    """The turn this event ends: the role, its task and when, or None."""
    role = str(event.get("role") or "")
    if event.get("type") != EventType.ROLE_SESSION_TURN or role not in WATCHED_ROLES or event.get("ends_work"):
        return None
    return {"role": role, "item_id": str(event.get("item_id") or ""), "ts": float(event.get("ts") or time.time())}


def _earlier_remarks(root: Path) -> list[str]:
    """The Observer's latest remarks, oldest first, read back from the event log."""
    from .memory import _read_jsonl_tail_history

    rows = _read_jsonl_tail_history(
        root / "events.jsonl", _REMEMBERED,
        raw_markers=(EventType.LIFE_OBSERVER_NOTE.value.encode(),),
        predicate=lambda row: row.get("type") == EventType.LIFE_OBSERVER_NOTE,
    )
    return [str(row.get("text") or "") for row in rows]


def _remark(payload: dict[str, Any]) -> tuple[str, tuple[float, ...]]:
    """The ``observe`` form's rule: a remark says where it was seen, and only a remark does."""
    if ("remark" in payload) != ("seen_at" in payload):
        raise ValueError("remark and seen_at go together: leave both out when the flow is fine.")
    return payload.get("remark", ""), tuple(payload.get("seen_at", ()))


def look(
    backend: Any, *, root: Path, turns: list[dict[str, Any]], model: str,
    session: tuple[str, float] | None = None,
) -> tuple[tuple[str, tuple[float, ...]] | None, str]:
    """One look at the flow up to the turns that just ended: a new remark and where it was seen, and its session.

    ``session`` is the task's session so far, the thread and when its last
    look began: the look resumes it and reads only the events since. Raises
    when the call fails; the caller then skips these turns.
    """
    from ..core.mission_view import load_mission_view, session_is_chinese
    from ..roles.prompts.observer import build_observer_prompt

    said = _earlier_remarks(root)
    thread, since = session or ("", 0.0)
    prompt = build_observer_prompt(
        root=root, turns=turns, said=said, chinese=session_is_chinese(load_mission_view(root)),
        since=since,
    )
    options = RunnerOptions(
        model=model or None, reasoning_effort="low",
        working_dir=str(root), skip_git_repo_check=True,
        # The Engineer's access: full outside safe mode, full-auto inside it.
        full_auto=True,
        # It only reads the event log: tools for writing, agents and
        # GitHub/web search stay out of its requests.
        lean_tool_surface=True,
        excluded_tools=(*READING_ROLE_UNUSED_COPILOT_TOOLS, "apply_patch"),
        disabled_mcp_servers=("github-mcp-server",),
    )
    actions = HandOffActions("observe", check=_remark, operator_available=False)
    with role_actions(backend, options, actions) as bound:
        result = run_exec(
            backend, prompt=prompt, run_label=RUN_LABEL, resume_thread_id=thread or None, options=bound,
        )
    if int(getattr(result, "exit_code", 0) or 0) != 0 or getattr(result, "fatal_error", None):
        raise RuntimeError(f"the Observer's call ended with exit code {getattr(result, 'exit_code', '?')}")
    heard = actions.handoff.value if actions.handoff else None
    if heard and (not heard[0] or heard[0] in said):
        heard = None
    return heard, str(getattr(result, "thread_id", "") or "")


def _ended(root: Path, task: str) -> bool:
    """Whether ``task`` has completed since it last started."""
    from .memory import _read_jsonl_tail_history

    bounds = (EventType.LIFE_MISSION_STARTED, EventType.LIFE_MISSION_COMPLETED)
    last = _read_jsonl_tail_history(
        root / "events.jsonl", 1,
        raw_markers=tuple(bound.value.encode() for bound in bounds),
        predicate=lambda row: row.get("type") in bounds and row.get("item_id") == task,
    ) if task else []
    return bool(last) and last[-1].get("type") == EventType.LIFE_MISSION_COMPLETED


@dataclass
class _Watch:
    """One project's Observer: the turns waiting for a look, the look under way, and each task's session."""

    waiting: list[dict[str, Any]] = field(default_factory=list)
    looking: threading.Thread | None = None
    #: Each running task's session: the thread and when its last look began.
    sessions: dict[str, tuple[str, float]] = field(default_factory=dict)


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
        if backend is None or turn is None:
            return
        root = Path(self.supervisor._project_state_root()).resolve()
        with _GUARD:
            watch = _WATCHES.setdefault(str(root), _Watch())
            watch.waiting.append(turn)
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
            for task in dict.fromkeys(turn["item_id"] for turn in turns):
                self._look(backend, root, watch, task, [turn for turn in turns if turn["item_id"] == task])

    def _look(self, backend: Any, root: Path, watch: _Watch, task: str, turns: list[dict[str, Any]]) -> None:
        # A look that fails leaves the next one to start afresh.
        session = watch.sessions.pop(task, None)
        began = time.time()
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
            heard, thread = look(
                quiet, root=root, turns=turns, model=_model(self.supervisor.reviewer_model), session=session,
            )
        except Exception:  # noqa: BLE001 - a failed look is skipped, not retried
            LOG.warning("the Observer could not look at the turns that ended", exc_info=True)
            return
        finally:
            close = getattr(quiet, "close_acp_clients", None)
            if callable(close):
                close()
        if thread and task and not _ended(root, task):
            watch.sessions[task] = (thread, began)
        if heard is None:
            return
        try:
            self._say(task, *heard)
        except Exception:  # noqa: BLE001 - an unsaid remark is not recorded as said
            LOG.warning("the Observer's remark was not delivered", exc_info=True)

    def _say(self, task: str, text: str, seen_at: tuple[float, ...]) -> None:
        """One remark on ``task``, as one event: for the Manager, else an alert for the operator."""
        event: dict[str, Any] = {
            "type": EventType.LIFE_OBSERVER_NOTE, "agent_layer": "observer", "text": text, "seen_at": list(seen_at),
        }
        if task:
            event["item_id"] = task
        if self.supervisor.manager is None:
            event.update(to="operator", operator_alert=True)
        else:
            event["to"] = "manager"
        # Through the team's own sink, where a remark to the Manager reaches its supervision.
        self.supervisor._emit(event)


__all__ = ["ObserverSink", "RUN_LABEL", "look", "settle"]
