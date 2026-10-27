"""Display cleanup for role replies, and the named-line reader of two previews.

Handoffs travel only by form; nothing here decides who holds the work.
``visible_text`` strips an old ``Decision:`` footer so a person reads the prose.
``read_key_values``/``read_block``/``read_list`` read the plan and prompt-rewrite
previews (``plan_mode``, ``prompt_rewrite``), which never enter the handoff
chain. They tolerate how models decorate a ``KEY=value`` line: a bullet, bold,
backticks, an ``ARGUS_`` prefix, a key welded to the end of a sentence; the
last occurrence of a key wins.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping

_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$")
_DECISION_FOOTER = re.compile(
    r"(?im)^\s*(?:final\s+)?decision\s*:\s*$"
)

#: What may sit immediately before a key for it to still be starting a footer.
#:
#: A character-class *fragment*, spliced into the lookbehind below — distinct
#: Deliberately only sentence terminators. A model that writes
#: ``...the requested Lean source.STATUS=done`` has ended its prose and begun
#: its verdict; a model that writes ``end with `MILESTONE_STATUS=done|continue``
#: is quoting its own instructions mid-sentence, and splitting there would
#: manufacture a verdict out of an example. Backtick, comma and colon are
#: therefore not here. An underscore is not here either, which is what keeps
#: ``STATUS`` from being found inside ``MILESTONE_STATUS``.
_SENTENCE_END_CLASS = r"[.!?)\]\"']"


def decision_footer_text(text: str) -> str:
    """The lines after the last ``Decision:`` marker, or the whole text without one."""
    source = str(text or "")
    matches = tuple(_DECISION_FOOTER.finditer(source))
    return source[matches[-1].end() :] if matches else source


def visible_text(text: str) -> str:
    """The prose of a reply as a person reads it: the ``Decision:`` footer the host reads goes."""
    source = str(text or "")
    matches = tuple(_DECISION_FOOTER.finditer(source))
    return (source[: matches[-1].start()] if matches else source).strip()


def _split_glued_keys(text: str, keys: Iterable[str]) -> str:
    """Give a key welded to the end of a sentence its own line.

    Every reader here is line-based, which is the right shape for a footer and
    one newline away from losing one. Testbed run 15 (``s-f0dbba19``) lost a
    complete Reviewer ``done`` verdict — status, reason, research result and
    frontier report, 6767 output tokens — because a single message ran
    ``...for the requested Lean source.STATUS=done`` with no line break. The
    other eighteen named fields, on their own lines below it, parsed fine. The
    harness reported "Reviewer output did not contain a valid named verdict
    footer", defaulted to ``continue``, and bought an Engineer round and a
    second Reviewer round to re-derive the verdict it had already been given.

    The module docstring promises tolerance of a bullet, bold, backticks and an
    ``ARGUS_`` prefix. This is the same promise for the one decoration that
    actually cost something.
    """
    names = sorted(
        {str(k).strip().upper() for k in keys if str(k).strip()},
        key=len,
        reverse=True,
    )
    if not names:
        return str(text or "")
    joined = "|".join(re.escape(name) for name in names)
    return re.sub(
        r"(?<=" + _SENTENCE_END_CLASS + r")"
        r"((?:ARGUS_)?(?:" + joined + r")[`*_]*\s*[:=])",
        r"\n\1",
        str(text or ""),
        flags=re.IGNORECASE,
    )


def _line_pattern(keys: Iterable[str]) -> re.Pattern[str]:
    names = sorted({str(k).strip().upper() for k in keys if str(k).strip()}, key=len, reverse=True)
    if not names:
        raise ValueError("read_key_values needs at least one key")
    joined = "|".join(re.escape(name) for name in names)
    # Models decorate keys in half a dozen ways — a bullet, bold, backticks, an
    # ARGUS_ prefix. Each of those is the model writing normally, not an error,
    # so the pattern absorbs the decoration rather than the harness rejecting
    # an answer that was perfectly clear to a reader.
    return re.compile(
        r"^(?:[-*+]\s*)?(?:[^\w`*]+\s*)?[`*_]*(?:ARGUS_)?(?P<key>"
        + joined
        + r")[`*_]*\s*[:=]\s*(?P<value>.*)$",
        re.IGNORECASE,
    )


def read_key_values(text: str, keys: Iterable[str]) -> dict[str, str]:
    """Pull the named lines out of ``text``. Missing keys are simply absent.

    Absent rather than empty-string: a caller needs to tell "the role did not
    answer this" from "the role answered with nothing", and collapsing the two
    is how a silent default gets mistaken for a decision.

    A key found welded to the end of a sentence is read too, but only after the
    line-based pass has had its say and only for the keys that pass did not
    find — so no reply that parses today can be reinterpreted by the rescue.
    """
    original = str(text or "")
    source = decision_footer_text(original)
    found = _read_key_values(source, keys)
    if not found and source != original:
        found = _read_key_values(original, keys)
    missing = [
        key
        for key in keys
        if str(key).strip() and str(key).strip().upper() not in found
    ]
    if missing:
        for key, value in _read_key_values(
            _split_glued_keys(source, missing), missing
        ).items():
            found.setdefault(key, value)
    return found


def _read_key_values(text: str, keys: Iterable[str]) -> dict[str, str]:
    pattern = _line_pattern(keys)
    found: dict[str, str] = {}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if _FENCE.match(line):
            continue
        line = line.strip("`").strip()
        match = pattern.match(line)
        if match is None:
            continue
        found[match.group("key").upper()] = match.group("value").strip().strip("`").strip()
    return found


def read_block(text: str, key: str, keys: Iterable[str]) -> str:
    """A value that runs past the end of its line, up to the next named key.

    Some fields are genuinely prose — a Reviewer's rationale is the obvious one
    — and a role writing several paragraphs is writing well, not writing
    wrongly. Truncating at the newline would silently discard the part of the
    verdict that explains it, so the value continues until the next recognised
    key or the end of the reply.
    """
    original = str(text or "")
    source = decision_footer_text(original)
    value = _read_block(source, key, keys)
    if value:
        return value
    value = _read_block(_split_glued_keys(source, keys), key, keys)
    if value or source == original:
        return value
    return _read_block(original, key, keys)


def _read_block(text: str, key: str, keys: Iterable[str]) -> str:
    pattern = _line_pattern(keys)
    wanted = str(key).strip().upper()
    collected: list[str] | None = None
    best: list[str] | None = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if _FENCE.match(line):
            continue
        match = pattern.match(line.strip("`").strip())
        if match is not None:
            if collected is not None:
                best = collected
            if match.group("key").upper() == wanted:
                collected = [match.group("value").strip()]
            else:
                collected = None
            continue
        if collected is not None:
            collected.append(raw.rstrip())
    if collected is not None:
        best = collected
    if best is None:
        return ""
    return "\n".join(best).strip().strip("`").strip()


def read_list(values: Mapping[str, str], key: str) -> tuple[str, ...]:
    """A repeated field written on one line, separated by ``;`` or ``|``.

    Commas are deliberately not separators: constraints and titles contain
    commas far more often than they contain semicolons, and splitting on them
    would quietly cut a requirement in half.
    """
    raw = str(values.get(key.upper()) or "").strip()
    if not raw or raw.lower() in {"none", "null", "(none)", "-"}:
        return ()
    parts = [part.strip() for part in re.split(r"[;|]", raw)]
    return tuple(dict.fromkeys(part for part in parts if part))


__all__ = [
    "read_block",
    "read_key_values",
    "read_list",
]
