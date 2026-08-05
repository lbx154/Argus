"""Manager front-door parsers and runtime calls.

Prompt builders are re-exported from :mod:`argus_skill.roles.prompts.manager`
for source compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal

from ..roles.prompts.manager import (
    _IDENTITY_GUARD as _PROMPT_IDENTITY_GUARD,
)
from ..roles.prompts.manager import (
    build_chat_prompt,
    build_classify_prompt,
    build_config_intent_prompt,
    build_front_door_prompt,
    build_route_prompt,
    build_simple_prompt,
)

_IDENTITY_GUARD = _PROMPT_IDENTITY_GUARD


def _route_from_token(token: str) -> str:
    """``SELF``/``SIMPLE`` → ``"simple"``; anything else (TEAM / COMPLEX /
    unrecognized) → ``"complex"`` (the safe default that never routes work
    needing review to a lone worker). Shared by ``classify_route`` and
    ``classify_front_door`` so the two paths can never drift."""
    return "simple" if str(token or "").upper() in {"SELF", "SIMPLE"} else "complex"


def classify_route(
    text: str,
    *,
    run_exec: Callable[[str], Any],
) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "complex"
    try:
        result = run_exec(build_route_prompt(cleaned))
    except Exception:  # noqa: BLE001
        return "complex"
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return "complex"
    return _route_from_token(_first_alpha_token(_extract_answer(result)))


def classify_is_conversational(
    text: str,
    *,
    run_exec: Callable[[str], Any],
) -> bool:
    """Is ``text`` a conversational turn (greeting / capability question / ack)
    rather than a real task to execute?

    Biases hard toward ``False`` (TASK) — the safe default. Empty input, a
    classify error, a non-zero exit, or any answer that is not exactly ``CHAT``
    all resolve to TASK, so a real task is never silently answered as chat
    instead of being carried out.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    try:
        result = run_exec(build_classify_prompt(cleaned))
    except Exception:  # noqa: BLE001
        return False
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return False
    return _first_alpha_token(_extract_answer(result)).upper() == "CHAT"




def _extract_answer(result: Any) -> str:
    msg = getattr(result, "last_agent_message", None)
    if not msg:
        msgs = getattr(result, "agent_messages", None) or []
        msg = msgs[-1] if msgs else ""
    return str(msg or "")


def _first_alpha_token(text: str) -> str:
    token = ""
    for ch in str(text or "").strip():
        if ch.isalpha():
            token += ch
        elif token:
            break
    return token


# ── config-intent: LLM-decides "change one of my own runtime knobs" ───────────
#
# Argus's cockpit-editable surface, phrased for the operator. Role-scoped knobs
# take a role list; global knobs do not. This is the ONE place natural-language
# config changes are recognized — no keyword/regex handlers (an LLM decides
# intent from any wording, and a bare mention of a model/backend is NOT a
# switch). Mirrors classify_is_conversational/route: one low-reasoning call,
# biased hard toward None so real work is never swallowed as a config change.

_CONFIG_ROLE_KNOBS = frozenset({"backend", "model", "effort"})
_CONFIG_GLOBAL_KNOBS = frozenset(
    {
        "global_daily_cap",
        "max_daemons",
        "codex_daily_requests",
        "copilot_daily_requests",
        "copilot_daily_premium",
        "safe_mode",
        "show_reasoning",
        "telegram",
    }
)
_CONFIG_KNOBS = _CONFIG_ROLE_KNOBS | _CONFIG_GLOBAL_KNOBS
_CONFIG_ROLES = frozenset({"manager", "planner", "engineer", "reviewer"})


@dataclass(frozen=True)
class ConfigIntent:
    """A parsed "change one of Argus's own runtime knobs" request."""

    knob: str  # see _CONFIG_KNOBS
    roles: tuple[str, ...]  # role-scoped knobs only; () = ALL roles / the shared default
    value: str  # target value, verbatim (backend / model id / effort / $amount / on|off)


ControlIntent = Literal["abort", "no_dispatch", "steer"]
LifetimeIntent = Literal["bounded", "standing"]
AuthorizationAction = Literal[
    "validator_repair",
    "acceptance_retry",
    "provenance_repair",
    "artifact_refresh",
    "resume_blocked_work",
]
_AUTHORIZATION_ACTIONS = {
    "validator_repair",
    "acceptance_retry",
    "provenance_repair",
    "artifact_refresh",
    "resume_blocked_work",
}


