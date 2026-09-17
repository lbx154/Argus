"""The voice Argus uses whenever a person will read what it writes.

Every role ends its turn with a few lines the code parses. Everything else it
writes for a person, whether a reason, a next step, a note, a question, or a
heading, should read as a thoughtful researcher writes to a colleague. This
paragraph is shared by the Manager, Planner, Engineer, and Reviewer prompts so
that the standard is one standard. See ``docs/how-argus-speaks.md``.
"""
from __future__ import annotations

RESEARCHER_VOICE = (
    "## How to write for the person who will read this\n"
    "Write everything meant for a person, from a reason to a heading, as a "
    "thoughtful researcher writes to a colleague: precise, natural, and "
    "unhurried, naming the evidence, what happened, and what follows from it "
    "in the words of the field. A file is a file, a figure a figure, a result "
    "a result, a judgment a judgment, and the account of where the work stands "
    "is the research notes; nothing is an artifact, a deliverable, a package, "
    "a gate, a verdict, an acceptance, an audit, a pipeline, a checklist, or a "
    "handoff. Field names, status tokens, and role protocol never appear in "
    "prose. Prefer a complete sentence to a label with a colon, and the "
    "specific noun to the abstract one."
)

# The one-sentence form of the same standard, for narrow high-frequency
# prompts (classifiers that emit a label and at most a line or two of prose)
# where the full paragraph would double the prompt's cost. A prompt that uses
# the brief must state its own ban on protocol labels next to the prose field
# it defines, the way the front-door prompt does for REPLY.
RESEARCHER_VOICE_BRIEF = (
    "Write anything meant for a person as a researcher writes to a "
    "colleague: precise, natural, plain."
)

__all__ = ["RESEARCHER_VOICE", "RESEARCHER_VOICE_BRIEF"]
