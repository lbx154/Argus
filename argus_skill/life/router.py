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
  the chat fast-path: a conversational, capable turn (tools ALLOWED when
  the message needs them) with no reviewer loop and no report scaffolding.
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


# ── 3-tier route (CHAT / SIMPLE / COMPLEX) ──────────────────────────────────
# The manager picks the SMALLEST block that fits, lego-style:
#   CHAT    → one conversational codex turn; tools ALLOWED when the message needs
#             them (a greeting just gets a reply, "check GPU" runs the command),
#             but NO reviewer loop — a direct back-and-forth with the operator
#   SIMPLE  → one bounded codex turn (+ at most one skill), tools allowed, but
#             NO planner and NO iterative reviewer loop — for a self-contained
#             one-shot the operator can eyeball (a standalone math problem, a
#             short explanation, a small snippet, a tiny single-file edit)
#   COMPLEX → the full mission pipeline (matcher → engineer rounds → reviewer
#             every round → skill ops → planner) — for anything that needs
#             verification, measurement, iteration, or touches the repo broadly
# Conservative by construction: the reviewer is the sole done-ness gate, so the
# classifier biases HARD toward COMPLEX — SIMPLE only on a clearly trivial,
# self-verifying one-shot; everything ambiguous, repo-spanning, measured, or
# benchmark-facing is COMPLEX.
_ROUTE_INSTRUCTIONS = (
    "You are a strict intent router for a coding-agent cockpit. Read the "
    "operator's single message and answer with EXACTLY one word — CHAT, "
    "SIMPLE, or COMPLEX — and nothing else.\n\n"
    "CHAT: greetings, thanks, acknowledgements, small talk, or questions about "
    "who/what you are or what you can do (identity / capability questions). No "
    "work is requested.\n\n"
    "SIMPLE: a self-contained request that ONE bounded codex turn can finish and "
    "the operator can verify at a glance — a standalone math/logic problem, a "
    "short factual or how-to explanation, a small self-contained code snippet, a "
    "tiny single-file edit. No measurement, no benchmark, no multi-step build, "
    "nothing whose correctness needs an independent reviewer.\n\n"
    "COMPLEX: anything that builds/optimizes/measures, runs an eval or benchmark, "
    "spans multiple files or the wider repo, needs iteration or verification, is "
    "a follow-up/continuation, or is ambiguous. When in doubt, answer COMPLEX — "
    "skipping the reviewer on real work is the costly mistake.\n\n"
    "Output only the single word."
)


def build_route_prompt(text: str, role_skill_block: str = "") -> str:
    """Render the prompt for the 3-tier CHAT/SIMPLE/COMPLEX route decision."""
    return (
        f"{role_skill_block}"
        f"{_ROUTE_INSTRUCTIONS}\n\n"
        "## Operator message\n"
        f"{(text or '').strip()}\n\n"
        "## Your answer (CHAT, SIMPLE, or COMPLEX)\n"
    )


