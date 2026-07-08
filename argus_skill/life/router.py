"""Tiny REPL front-door prompts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


def build_route_prompt(text: str) -> str:
    return (
        "Reply with exactly one word: SELF or TEAM.\n"
        "SELF = one worker can carry it out end-to-end on its own.\n"
        "TEAM = needs Argus's Planner/Engineer/Reviewer coordination — "
        "multi-step research or engineering, or a change to Argus itself.\n"
        "When in doubt, answer TEAM — never route work that needs review to a "
        "lone worker.\n\n"
        f"Message:\n{(text or '').strip()}\n\n"
        "Answer:\n"
    )


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
    token = _first_alpha_token(_extract_answer(result)).upper()
    if token in {"SELF", "SIMPLE"}:
        return "simple"
    return "complex"  # TEAM / COMPLEX / anything unrecognized → the safe default


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
        "concrete goal is met. e.g. \"fix the bug in module X\", \"add feature "
        "Y\", \"answer whether Z is faster\", \"run one benchmark\".\n"
        "STANDING = open-ended work with NO natural finish line that should "
        "keep running autonomously (7x24) until the objective is exhausted or "
        "the operator stops it. e.g. \"optimize as many X as possible\", "
        "\"keep improving Y\", \"continuously search/monitor Z\".\n"
        "When in doubt, answer BOUNDED — never force standing/continuous mode "
        "onto a task that did not ask for it.\n\n"
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

    Biases hard toward ``False`` (BOUNDED) — the safe default, since forcing an
    expensive 7x24 campaign onto a task that did not ask for one is the
    dangerous failure direction (never silently spend budget the operator did
    not intend).
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    try:
        result = run_exec(build_persistence_prompt(cleaned))
    except Exception:  # noqa: BLE001
        return False
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return False
    return _first_alpha_token(_extract_answer(result)).upper() in {
        "STANDING", "CONTINUOUS", "PERSIST", "PERSISTENT",
    }


_IDENTITY_GUARD = (
    "The backend/worker named above is only the CLI process executing THIS "
    "reply — an internal implementation detail the operator never sees or "
    "touches directly, not a separate product with its own terminal. The "
    "operator's ONLY interface is Argus itself. If asked to change Argus's "
    "own model, backend, or reasoning effort, never tell them to open, run a "
    "command in, or otherwise interact with \"the backend's CLI\" — you have "
    "no ability to do that on their behalf, and neither do they from inside "
    "Argus. Instead tell them the actual Argus-native ways: plain sentences "
    "like \"switch the model to <name>\" / \"把模型换成 <name>\" / \"把backend"
    "换成 <name>\" / \"effort 设为 <level>\" (Argus recognizes these directly, "
    "no restart needed), or the /backend and /config slash commands.\n\n"
)


def build_chat_prompt(*, objective: str, identity_card: str = "") -> str:
    from ..cli.roles_status import runner_backend_label
    prefix = f"{identity_card.strip()}\n\n" if identity_card.strip() else ""
    return (
        f"{prefix}You are Argus Manager, powered by one {runner_backend_label()} "
        "worker. Answer as Argus Manager.\n\n"
        f"{_IDENTITY_GUARD}"
        f"Message:\n{objective.strip()}"
    )


def build_simple_prompt(
    *, objective: str, mission_status: str = ""
) -> str:
    from ..cli.roles_status import runner_backend_label
    prefix = f"{mission_status.strip()}\n\n" if mission_status.strip() else ""
    return (
        f"{prefix}"
        f"You are Argus Manager, powered by one {runner_backend_label()} worker. "
        "Answer as Argus Manager.\n\n"
        f"{_IDENTITY_GUARD}"
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
    {"per_mission_cap", "daily_cap", "safe_mode", "show_reasoning", "telegram"}
)
_CONFIG_KNOBS = _CONFIG_ROLE_KNOBS | _CONFIG_GLOBAL_KNOBS
_CONFIG_ROLES = frozenset({"manager", "planner", "engineer", "reviewer"})


@dataclass(frozen=True)
class ConfigIntent:
    """A parsed "change one of Argus's own runtime knobs" request."""

    knob: str               # see _CONFIG_KNOBS
    roles: tuple[str, ...]  # role-scoped knobs only; () = ALL roles / the shared default
    value: str              # target value, verbatim (backend / model id / effort / $amount / on|off)


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
        "current run (\"这轮就给 200\", \"for this mission only\", \"this run gets "
        "$50\") is a per-mission TASK constraint, NOT a settings write — answer NONE.\n"
        "    daily_cap       — the STANDING default USD cap per local day (a dollar amount)\n"
        "    safe_mode       — extra-conservative guardrails: on | off\n"
        "    show_reasoning  — stream the agent's reasoning to the cockpit: on | off\n"
        "    telegram        — the Telegram notification bridge: on | off\n\n"
        "Answer NONE if the message is a real task to execute, a question "
        "(including \"should I use X?\" / \"which model is better?\"), small talk, "
        "or merely MENTIONS a model/backend/setting without asking to change it. "
        "When in doubt, answer NONE — never swallow real work as a settings change. "
        "A budget stated for ONE specific run, or a model / backend / effort asked "
        "for WITHIN a single task (\"这轮\" / \"do THIS on claude with high effort\" "
        "/ \"for this task\"), is part of the task, not a standing knob change — "
        "answer NONE.\n\n"
        "If it IS a settings-change request, reply with EXACTLY one line:\n"
        "SET <knob> <roles> <value>\n"
        "  <knob>  = backend | model | effort | per_mission_cap | daily_cap | "
        "safe_mode | show_reasoning | telegram\n"
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
                r for r in (tok.strip() for tok in roles_raw.split(","))
                if r in _CONFIG_ROLES
            )
            if not roles:
                return None
    else:
        roles = ()  # global knob — roles field ("-") is ignored
    value = parts[3].strip().strip("`\"'")
    if not value:
        return None
    return ConfigIntent(knob=knob, roles=roles, value=value)


__all__ = [
    "ConfigIntent",
    "classify_is_conversational",
    "classify_route",
    "classify_needs_persistence",
    "classify_config_intent",
    "build_classify_prompt",
    "build_route_prompt",
    "build_persistence_prompt",
    "build_config_intent_prompt",
    "build_chat_prompt",
    "build_simple_prompt",
]
