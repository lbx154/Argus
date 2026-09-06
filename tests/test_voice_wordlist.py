"""The cleaned template files stay in Argus's voice.

``docs/how-argus-speaks.md`` retires the machinery words — gate, verdict,
handoff, audit, pipeline, checklist, artifact, and their kin — from every
sentence written for a person or a model. The 48-hour billing forensics found
tens of thousands of engineer.progress lines echoing exactly those words, and
their direct upstream was a handful of shared template files. This test keeps
those files clean once they have been rewritten, so the vocabulary cannot leak
back in through one edited string.

What is scanned, and what is exempt:

- Only the files in ``VOICE_CLEAN_FILES`` are scanned. A file joins the list
  when its model- and operator-visible prose has been rewritten to the
  standard; extend the tuple with the repo-relative path and nothing else.
- Only string constants are scanned (via ``ast``), so comments never trip the
  scan, and module/class/function docstrings are explicitly excluded — both
  may quote the retired words when explaining history or intent.
- String constants with no whitespace are exempt: field names, enum values,
  event types, paths, and regexes (``artifact_root``, ``artifacts``,
  ``reviewed_handoff``) are the machine's tokens and stay as the code expects
  them (principle 9 of the doc).
- A discipline's own use of a word survives: the phrases in
  ``DISCIPLINE_PHRASES`` mirror the "disciplines keep their words" section of
  the doc and are removed from a string before the scan. Extend both together.
- A specific prose-shaped literal that must keep a word (for example a
  verbatim quote of an old message) goes into ``ALLOWED_LITERALS`` as its
  exact full value, ideally with a comment saying why.

The word list mirrors the word map in ``docs/how-argus-speaks.md``. When a new
word is retired there, add its forms to ``BANNED_WORD_PATTERNS`` here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Files whose model- and operator-visible prose has been brought up to the
# standard of docs/how-argus-speaks.md. Append a repo-relative path once a
# file's prose is rewritten; never remove one to silence a failure.
VOICE_CLEAN_FILES: tuple[str, ...] = (
    "argus_skill/apps/_runtime_supervisor.py",
    "argus_skill/cli/event_format.py",
    "argus_skill/core/operator_messages.py",
    "argus_skill/engineer/round_reviewer.py",
    "argus_skill/engineer/round_self_review.py",
    "argus_skill/engineer/round_settlement.py",
    "argus_skill/life/supervisor/_mission_execution_settlement.py",
    "argus_skill/reviewer/_core.py",
    "argus_skill/reviewer/_parsing.py",
    "argus_skill/verticals/argus_maintenance/stages.py",
)

# One pattern per retired word family, matched case-insensitively on word
# boundaries. Mirrors the word map in docs/how-argus-speaks.md.
BANNED_WORD_PATTERNS: tuple[str, ...] = (
    r"gate[sd]?",
    r"gating",
    r"verdicts?",
    r"hand-?offs?",
    r"audit(?:s|ed|ing)?",
    r"pipelines?",
    r"checklists?",
    r"artifacts?",
    r"deliverables?",
    r"blockers?",
    r"sign-?offs?",
    r"kill\s+(?:condition|argument)s?",
    r"work\s+packages?",
)

_BANNED = re.compile(
    r"\b(?:" + "|".join(BANNED_WORD_PATTERNS) + r")\b",
    re.IGNORECASE,
)

# The disciplines keep their words (see the section of the same name in
# docs/how-argus-speaks.md). These phrases are removed before the scan, so
# "a data pipeline in the method figure" passes while "the review pipeline"
# fails. Keep this list and the doc's list in step.
DISCIPLINE_PHRASES: tuple[str, ...] = (
    "data pipeline",
    "data pipelines",
    "method pipeline",
    "CPU pipeline",
    "logic gate",
    "logic gates",
    "AND gate",
    "OR gate",
    "NAND gate",
    "gate count",
    "clock gating",
    "measurement artifact",
    "measurement artifacts",
    "imaging artifact",
    "imaging artifacts",
    "compression artifact",
    "compression artifacts",
    "timing sign-off",
    "design sign-off",
)

# Exact full values of prose-shaped string constants that may keep a retired
# word — verbatim quotes of historical output, for example. Empty today.
ALLOWED_LITERALS: frozenset[str] = frozenset()


def _docstring_constants(tree: ast.AST) -> set[int]:
    """Node ids of every docstring constant, which the scan must skip."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _strip_discipline_phrases(text: str) -> str:
    for phrase in DISCIPLINE_PHRASES:
        text = re.sub(re.escape(phrase), " ", text, flags=re.IGNORECASE)
    return text


def _violations_in_file(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    docstrings = _docstring_constants(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        text = node.value
        if text in ALLOWED_LITERALS:
            continue
        # Machine tokens carry no whitespace: keys, enums, paths, regexes.
        if not any(ch.isspace() for ch in text):
            continue
        match = _BANNED.search(_strip_discipline_phrases(text))
        if match:
            snippet = " ".join(text.split())[:120]
            found.append(
                f"{path.relative_to(REPO_ROOT)}:{node.lineno}: "
                f"{match.group(0)!r} in {snippet!r}"
            )
    return found


def test_voice_clean_files_exist() -> None:
    missing = [name for name in VOICE_CLEAN_FILES if not (REPO_ROOT / name).is_file()]
    assert not missing, (
        "These files moved or were deleted; update VOICE_CLEAN_FILES so the "
        f"voice standard keeps covering them: {missing}"
    )


def test_no_retired_words_in_visible_prose() -> None:
    violations: list[str] = []
    for name in VOICE_CLEAN_FILES:
        violations.extend(_violations_in_file(REPO_ROOT / name))
    assert not violations, (
        "Retired machinery words reappeared in prose that a model or the "
        "operator reads. Rewrite the sentence in the words of the field (see "
        "docs/how-argus-speaks.md, word map), or — only for a discipline's own "
        "term or a verbatim historical quote — extend DISCIPLINE_PHRASES or "
        "ALLOWED_LITERALS:\n" + "\n".join(violations)
    )


def test_discipline_phrases_match_the_doc() -> None:
    """The doc's whitelist section and this file's list cannot drift apart."""
    doc = (REPO_ROOT / "docs" / "how-argus-speaks.md").read_text(encoding="utf-8")
    assert "## The disciplines keep their words" in doc, (
        "docs/how-argus-speaks.md lost its discipline whitelist section; the "
        "allowances in this test are grounded there."
    )
    for anchor in ("logic gate", "sign-off", "measurement artifact", "data pipeline"):
        assert anchor in doc, (
            f"docs/how-argus-speaks.md no longer names {anchor!r}; realign "
            "DISCIPLINE_PHRASES with the doc before changing either."
        )
