"""The Observer: the person who used to sit beside the screen.

Someone watching the team work sees at a glance what no role sees about
itself: a Planner that wakes every few minutes only to say it is still
waiting, an Engineer reporting a test it never ran, a wait on a job that died
an hour ago, a course drifting off the task, money going out faster than
anything changes. They say one sentence to whoever can fix it, and leave the
decision to stop to whoever may make it.

The Observer is that person as a role, on the same backends as the others. It
reads the real record (the event log, the raw stream of every model call, the
usage ledger) with the Reviewer's read-only tools, and is woken only when the
other roles have done enough since it last looked. It is a bystander: the
Manager decides the direction and keeps the authority, the Reviewer judges
the results. When all is well it says nothing and leaves no record of its own.
A remark is one ``life.observer.note`` event, delivered through a channel
that already exists: the operator's channel into one role's next prompt, the
Manager's supervision, or an operator alert.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from inspect import signature
from pathlib import Path
from typing import Any

from ..core.event_catalog import EventType
from ..core.file_facts import file_facts
from ..core.models import RunnerOptions
from ..core.operator_context import OBSERVER_SOURCE, OperatorContextStore, append_directive
from ..core.run_gateway import run_exec
from ..core.usage import USAGE_FILE, UsageLedger

LOG = logging.getLogger(__name__)

RUN_LABEL = "observer"
#: Who may hear a remark: these roles in their next prompt, the Manager through
#: its supervision, the operator through an alert.
PROMPTED_ROLES = ("engineer", "planner", "reviewer")
HEARERS = (*PROMPTED_ROLES, "manager", "operator")
CALLS_KNOB = "ARGUS_SKILL_OBSERVER_CALLS"
SPEND_KNOB = "ARGUS_SKILL_OBSERVER_SPEND_USD"
MODEL_KNOB = "ARGUS_SKILL_OBSERVER_MODEL"
#: The Planner's rolling-session budget: the Observer reads on from where it
#: stopped for as long as the Planner plans on in one session.
_SESSION_TURNS = 20
#: Earlier remarks shown with each look, so that none is made twice.
_REMEMBERED = 12


def _threshold(name: str, default: float) -> float:
    from ..core.knobs import resolve_knob

    try:
        return max(0.0, float(resolve_knob(name, f"{default:g}").value))
    except ValueError:
        return default


def _model(reviewer_model: str) -> str:
    """The Observer's own model when one is set, else the Reviewer's."""
    from ..core.knobs import resolve_knob

    own = resolve_knob(MODEL_KNOB, "auto").value
    return reviewer_model if own.lower() in {"", "auto"} else own


def _activity(root: Path, since: float) -> tuple[int, float]:
    """The model calls the other roles completed since ``since``, and their cost."""
    calls = [
        record for record in UsageLedger(root, migrate_legacy=False).records(since=since)
        if record.run_label != RUN_LABEL
    ]
    return len(calls), sum(record.cost_usd or 0.0 for record in calls)


def _last_look(root: Path) -> float:
    """When the Observer last began to look: its own latest call in the ledger."""
    return max(
        (record.started_at for record in UsageLedger(root, migrate_legacy=False).records()
         if record.run_label == RUN_LABEL),
        default=0.0,
    )


def _delivered(event: dict[str, Any]) -> bool:
    """A result handed over: a mission done, or the whole project complete."""
    kind = event.get("type")
    if kind == EventType.LIFE_MISSION_COMPLETED:
        return event.get("success") is True and event.get("status") == "done"
    return kind == EventType.LIFE_PLANNER_VERDICT and event.get("project_done") is True


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
    backend: Any, *, root: Path, workdir: Path, since: float, reason: str, model: str,
) -> list[tuple[str, str]]:
    """One look at the work since ``since``: the remarks worth making, none made before.

    Raises when the call fails; the caller then skips this look.
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
    prompt = build_observer_prompt(root=root, workdir=workdir, since=since, reason=reason, said=said)
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
    """One project's Observer: where it stopped, the ledger it last checked, its look under way."""

    since: float | None = None
    ledger: list[int] | None = None
    looking: threading.Thread | None = None


#: One Observer per project, however many supervisors run its missions.
_WATCHES: dict[str, _Watch] = {}
_GUARD = threading.Lock()


class ObserverSink:
    """Every event the team emits passes here; a look starts only when one is due.

    A look is due when the usage ledger shows enough calls or spend by the other
    roles since the Observer last looked, or a result is delivered after any new
    call; with nothing new it never looks. The look runs on its own thread and
    no lock is held while the model works. A look that fails is skipped: the
    next stretch of work wakes the Observer again.
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
                self._wake_if_due(event)
            except Exception:  # noqa: BLE001 - watching never holds up the work
                LOG.exception("could not tell whether the Observer should look")
        return result

    def _wake_if_due(self, event: dict[str, Any]) -> None:
        root = Path(self.supervisor._project_state_root()).resolve()
        delivered = _delivered(event)
        with _GUARD:
            watch = _WATCHES.setdefault(str(root), _Watch())
            if watch.looking is not None and watch.looking.is_alive():
                return
            try:
                ledger: list[int] | None = file_facts(root / USAGE_FILE)
            except OSError:
                ledger = None
            if ledger == watch.ledger and not delivered:
                return
            watch.ledger = ledger
            backend = getattr(self.supervisor.runner, "reviewer_backend", None)
            calls_due, spend_due = _threshold(CALLS_KNOB, 10), _threshold(SPEND_KNOB, 2.0)
            if backend is None or not (calls_due or spend_due):
                return
            if watch.since is None:
                watch.since = _last_look(root)
            calls, spend = _activity(root, watch.since)
            if not (
                (calls_due and calls >= calls_due)
                or (spend_due and spend >= spend_due)
                or (delivered and calls)
            ):
                return
            reason = f"the other roles made {calls} model calls costing ${spend:.2f} since your last look"
            if delivered:
                reason += ", and a result was just delivered"
            since, watch.since = watch.since, time.time()
            watch.looking = threading.Thread(
                target=self._look, args=(backend, root, since, reason),
                name="argus-observer", daemon=True,
            )
            watch.looking.start()

    def _look(self, backend: Any, root: Path, since: float, reason: str) -> None:
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
                quiet, root=root, workdir=Path(self.supervisor._project_workdir()), since=since,
                reason=reason, model=_model(self.supervisor.reviewer_model),
            )
        except Exception:  # noqa: BLE001 - a failed look is skipped, not retried
            LOG.warning("the Observer could not look; it looks again after more work", exc_info=True)
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
