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
        "per_mission_cap",
        "daily_cap",
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
        "    per_mission_cap — the STANDING default USD cap applied to EVERY "
        "future mission (a dollar amount). A budget stated for ONE specific / "
        'current run ("这轮就给 200", "for this mission only", "this run gets '
        '$50") is a per-mission TASK constraint, NOT a settings write — answer NONE.\n'
        "    daily_cap       — the STANDING default USD cap per local day (a dollar amount)\n"
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
        "  <knob>  = backend | model | effort | per_mission_cap | daily_cap | "
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


def build_front_door_prompt(text: str) -> str:
    """Merged cockpit front door: classify once and reuse every cheap decision."""
    cleaned = (text or "").strip()
    return (
        "Classify one operator message on SIX independent axes.\n\n"
        "AXIS 1 — CONFIG: does the message ask to CHANGE one of Argus's own "
        "runtime settings (its cockpit knobs), as opposed to a research task, a "
        "question, or small talk?\n"
        "Argus has four roles — manager, planner, engineer, reviewer. The "
        "operator-changeable settings are:\n"
        "  PER-ROLE (may name one role, several, or ALL / the shared default):\n"
        "    backend  — which agent CLI runs a role: codex | claude | copilot\n"
        "    model    — which model a role calls, e.g. gpt-5.5, claude-sonnet-5, "
        "o3, gemini-3.5 (any id the backend supports)\n"
        "    effort   — a role's reasoning effort: low | medium | high | max | xhigh\n"
        "  GLOBAL (no role):\n"
        "    per_mission_cap — the STANDING default USD cap applied to EVERY "
        'future mission. A budget for ONE specific / current run ("这轮就给 '
        '200", "for this mission only") is a TASK constraint, NOT a settings '
        "write — CONFIG is NONE.\n"
        "    daily_cap       — the STANDING default USD cap per local day\n"
        "    max_daemons     — maximum background daemons running at once\n"
        "    codex_daily_requests — host-wide Codex provider-call cap per day\n"
        "    copilot_daily_requests — host-wide Copilot provider-call cap per day\n"
        "    copilot_daily_premium — host-wide Copilot premium-request cap per day\n"
        "    safe_mode       — extra-conservative guardrails: on | off\n"
        "    show_reasoning  — stream the agent's reasoning to the cockpit: on | off\n"
        "    telegram        — the Telegram notification bridge: on | off\n"
        'CONFIG is NONE if the message is a real task, a question ("should I use '
        'X?" / "which model is better?"), small talk, or merely MENTIONS a '
        "model/backend/setting without asking to change the STANDING default. A "
        'model/backend/effort/budget asked for WITHIN a single task ("这轮" / '
        '"do THIS on claude with high effort") is part of the task — CONFIG is '
        "NONE. When in doubt, NONE.\n\n"
        "AXIS 2 — CONTROL: does the operator clearly constrain what Argus may do "
        "with this message?\n"
        "  ABORT = immediately stop the current in-flight mission. This is an "
        "operator control action, never a new task.\n"
        "  NO_DISPATCH = the operator explicitly says not to create, queue, or "
        "dispatch a task/mission, not to start a daemon, or to keep the request "
        "read-only with no persistent side effect. Handle it entirely as inline "
        "Manager SELF work, even when answering requires inspecting the current "
        "workspace with read-only tools. If it cannot be satisfied without a "
        "persistent side effect, explain that and ask for authorization; never "
        "turn it into TEAM work.\n"
        "  STEER = change the direction, priorities, method, or constraints of "
        "the mission already running (for example: stop formal checking and "
        "focus on inventing a mathematical tool). This is a durable Manager "
        "directive to the active Engineer/Planner, never a new mission.\n"
        "  NONE = every other message, including questions about how stopping "
        "works, requests to implement a stop feature, and tasks that merely "
        "mention stopping something as part of their objective.\n"
        "  When in doubt, answer NONE. If CONTROL is ABORT, NO_DISPATCH, or STEER, ROUTE "
        "must be SELF.\n\n"
        "AXIS 3 — ROUTE: SELF or TEAM?\n"
        "  SELF = conversational or read-only Manager work: a greeting, ack, "
        "capability/status question, source/workspace inspection with no durable "
        "side effect, explanation, or an operator control action.\n"
        "  TEAM = any request to create or modify a persistent file/artifact, "
        "run commands, or perform research/engineering. Small one-shot artifacts "
        "still use TEAM; the `direct` workflow keeps them lean.\n"
        "  When in doubt, answer TEAM — never route work that needs review to a "
        "lone worker.\n\n"
        "AXIS 4 — LIFETIME: for TEAM work only, is this BOUNDED or STANDING?\n"
        "  BOUNDED = one concrete goal with a natural finish line, such as fixing "
        "one bug, adding one feature, proving one stated result, or running one "
        "benchmark.\n"
        "  STANDING = open-ended work with no natural finish line that should keep "
        "running autonomously until the operator stops it, such as continuously "
        "improving, searching, monitoring, or optimizing as many cases as possible.\n"
        "  For SELF messages answer NONE. For TEAM ambiguity answer STANDING; only "
        "choose BOUNDED when the one-mission finish line is clear.\n\n"
        "AXIS 5 — FAST_REPLY: optionally answer a lightweight SELF turn in this "
        "same call. Write a brief one-line reply in the message's language for a "
        "greeting, thanks, acknowledgement, farewell, small talk, or a generic "
        "identity/capability question that can be answered only from these fixed "
        "facts: Argus Manager is the operator's interface to an autonomous system; "
        "it can answer read-only questions inline and route durable research or "
        "engineering work to Planner, Engineer, and Reviewer. For questions about "
        "the current model/backend/configuration, live mission status, workspace "
        "or source contents, prior conversation, or any action/config/control/TEAM "
        "task answer NONE because the full Manager must inspect real state. This "
        "field never changes ROUTE.\n\n"
        "AXIS 6 — NAME: create a concise conversation title from the core intent "
        "of this message. This title is required for SELF, TEAM, config, control, "
        "and conversational messages alike. Use the message's language. Distill "
        "the subject and requested action instead of copying polite framing such "
        "as 'please' or 'help me'. Prefer 2-12 Chinese characters or 2-8 words; "
        "use a short noun phrase, with no quotes, trailing punctuation, or session "
        "ID.\n\n"
        "Reply with EXACTLY six lines and nothing else:\n"
        "CONFIG: <SET <knob> <roles> <value> | NONE>\n"
        "CONTROL: <ABORT | NO_DISPATCH | STEER | NONE>\n"
        "ROUTE: <SELF | TEAM>\n"
        "LIFETIME: <BOUNDED | STANDING | NONE>\n"
        "FAST_REPLY: <brief one-line lightweight SELF reply | NONE>\n"
        "NAME: <concise conversation title>\n"
        "  For a SET line: <knob> = backend | model | effort | per_mission_cap | "
        "daily_cap | max_daemons | codex_daily_requests | "
        "copilot_daily_requests | copilot_daily_premium | safe_mode | "
        "show_reasoning | telegram; <roles> = a "
        "comma-separated list from manager,planner,engineer,reviewer or ALL "
        "(role knobs), or a single dash - (global knobs); <value> = the target "
        "verbatim (backend name / model id / effort / dollar amount / on | off).\n\n"
        f"Message:\n{cleaned}\n\n"
        "Answer:\n"
    )


