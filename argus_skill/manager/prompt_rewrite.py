"""Prompt rewrite — restate an operator's short draft BEFORE it is dispatched.

Operators type terse requests ("优化一下 kernel", "写个 paper", "fix the flaky
test"). Feeding that verbatim to the Planner/Engineer/Reviewer team burns rounds
on guessing what was meant. The Manager already owns front-door judgment, so it
is the right role to restate the request as an executable brief.

This module is deliberately thin and mirrors :mod:`argus_skill.manager.plan_mode`:

* :class:`PromptRewrite` — the in-memory result shape.
* :func:`rewrite_prompt` — ask the model (via the runner the cockpit already
  holds) for a rewrite and parse it. Failures are surfaced explicitly via
  ``PromptRewrite.error``; we never silently hand back a fabricated rewrite.
* :func:`parse_rewrite_text` — the pure, unit-testable parser (no live model).

Design note (matches the rest of the Manager): the harness makes no judgment
about whether a rewrite is *good*. It renders a request, parses the reply
robustly, and hands the result to the operator to accept, edit, or discard. The
rewrite is never auto-sent — the operator always sees it first.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ..roles.prompts.manager import build_prompt_rewrite_prompt

# A rewrite is a brief, not a specification: keep the advisory lists short so
# the cockpit can show the whole thing without scrolling.
_MAX_LIST_ITEMS = 6
_MAX_REWRITE_CHARS = 4000


@dataclass
class PromptRewrite:
    """One Manager-authored restatement of an operator draft.

    ``rewritten`` is the text the operator is invited to send (after editing it
    however they like). ``changes`` explains what was made explicit.

    ``questions`` is what the Manager deliberately kept OUT of the rewrite and
    is putting to the operator instead: both things it could not infer and
    things it is actively proposing (a metric, a threshold, a scope limit) but
    will not decide on their behalf. The Manager is expected to bring this kind
    of judgment — it just may not smuggle it into ``rewritten``.

    ``error`` is non-empty only when the rewrite failed, in which case
    ``rewritten`` is empty and callers must keep the operator's original draft.
    """

    original: str
    rewritten: str = ""
    changes: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.rewritten.strip()) and not self.error


def _clean(text: str) -> str:
    """Trim a fragment and strip one layer of wrapping quotes / emphasis."""
    s = (text or "").strip()
    for quote in ('"', "'", "`"):
        if len(s) >= 2 and s.startswith(quote) and s.endswith(quote):
            s = s[1:-1].strip()
            break
    s = re.sub(r"^[*_]{1,3}\s*", "", s)
    s = re.sub(r"\s*[*_]{1,3}$", "", s)
    return s.strip()


def _loads_json(text: str) -> Any:
    """Best-effort JSON parse: strip a code fence, else take the widest object."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001 — fall through to bracket extraction
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(cleaned[start : end + 1])
        except Exception:  # noqa: BLE001
            return None
    return None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        cleaned = _clean(value)
        return [cleaned] if cleaned else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                cleaned = _clean(item)
                if cleaned:
                    out.append(cleaned)
        return out[:_MAX_LIST_ITEMS]
    return []


def _strip_fence(text: str) -> str:
    body = (text or "").strip()
    body = re.sub(r"^```[a-zA-Z]*\s*\n?", "", body)
    body = re.sub(r"\n?```$", "", body)
    return body.strip()


_REWRITE_KEYS = ("REWRITTEN", "CHANGES", "QUESTIONS")


def _named_rewrite(raw: str) -> PromptRewrite | None:
    """The rewrite as stated on named lines, or ``None`` when it was not.

    ``REWRITTEN`` is read as a block: a rewritten request is prose and often
    runs to several paragraphs, and cutting it at the first newline would hand
    the operator a truncated draft. ``None`` rather than an empty result keeps
    the JSON and plain-text fallbacks below reachable.
    """
    from ..core.role_reply import read_block, read_key_values, read_list

    values = read_key_values(raw, _REWRITE_KEYS)
    if "REWRITTEN" not in values:
        return None
    rewritten = _strip_fence(read_block(raw, "REWRITTEN", _REWRITE_KEYS))
    if not rewritten:
        return None
    return PromptRewrite(
        original="",
        rewritten=rewritten[:_MAX_REWRITE_CHARS],
        changes=list(read_list(values, "CHANGES"))[:_MAX_LIST_ITEMS],
        questions=list(read_list(values, "QUESTIONS"))[:_MAX_LIST_ITEMS],
    )


