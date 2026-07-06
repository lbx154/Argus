"""Tiny REPL front-door prompts."""
from __future__ import annotations

from typing import Any, Callable


def build_route_prompt(text: str, role_skill_block: str = "") -> str:
    return (
        "Reply with exactly one word: SELF or TEAM.\n"
        "SELF = one Codex can handle it independently.\n"
        "TEAM = needs Argus coordination with Planner/Engineer/Reviewer, "
        "including changes to Argus itself.\n\n"
        f"Message:\n{(text or '').strip()}\n\n"
        "Answer:\n"
    )


def classify_route(
    text: str,
    *,
    run_exec: Callable[[str], Any],
    role_skill_block: str = "",
) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "complex"
    try:
        result = run_exec(build_route_prompt(cleaned, role_skill_block))
    except Exception:  # noqa: BLE001
        return "complex"
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return "complex"
    token = _first_alpha_token(_extract_answer(result)).upper()
    if token in {"SELF", "CHAT", "SIMPLE"}:
        return "simple"
    if token in {"TEAM", "COMPLEX", "TASK"}:
        return "complex"
    return "complex"


def build_classify_prompt(text: str, role_skill_block: str = "") -> str:
    return (
        "Reply with exactly one word: SELF or TEAM.\n"
        "SELF = one Codex can handle it independently.\n"
        "TEAM = needs Argus coordination with Planner/Engineer/Reviewer, "
        "including changes to Argus itself.\n\n"
        f"Message:\n{(text or '').strip()}\n\n"
        "Answer:\n"
    )


def classify_is_conversational(
    text: str,
    *,
    run_exec: Callable[[str], Any],
    role_skill_block: str = "",
) -> bool:
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    try:
        result = run_exec(build_classify_prompt(cleaned, role_skill_block))
    except Exception:  # noqa: BLE001
        return False
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return False
    return _first_alpha_token(_extract_answer(result)).upper() in {"SELF", "CHAT"}


def build_persistence_prompt(text: str, role_skill_block: str = "") -> str:
    return (
        "Reply with exactly one word: BOUNDED or STANDING.\n"
        "BOUNDED = the task has a natural finish line (a specific fix, a "
        "specific feature, answering a question, running one experiment) — "
        "work stops once that goal is met.\n"
        "STANDING = open-ended work with NO natural finish line that should "
        "keep running autonomously (7x24) until the objective is exhausted or "
        "the operator stops it — e.g. \"optimize as many X as possible\", "
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
    role_skill_block: str = "",
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
        result = run_exec(build_persistence_prompt(cleaned, role_skill_block))
    except Exception:  # noqa: BLE001
        return False
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return False
    return _first_alpha_token(_extract_answer(result)).upper() in {
        "STANDING", "CONTINUOUS", "PERSIST", "PERSISTENT",
    }


def build_chat_prompt(*, objective: str, identity_card: str = "") -> str:
    prefix = f"{identity_card.strip()}\n\n" if identity_card.strip() else ""
    return f"{prefix}You are Argus Manager. Answer as Argus Manager.\n\nMessage:\n{objective.strip()}"


def build_simple_prompt(
    *, objective: str, skill_block: str = "", mission_status: str = ""
) -> str:
    prefix = f"{mission_status.strip()}\n\n" if mission_status.strip() else ""
    return (
        f"{prefix}"
        "You are Argus Manager, powered by one Codex worker. Answer as Argus Manager.\n\n"
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


__all__ = [
    "classify_is_conversational",
    "classify_route",
    "classify_needs_persistence",
    "build_classify_prompt",
    "build_route_prompt",
    "build_persistence_prompt",
    "build_chat_prompt",
    "build_simple_prompt",
]