def classify_front_door(
    text: str,
    *,
    run_exec: Callable[[str], Any],
    name_sink: Callable[[str], None] | None = None,
    lifetime_sink: Callable[[LifetimeIntent], None] | None = None,
    fast_reply_sink: Callable[[str], None] | None = None,
) -> "tuple[ConfigIntent | None, ControlIntent | None, str]":
    """One model call for every cheap front-door decision.

    The return shape stays backward-compatible; optional sinks expose the
    lifetime verdict and a strictly social one-line reply so callers can avoid
    otherwise redundant model turns.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None, None, "complex"
    try:
        result = run_exec(build_front_door_prompt(cleaned))
    except Exception:  # noqa: BLE001
        return None, None, "complex"
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return None, None, "complex"
    answer = _extract_answer(result)
    config_line = _line_after_prefix(answer, "CONFIG:")
    control_line = _line_after_prefix(answer, "CONTROL:")
    route_line = _line_after_prefix(answer, "ROUTE:")
    lifetime_line = _line_after_prefix(answer, "LIFETIME:")
    fast_reply_line = _line_after_prefix(answer, "FAST_REPLY:")
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
    fast_reply = str(fast_reply_line or "").strip()
    fast_reply_token = fast_reply.rstrip(".。!！").upper()
    if (
        callable(fast_reply_sink)
        and route == "simple"
        and intent is None
        and control is None
        and fast_reply
        and fast_reply_token not in {"NONE", "N/A", "NA", "NULL"}
        and len(fast_reply) <= 500
    ):
        try:
            fast_reply_sink(fast_reply)
        except Exception:  # noqa: BLE001 - optional latency fast-path only
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
