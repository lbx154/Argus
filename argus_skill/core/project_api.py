"""The one transaction that marks a Project done.

Before this module, "is this project finished?" had four different answers
depending on which vertical was running and which file you asked:

* ``lifecycle.json`` reaching ``ProjectState.DONE`` — written in exactly one
  place, and only for verticals whose completion gate is ``full_paper``;
* the versioned final-stage completion certificate in ``PIPELINE_STATE.json``,
  read by the planning cycle but never joined to the lifecycle sidecar;
* the Planner's ``project_done`` verdict, which stops the run loop;
* ``continuous.json``'s ``done_reason``, which stops the daemon.

Those are not redundant copies of one fact — they are four facts that happen to
share a name, and nothing reconciled them. This module makes the *write* side
single: everything that wants to mark a Project done goes through
:func:`complete_project`, which refuses anything the active vertical has not
declared sufficient.

Two deliberate non-goals, both of which would be regressions:

1. **This does not broaden when completion happens.** ``lifecycle.json``
   currently only reaches DONE on the paper track, and reaching DONE stops
   token allocation. Letting the twenty verticals that declare ``metric`` or
   ``none`` suddenly write DONE would park live daemons mid-mission. Callers
   pass the completion source they already had; the API decides only whether it
   is strong enough.
2. **The harness does not judge whether the work is good.** The required
   strength is read from the vertical's own ``completion_gate`` declaration and
   the evidence comes from the Reviewer. All this module contributes is the
   mechanical comparison of a declared requirement against a declared source,
   plus one atomic write.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .event_catalog import EventType

# Completion sources, weakest to strongest. The ordering is mechanical — it
# says which source *outranks* which, not which work is worth more. A source
# may satisfy any requirement at or below its own rank, so certifying a full
# paper also satisfies a vertical that only asked for a metric.
SOURCE_PLANNER_VERDICT = "planner_verdict"
SOURCE_VERTICAL_CERTIFICATE = "vertical_completion_certificate"
SOURCE_REVIEWER_FULL_PAPER = "reviewer_full_paper_gate"

_SOURCE_RANK: dict[str, int] = {
    SOURCE_PLANNER_VERDICT: 1,
    SOURCE_VERTICAL_CERTIFICATE: 2,
    SOURCE_REVIEWER_FULL_PAPER: 3,
}

# What each vertical-declared completion gate demands. Keys are the values
# ``vertical_completion_gate()`` can return; the vertical chooses, not us.
_GATE_REQUIRED_RANK: dict[str, int] = {
    "none": 1,
    "metric": 2,
    "full_paper": 3,
}

_UNKNOWN_GATE_RANK = 3
"""An unrecognised gate demands the strongest evidence.

