"""Tiny Manager front-door prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal


def build_route_prompt(text: str) -> str:
    return (
        "Reply with exactly one word: SELF or TEAM.\n"
        "SELF = conversational or read-only Manager work: greetings, acks, "
        "capability/status questions, explanations with no durable side effect, "
        "or operator control of the mission already running.\n"
        "TEAM = any request to create or modify a persistent file/artifact, run "
        "commands, perform research/engineering, or change Argus itself. Small "
        "one-shot artifacts still use TEAM; the `direct` workflow keeps them lean.\n"
        "When in doubt, answer TEAM — never route work that needs review to a "
        "lone worker.\n\n"
        f"Message:\n{(text or '').strip()}\n\n"
        "Answer:\n"
    )


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


def build_classify_prompt(text: str) -> str:
    return (
        "Reply with exactly one word: CHAT or TASK.\n"
        "CHAT = a greeting, an acknowledgement, small talk, or a question about "
        "Argus / your own capabilities — there is nothing to execute.\n"
        "TASK = a real task or objective to carry out — a fix, a feature, an "
        "experiment, an analysis, a codebase change, or a change to Argus "
        "itself — however small, even if one worker could do it alone.\n"
        "When in doubt, answer TASK — never treat real work as chat.\n\n"
        f"Message:\n{(text or '').strip()}\n\n"
        "Answer:\n"
    )


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


def build_persistence_prompt(text: str) -> str:
    return (
        "Reply with exactly one word: BOUNDED or STANDING.\n"
        "BOUNDED = the task has a natural finish line — work stops once one "
        'concrete goal is met. e.g. "fix the bug in module X", "add feature '
        'Y", "answer whether Z is faster", "run one benchmark".\n'
        "STANDING = open-ended work with NO natural finish line that should "
        "keep running autonomously (7x24) until the objective is exhausted or "
        'the operator stops it. e.g. "optimize as many X as possible", '
        '"keep improving Y", "continuously search/monitor Z".\n'
        "This classifier only sees substantive TEAM work after chat and simple "
        "one-turn requests were already removed. When in doubt, answer STANDING. "
        "Answer BOUNDED only when this TEAM task clearly has a natural one-mission "
        "finish line.\n\n"
        f"Message:\n{(text or '').strip()}\n\n"
        "Answer:\n"
    )


def classify_needs_persistence(
    text: str,
    *,
    run_exec: Callable[[str], Any],
) -> bool:
    """Is ``text`` open-ended work that should run as a standing (continuous)
    campaign, rather than a one-shot bounded mission?

    This is called only for substantive TEAM work after chat and simple one-turn
    requests have been removed. Bias toward ``True`` (STANDING), preserving
    Argus's autonomous lifetime by default; only an explicit BOUNDED verdict
    makes the task one-shot.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    try:
        result = run_exec(build_persistence_prompt(cleaned))
    except Exception:  # noqa: BLE001
        return True
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return True
    return _first_alpha_token(_extract_answer(result)).upper() not in {
        "BOUNDED",
        "ONE_SHOT",
        "ONESHOT",
    }


_IDENTITY_GUARD = (
    "The backend/worker named above is only the CLI process executing THIS "
    "reply — an internal implementation detail the operator never sees or "
    "touches directly, not a separate product with its own terminal. The "
    "operator's ONLY interface is Argus itself. If asked to change Argus's "
    "own model, backend, or reasoning effort, never tell them to open, run a "
    'command in, or otherwise interact with "the backend\'s CLI" — you have '
    "no ability to do that on their behalf, and neither do they from inside "
    "Argus. Instead tell them the actual Argus-native ways: plain sentences "
    'like "switch the model to <name>" / "把模型换成 <name>" / "把backend'
    '换成 <name>" / "effort 设为 <level>" (Argus recognizes these directly, '
    "no restart needed), or the /backend and /config slash commands.\n\n"
)


def build_chat_prompt(
    *,
    objective: str,
    identity_card: str = "",
    runtime_context: str = "",
) -> str:
    from ..cli.roles_status import runner_backend_label

    prefix = f"{identity_card.strip()}\n\n" if identity_card.strip() else ""
    runtime = f"{runtime_context.strip()}\n\n" if runtime_context.strip() else ""
    return (
        f"{prefix}You are Argus Manager, powered by one {runner_backend_label()} "
        "worker. Answer as Argus Manager.\n\n"
        f"{_IDENTITY_GUARD}"
        f"{runtime}"
        f"Message:\n{objective.strip()}"
    )


