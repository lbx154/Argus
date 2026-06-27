"""Pre-mission classifier: separate conversational chat from real tasks.

Background. The REPL pipes every operator message through the full
mission pipeline:

    matcher → distill (on miss) → engineer round-loop → reviewer →
    skill writeback

That's correct for "build a Python package with strict gates", but it
is a $0.10 + 30-second misfire for "hello" or "你能干什么". In one
trace the engineer ran ``pwd && ls && rg --files && sed README.md``
just to answer a greeting, then the reviewer rejected it for "doing
unrelated repo inspection" and forced a redo round.

Design philosophy — "the harness is not smarter than the agent". The
chat/task decision used to be a bilingual pile of hand-maintained
regexes (coding-verb lists, greeting openers, a char cap). Every new
phrasing meant another regex. That is exactly the kind of harness-side
cleverness we want gone: the decision is a judgment call, so a model
makes it. ``classify_is_conversational`` does one cheap model call and
parses a single-token CHAT/TASK answer.

Conservative by construction: false negatives (a chat treated as a
task) only cost one needless pipeline run; false positives (a real
task treated as chat) silently skip the engineer loop and lose work.
So the classifier biases hard toward TASK — it returns chat ONLY when
the model answers exactly ``CHAT``, and returns task on any imperative,
ambiguous, follow-up, repo-dependent message, parse failure, or backend
error.

This path is REPL/operator-only: it runs solely for free text typed by
a human at the cockpit prompt (gated by the runner's
``_allow_chat_fast_path``). Planner / backlog / daemon missions never
reach it, so the harness never second-guesses agent-produced work.

Public surface:

* ``classify_is_conversational(text, *, run_exec)`` — the classifier.
* ``build_classify_prompt(text)`` — the classifier prompt (exposed for
  tests / observability).
* ``build_chat_prompt(...)`` — render the codex system+user prompt for
  the chat fast-path (no Verification block, no tool use).
"""
from __future__ import annotations

from typing import Any, Callable

_CLASSIFY_INSTRUCTIONS = (
    "You are a strict intent classifier for a coding-agent cockpit. "
    "Read the operator's single message and answer with exactly one "
    "word — either CHAT or TASK — and nothing else.\n\n"
    "Answer CHAT only for: greetings, thanks, acknowledgements, "
    "small talk, or questions about who/what you are or what you can "
    "do (identity / capability questions).\n\n"
    "Answer TASK for anything that requests work or could depend on "
    "the repository: any imperative or coding verb, any file / module "
    "/ project reference, any follow-up or continuation ('continue', "
    "'继续', 'fix it', 'run it', 'do the next step', 'try again', "
    "'proceed'), any bug report, any multi-line or detailed message, "
    "and anything ambiguous.\n\n"
    "When in doubt, answer TASK. Output only the single word."
)


def build_classify_prompt(text: str, role_skill_block: str = "") -> str:
    """Render the prompt sent to the model for chat-vs-task classification.

    ``role_skill_block`` is OPTIONALLY prepended to the prompt — the Manager
    passes its role skill block here so the classifier shares the same injected
    identity/duties context the Manager's other LLM calls use. It defaults to
    ``""`` so every existing caller (and a Manager with no ``skill_store``) gets
    a byte-for-byte identical prompt to before this parameter existed.
    """
    return (
        f"{role_skill_block}"
        f"{_CLASSIFY_INSTRUCTIONS}\n\n"
        "## Operator message\n"
        f"{(text or '').strip()}\n\n"
        "## Your answer (CHAT or TASK)\n"
    )


def _extract_answer(result: Any) -> str:
    """Pull the model's reply text out of a RunnerResult-shaped object."""
    msg = getattr(result, "last_agent_message", None)
    if not msg:
        msgs = getattr(result, "agent_messages", None) or []
        msg = msgs[-1] if msgs else ""
    return str(msg or "")


def classify_is_conversational(
    text: str,
    *,
    run_exec: Callable[[str], Any],
    role_skill_block: str = "",
) -> bool:
    """Model-based chat/task classifier.

    ``run_exec`` is a callable that takes the classifier prompt and
    returns a ``RunnerResult``-shaped object (``agent_messages`` /
    ``last_agent_message`` + ``exit_code``). Returns True only when the
    model clearly answers ``CHAT``. Returns False (route to the full
    mission pipeline) for any task-like answer, parse failure, non-zero
    exit, or backend exception — false positives lose operator work, so
    the bias is hard toward TASK.

    ``role_skill_block`` is forwarded to :func:`build_classify_prompt` and
    defaults to ``""`` — so an existing caller that does not pass it gets a
    byte-for-byte identical prompt to before.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    try:
        result = run_exec(build_classify_prompt(cleaned, role_skill_block))
    except Exception:  # noqa: BLE001 — any backend error -> treat as task
        return False
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return False
    answer = _extract_answer(result).strip()
    if not answer:
        return False
    # Take the first alphabetic token only; ignore punctuation / trailers.
    token = ""
    for ch in answer:
        if ch.isalpha():
            token += ch
        elif token:
            break
    return token.upper() == "CHAT"


_CHAT_SYSTEM_INSTRUCTIONS = (
    "## You are in CHAT mode\n"
    "The operator sent a brief conversational message — a greeting, "
    "capability question, or short ack. Reply directly in 1-3 "
    "sentences in the same language they used. Match their register: "
    "concise, plain prose, no boilerplate.\n\n"
    "Hard rules:\n"
    "1. Do NOT inspect the workspace, list files, or run any shell "
    "command. Do NOT invoke any tool.\n"
    "2. Do NOT add `## Verification`, `## Summary`, or any structured "
    "section. The reviewer is OFF.\n"
    "3. Reply with prose only. No code fences, no markdown headings, "
    "no bullet lists unless the user explicitly asked for a list.\n"
    "4. If the user asks about your capabilities, say what argus-skill "
    "does in plain terms (supervises a coding agent end-to-end with a "
    "skill cache, runs missions on a 7×24 daemon, etc.) — keep it "
    "short.\n"
)


def build_chat_prompt(*, objective: str, identity_card: str = "") -> str:
    """Render the full prompt sent to codex on the chat fast-path."""
    sections: list[str] = []
    if identity_card.strip():
        sections.append("## Identity context\n" + identity_card.strip())
    sections.append(_CHAT_SYSTEM_INSTRUCTIONS)
    sections.append("## User message\n" + objective.strip())
    return "\n\n".join(sections)


__all__ = [
    "classify_is_conversational",
    "build_classify_prompt",
    "build_chat_prompt",
]
