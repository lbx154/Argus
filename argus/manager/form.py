"""The Manager's own ``hand_off`` form: what Argus does after the Manager's turn.

The Manager answers the operator in its own session. It files the ``manager``
form only when Argus must act: hand a ticket to the team, ask the operator,
change a setting, control the running mission, record a grant or a lasting rule,
apply the answer to a paused task's question, or settle a workflow setup.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Mapping, Sequence

from ..core.handoff import HandOffActions

_ROLE_KNOBS = frozenset({"model", "effort"})
# The ticket's routing fields, handed to the Manager's route check as they are.
_ROUTE_FIELDS = (
    "summary", "assumptions", "vertical", "domain", "workflow_mode", "start_stage",
    "research_target_level", "research_direction_mode", "target_venue", "require_independent_review",
    "operator_decides_stages",
)


@dataclass(frozen=True)
class ConfigIntent:
    """One runtime setting the operator changes."""

    knob: str
    roles: tuple[str, ...]  # role-scoped knobs only; () = ALL roles / the shared default
    value: str  # target value, verbatim (backend / model id / effort / $amount / on|off)


def config_intents(rows: Any) -> tuple[ConfigIntent, ...]:
    return tuple(
        ConfigIntent(
            knob=row["knob"],
            roles=tuple(dict.fromkeys(row.get("roles", ()))) if row["knob"] in _ROLE_KNOBS else (),
            value=row["value"],
        )
        for row in rows
    )


def _folded(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _route(payload: dict[str, Any]) -> dict[str, Any]:
    """The routing decision a ``to=team`` ticket carries."""
    return {
        **{key: payload[key] for key in _ROUTE_FIELDS if key in payload},
        "execution_task": ticket(payload),
        "precise_constraints": payload.get("constraints", ()),
        "session_title": payload.get("name", ""),
    }


def _check_answer(payload: dict[str, Any], waiting: Mapping[str, Sequence[str]]) -> None:
    """``answer`` names a waiting task and, where its question offers answers, one of them."""
    answer = payload["answer"]
    if payload["to"] != "manager":
        raise ValueError("An answer is applied by Argus: file it with to=manager.")
    if answer["item"] not in waiting:
        raise ValueError("answer.item: name a task listed as waiting on the operator.")
    offered = waiting[answer["item"]]
    if "option" in answer and answer["option"] not in offered:
        raise ValueError(f"answer.option: one of {', '.join(offered) or 'none'}; leave it out for an answer of their own.")


def _check_setup(payload: dict[str, Any], setting_up: bool) -> None:
    """While a workflow is set up, a question offers two to four answers and a prepared setup is whole."""
    setup = payload.get("workflow_setup")
    if setup and not setting_up:
        raise ValueError("No workflow setup is under way: leave workflow_setup out.")
    if setup and payload["to"] != "manager":
        raise ValueError("workflow_setup is applied by Argus: file it with to=manager.")
    if setup and setup["action"] == "prepare" and not all(
            key in setup for key in ("name", "purpose", "brief", "title", "summary")):
        raise ValueError("workflow_setup: prepare goes with name, purpose, brief, title and summary.")
    if setting_up and payload["to"] == "operator" and not 2 <= len(payload["ask"]["options"]) <= 4:
        raise ValueError("ask: offer two to four answers to the setup question, your recommendation first.")


def _check(payload: dict[str, Any], *, message: str, active_mission: bool, team_allowed: bool,
           route: Callable[[dict[str, Any]], Any] | None,
           stage: Callable[[dict[str, Any]], Any] | None, waiting: Mapping[str, Sequence[str]],
           setting_up: bool) -> Any:
    """The rules Argus enforces on the Manager's call; each refusal is one sentence the model can act on."""
    to = payload["to"]
    if "answer" in payload:
        _check_answer(payload, waiting)
    _check_setup(payload, setting_up)
    if to == "team" and not team_allowed:
        raise ValueError("The operator asked you to answer this yourself: do not hand it to the team.")
    if (to == "team") != all(key in payload for key in ("goal", "acceptance", "vertical")):
        raise ValueError("to=team goes with goal, acceptance and vertical, and they only with to=team.")
    control = payload.get("control")
    if (control or payload.get("authorization") or payload.get("config")) and to == "team":
        raise ValueError("A control, grant or setting is applied by Argus: file it with to=manager.")
    settings = [(row["knob"], tuple(sorted(row.get("roles", ())))) for row in payload.get("config", ())]
    if len(set(settings)) < len(settings):
        raise ValueError("config: one entry per setting.")
    if settings and payload.get("intake", {}).get("type") == "standing_directive":
        raise ValueError("A setting change is intake.type=preference; config stores the setting itself.")
    if to == "team":
        return route(_route(payload)) if route else None
    if "stage" in payload and to != "manager":
        raise ValueError("A stage ruling is carried out by Argus: file it with to=manager.")
    ruling = stage(payload) if "stage" in payload and stage else None
    if not control:
        return ruling
    if _folded(control["quote"]) not in _folded(message):
        raise ValueError("control.quote must be the operator's own words giving the command, copied from the message.")
    if control["kind"] == "steer" and not active_mission:
        raise ValueError("No mission is running, so there is nothing to steer: leave control out.")
    if control["kind"] == "steer" and not control.get("directive"):
        raise ValueError("A steer needs its new direction in control.directive.")
    return ruling


