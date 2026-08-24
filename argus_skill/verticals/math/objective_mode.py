"""What "done" means for a mathematics project — a choice, not a default.

Two kinds of work get run through the same vertical and need opposite
completion rules:

``targeted``
    One named goal: prove this conjecture, or refute it. Everything else —
    lemmas, computations, ruled-out methods — is progress only insofar as it
    shrinks the gap to that goal. Ruling out a sufficient criterion is not
    solving the problem, and a project that drifts into perfecting such a
    refutation has stopped working on what it was asked to work on.

``exploratory``
    A direction rather than a target: find what is true near here, produce
    partial results, bounds, counterexamples, or structure worth reporting.
    There is no single G to close, so demanding one would reject genuinely
    useful work.

Guessing between them makes one of the two behave badly, so the operator
picks. An unset mode is reported rather than assumed: the two have different
completion bars, and silently choosing either is wrong in one direction.

The operator picks through the CLI at the bottom of this module, and that
choice is authoritative: nothing overwrites it. No model decides it either. A
vertical, a research target level, and a workflow mode are all read out of the
operator's request by the Manager because a wrong guess there costs a
misrouted mission; a wrong guess *here* silently changes what "finished"
means, and the project reports success against a bar nobody chose.

That used to be the whole channel, and it made the vertical unusable in the
product: a math project created through the real front door had no mode, every
stage refused to complete, and the only remedy was shell access to the host.
"The operator decides" had been implemented as "an operator with shell access
decides". So there is now a second, narrower way in —
:func:`adopt_operator_objective`, which restates the operator's own request as
a ``targeted`` goal when nobody has chosen anything. It is transcription, not
choice: it never overwrites a resolved objective, it records itself as
``transcribed_from_request`` so the two are distinguishable on disk, and it
only ever writes the *stronger* of the two bars, so nothing can be certified
under a weaker rule than the operator would have picked.

Waiting still has a cost and it is still paid up front: until the mode is set
by either route, every math stage refuses to complete — ``scope`` included,
because the objective gate in :mod:`argus_skill.verticals.math.stages` runs
before the stage dispatch. That is the intended shape. A project that ran
``scope`` to completion under an unchosen bar would have to be re-judged
afterwards against whichever bar was later picked, and the retrieval it did is
exactly the work that depends on knowing whether there is one goal to close.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from ...core.pipeline_state import read_pipeline_state, write_pipeline_state

__all__ = [
    "MATH_OBJECTIVE_MODES",
    "MODE_COMPLETION",
    "SOURCE_KEY",
    "SOURCE_OPERATOR",
    "SOURCE_TRANSCRIBED",
    "MathObjective",
    "adopt_operator_objective",
    "main",
    "normalize_mode",
    "resolve_objective",
    "set_objective",
]

MATH_OBJECTIVE_MODES = ("targeted", "exploratory")

#: Who chose the mode. An operator choice is authoritative and is never
#: overwritten; a transcription is the runtime restating the operator's own
#: request so the project is not stalled before anyone can be asked.
SOURCE_KEY = "math_objective_source"
SOURCE_OPERATOR = "operator"
SOURCE_TRANSCRIBED = "transcribed_from_request"

#: What completion requires under each mode. Injected verbatim, so it is
#: written to be read by a reviewer deciding `done`.
MODE_COMPLETION = {
    "targeted": (
        "the named goal is proved or refuted; partial results and ruled-out "
        "methods are progress only if they shrink the gap to it"
    ),
    "exploratory": (
        "a substantive, correctly-scoped mathematical result in the stated "
        "direction; no single named goal has to close"
    ),
}

def normalize_mode(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in MATH_OBJECTIVE_MODES else None


@dataclass(frozen=True)
class MathObjective:
    """The selected mode and, for ``targeted``, the goal it must close."""

    mode: str | None
    goal: str = ""
    resolved: bool = True
    note: str = ""

    @property
    def is_targeted(self) -> bool:
        return self.mode == "targeted"

    @property
    def completion_rule(self) -> str:
        return MODE_COMPLETION.get(self.mode or "", "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "goal": self.goal,
            "resolved": self.resolved,
            "note": self.note,
        }


def _read_state(project_root: object) -> dict[str, Any]:
    try:
        payload = read_pipeline_state(project_root)
    except (OSError, ValueError):
        return {}
    return payload


def resolve_objective(project_root: object) -> MathObjective:
    """Read the selected mode, or report that nobody has chosen one."""
    payload = _read_state(project_root)
    mode = normalize_mode(payload.get("math_objective_mode"))
    goal = str(payload.get("math_goal") or "").strip()
    if mode is None:
        return MathObjective(
            mode=None,
            goal=goal,
            resolved=False,
            note=(
                "no math objective mode selected; a targeted project and an "
                "exploratory one have different completion bars, so ask the "
                "operator which this is instead of assuming. The operator "
                "settles it with `python -m "
                "argus_skill.verticals.math.objective_mode set --mode "
                "targeted --goal \"<the statement to prove or refute>\"` or "
                "`--mode exploratory`"
            ),
        )
    if mode == "targeted" and not goal:
        return MathObjective(
            mode=mode,
            goal="",
            resolved=False,
            note=(
                "targeted mode needs the goal it must close; without it there is "
                "nothing to measure the gap against and the project will drift "
                "into whichever subproblem is most tractable. Restate it with "
                "`python -m argus_skill.verticals.math.objective_mode set "
                "--mode targeted --goal \"<the statement to prove or refute>\"`"
            ),
        )
    return MathObjective(mode=mode, goal=goal)


def set_objective(
    project_root: object, *, mode: Any, goal: str = "", source: str = SOURCE_OPERATOR
) -> MathObjective:
    """Persist the operator's choice into the Manager-owned pipeline state.

    Written the way ``skills.vertical_select.persist_vertical`` writes the same
    file — temp file plus ``os.replace`` — because that is now not the only
    writer of ``.argus/PIPELINE_STATE.json``. A plain ``write_text`` truncates
    before it fills, so a reader arriving in that window (``resolve_objective``,
    ``resolve_vertical``, ``stage_machine.current_stage``, the Manager's own raw
    reader) sees an empty or half-written file. Two of those four RAISE on
    invalid JSON rather than falling back, so the torn read is not hypothetical
    damage — it takes down a completion gate.

    Read-modify-write is still not atomic against a concurrent ``persist_vertical``:
    both read the payload, change their own keys, and rewrite the whole object,
    so an interleaving loses one side's edit. That race is left alone. This is an
    operator command run once at project setup, ``persist_vertical`` runs on the
    Manager's vertical decision, and the two do not overlap in normal operation;
    a lock file guarding a once-per-project write against a once-per-decision
    write would be machinery bought for nothing.
    """
    normalized = normalize_mode(mode)
    if normalized is None:
        raise ValueError(
            f"unknown math objective mode {mode!r}; expected one of "
            f"{', '.join(MATH_OBJECTIVE_MODES)}"
        )
    if normalized == "targeted" and not goal.strip():
        raise ValueError(
            "targeted mode requires the goal statement it must prove or refute"
        )
    root = Path(str(project_root))
    payload = _read_state(root)
    payload["math_objective_mode"] = normalized
    payload[SOURCE_KEY] = (
        source if source in (SOURCE_OPERATOR, SOURCE_TRANSCRIBED) else SOURCE_OPERATOR
    )
    if goal.strip():
        payload["math_goal"] = goal.strip()
    write_pipeline_state(root, payload)
    return resolve_objective(root)


def adopt_operator_objective(project_root: object, request: str) -> MathObjective:
    """Restate the operator's own request as the objective, once, if nobody has.

    ``set_objective`` had exactly one production entry point: the argparse CLI
    at the bottom of this module. Nothing in the Manager, front door, webapi,
    daemon or any UI called it, and no agent prompt named the concept. Meanwhile
    an unset mode blocks *every* math stage — ``scope`` included, by design —
    and ``_ensure_stage_completion`` turns that into a ``StageCompletionError``
    the operator never sees. So a math project created through the real product
    could not close a single stage, and the only remedy was to shell into the
    host and run a module CLI. Every testbed run that appeared to work did so
    because a development-time launch harness wrote the mode before the Manager
    was ever invoked — a harness self-described as "not a fixture".

    "The operator decides" was implemented as "an operator with shell access
    decides", which is not the same thing. This is the difference: the operator
    already stated what they wanted when they opened the project, and this
    transcribes that statement rather than inventing one. The runtime is
    restating, not choosing — which is why the mode it writes is recorded as
    ``transcribed_from_request`` and why an operator value is never touched.

    The transcription is always ``targeted``, and that direction is deliberate:
    targeted is the *strictly stronger* bar (it additionally demands the named
    goal be proved or refuted — see ``_targeted_goal_closure_issues``), so a
    transcription can never let a project report success against a weaker bar
    than the operator would have picked. Loosening it to ``exploratory`` stays
    an explicit operator act, because that is the direction where a wrong guess
    costs something.

    Idempotent and non-destructive: a resolved objective from any source is
    left alone, so re-dispatching a mission never rewrites a goal mid-project.
    """
    root = Path(str(project_root))
    payload = _read_state(root)
    if normalize_mode(payload.get("math_objective_mode")) is not None:
        return resolve_objective(root)
    statement = " ".join(str(request or "").split())
    if not statement:
        return resolve_objective(root)
    return set_objective(
        root, mode="targeted", goal=statement, source=SOURCE_TRANSCRIBED
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m argus_skill.verticals.math.objective_mode",
        description=(
            "Choose what finishing means for this mathematics project, or "
            "report what is currently chosen."
        ),
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="project root holding .argus/PIPELINE_STATE.json (default: .)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "show",
        help=(
            "print the selected mode and goal, or why the project is not "
            "runnable yet"
        ),
    )

    setter = sub.add_parser("set", help="record the operator's choice")
    setter.add_argument(
        "--mode",
        required=True,
        choices=MATH_OBJECTIVE_MODES,
        help=(
            "targeted: one named goal to prove or refute. exploratory: a "
            "direction, with no single goal that has to close"
        ),
    )
    setter.add_argument(
        "--goal",
        default="",
        help=(
            "for targeted mode, the statement the project must prove or "
            "refute, stated in full. Every gap measurement and the proof "
            "graph's root are checked against this text, so a paraphrase "
            "here becomes a different problem downstream"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Set or report the objective mode.

    Exit 1 on an unresolved objective — including a bare ``show`` on a project
    that has not chosen one. A caller scripting project setup can then test the
    status rather than parse the note, and the note still says which command
    clears it.
    """
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    root = args.project_root.expanduser().resolve()
    try:
        if args.command == "set":
            objective = set_objective(root, mode=args.mode, goal=args.goal)
        else:
            objective = resolve_objective(root)
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    payload = dict(objective.as_dict())
    payload["ok"] = objective.resolved
    payload["completion_rule"] = objective.completion_rule
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if objective.resolved else 1


if __name__ == "__main__":
    raise SystemExit(main())
