"""Manager front-door parsers, runtime calls, and active prompt imports."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, cast

from ..core.role_decision import latest_role_decision
from ..roles.prompts.manager import (
    _IDENTITY_GUARD as _PROMPT_IDENTITY_GUARD,
)
from ..roles.prompts.manager import (
    build_front_door_prompt,
    build_route_prompt,
    build_simple_prompt,
    build_steer_confirmation_prompt,
)

_IDENTITY_GUARD = _PROMPT_IDENTITY_GUARD
log = logging.getLogger(__name__)


def _routing_diagnostic(
    message: str,
    failure_sink: Callable[[str], None] | None,
) -> None:
    log.warning("Manager front-door diagnostic: %s", message)
    if callable(failure_sink):
        failure_sink(message)


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
    return _route_from_token(_one_word_answer(result, "route"))


#: The front-door decision fields, in the order the Manager contract lists them.
_FRONT_DOOR_FIELDS = (
    "intake_type",
    "intake_scope",
    "intake_roles",
    "preference_kind",
    "preference_value",
    "revoke_revision",
    "config",
    "control",
    "authorization",
    "steer_directive",
    "operator_question_policy",
    "route",
    "self_mode",
    "reply",
    "lifetime",
    "greeting",
    "name",
)


def _answer_text(result: Any) -> str:
    """The model's last plain message."""
    message = getattr(result, "last_agent_message", None)
    if not message:
        messages = getattr(result, "agent_messages", None) or []
        message = messages[-1] if messages else ""
    return str(message or "")


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


def _front_door_fields(result: Any) -> dict[str, str]:
    """Read the front-door fields, preferring the structured decision.

    A structured decision is used as it stands. This module used to render one
    back into ``KEY: VALUE`` lines and re-read them, which handed every field
    the power to forge the fields below it: a two-line ``steer_directive`` could
    publish its own ``CONTROL: ABORT``, and the reader takes the first match.
    A model that answered in plain prose is still read line by line, because
    there the lines are all there is.
    """
    decision = latest_role_decision(result, "manager")
    if isinstance(decision, dict):
        return {
            name: str(decision.get(name, "") or "").strip()
            for name in _FRONT_DOOR_FIELDS
        }
    from ..core.role_reply import read_key_values

    values = read_key_values(
        _answer_text(result),
        (name.upper() for name in _FRONT_DOOR_FIELDS),
    )
    return {
        name: str(values.get(name.upper()) or "").strip()
        for name in _FRONT_DOOR_FIELDS
    }


def _one_word_answer(result: Any, *names: str) -> str:
    """Read a one-word verdict from the named fields, else from the message.

    The route and steer gates ask for a bare word and define no decision
    schema, so the message is the normal channel; the named field is read first
    for a model that answers those gates structurally anyway.
    """
    fields = _front_door_fields(result)
    for name in names:
        if token := _first_alpha_token(fields.get(name, "")):
            return token
    return _first_alpha_token(_answer_text(result))


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
# switch). The merged front-door call is biased hard toward None so real work
# is never swallowed as a config change.

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