def build_simple_prompt(
    *,
    objective: str,
    mission_status: str = "",
    runtime_context: str = "",
    operator_workspace: str = "",
) -> str:
    from ..cli.roles_status import runner_backend_label

    prefix = f"{mission_status.strip()}\n\n" if mission_status.strip() else ""
    runtime = f"{runtime_context.strip()}\n\n" if runtime_context.strip() else ""
    workspace = ""
    if operator_workspace.strip():
        workspace = (
            "## Grounding workspace\n"
            f"Operator launch workspace: {operator_workspace.strip()}\n"
            "For any claim about the current project, source tree, configuration, "
            "or artifacts, inspect this workspace with tools before "
            "answering. Do not substitute generic prior knowledge for current "
            "workspace evidence. You are the Manager and may modify state or use "
            "tools when that is required to carry out the operator's instruction.\n\n"
        )
    return (
        f"{prefix}"
        f"You are Argus Manager, powered by one {runner_backend_label()} worker. "
        "Answer and act as Argus Manager. You have authority to intervene in the "
        "running mission; never claim that you are read-only or lack permission.\n\n"
        f"{_IDENTITY_GUARD}"
        f"{runtime}"
        f"{workspace}"
        f"Task:\n{objective.strip()}"
    )


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


def build_config_intent_prompt(text: str) -> str:
    return (
        "You decide whether an operator's message asks to CHANGE one of Argus's "
        "own runtime settings (its cockpit knobs) — as opposed to a research "
        "task to run, a question, or small talk.\n\n"
        "Argus has four roles — manager, planner, engineer, reviewer. The "
        "operator-changeable settings are:\n"
        "  PER-ROLE (may name one role, several, or ALL / the shared default):\n"
        "    backend  — which agent CLI runs a role: codex | claude | copilot\n"
        "    model    — which model a role calls, e.g. gpt-5.5, claude-sonnet-5, "
        "o3, gemini-3.5 (any id the backend supports)\n"
        "    effort   — a role's reasoning effort: low | medium | high | max | xhigh\n"
        "  GLOBAL (no role):\n"
        "    global_daily_cap — the sole host-global USD cap per local day\n"
        "    max_daemons     — maximum background daemons running at once (non-negative integer)\n"
        "    codex_daily_requests — host-wide Codex provider-call cap per local day\n"
        "    copilot_daily_requests — host-wide Copilot provider-call cap per local day\n"
        "    copilot_daily_premium — host-wide Copilot premium-request cap per local day\n"
        "    safe_mode       — extra-conservative guardrails: on | off\n"
        "    show_reasoning  — stream the agent's reasoning to the cockpit: on | off\n"
        "    telegram        — the Telegram notification bridge: on | off\n\n"
        "Answer NONE if the message is a real task to execute, a question "
        '(including "should I use X?" / "which model is better?"), small talk, '
        "or merely MENTIONS a model/backend/setting without asking to change it. "
        "When in doubt, answer NONE — never swallow real work as a settings change. "
        "A budget stated for ONE specific run, or a model / backend / effort asked "
        'for WITHIN a single task ("这轮" / "do THIS on claude with high effort" '
        '/ "for this task"), is part of the task, not a standing knob change — '
        "answer NONE.\n\n"
        "If it IS a settings-change request, reply with EXACTLY one line:\n"
        "SET <knob> <roles> <value>\n"
        "  <knob>  = backend | model | effort | global_daily_cap | "
        "max_daemons | codex_daily_requests | copilot_daily_requests | "
        "copilot_daily_premium | safe_mode | show_reasoning | telegram\n"
        "  <roles> = for backend/model/effort: a comma-separated list drawn from "
        "manager,planner,engineer,reviewer, or the word ALL when the operator "
        "does not name a specific role. For the GLOBAL knobs ALWAYS use a single "
        "dash: - (any other value in the roles field is ignored)\n"
        "  <value> = the target value verbatim (a backend name / model id / effort "
        "level / a dollar amount like 50 / on / off)\n"
        "Otherwise reply with EXACTLY:\n"
        "NONE\n\n"
        f"Message:\n{(text or '').strip()}\n\n"
        "Answer:\n"
    )


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


