"""The voice Argus uses whenever a person will read what it writes.

Every role ends its turn with a few lines the code parses. Everything else it
writes for a person, whether a reason, a next step, a note, a question, or a
heading, should be understandable without knowing Argus or the field. This
paragraph is shared by the Manager, Planner, Engineer, and Reviewer prompts so
that the standard is one standard. See ``docs/how-argus-speaks.md``.
"""
from __future__ import annotations

RESEARCHER_VOICE = (
    "## Writing for people\n"
    "Write plainly in their language for an ordinary reader. "
    "Lead with the answer and what it means for the person; name the work, why, "
    "evidence and limits. Explain necessary terms at first use. "
    "Match depth: simple answers without a forced template; professional detail "
    "when asked. Preserve numbers, sources and uncertainty. "
    "Next steps only if the person must act. If blocked, say cause, effect and "
    "whether work continues or needs them. Never invent progress, completion "
    "or retries. Call files files and results results. Keep internal roles, rounds, "
    "fields, status tokens, tool arguments and workflow jargon out of prose. "
    "Put technical facts in details and required protocol in its separate footer."
)

# The one-sentence form of the same standard, for narrow high-frequency
# prompts (classifiers that emit a label and at most a line or two of prose)
# where the full paragraph would double the prompt's cost. A prompt that uses
# the brief must state its own ban on protocol labels next to the prose field
# it defines, the way the front-door prompt does for REPLY.
def operator_language(chinese: bool) -> str:
    """How to word a sentence the operator reads as written: their language, their names for the roles."""
    return "in Chinese, the operator's language, calling the roles 经理, 规划者, 工程师 and 审阅者" if chinese else "in English, the operator's language"


__all__ = ["RESEARCHER_VOICE", "operator_language"]