ControlIntent = Literal["abort", "pause", "no_dispatch", "steer"]
SelfModeIntent = Literal[
    "reply",
    "inspect",
    "micro",
    "implement",
    "debug",
    "review",
    "synthesize",
]
ConfigDecision = ConfigIntent | tuple[ConfigIntent, ...] | None
LifetimeIntent = Literal["bounded", "bounded_increment", "standing"]
AuthorizationAction = Literal[
    "validator_repair",
    "acceptance_retry",
    "provenance_repair",
    "artifact_refresh",
    "resume_blocked_work",
]
OperatorQuestionPolicy = Literal["allow", "forbid", "unchanged"]
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

    Returns ``None`` for ``NONE`` / empty / malformed."""
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


def _parse_config_decision(line: str | None) -> ConfigDecision:
    """Parse one or more ``SET`` clauses separated by semicolons.

    Multi-knob requests are one operator transaction. Any malformed clause
    rejects the whole decision instead of concatenating trailing SET text into
    the first value (which previously turned ``pi; SET model ...`` into an
    unknown backend that silently fell back to Codex).
    """
    raw = str(line or "").strip()
    if not raw or raw.upper() == "NONE":
        return None
    clauses = [clause.strip() for clause in raw.split(";") if clause.strip()]
    if not clauses or len(clauses) > 8:
        return None
    intents: list[ConfigIntent] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for clause in clauses:
        intent = _parse_config_line(clause)
        if intent is None:
            return None
        identity = (intent.knob, intent.roles)
        if identity in seen:
            return None
        seen.add(identity)
        intents.append(intent)
    return intents[0] if len(intents) == 1 else tuple(intents)


def _plain_reply(value: str) -> str:
    """Unwrap a reply that arrived as the JSON string the old renderer wrote."""
    if len(value) > 1 and value.startswith('"') and value.endswith('"'):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return value
        if isinstance(decoded, str):
            return decoded.strip()
    return value


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
    self_mode_sink: Callable[[SelfModeIntent], None] | None = None,
    reply_sink: Callable[[str], None] | None = None,
    greeting_sink: Callable[[str], None] | None = None,
    steering_sink: Callable[[str], None] | None = None,
    operator_question_policy_sink: Callable[[OperatorQuestionPolicy], None] | None = None,
    authorization_sink: Callable[[tuple[str, ...]], None] | None = None,
    intake_sink: Callable[[dict[str, Any]], None] | None = None,
    failure_sink: Callable[[str], None] | None = None,
    active_mission: bool = False,
) -> "tuple[ConfigDecision, ControlIntent | None, str]":
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
    except Exception as exc:  # noqa: BLE001
        if callable(failure_sink):
            failure_sink(f"{type(exc).__name__}: {exc}")
        return None, None, "complex"
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        if callable(failure_sink):
            failure_sink(_classifier_failure_detail(result))
        return None, None, "complex"
    fields = _front_door_fields(result)
    intent = _parse_config_decision(fields["config"])
    control_token = fields["control"].upper().replace("-", "_")
    control: ControlIntent | None
    if control_token.startswith("ABORT"):
        control = "abort"
    elif control_token.startswith("PAUSE"):
        control = "pause"
    elif control_token.startswith(("NO_DISPATCH", "NO DISPATCH", "NODISPATCH")):
        control = "no_dispatch"
    elif control_token.startswith("STEER"):
        control = "steer"
    else:
        control = None
    if control == "steer" and not active_mission:
        control = None
    elif control == "steer":
        try:
            confirmation = run_exec(
                build_steer_confirmation_prompt(
                    cleaned,
                    active_mission=active_mission,
                )
            )
        except Exception:  # noqa: BLE001 - mutation fails closed
            control = None
        else:
            if int(getattr(confirmation, "exit_code", 0) or 0) != 0:
                control = None
            elif _one_word_answer(confirmation, "control").upper() != "STEER":
                control = None
    route_token = _first_alpha_token(fields["route"])
    if not route_token or route_token.upper() not in {
        "SELF",
        "SIMPLE",
        "TEAM",
        "COMPLEX",
    }:
        if control in {"abort", "pause", "no_dispatch", "steer"}:
            _routing_diagnostic(
                "route token invalid; control preserved "
                f"(token={route_token or '<missing>'!r}, control={control!r})",
                failure_sink,
            )
            route = "simple"
        else:
            _routing_diagnostic("classifier returned no valid route", failure_sink)
            return intent, None, "complex"
    else:
        route = _route_from_token(route_token)
    if control in {"abort", "pause", "no_dispatch", "steer"}:
        route = "simple"
    authorization = _parse_authorization_line(fields["authorization"])
    if authorization:
        route = "simple"
        if callable(authorization_sink):
            try:
                authorization_sink(authorization)
            except Exception:  # noqa: BLE001 - advisory metadata never owns routing
                pass
    self_mode: SelfModeIntent | None = None
    if route == "simple":
        self_mode_token = _first_alpha_token(fields["self_mode"]).upper()
        self_mode = {
            "REPLY": "reply",
            "MICRO": "micro",
            "IMPLEMENT": "implement",
            "DEBUG": "debug",
            "REVIEW": "review",
            "SYNTHESIZE": "synthesize",
        }.get(self_mode_token, "inspect")
    if callable(self_mode_sink) and self_mode is not None:
        try:
            self_mode_sink(self_mode)
        except Exception:  # noqa: BLE001 - advisory metadata never owns routing
            pass
    reply = _plain_reply(fields["reply"])
    reply_eligible = (
        callable(reply_sink)
        and route == "simple"
        and self_mode == "reply"
        and intent is None
        and control in {None, "no_dispatch"}
        and not authorization
        and reply.upper() != "NONE"
        and len(reply) > 0
    )
    if reply_eligible:
        try:
            reply_sink(reply)
        except Exception:  # noqa: BLE001 - optional fast reply only
            pass
    lifetime: LifetimeIntent | None = None
    lifetime_parts = fields["lifetime"].split(maxsplit=1)
    lifetime_token = (
        lifetime_parts[0].replace("-", "_").upper()
        if lifetime_parts
        else ""
    )
    if route == "complex":
        # BOUNDED_INCREMENT preserves an operator's explicit instruction to do
        # only one named stage/increment even when vertical classification later
        # identifies a normally-staged workflow.
        if lifetime_token == "BOUNDED_INCREMENT":
            lifetime = "bounded_increment"
        elif lifetime_token == "STANDING":
            lifetime = "standing"
        else:
            lifetime = "bounded"
    elif control == "steer":
        # A steering turn stays on the SELF control path, but it may explicitly
        # promote the active bounded mission to a standing campaign.
        if lifetime_token == "STANDING":
            lifetime = "standing"
        elif lifetime_token == "BOUNDED_INCREMENT":
            lifetime = "bounded_increment"
        elif lifetime_token == "BOUNDED":
            lifetime = "bounded"
    if callable(lifetime_sink) and lifetime is not None:
        try:
            lifetime_sink(lifetime)
        except Exception:  # noqa: BLE001 - advisory metadata never owns routing
            pass
    greeting_token = fields["greeting"].upper()
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
    steering = fields["steer_directive"]
    steering_token = steering.rstrip(".。!！").upper()
    steering_eligible = (
        callable(steering_sink)
        and control == "steer"
        and steering
        and steering_token not in {"NONE", "N/A", "NA", "NULL"}
    )
    if steering_eligible:
        try:
            steering_sink(steering)
        except Exception:  # noqa: BLE001 - advisory metadata never owns routing
            pass
    question_policy = fields["operator_question_policy"].strip().lower()
    if (
        callable(operator_question_policy_sink)
        and control == "steer"
        and question_policy in {"allow", "forbid", "unchanged"}
    ):
        try:
            operator_question_policy_sink(
                cast(OperatorQuestionPolicy, question_policy)
            )
        except Exception:  # noqa: BLE001 - advisory metadata never owns routing
            pass
    if callable(name_sink) and (name := fields["name"]):
        try:
            name_sink(name)
        except Exception:  # noqa: BLE001 - cosmetic metadata never owns routing
            pass
    intake_type = fields["intake_type"].strip().lower()
    if intake_type not in {
        "ephemeral",
        "objective_amendment",
        "standing_directive",
        "preference",
        "credential_grant",
        "revocation",
    }:
        if intent is not None:
            intake_type = "preference"
        elif control == "steer" or route == "complex":
            intake_type = "objective_amendment"
        else:
            intake_type = "ephemeral"
    intake_scope = fields["intake_scope"].strip().lower()
    if intake_scope not in {"mission", "project", "global"}:
        intake_scope = "mission" if intake_type == "objective_amendment" else "project"
    raw_roles = fields["intake_roles"].strip().lower()
    if raw_roles == "all" or not raw_roles:
        intake_roles: str | tuple[str, ...] = "all"
    else:
        intake_roles = tuple(
            dict.fromkeys(
                role.strip()
                for role in raw_roles.split(",")
                if role.strip() in {"manager", "planner", "engineer", "reviewer", "teammate"}
            )
        ) or "all"
    preference_kind = fields["preference_kind"].strip().lower()
    if preference_kind not in {"autonomy", "interaction", "workflow"}:
        preference_kind = "workflow"
    try:
        revocation_target = int(fields["revoke_revision"])
    except (TypeError, ValueError):
        revocation_target = 0
    if callable(intake_sink):
        try:
            intake_sink({
                "kind": intake_type,
                "scope": intake_scope,
                "applies_to_roles": intake_roles,
                "preference_kind": preference_kind,
                "preference_value": (
                    "" if fields["preference_value"].upper() == "NONE"
                    else fields["preference_value"]
                ),
                "target_revision": revocation_target,
            })
        except Exception:  # noqa: BLE001 - intake metadata never owns routing
            pass
    return intent, control, route


__all__ = [
    "ConfigIntent",
    "ConfigDecision",
    "ControlIntent",
    "SelfModeIntent",
    "LifetimeIntent",
    "AuthorizationAction",
    "OperatorQuestionPolicy",
    "classify_route",
    "classify_front_door",
    "build_route_prompt",
    "build_front_door_prompt",
    "build_simple_prompt",
]