_GREETING_REPLIES = {
    "zh": "你好，我是 Argus Manager。",
    "ja": "こんにちは、Argus Managerです。",
    "ko": "안녕하세요, Argus Manager입니다.",
    "default": "Hi, I'm Argus Manager.",
}


def _greeting_reply(message: str) -> str:
    text = message or ""
    if any("\u3040" <= ch <= "\u30ff" for ch in text):
        language = "ja"
    elif any("\uac00" <= ch <= "\ud7af" for ch in text):
        language = "ko"
    elif any("\u3400" <= ch <= "\u9fff" for ch in text):
        language = "zh"
    else:
        language = "default"
    return _GREETING_REPLIES[language]


def _parse_config_line(line: str) -> "ConfigIntent | None":
    """Parse ONE ``SET <knob> <roles> <value>`` line into a ``ConfigIntent``.

    Returns ``None`` for ``NONE`` / empty / malformed. Shared by
    ``classify_config_intent`` and ``classify_front_door`` so the two paths can
    never drift on what counts as a valid config write."""
    line = (line or "").strip()
    if not line or line.upper() == "NONE":
        return None
    parts = line.split(maxsplit=3)
    if len(parts) < 4 or parts[0].upper() != "SET":
        return None
    knob = parts[1].strip().lower()
    if knob not in _CONFIG_KNOBS:
        return None
    roles_raw = parts[2].strip().lower()
    if knob in _CONFIG_ROLE_KNOBS:
        if roles_raw == "all":
            roles: tuple[str, ...] = ()
        else:
            roles = tuple(
                r for r in (tok.strip() for tok in roles_raw.split(",")) if r in _CONFIG_ROLES
            )
            if not roles:
                return None
    else:
        roles = ()  # global knob — roles field ("-") is ignored
    value = parts[3].strip().strip("`\"'")
    if not value:
        return None
    return ConfigIntent(knob=knob, roles=roles, value=value)


def classify_config_intent(
    text: str,
    *,
    run_exec: Callable[[str], Any],
) -> ConfigIntent | None:
    """Does this free text ask to change one of Argus's own runtime knobs?

    Intent recognition, not keyword matching: one low-reasoning model call
    decides — never a substring/regex guess — so a genuine request phrased in
    ANY wording is caught, and a message that merely mentions a model/backend/
    setting (or names one as part of a real task) is not misread as a config
    change. Biases hard toward ``None`` on any ambiguity, error, or malformed
    answer, so the message then flows through the normal chat/task path — the
    safe default, mirroring ``classify_is_conversational``/``classify_route``.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    try:
        result = run_exec(build_config_intent_prompt(cleaned))
    except Exception:  # noqa: BLE001
        return None
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return None
    answer = _extract_answer(result).strip()
    line = next((ln.strip() for ln in answer.splitlines() if ln.strip()), "")
    return _parse_config_line(line)


def _line_after_prefix(answer: str, prefix: str) -> "str | None":
    """First line whose stripped form starts (case-insensitively) with
    ``prefix``, returned with the prefix removed and stripped. ``None`` when no
    such line exists — the caller then applies that axis's safe default."""
    up = prefix.upper()
    for ln in str(answer or "").splitlines():
        s = ln.strip()
        if s.upper().startswith(up):
            return s[len(prefix) :].strip()
    return None


def _parse_authorization_line(line: str | None) -> tuple[str, ...]:
    value = str(line or "").strip()
    if not value or value.upper() == "NONE":
        return ()
    parts = value.split(maxsplit=1)
    if len(parts) != 2 or parts[0].upper() != "AUTHORIZE":
        return ()
    actions = tuple(dict.fromkeys(
        token.strip().lower()
        for token in parts[1].split(",")
        if token.strip().lower() in _AUTHORIZATION_ACTIONS
    ))
    return actions


