"""The Observer: the person who used to sit beside the screen.

Someone watching the team work sees at a glance what no role sees about
itself: a Planner that wakes every few minutes only to say it is still
waiting, an Engineer reporting a test it never ran, a wait on a job that died
an hour ago, a course drifting off the task, money going out faster than
anything changes. When an agent stops and says it is done, they look at what
it just did, say one sentence to whoever can fix it, and leave the decision to
stop to whoever may make it.

The Observer is that person as a role, on the same backends as the others.
Whenever the Engineer, Reviewer or Planner ends a turn, it reads that turn's
real record, the raw stream of its model call and the events around it, with
the Reviewer's read-only tools. It is a bystander: the Manager decides the direction and
keeps the authority, the Reviewer judges the results. When all is well it
says nothing and leaves no record of its own. A remark is one
``life.observer.note`` event, delivered through a channel that already exists:
the operator's channel into one role's next prompt, the Manager's
supervision, or an operator alert.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from inspect import signature
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType
from ..core.models import RunnerOptions
from ..core.operator_context import OBSERVER_SOURCE, OperatorContextStore, append_directive
from ..core.run_gateway import run_exec

LOG = logging.getLogger(__name__)

RUN_LABEL = "observer"
#: Who may hear a remark: these roles in their next prompt, the Manager through
#: its supervision, the operator through an alert.
PROMPTED_ROLES = ("engineer", "planner", "reviewer")
HEARERS = (*PROMPTED_ROLES, "manager", "operator")
MODEL_KNOB = "ARGUS_SKILL_OBSERVER_MODEL"
#: The Planner's rolling-session budget: the Observer reads on from where it
#: stopped for as long as the Planner plans on in one session.
_SESSION_TURNS = 20
#: Earlier remarks shown with each look, so that none is made twice.
_REMEMBERED = 12


def _model(reviewer_model: str) -> str:
    """The Observer's own model when one is set, else the Reviewer's."""
    from ..core.knobs import resolve_knob

    own = resolve_knob(MODEL_KNOB, "auto").value
    return reviewer_model if own.lower() in {"", "auto"} else own


def _ended_turn(root: Path, event: dict[str, Any]) -> dict[str, Any] | None:
    """The turn this event ends: the role and its model call, or None.

    Every Engineer, Reviewer and Planner call ends with ``role.session.turn``,
    whose session is the thread of the call it ends.
    """
    from .memory import _read_jsonl_tail_history

    role = str(event.get("role") or "")
    if event.get("type") != EventType.ROLE_SESSION_TURN or role not in PROMPTED_ROLES:
        return None
    thread = event.get("session_id")
    rows = _read_jsonl_tail_history(
        root / "events.jsonl", 1,
        raw_markers=(EventType.AGENT_IO_COMPLETE.value.encode(),),
        predicate=lambda row: row.get("type") == EventType.AGENT_IO_COMPLETE
        and str(row.get("run_label") or "").startswith(role)
        and (not thread or row.get("thread_id") == thread),
    )
    return {"role": role, **{key: rows[-1].get(key) for key in ("call_id", "run_label", "ts")}} if rows else None


def _earlier_remarks(root: Path) -> list[tuple[str, str]]:
    """The Observer's latest remarks, oldest first, read back from the event log."""
    from .memory import _read_jsonl_tail_history

    rows = _read_jsonl_tail_history(
        root / "events.jsonl", _REMEMBERED,
        raw_markers=(EventType.LIFE_OBSERVER_NOTE.value.encode(),),
        predicate=lambda row: row.get("type") == EventType.LIFE_OBSERVER_NOTE,
    )
    return [(str(row.get("to") or ""), str(row.get("text") or "")) for row in rows]


def remarks(reply: str) -> list[tuple[str, str]]:
    """The ``→ <hearer>: <remark>`` lines of a reply; the last one per hearer counts."""
    from ..core.role_handoff import baton_lines

    return list({to: text for to, text in baton_lines(reply) if to in HEARERS and text}.items())


