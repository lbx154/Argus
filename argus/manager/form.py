"""The Manager's own ``hand_off`` form: what Argus does after the Manager's turn.

The Manager answers the operator in its own session. It files the ``manager``
form only when Argus must act: hand a ticket to the team, ask the operator,
change a setting, control the running mission, record a grant or a lasting rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any

from ..core.handoff import HandOffActions

_ROLE_KNOBS = frozenset({"model", "effort"})


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


def _check(payload: dict[str, Any], *, message: str, active_mission: bool, team_allowed: bool) -> None:
    """The rules Argus enforces on the Manager's call; each refusal is one sentence the model can act on."""
    to = payload["to"]
    if to == "team" and not team_allowed:
        raise ValueError("The operator asked you to answer this yourself: do not hand it to the team.")
    if (to == "team") != ("goal" in payload and "acceptance" in payload):
        raise ValueError("to=team goes with goal and acceptance, and they only with to=team.")
    control = payload.get("control")
    if (control or payload.get("authorization") or payload.get("config")) and to == "team":
        raise ValueError("A control, grant or setting is applied by Argus: file it with to=manager.")
    settings = [(row["knob"], tuple(sorted(row.get("roles", ())))) for row in payload.get("config", ())]
    if len(set(settings)) < len(settings):
        raise ValueError("config: one entry per setting.")
    if settings and payload.get("intake", {}).get("type") == "standing_directive":
        raise ValueError("A setting change is intake.type=preference; config stores the setting itself.")
    if not control:
        return
    if _folded(control["quote"]) not in _folded(message):
        raise ValueError("control.quote must be the operator's own words giving the command, copied from the message.")
    if control["kind"] == "steer" and not active_mission:
        raise ValueError("No mission is running, so there is nothing to steer: leave control out.")
    if control["kind"] == "steer" and not control.get("directive"):
        raise ValueError("A steer needs its new direction in control.directive.")


def manager_actions(
    message: str, *, active_mission: bool, team_allowed: bool = True, operator_available: bool | None = None,
) -> HandOffActions:
    """The ``hand_off`` tool bound to one Manager turn on ``message``."""
    return HandOffActions(
        "manager", operator_available=operator_available,
        check=partial(_check, message=message, active_mission=active_mission, team_allowed=team_allowed),
    )


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


__all__ = ["ConfigIntent", "config_intents", "manager_actions", "ticket"]