def manager_actions(
    message: str, *, active_mission: bool, team_allowed: bool = True, operator_available: bool | None = None,
    route: Callable[[dict[str, Any]], Any] | None = None,
    stage: Callable[[dict[str, Any]], Any] | None = None,
    waiting: Mapping[str, Sequence[str]] | None = None, setting_up: bool = False,
) -> HandOffActions:
    """The ``hand_off`` tool bound to one Manager turn on ``message``.

    ``route`` checks a ticket's routing decision (``Manager.adopt_route``); what
    it returns is the handoff's value, the decision the team is dispatched with.
    ``stage`` checks a stage ruling (``Manager.stage_ruling``).
    ``waiting`` maps each task paused on a question to the ids of the answers it
    offers; ``setting_up`` is a workflow setup under way with the operator.
    """
    return HandOffActions(
        "manager", operator_available=True if setting_up else operator_available,
        check=partial(_check, message=message, active_mission=active_mission, team_allowed=team_allowed,
                      route=route, stage=stage, waiting=waiting or {}, setting_up=setting_up),
    )


def waiting_questions(items: Sequence[Any]) -> tuple[dict[str, list[str]], str]:
    """The tasks paused on a question for the operator: their offered answer ids, and how the Manager is told."""
    waiting: dict[str, list[str]] = {}
    lines: list[str] = []
    for item in items:
        options = [option for option in item.operator_decision.get("options") or () if isinstance(option, dict)]
        waiting[item.id] = [str(option.get("id") or "") for option in options]
        lines.append(f"- task {item.id}, {item.title}: {item.pending_question.strip()}")
        lines.extend(f"  - answer {option.get('id')}: {option.get('label')}" for option in options)
    if not lines:
        return waiting, ""
    return waiting, "Paused tasks waiting on the operator (asking-the-operator rules):\n" + "\n".join(lines)


def ticket(payload: Any) -> str:
    """The team's work order, from a ``to=team`` call."""
    sections = [payload["goal"].strip()]
    for title, key in (("Acceptance", "acceptance"), ("Constraints", "constraints"), ("Read first", "context")):
        if payload.get(key):
            sections.append(f"{title}:\n" + "\n".join(f"- {line}" for line in payload[key]))
    if payload.get("assumptions"):
        sections.append("Assumptions:\n" + "\n".join(
            f"- {row['chose']} (not: {row['instead_of']})" for row in payload["assumptions"]
        ))
    return "\n\n".join(sections)


__all__ = ["ConfigIntent", "config_intents", "manager_actions", "ticket", "waiting_questions"]