def classify_route(
    text: str,
    *,
    run_exec: Callable[[str], Any],
    role_skill_block: str = "",
) -> str:
    """Model-based 3-tier router. Returns ``"chat"``, ``"simple"``, or
    ``"complex"``. Biases HARD toward ``"complex"``: an empty message, a
    non-zero exit, a backend exception, or any unrecognised answer all route to
    ``"complex"`` so real work never silently skips the reviewer gate."""
    cleaned = (text or "").strip()
    if not cleaned:
        return "complex"
    try:
        result = run_exec(build_route_prompt(cleaned, role_skill_block))
    except Exception:  # noqa: BLE001 — any backend error -> full pipeline
        return "complex"
    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return "complex"
    answer = _extract_answer(result).strip()
    token = ""
    for ch in answer:
        if ch.isalpha():
            token += ch
        elif token:
            break
    up = token.upper()
    if up == "CHAT":
        return "chat"
    if up == "SIMPLE":
        return "simple"
    return "complex"



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
    "## You are the argus-skill MANAGER, the operator's direct line\n"
    "argus-skill supervises an autonomous coding/research agent (engineer L1 "
    "writes/runs code, reviewer L2 judges done-ness, you the manager route + own "
    "the plan) on a 7×24 daemon with a self-distilling skill cache. Reply in the "
    "SAME language the operator used, with enough detail to be useful. Match "
    "their register and avoid empty boilerplate.\n\n"
    "You are a capable acting agent, NOT a locked chatbot:\n"
    "1. You MAY run shell commands, inspect the workspace and files, and use any "
    "tool when it helps you answer or act — e.g. check GPU usage, read a file, "
    "grep the repo, look up real state before answering. For a bare greeting or a "
    "pure who/what-are-you question, just reply; don't run tools you don't need.\n"
    "2. You MAY do real work right here when a single focused turn can finish it "
    "(a small edit, a quick check, run a command and report what it said). For "
    "substantial, multi-step, or measured/benchmarked work — optimize a kernel, "
    "reproduce an experiment, build or refactor across many files — do NOT "
    "half-do it in one turn: tell the operator you'll take it on as a full task "
    "and let the engineer→reviewer pipeline run it (they can restate it as a task "
    "and it is queued for the daemon).\n"
    "3. Keep replies conversational, but do not artificially compress useful "
    "context. Use headings, bullets, code fences, or examples when they make the "
    "answer clearer.\n"
    "4. If the operator greets you or asks what you can do, introduce yourself as "
    "the argus-skill manager and give 2-3 CONCRETE example tasks — optimize a "
    "CUDA/Triton kernel to beat a benchmark (SOL-ExecBench / KernelBench), "
    "reproduce and measure a research benchmark and report honest numbers, or "
    "implement/refactor a feature with tests — then invite them to just describe "
    "their task in plain words.\n"
)



def build_chat_prompt(*, objective: str, identity_card: str = "") -> str:
    """Render the full prompt sent to codex on the chat fast-path."""
    sections: list[str] = []
    if identity_card.strip():
        sections.append("## Identity context\n" + identity_card.strip())
    sections.append(_CHAT_SYSTEM_INSTRUCTIONS)
    sections.append("## User message\n" + objective.strip())
    return "\n\n".join(sections)


_SIMPLE_SYSTEM_INSTRUCTIONS = (
    "## You are the argus-skill MANAGER handling a SIMPLE one-shot task\n"
    "The operator asked for something a single bounded turn can finish — and "
    "they will verify the result themselves, so there is NO reviewer and NO "
    "iteration after this. Do the task NOW, completely, in this one turn.\n\n"
    "Rules:\n"
    "1. You MAY use tools (read/edit files, run shell) when the task needs them; "
    "for a pure question just answer.\n"
    "2. Stay tightly scoped to exactly what was asked — do not start a "
    "multi-step build, do not wander the repo, do not open new workstreams. If "
    "the task turns out to be bigger than one turn, say so plainly instead of "
    "half-doing it.\n"
    "3. End with a clear statement of what you did (and the answer / the file "
    "you changed). Include enough context for the operator to understand the "
    "result; use structure when it helps.\n"
)


def build_simple_prompt(*, objective: str, skill_block: str = "") -> str:
    """Render the prompt for the SIMPLE one-shot path (tools allowed, no
    reviewer). ``skill_block`` is an optional matched-skill playbook prepended
    as guidance; empty when no skill matched."""
    sections: list[str] = []
    if skill_block.strip():
        sections.append("## Relevant skill\n" + skill_block.strip())
    sections.append(_SIMPLE_SYSTEM_INSTRUCTIONS)
    sections.append("## Task\n" + objective.strip())
    return "\n\n".join(sections)


__all__ = [
    "classify_is_conversational",
    "classify_route",
    "build_classify_prompt",
    "build_route_prompt",
    "build_chat_prompt",
    "build_simple_prompt",
]