def look(
    backend: Any, *, root: Path, workdir: Path, turns: list[dict[str, Any]], model: str,
) -> list[tuple[str, str]]:
    """One look at the turns that just ended: the remarks worth making, none made before.

    Raises when the call fails; the caller then skips these turns.
    """
    from ..core.knobs import resolve_role_reasoning_effort
    from ..core.role_session import (
        RoleSessionCapsule,
        configured_role_session_policy,
        effective_role_session_policy,
    )
    from ..planner import configured_role_session_max_input_tokens
    from ..roles.prompts.observer import build_observer_prompt

    said = _earlier_remarks(root)
    name = str(getattr(backend, "backend", type(backend).__name__))
    policy = effective_role_session_policy(configured_role_session_policy(), name)
    session = RoleSessionCapsule.open(
        role=RUN_LABEL, policy=policy, objective_revision="", workdir=workdir,
        backend=name, model=model, checkpoint_path=None,
        path=None if policy == "fresh" else root / "role-sessions" / f"{RUN_LABEL}.json",
    )
    thread = session.prepare(
        max_turns=_SESSION_TURNS, max_input_tokens=configured_role_session_max_input_tokens(),
    )
    prompt = build_observer_prompt(root=root, workdir=workdir, turns=turns, said=said)
    result = run_exec(
        backend, prompt=session.resume_prompt(prompt), run_label=RUN_LABEL,
        resume_thread_id=thread,
        options=RunnerOptions(
            model=model or None,
            reasoning_effort=resolve_role_reasoning_effort(
                "ARGUS_SKILL_REVIEWER_REASONING_EFFORT", default="high",
            ),
            working_dir=str(root), add_dirs=[str(workdir)], skip_git_repo_check=True,
            # The Reviewer's read-only boundary: read and search, change nothing.
            sandbox_mode="read-only", force_safe_mode=True, dangerous_yolo=False, full_auto=False,
        ),
    )
    if int(getattr(result, "exit_code", 0) or 0) != 0 or getattr(result, "fatal_error", None):
        session.rotate("backend_failure")
        raise RuntimeError(f"the Observer's call ended with exit code {getattr(result, 'exit_code', '?')}")
    reply = "\n".join(getattr(result, "agent_messages", None) or [])
    session.complete(result, decisive_output=reply, prompt=prompt)
    return [remark for remark in remarks(reply) if remark not in said]


@dataclass
class _Watch:
    """One project's Observer: the turns waiting for a look, and the look under way."""

    waiting: list[dict[str, Any]] = field(default_factory=list)
    looking: threading.Thread | None = None


#: One Observer per project, however many supervisors run its missions.
_WATCHES: dict[str, _Watch] = {}
_GUARD = threading.Lock()


class ObserverSink:
    """Every event the team emits passes here; a turn another role ends wakes the Observer.

    One look runs at a time per project, on its own thread, with no lock held
    while the model works; turns that end meanwhile wait for the next look. A
    look that fails is skipped. The Observer's own turns end no turn here.
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
        if event.get("type") != EventType.ROLE_SESSION_TURN or backend is None:
            return
        root = Path(self.supervisor._project_state_root()).resolve()
        turn = _ended_turn(root, event)
        if turn is None:
            return
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
            heard = look(
                quiet, root=root, workdir=Path(self.supervisor._project_workdir()), turns=turns,
                model=_model(self.supervisor.reviewer_model),
            )
        except Exception:  # noqa: BLE001 - a failed look is skipped, not retried
            LOG.warning("the Observer could not look at the turns that ended", exc_info=True)
            return
        finally:
            close = getattr(quiet, "close_acp_clients", None)
            if callable(close):
                close()
        for to, text in heard:
            try:
                self._say(root, to, text)
            except Exception:  # noqa: BLE001 - an unsaid remark is not recorded as said
                LOG.warning("the Observer's remark to the %s was not delivered", to, exc_info=True)

    def _say(self, root: Path, to: str, text: str) -> None:
        """Deliver one remark on the channel its hearer already reads, as one event."""
        event: dict[str, Any] = {
            "type": EventType.LIFE_OBSERVER_NOTE, "agent_layer": "observer", "to": to, "text": text,
        }
        if to in PROMPTED_ROLES:
            append_directive(
                root, text, expected_revision=OperatorContextStore(root).revision,
                scope="mission", applies_to_roles=(to,), lifetime="once", source=OBSERVER_SOURCE,
            )
        elif to == "operator":
            event["operator_alert"] = True
        # Through the team's own sink, where a remark to the Manager reaches its supervision.
        self.supervisor._emit(event)


__all__ = ["ObserverSink", "RUN_LABEL", "look", "remarks"]