def classify_front_door(
    text: str,
    *,
    run_exec: Callable[[str], Any],
    name_sink: Callable[[str], None] | None = None,
    lifetime_sink: Callable[[LifetimeIntent], None] | None = None,
    greeting_sink: Callable[[str], None] | None = None,
    steering_sink: Callable[[str], None] | None = None,
    authorization_sink: Callable[[tuple[str, ...]], None] | None = None,
    active_mission: bool = False,
) -> "tuple[ConfigIntent | None, ControlIntent | None, str]":
    """One model call for every cheap front-door decision.

    The return shape stays backward-compatible; optional sinks expose reusable
    routing metadata. The classifier never writes an operator-facing reply.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None, None, "complex"
    try:
        result = run_exec(
            build_front_door_prompt(cleaned, active_mission=active_mission)
        )
    except Exception:  # noqa: BLE001
        return None, None, "complex"
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return None, None, "complex"
    answer = _extract_answer(result)
    config_line = _line_after_prefix(answer, "CONFIG:")
    control_line = _line_after_prefix(answer, "CONTROL:")
    authorization_line = _line_after_prefix(answer, "AUTHORIZATION:")
    steering_line = _line_after_prefix(answer, "STEER_DIRECTIVE:")
    route_line = _line_after_prefix(answer, "ROUTE:")
    lifetime_line = _line_after_prefix(answer, "LIFETIME:")
    greeting_line = _line_after_prefix(answer, "GREETING:")
    name_line = _line_after_prefix(answer, "NAME:")
    intent = _parse_config_line(config_line) if config_line is not None else None
    control_token = (
        str(control_line or "").strip().upper().replace("-", "_")
    )
    control: ControlIntent | None
    if control_token.startswith("ABORT"):
        control = "abort"
    elif control_token.startswith(("NO_DISPATCH", "NO DISPATCH", "NODISPATCH")):
        control = "no_dispatch"
    elif control_token.startswith("STEER"):
        control = "steer"
    else:
        control = None
    route = (
        _route_from_token(_first_alpha_token(route_line)) if route_line is not None else "complex"
    )
    if control in {"abort", "no_dispatch", "steer"}:
        route = "simple"
    authorization = _parse_authorization_line(authorization_line)
    if authorization:
        route = "simple"
        if callable(authorization_sink):
            try:
                authorization_sink(authorization)
            except Exception:  # noqa: BLE001 - advisory metadata never owns routing
                pass
    lifetime: LifetimeIntent | None = None
    if route == "complex":
        # Missing/malformed output keeps the conservative standing default, but
        # an explicit BOUNDED verdict is authoritative and routes through the
        # existing bounded-DAG path without paying for a second model call.
        lifetime_token = _first_alpha_token(lifetime_line).upper()
        lifetime = "bounded" if lifetime_token == "BOUNDED" else "standing"
    if callable(lifetime_sink) and lifetime is not None:
        try:
            lifetime_sink(lifetime)
        except Exception:  # noqa: BLE001 - advisory metadata never owns routing
            pass
    greeting_token = str(greeting_line or "").strip().upper()
    if (
        callable(greeting_sink)
        and greeting_token == "GREETING"
        and route == "simple"
        and intent is None
        and control is None
    ):
        try:
            greeting_sink(_greeting_reply(cleaned))
        except Exception:  # noqa: BLE001 - optional one-call greeting path only
            pass
    steering = str(steering_line or "").strip()
    steering_token = steering.rstrip(".。!！").upper()
    if (
        callable(steering_sink)
        and control == "steer"
        and steering
        and steering_token not in {"NONE", "N/A", "NA", "NULL"}
        and len(steering) <= 1600
    ):
        try:
            steering_sink(steering)
        except Exception:  # noqa: BLE001 - advisory metadata never owns routing
            pass
    if callable(name_sink) and name_line:
        try:
            name_sink(name_line)
        except Exception:  # noqa: BLE001 - cosmetic metadata never owns routing
            pass
    return intent, control, route


__all__ = [
    "ConfigIntent",
    "ControlIntent",
    "LifetimeIntent",
    "AuthorizationAction",
    "classify_is_conversational",
    "classify_route",
    "classify_config_intent",
    "classify_front_door",
    "build_classify_prompt",
    "build_route_prompt",
    "build_config_intent_prompt",
    "build_front_door_prompt",
    "build_chat_prompt",
    "build_simple_prompt",
]