def build_front_door_prompt(text: str, *, active_mission: bool = False) -> str:
    """Merged cockpit front door: classify once and reuse every cheap decision."""
    cleaned = (text or "").strip()
    return (
        "Classify ONLY the current operator message on eight independent axes. "
        "You do NOT choose the task vertical or execution workflow; the Manager "
        "does that later for every formal task.\n"
        f"ACTIVE_MISSION: {'YES' if active_mission else 'NO'}\n\n"
        "CONFIG: SET only when the operator asks to change an Argus STANDING "
        "cockpit default. Role knobs: backend|model|effort for "
        "manager,planner,engineer,reviewer or ALL. Global knobs: "
        "global_daily_cap,max_daemons,codex_daily_requests,"
        "copilot_daily_requests,copilot_daily_premium,safe_mode,show_reasoning,"
        "telegram. Questions, mentions, recommendations, and settings/budgets "
        "limited to this one task are NONE. Default NONE.\n\n"
        "CONTROL: ABORT only for an explicit request to stop the current mission. "
        "NO_DISPATCH only when the operator explicitly forbids queueing/starting "
        "work or requires no persistent side effect. STEER only when "
        "ACTIVE_MISSION=YES and the message changes that mission's direction, "
        "priority, method, evidence, or constraints; criticism such as 'search how "
        "others solved it' still counts. Questions about stopping and tasks merely "
        "mentioning stop are NONE. Any control forces ROUTE SELF.\n\n"
        "AUTHORIZATION: AUTHORIZE only when the operator explicitly grants an "
        "action blocked by the active campaign. Allowed actions: validator_repair,"
        "acceptance_retry,provenance_repair,artifact_refresh,resume_blocked_work. "
        "List only explicitly granted actions, comma-separated. Questions, advice, "
        "or quoted authorization are NONE. Authorization forces ROUTE SELF.\n\n"
        "STEER_DIRECTIVE: only for STEER, write the Manager's concise professional "
        "team instruction. Preserve the goal while choosing method, evidence, scope, "
        "and stopping condition. Never copy insults/raw wording. Else NONE.\n\n"
        "ROUTE: SELF for conversation, read-only inspection/explanation/status, or "
        "control. TEAM for persistent file/artifact changes, commands, research, or "
        "engineering. Small one-shot artifacts are TEAM. If unsure, TEAM.\n\n"
        "LIFETIME: TEAM only. BOUNDED has one concrete natural finish line. STANDING "
        "is open-ended continuous improvement/search/monitoring. SELF=>NONE; "
        "ambiguous TEAM=>STANDING.\n\n"
        "GREETING: GREETING only when the entire message is a pure greeting with "
        "no question, request, context reference, or other content. Otherwise NONE. "
        "This is a control token, never prose, and never changes ROUTE.\n\n"
        "NAME: concise title in the message language; 2-12 Chinese characters or "
        "2-8 words, core subject/action only, no polite framing, quotes, punctuation, "
        "or session id.\n\n"
        "Reply with EXACTLY eight lines and nothing else:\n"
        "CONFIG: <SET <knob> <roles> <value> | NONE>\n"
        "CONTROL: <ABORT | NO_DISPATCH | STEER | NONE>\n"
        "AUTHORIZATION: <AUTHORIZE <allowed-action[,allowed-action]> | NONE>\n"
        "STEER_DIRECTIVE: <Manager-authored team directive | NONE>\n"
        "ROUTE: <SELF | TEAM>\n"
        "LIFETIME: <BOUNDED | STANDING | NONE>\n"
        "GREETING: <GREETING | NONE>\n"
        "NAME: <concise conversation title>\n"
        "SET syntax: SET <knob> <comma-separated roles|ALL|-> <verbatim value>.\n\n"
        f"Message:\n{cleaned}\n\n"
        "Answer:\n"
    )


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
    lifetime_token = _first_alpha_token(lifetime_line).upper()
    lifetime: LifetimeIntent | None = None
    if route == "complex":
        if lifetime_token in {"BOUNDED", "ONE_SHOT", "ONESHOT"}:
            lifetime = "bounded"
        elif lifetime_token in {"STANDING", "CONTINUOUS", "PERSIST", "PERSISTENT"}:
            lifetime = "standing"
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
    "classify_needs_persistence",
    "classify_config_intent",
    "classify_front_door",
    "build_classify_prompt",
    "build_route_prompt",
    "build_persistence_prompt",
    "build_config_intent_prompt",
    "build_front_door_prompt",
    "build_chat_prompt",
    "build_simple_prompt",
]
