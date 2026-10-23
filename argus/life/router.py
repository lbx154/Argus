"""The Manager's front door: one classify call that fills the ``front_door`` form."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable, Literal

from ..core.handoff import HandOffActions
from ..roles.prompts.manager import build_front_door_prompt

log = logging.getLogger(__name__)


def _routing_diagnostic(
    message: str,
    failure_sink: Callable[[str], None] | None,
) -> None:
    log.warning("Manager front-door diagnostic: %s", message)
    if callable(failure_sink):
        failure_sink(message)


def _classifier_failure_detail(result: Any) -> str:
    """Return bounded, redacted runner evidence for a failed front-door call.

    A bare ``classifier backend failed`` hid actionable process failures such
    as an npm ``codex.cmd`` wrapper whose GUI-host PATH did not contain Node.
    Keep the fail-closed routing behavior, but retain enough local stderr for
    an operator to repair the configured runner without exposing credentials.
    """
    exit_code = getattr(result, "exit_code", "unknown")
    fragments: list[str] = []
    fatal = str(getattr(result, "fatal_error", "") or "").strip()
    if fatal:
        fragments.append(fatal)
    raw_stderr = getattr(result, "stderr_lines", None) or []
    if isinstance(raw_stderr, (str, bytes)):
        raw_stderr = [raw_stderr]
    try:
        stderr = "\n".join(
            str(line, errors="replace") if isinstance(line, bytes) else str(line)
            for line in raw_stderr
            if str(line).strip()
        ).strip()
    except TypeError:
        stderr = str(raw_stderr or "").strip()
    if stderr and not fatal:
        # ``fatal_error`` is the runner's authoritative summary and retains the
        # remote contract verbatim. Raw stderr is a bounded fallback for launch
        # failures (for example an npm wrapper that cannot find Node).
        fragments.append(stderr)
    detail = " | ".join(fragments)
    if detail:
        try:
            from ..core.secret_guard import known_secret_values, redact_secrets_text
            from ..tools.capability_vault import read_auth_json_key

            # Provider stderr can echo a raw Codex API key without an
            # `api_key=` label.  Read it only to redact the exact value before
            # this text reaches the cockpit; never render or persist it here.
            auth_key = read_auth_json_key()
            detail = redact_secrets_text(
                detail,
                known_values=(*known_secret_values(), auth_key),
            )
        except Exception:  # noqa: BLE001 — diagnostics must never alter routing
            pass
        detail = " ".join(detail.split())[:480]
    if fatal and detail:
        return detail
    return (
        f"classifier backend failed (exit {exit_code}): {detail}"
        if detail
        else f"classifier backend failed (exit {exit_code})"
    )


_CONFIG_ROLE_KNOBS = frozenset({"model", "effort"})


@dataclass(frozen=True)
class ConfigIntent:
    """A "change one of my own runtime knobs" request from the front-door form."""

    knob: str
    roles: tuple[str, ...]  # role-scoped knobs only; () = ALL roles / the shared default
    value: str  # target value, verbatim (backend / model id / effort / $amount / on|off)


ControlIntent = Literal["abort", "pause", "no_dispatch", "steer"]
SelfModeIntent = Literal[
    "reply",
    "inspect",
    "project_status",
    "argus_status",
    "host_status",
    "micro",
    "implement",
    "debug",
    "review",
    "synthesize",
]
ConfigDecision = ConfigIntent | tuple[ConfigIntent, ...] | None
LifetimeIntent = Literal["bounded", "standing"]
AuthorizationAction = Literal[
    "validator_repair",
    "acceptance_retry",
    "provenance_repair",
    "artifact_refresh",
    "resume_blocked_work",
]
OperatorQuestionPolicy = Literal["allow", "forbid"]


def _folded(text: str) -> str:
    return " ".join(str(text).casefold().split())


def _front_door_check(payload: dict[str, Any], *, message: str, active_mission: bool) -> None:
    """The front door's own rules: a control or a grant is the operator's explicit word, answered here."""
    control = payload.get("control")
    if (control or payload.get("authorization")) and payload["to"] == "team":
        raise ValueError("A control or a grant is answered by you: hand it to=manager.")
    settings = [(row["knob"], tuple(sorted(row.get("roles", ())))) for row in payload.get("config", ())]
    if len(set(settings)) < len(settings):
        raise ValueError("config: one entry per setting.")
    if settings and payload["intake"]["type"] == "standing_directive":
        raise ValueError("A setting change is intake.type=preference; config stores the setting itself.")
    if not control:
        return
    if _folded(control["quote"]) not in _folded(message):
        raise ValueError("control.quote must be the operator's own words giving the command, copied from the message.")
    if control["kind"] == "steer" and not active_mission:
        raise ValueError("No mission is running, so there is nothing to steer: leave control out.")
    if control["kind"] == "steer" and not control.get("directive"):
        raise ValueError("A steer needs its new direction in control.directive.")


def _config(rows: list[dict[str, Any]]) -> ConfigDecision:
    intents = tuple(
        ConfigIntent(
            knob=row["knob"],
            roles=tuple(dict.fromkeys(row.get("roles", ()))) if row["knob"] in _CONFIG_ROLE_KNOBS else (),
            value=row["value"],
        )
        for row in rows
    )
    return intents[0] if len(intents) == 1 else intents or None


def _sink(sink: Callable[[Any], None] | None, value: Any, failure_sink: Callable[[str], None] | None) -> None:
    """Hand ``value`` to an optional consumer; metadata never owns routing."""
    if not callable(sink):
        return
    try:
        sink(value)
    except Exception as exc:  # noqa: BLE001 - advisory metadata never owns routing
        _routing_diagnostic(f"front-door sink failed ({type(exc).__name__}: {exc})", failure_sink)


def classify_front_door(
    text: str,
    *,
    run_exec: Callable[[str, HandOffActions], Any],
    name_sink: Callable[[str], None] | None = None,
    lifetime_sink: Callable[[LifetimeIntent], None] | None = None,
    self_mode_sink: Callable[[SelfModeIntent], None] | None = None,
    reply_sink: Callable[[str], None] | None = None,
    greeting_sink: Callable[[str], None] | None = None,
    steering_sink: Callable[[str], None] | None = None,
    operator_question_policy_sink: Callable[[OperatorQuestionPolicy], None] | None = None,
    authorization_sink: Callable[[tuple[str, ...]], None] | None = None,
    intake_sink: Callable[[dict[str, Any]], None] | None = None,
    failure_sink: Callable[[str], None] | None = None,
    active_mission: bool = False,
    domain_sink: Callable[[dict[str, str]], None] | None = None,
    domain_prompt: str = "",
) -> "tuple[ConfigDecision, ControlIntent | None, str]":
    """One model call for every cheap front-door decision.

    ``run_exec(prompt, actions)`` runs the call with ``actions`` bound. Only
    the form it filled is read; without one nothing is applied and the work
    takes the team route. The classifier writes an operator-facing reply only
    where ``reply_sink`` allows one.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None, None, "complex"
    actions = HandOffActions(
        "front_door", check=partial(_front_door_check, message=cleaned, active_mission=active_mission),
    )
    prompt = build_front_door_prompt(
        cleaned, active_mission=active_mission, allow_reply=callable(reply_sink),
    ) + domain_prompt
    try:
        result = run_exec(prompt, actions)
    except Exception as exc:  # noqa: BLE001
        _routing_diagnostic(f"{type(exc).__name__}: {exc}", failure_sink)
        return None, None, "complex"
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        _routing_diagnostic(_classifier_failure_detail(result), failure_sink)
        return None, None, "complex"
    if actions.handoff is None:
        _routing_diagnostic("classifier ended without handing off a decision", failure_sink)
        return None, None, "complex"
    form = actions.handoff.payload
    intent = _config(form.get("config", ()))
    control: ControlIntent | None = form.get("control", {}).get("kind")
    route = "simple" if form["to"] == "manager" else "complex"
    authorization = tuple(dict.fromkeys(form.get("authorization", ())))
    if authorization:
        _sink(authorization_sink, authorization, failure_sink)
    self_mode: SelfModeIntent | None = form.get("self_mode", "inspect") if route == "simple" else None
    if self_mode is not None:
        _sink(self_mode_sink, self_mode, failure_sink)
    reply = form.get("reply", "").strip()
    if (
        reply and self_mode == "reply" and intent is None
        and control in {None, "no_dispatch"} and not authorization
    ):
        _sink(reply_sink, reply, failure_sink)
    # One bit survives the single dispatch topology: whether the campaign
    # stops when its Planner certifies the outcome (bounded) or keeps going
    # (standing). A steering turn stays on the SELF control path but may
    # explicitly promote the active campaign to standing.
    lifetime = form.get("lifetime", "bounded")
    if route == "complex" or (control == "steer" and lifetime == "standing"):
        _sink(lifetime_sink, lifetime, failure_sink)
    if form.get("greeting") and reply and route == "simple" and intent is None and control is None:
        _sink(greeting_sink, reply, failure_sink)
    if control == "steer":
        _sink(steering_sink, form["control"]["directive"], failure_sink)
    if "operator_question_policy" in form:
        _sink(operator_question_policy_sink, form["operator_question_policy"], failure_sink)
    if form.get("name"):
        _sink(name_sink, form["name"], failure_sink)
    if "domain" in form:
        _sink(domain_sink, {"action": "none", **form["domain"]}, failure_sink)
    intake = form["intake"]
    _sink(intake_sink, {
        "kind": intake["type"],
        "scope": intake["scope"],
        "applies_to_roles": tuple(dict.fromkeys(intake.get("roles", ()))) or "all",
        "preference_kind": intake.get("preference_kind", "workflow"),
        "preference_value": intake.get("preference_value", ""),
        "target_revision": intake.get("revoke_revision", 0),
    }, failure_sink)
    return intent, control, route


__all__ = [
    "ConfigIntent",
    "ConfigDecision",
    "ControlIntent",
    "SelfModeIntent",
    "LifetimeIntent",
    "AuthorizationAction",
    "OperatorQuestionPolicy",
    "classify_front_door",
]
