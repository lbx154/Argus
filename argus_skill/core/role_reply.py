"""Read a role's decision out of ordinary prose, without dictating its shape.

Roles are not forced to emit JSON. A model told to reply with "ONE JSON object
and NOTHING else" spends its answer satisfying a serialiser instead of thinking,
loses the ability to explain itself, and fails the whole decision when it adds a
sentence of context. The harness is not smarter than the agent, and demanding a
wire format is the harness deciding how the agent may speak.

Instead the role writes naturally and states its decision on named lines, the
same convention the Planner has always used for ``PROJECT_DONE=`` /
``REASON=``. This module is that convention, generalised so every role decision
can use one reader.

The parsing is deliberately tolerant:

* ``KEY=value`` and ``KEY: value`` both work;
* a leading bullet or an ``ARGUS_`` prefix is ignored;
* surrounding backticks and code fences are stripped;
* the key is matched case-insensitively;
* anything that is not a recognised key is skipped, so prose above, below or
  between the lines costs nothing;
* the last occurrence wins, so a role that restates its conclusion at the end
  is read the way a human would read it.

JSON is still *accepted* where a caller opts in — an older session or a model
that volunteers a JSON object should not fail — but it is never required.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

_FENCE = re.compile(r"^\s*```[a-zA-Z0-9_-]*\s*$")


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
        r"^(?:[-*+]\s*)?[`*_]*(?:ARGUS_)?(?P<key>"
        + joined
        + r")[`*_]*\s*[:=]\s*(?P<value>.*)$",
        re.IGNORECASE,
    )


def read_key_values(text: str, keys: Iterable[str]) -> dict[str, str]:
    """Pull the named lines out of ``text``. Missing keys are simply absent.

    Absent rather than empty-string: a caller needs to tell "the role did not
    answer this" from "the role answered with nothing", and collapsing the two
    is how a silent default gets mistaken for a decision.
    """
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


def read_records(
    text: str,
    keys: Iterable[str],
    *,
    start_key: str,
) -> list[dict[str, str]]:
    """Repeated blocks, each begun by ``start_key``.

    The convention the Planner has always used for its ``TASK_*`` blocks,
    generalised: a role listing several things writes them one after another,
    and each new ``start_key`` line opens the next record. Plain
    :func:`read_key_values` keeps only the last occurrence of a key, which is
    right for a single verdict and wrong for a list.
    """
    pattern = _line_pattern(keys)
    wanted = str(start_key).strip().upper()
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if _FENCE.match(line):
            continue
        match = pattern.match(line.strip("`").strip())
        if match is None:
            continue
        key = match.group("key").upper()
        value = match.group("value").strip().strip("`").strip()
        if key == wanted:
            if current is not None:
                records.append(current)
            current = {key: value}
        elif current is not None:
            current[key] = value
    if current is not None:
        records.append(current)
    return records


def read_block(text: str, key: str, keys: Iterable[str]) -> str:
    """A value that runs past the end of its line, up to the next named key.

    Some fields are genuinely prose — a Reviewer's rationale is the obvious one
    — and a role writing several paragraphs is writing well, not writing
    wrongly. Truncating at the newline would silently discard the part of the
    verdict that explains it, so the value continues until the next recognised
    key or the end of the reply.
    """
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


def read_bool(values: Mapping[str, str], key: str, default: bool = False) -> bool:
    raw = str(values.get(key.upper()) or "").strip().casefold()
    if raw in {"true", "yes", "y", "1", "done", "complete", "completed"}:
        return True
    if raw in {"false", "no", "n", "0", "retry", "blocked", "incomplete"}:
        return False
    return default


def read_float(values: Mapping[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(str(values.get(key.upper()) or "").strip())
    except (TypeError, ValueError):
        return default


def read_optional(values: Mapping[str, str], key: str) -> str:
    """A value the role may explicitly decline to give.

    ``none``/``null``/``n/a`` come back as ``""`` so a caller does not have to
    re-check for the four ways a model writes "not applicable".
    """
    raw = str(values.get(key.upper()) or "").strip()
    if raw.casefold() in {"none", "null", "n/a", "na", "-", "(none)"}:
        return ""
    return raw


def legacy_json_object(text: str) -> dict[str, Any] | None:
    """A JSON object the role volunteered, if it did. Never required.

    Kept so that a daemon mid-flight on an older prompt, or a model that
    answers in JSON out of habit, still parses. Callers try the named lines
    first: this is a fallback, not a second contract.
    """
    cleaned = str(text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        value = json.loads(cleaned)
    except (TypeError, ValueError):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            value = json.loads(cleaned[start : end + 1])
        except (TypeError, ValueError):
            return None
    return value if isinstance(value, dict) else None


__all__ = [
    "legacy_json_object",
    "read_block",
    "read_bool",
    "read_float",
    "read_key_values",
    "read_list",
    "read_optional",
    "read_records",
]