def parse_rewrite_text(text: str) -> PromptRewrite:
    """Parse a model reply into a :class:`PromptRewrite`.

    Accepts the contracted JSON object, and falls back to treating a plain
    (non-JSON) reply as the rewrite itself — models occasionally answer with
    just the rewritten text, and throwing that away would be worse than using
    it. Returns a :class:`PromptRewrite` with an empty ``rewritten`` when there
    is nothing usable, so callers can fail soft without inventing anything.
    """
    raw = (text or "").strip()
    if not raw:
        return PromptRewrite(original="", rewritten="")

    named = _named_rewrite(raw)
    if named is not None:
        return named

    obj = _loads_json(raw)
    if isinstance(obj, dict):
        rewritten = ""
        for key in ("rewritten", "rewrite", "prompt", "text", "result"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                rewritten = _strip_fence(value)
                break
        changes = _string_list(obj.get("changes") or obj.get("notes"))
        questions = _string_list(obj.get("questions") or obj.get("open_questions"))
        if rewritten:
            return PromptRewrite(
                original="",
                rewritten=rewritten[:_MAX_REWRITE_CHARS],
                changes=changes,
                questions=questions,
            )

    # Plain-text reply: use it verbatim, minus any wrapping code fence.
    body = _strip_fence(raw)
    if body.startswith("{") or not body:
        return PromptRewrite(original="", rewritten="")
    return PromptRewrite(original="", rewritten=body[:_MAX_REWRITE_CHARS])


def _extract_text(result: Any) -> str:
    """Pull the model's reply text out of a RunnerResult-shaped object."""
    msg = getattr(result, "last_agent_message", None)
    if not msg:
        msgs = getattr(result, "agent_messages", None) or []
        msg = msgs[-1] if msgs else ""
    return str(msg or "")


def _resolve_run_exec(
    runner: Any,
    *,
    model: str | None = None,
    reasoning_effort: str = "low",
    run_label: str = "manager-rewrite",
):  # noqa: ANN202 — returns a callable or None
    """Find a ``run_exec``-capable backend on ``runner`` and wrap it.

    Mirrors ``plan_mode._resolve_run_exec``: the cockpit hands us its high-level
    runner, test stubs hand us a raw backend. Returns ``None`` when no usable
    backend exists so the caller can fail soft.
    """
    if runner is None:
        return None
    for candidate in (
        runner,
        getattr(runner, "backend", None),
        getattr(runner, "_backend", None),
    ):
        if candidate is None:
            continue
        run_exec = getattr(candidate, "run_exec", None)
        if not callable(run_exec):
            continue

        def _call(prompt: str, _run_exec=run_exec):  # noqa: ANN001
            from ..core.models import RunnerOptions

            return _run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=model,
                    reasoning_effort=reasoning_effort,
                    skip_git_repo_check=True,
                ),
                run_label=run_label,
            )

        return _call
    return None


def _failed(original: str, reason: str) -> PromptRewrite:
    return PromptRewrite(original=original, rewritten="", error=reason)


def rewrite_prompt(
    runner: Any,
    draft: str,
    *,
    model: str | None = None,
    reasoning_effort: str = "low",
    run_label: str = "manager-rewrite",
    role_banner: str = "",
    project_context: str = "",
    operator_context: str = "",
) -> PromptRewrite:
    """Ask the Manager to restate ``draft`` as an executable brief.

    A missing/raising runner, a non-zero exit, or an empty/unparseable reply are
    surfaced explicitly via ``PromptRewrite.error``. The caller keeps the
    operator's original draft in that case — a failed rewrite must never
    silently replace what the operator typed.
    """
    original = (draft or "").strip()
    if not original:
        return _failed("", "nothing to rewrite")

    run_exec = _resolve_run_exec(
        runner,
        model=model,
        reasoning_effort=reasoning_effort,
        run_label=run_label,
    )
    if run_exec is None:
        return _failed(original, "could not rewrite: no runner backend")

    try:
        result = run_exec(
            build_prompt_rewrite_prompt(
                original,
                role_banner=role_banner,
                project_context=project_context,
                operator_context=operator_context,
            )
        )
    except Exception:  # noqa: BLE001 — keep the cockpit alive but surface failure
        return _failed(original, "could not rewrite: backend error")

    if int(getattr(result, "exit_code", 0) or 0) != 0:
        return _failed(original, "could not rewrite: Manager exited non-zero")

    parsed = parse_rewrite_text(_extract_text(result))
    if not parsed.rewritten.strip():
        return _failed(original, "could not rewrite: model reply was empty or unparseable")
    parsed.original = original
    return parsed


__all__ = [
    "PromptRewrite",
    "build_prompt_rewrite_prompt",
    "parse_rewrite_text",
    "rewrite_prompt",
]
