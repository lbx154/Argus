"""The explicit way to ask Argus a question without starting work.

Argus classifies every message to decide whether it is conversation or a task,
and that classifier is deliberately biased toward "task" — silently answering
something that was meant to be done is worse than doing something that was
meant as a question. The consequence is that asking a quick question can still
cost a classify call and, when the classifier errs on the safe side, a queued
backlog item and a full Manager/Planner/Engineer/Reviewer round.

``/ask`` removes the guess. The operator has stated what this message is, so
no classification runs, nothing is queued, and no role beyond the Manager is
involved. It is the escape hatch that lets the automatic path stay
conservative: the classifier does not need to become more willing to treat
work as chat, because anyone who wants chat can simply say so.

Only an explicit prefix triggers it. Inferring the intent would reintroduce
exactly the guess this exists to avoid.
"""
from __future__ import annotations

__all__ = ["ASK_PREFIXES", "strip_ask_prefix"]

#: Written as a slash command in every surface: cockpit, web, Telegram, Feishu.
#: `/q` is deliberately absent — it already means /quit in the cockpit.
ASK_PREFIXES: tuple[str, ...] = ("/ask", "/chat")


def strip_ask_prefix(text: str) -> str | None:
    """Return the question when *text* is an explicit ask, else ``None``.

    ``/ask`` with nothing after it is not a question, so it falls through to
    normal handling rather than sending the Manager an empty prompt.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    head, _, rest = cleaned.partition(" ")
    # Tolerate the bot-mention suffix Telegram appends: /ask@argusbot
    head = head.split("@", 1)[0].lower()
    if head not in ASK_PREFIXES:
        return None
    question = rest.strip()
    return question or None