Fail closed: a vertical that declares a gate this module has never heard of has
made a claim we cannot check, and the safe reading of an unreadable requirement
is the strictest one, not the loosest.
"""


@dataclass(frozen=True)
class CompletionSource:
    """What is claiming the Project is finished, and on what evidence."""

    kind: str
    evidence_refs: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class CompletionOutcome:
    """The result of one completion attempt. Refusals carry their reason."""

    accepted: bool
    reason: str
    required_gate: str = ""
    source_kind: str = ""
    certificate: dict[str, Any] = field(default_factory=dict)


def required_completion_gate(project_root: object, vertical: str) -> str:
    """The completion strength the *vertical* declares it needs.

    Read from the vertical module, never decided here. An unresolvable vertical
    returns ``full_paper``, matching ``vertical_completion_gate``'s own default
    and keeping the failure closed.
    """
    try:
        from ..verticals._base import load_vertical, vertical_completion_gate

        module = load_vertical(vertical, project_root=project_root)
        return vertical_completion_gate(module)
    except Exception:  # noqa: BLE001 — an unreadable declaration fails closed
        return "full_paper"


def evaluate_completion(
    *,
    project_root: object,
    vertical: str,
    source: CompletionSource,
) -> CompletionOutcome:
    """Whether ``source`` satisfies what ``vertical`` declared, and why not.

    Pure: reads the vertical declaration and compares ranks. Writes nothing, so
    a caller can ask before committing and so the refusal path is testable
    without a project on disk.
    """
    gate = required_completion_gate(project_root, vertical)
    required = _GATE_REQUIRED_RANK.get(gate, _UNKNOWN_GATE_RANK)
    offered = _SOURCE_RANK.get(str(source.kind or "").strip(), 0)
    if offered <= 0:
        return CompletionOutcome(
            accepted=False,
            reason=(
                f"completion source {source.kind!r} is not a recognised source; "
                f"expected one of {', '.join(sorted(_SOURCE_RANK))}"
            ),
            required_gate=gate,
            source_kind=source.kind,
        )
    if offered < required:
        return CompletionOutcome(
            accepted=False,
            reason=(
                f"vertical {vertical!r} declares completion gate {gate!r}, which "
                f"{source.kind!r} does not satisfy"
            ),
            required_gate=gate,
            source_kind=source.kind,
        )
    if not source.evidence_refs:
        return CompletionOutcome(
            accepted=False,
            reason=(
                "a completion claim must name the evidence it rests on; "
                f"{source.kind!r} named none"
            ),
            required_gate=gate,
            source_kind=source.kind,
        )
    return CompletionOutcome(
        accepted=True,
        reason="",
        required_gate=gate,
        source_kind=source.kind,
        certificate={
            "vertical": vertical,
            "required_gate": gate,
            "source": source.kind,
            "evidence_refs": list(source.evidence_refs),
            "detail": source.detail[:2000],
            "certified_at": time.time(),
        },
    )


def complete_project(
    *,
    memory_root: Path,
    project_root: object,
    vertical: str,
    source: CompletionSource,
    status: Any,
    reason: str = "",
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> CompletionOutcome:
    """Mark the Project done, or refuse and say why. The only DONE writer.

    ``status`` is the current :class:`ProjectStatus` the caller already
    computed — it needs the project workdir and live budget numbers, which the
    supervisor has and this module does not. Recomputing it here would mean a
    second implementation of status inference that could disagree with the one
    the rest of the tick used.

    Order matters: the claim is checked *before* anything is written, so a
    refused completion leaves the sidecar exactly as it was. A caller that
    wrote DONE first and validated afterwards would have to unwind, and the
    unwind is the step that gets skipped.
    """
    outcome = evaluate_completion(
        project_root=project_root,
        vertical=vertical,
        source=source,
    )
    if not outcome.accepted:
        _emit(
            on_event,
            {
                "type": EventType.PROJECT_COMPLETION_REFUSED,
                "vertical": vertical,
                "source": source.kind,
                "required_gate": outcome.required_gate,
                "reason": outcome.reason,
                "agent_layer": "supervisor",
            },
        )
        return outcome

    from datetime import datetime, timezone

    from ..life.project_lifecycle import (
        LifecycleEvent,
        ProjectState,
        apply_event,
    )
    from ..life.project_lifecycle_io import append_event as _append_event

    if status is None:
        return CompletionOutcome(
            accepted=False,
            reason="no current project status was supplied",
            required_gate=outcome.required_gate,
            source_kind=source.kind,
        )
    if status.state in (ProjectState.DONE, ProjectState.ARCHIVED):
        # Already terminal. Not an error — a second certification of the same
        # finished project is a no-op, not a reason to rewrite history.
        return CompletionOutcome(
            accepted=True,
            reason="project was already terminal",
            required_gate=outcome.required_gate,
            source_kind=source.kind,
            certificate=outcome.certificate,
        )

    event = LifecycleEvent(
        at=datetime.now(timezone.utc),
        from_state=status.state,
        to_state=ProjectState.DONE,
        reason=str(reason or source.kind),
    )
    try:
        _append_event(memory_root, new_status=apply_event(status, event), event=event)
    except OSError as exc:
        return CompletionOutcome(
            accepted=False,
            reason=f"could not persist completion: {type(exc).__name__}: {exc}",
            required_gate=outcome.required_gate,
            source_kind=source.kind,
        )

    _emit(
        on_event,
        {
            "type": EventType.PROJECT_COMPLETED,
            "vertical": vertical,
            "source": source.kind,
            "required_gate": outcome.required_gate,
            "evidence_refs": list(source.evidence_refs),
            "from_state": event.from_state.value,
            "agent_layer": "supervisor",
        },
    )
    return outcome


def _emit(
    on_event: Callable[[dict[str, Any]], None] | None,
    payload: dict[str, Any],
) -> None:
    if callable(on_event):
        on_event(payload)


__all__ = [
    "SOURCE_PLANNER_VERDICT",
    "SOURCE_REVIEWER_FULL_PAPER",
    "SOURCE_VERTICAL_CERTIFICATE",
    "CompletionOutcome",
    "CompletionSource",
    "complete_project",
    "evaluate_completion",
    "required_completion_gate",
]
