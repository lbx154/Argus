"""Plan mode — preview a step-by-step plan BEFORE any work is queued.

Codex / Claude-Code / Cursor parity: when the operator types ``/plan <objective>``
the cockpit shows an ordered, scannable outline of HOW the agent *would* approach the
objective and asks for confirmation before anything reaches the backlog/daemon.
This is a preview only — drafting a plan must never run tools, write code, or
execute the work.

This module is deliberately thin and self-contained:

* :class:`PlanStep` / :class:`Plan` — the in-memory plan shape.
* :func:`draft_plan` — ask the model (via the runner the cockpit already holds) for
  an ordered plan and parse it. Failures are surfaced explicitly in the
  returned :class:`Plan`; the cockpit stays alive, but it does NOT silently
  invent a fake one-step plan.
* :func:`render_plan` — pretty, scannable multi-line text (numbered steps + notes).
* :func:`parse_plan_text` — the pure, unit-testable parser (no live model). Accepts
  JSON (list of steps, or an object with a ``steps`` key) and a numbered/bulleted
  list fallback; returns ``[]`` on garbage so the caller can surface the draft
  failure explicitly.

Design note (matches the rest of the Manager): the harness does no domain
judgment here — it only renders a request, parses the reply robustly, and lets
the operator approve. The plan's *quality* is the model's call, not ours.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..roles.prompts.manager import build_plan_prompt

# Product contract: a preview plan has a bounded number of steps;
# :func:`draft_plan` trims to this ceiling. :func:`parse_plan_text` stays
# uncapped so it faithfully reports whatever it parsed (tests target it).
_MAX_STEPS = 8

# Separators that split a single list line into "title — detail", in priority
# order (em dash, en dash, spaced hyphen, then colon as a last resort).
_TITLE_DETAIL_SEPS = (" — ", " – ", " -- ", " - ", ": ")

# Leading list markers we strip: "1.", "2)", "(3)", "- ", "* ", "• ".
_NUMBERED_RE = re.compile(r"^\s*\(?(\d{1,3})[.)\]]\s+(.*\S)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*•·]\s+(.*\S)\s*$")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class PlanStep:
    """One ordered step in a preview plan.

    ``title`` is an imperative ("Profile the kernel"); ``detail`` is an
    optional one-sentence what/why. ``detail`` may be empty.
    """

    title: str
    detail: str = ""


@dataclass
class Plan:
    """A previewable, not-yet-executed plan for one objective.

    ``steps`` is the ordered list the operator approves before anything is
    queued; ``notes`` are optional caveats / assumptions the model surfaced.
    ``error`` is non-empty only when drafting failed, in which case ``steps``
    is empty and callers should surface the failure rather than pretend a
    model-authored plan exists.
    """

    objective: str
    steps: list[PlanStep] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""


# ---------------------------------------------------------------------------
# Pure parsing (unit-testable without a live model)
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    """Trim a fragment and strip surrounding markdown emphasis / quotes."""
    s = (text or "").strip()
    # Drop matched wrapping quotes/backticks once.
    for q in ('"', "'", "`"):
        if len(s) >= 2 and s.startswith(q) and s.endswith(q):
            s = s[1:-1].strip()
            break
    # Strip leading/trailing markdown bold/italic markers.
    s = re.sub(r"^[*_]{1,3}\s*", "", s)
    s = re.sub(r"\s*[*_]{1,3}$", "", s)
    return s.strip()


def _split_title_detail(content: str) -> tuple[str, str]:
    """Split one list line into ``(title, detail)`` on the first known separator.

    No separator → the whole line is the title and the detail is empty.
    """
    body = (content or "").strip()
    for sep in _TITLE_DETAIL_SEPS:
        idx = body.find(sep)
        if idx > 0:
            title = _clean(body[:idx])
            detail = _clean(body[idx + len(sep):])
            if title:
                return title, detail
    return _clean(body), ""


def _step_from_obj(obj: Any) -> PlanStep | None:
    """Build a :class:`PlanStep` from one JSON element (dict or str)."""
    if isinstance(obj, str):
        title, detail = _split_title_detail(obj)
        return PlanStep(title, detail) if title else None
    if isinstance(obj, dict):
        title = ""
        for key in ("title", "step", "name", "action", "summary"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                title = _clean(val)
                break
        detail = ""
        for key in ("detail", "details", "description", "desc", "why", "note"):
            val = obj.get(key)
            if isinstance(val, str) and val.strip():
                detail = _clean(val)
                break
        if not title and detail:
            title, detail = detail, ""
        return PlanStep(title, detail) if title else None
    return None


def _loads_json(text: str) -> Any:
    """Best-effort JSON parse: strip a code fence, else extract the first
    bracketed region. Returns the parsed object or ``None`` (never raises)."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001 — fall through to bracket extraction
        pass
    # Try the widest list, then the widest object.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = cleaned.find(opener)
        end = cleaned.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except Exception:  # noqa: BLE001
                continue
    return None


def _steps_from_json(obj: Any) -> list[PlanStep]:
    """Pull a step list out of a parsed JSON object (list, or ``{"steps": [...]}``)."""
    seq: Any = None
    if isinstance(obj, list):
        seq = obj
    elif isinstance(obj, dict):
        for key in ("steps", "plan", "items"):
            val = obj.get(key)
            if isinstance(val, list):
                seq = val
                break
    if not isinstance(seq, list):
        return []
    out: list[PlanStep] = []
    for element in seq:
        step = _step_from_obj(element)
        if step is not None:
            out.append(step)
    return out


def _steps_from_lines(text: str) -> list[PlanStep]:
    """Parse a numbered / bulleted list into steps. Lines that match no marker
    are ignored; if nothing matches, returns ``[]`` (garbage → fail soft)."""
    out: list[PlanStep] = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        m = _NUMBERED_RE.match(line) or _BULLET_RE.match(line)
        if not m:
            continue
        content = m.group(m.lastindex or 1)
        title, detail = _split_title_detail(content)
        if title:
            out.append(PlanStep(title, detail))
    return out


def parse_plan_text(text: str) -> list[PlanStep]:
    """Parse a model reply into an ordered list of :class:`PlanStep`.

    Robust to two shapes, tried in order:

    1. **JSON** — either a list (``[{"title":…,"detail":…}, …]`` or a list of
       plain strings) or an object with a ``steps`` key.
    2. **Numbered / bulleted list** — ``1. …`` / ``2) …`` / ``- …`` / ``* …``,
       with an optional ``title — detail`` split per line.

    Returns ``[]`` on empty or unparseable input so callers can fail soft. This
    is the pure helper the unit tests target (no live model involved).
    """
    raw = (text or "").strip()
    if not raw:
        return []
    steps = _steps_from_json(_loads_json(raw))
    if steps:
        return steps
    return _steps_from_lines(raw)


def parse_plan_notes(text: str) -> list[str]:
    """Best-effort extraction of advisory ``notes`` from a JSON reply.

    Only applies to the object form ``{"steps": [...], "notes": [...]}``. A
    string ``notes`` value is wrapped into a single-element list. Returns ``[]``
    when there is nothing to surface (never raises)."""
    from ..core.role_reply import read_key_values, read_list

    values = read_key_values(text, ("NOTES",))
    if "NOTES" in values:
        return list(read_list(values, "NOTES"))

    obj = _loads_json(text)
    if not isinstance(obj, dict):
        return []
    val = obj.get("notes")
    if isinstance(val, str):
        cleaned = _clean(val)
        return [cleaned] if cleaned else []
    if isinstance(val, list):
        out: list[str] = []
        for item in val:
            if isinstance(item, str) and item.strip():
                out.append(_clean(item))
        return out
    return []


def _resolve_run_exec(
    runner: Any,
    *,
    model: str | None = None,
    reasoning_effort: str = "low",
    run_label: str = "manager-plan",
    working_dir: str | None = None,
    dangerous_yolo: bool = True,
    max_seconds: int | None = None,
):  # noqa: ANN202 — returns a callable or None
    """Find a ``run_exec``-capable backend on ``runner`` and wrap it.

    The cockpit hands us its high-level runner (``_SkillLoopRunner``), which exposes
    the underlying backend as ``.backend`` / ``._backend`` rather than a top-level
    ``run_exec``. Test stubs and raw backends expose ``run_exec`` directly. We try
    each candidate and return a ``(prompt) -> result`` closure, or ``None`` when no
    usable backend is found (caller then fails soft)."""
    if runner is None:
        return None
    for candidate in (runner, getattr(runner, "backend", None),
                      getattr(runner, "_backend", None)):
        if candidate is None:
            continue
        run_exec = getattr(candidate, "run_exec", None)
        if not callable(run_exec):
            continue

        def _call(prompt: str, _run_exec=run_exec):  # noqa: ANN001
            from ..core.models import RunnerOptions

            deadline = (
                time.monotonic() + max_seconds
                if max_seconds is not None and max_seconds > 0
                else None
            )
            return _run_exec(
                prompt=prompt,
                options=RunnerOptions(
                    model=model,
                    reasoning_effort=reasoning_effort,
                    working_dir=working_dir,
                    dangerous_yolo=dangerous_yolo,
                    full_auto=not dangerous_yolo,
                    skip_git_repo_check=True,
                    external_interrupt_reason_provider=(
                        (
                            lambda: (
                                "Planner grounding time budget reached"
                                if deadline is not None
                                and time.monotonic() >= deadline
                                else None
                            )
                        )
                        if deadline is not None
                        else None
                    ),
                ),
                run_label=run_label,
            )

        return _call
    return None


def _extract_text(result: Any) -> str:
    """Pull the model's reply text out of a RunnerResult-shaped object."""
    msg = getattr(result, "last_agent_message", None)
    if not msg:
        msgs = getattr(result, "agent_messages", None) or []
        msg = msgs[-1] if msgs else ""
    return str(msg or "")


def _truncate(text: str, limit: int = 80) -> str:
    s = " ".join((text or "").split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _emit(sink: Any, event_type: str, **fields: Any) -> None:
    """Best-effort structured event to an optional sink (never raises)."""
    if sink is None:
        return
    handler = getattr(sink, "handle_event", None)
    if not callable(handler):
        return
    try:
        handler({"type": event_type, **fields})
    except Exception:  # noqa: BLE001 — observability must never break planning
        pass


def _draft_failed(
    objective: str,
    *,
    reason: str,
    notes: list[str] | None = None,
) -> Plan:
    """Return an explicit drafting failure without inventing plan steps."""
    return Plan(objective=objective, steps=[], notes=list(notes or []), error=reason)


def draft_plan(
    runner: Any,
    objective: str,
    *,
    sink: Any = None,
    model: str | None = None,
    reasoning_effort: str = "low",
    run_label: str = "manager-plan",
    role_banner: str = "",
    working_dir: str | None = None,
    dangerous_yolo: bool = True,
    max_seconds: int | None = None,
    allow_repository_inspection: bool = False,
) -> Plan:
    """Ask the model for an ordered preview plan for ``objective``.

    Uses the same runner the cockpit already holds (its underlying backend is
    resolved automatically). The model is asked to OUTLINE only — never to do
    the work. The reply is parsed by :func:`parse_plan_text` /
    :func:`parse_plan_notes`.

    A missing/raising runner, a non-zero exit, an empty reply, or an
    unparseable plan are surfaced explicitly via ``Plan.error`` so the cockpit
    never crashes but also never silently invents a plan. On success,
    ``Plan.error`` is empty.
    """
    objective = objective or ""
    _emit(sink, "plan.draft.start", objective=_truncate(objective, 120))

    run_exec = _resolve_run_exec(
        runner,
        model=model,
        reasoning_effort=reasoning_effort,
        run_label=run_label,
        working_dir=working_dir,
        dangerous_yolo=dangerous_yolo,
        max_seconds=max_seconds,
    )
    if run_exec is None:
        _emit(sink, "plan.draft.failed", reason="no runner backend")
        return _draft_failed(objective, reason="could not draft plan: no runner backend")

    try:
        from ..core.role_slots import role_call_slot

        with role_call_slot("planner"):
            result = run_exec(
                build_plan_prompt(
                    objective,
                    role_banner=role_banner,
                    allow_repository_inspection=allow_repository_inspection,
                )
            )
    except Exception:  # noqa: BLE001 — keep the cockpit alive but surface failure
        _emit(sink, "plan.draft.failed", reason="backend error")
        return _draft_failed(objective, reason="could not draft plan: backend error")

    if int(getattr(result, "exit_code", 0) or 0) != 0:
        _emit(sink, "plan.draft.failed", reason="non-zero exit")
        return _draft_failed(objective, reason="could not draft plan: planner exited non-zero")

    text = _extract_text(result)
    steps = parse_plan_text(text)
    notes = parse_plan_notes(text)
    if not steps:
        _emit(sink, "plan.draft.failed", reason="unparseable plan")
        return _draft_failed(
            objective,
            reason="could not draft plan: model reply was empty or unparseable",
            notes=notes,
        )

    # Enforce the product contract: a preview plan has a bounded number of steps.
    steps = steps[:_MAX_STEPS]
    _emit(sink, "plan.draft.done", steps=len(steps), notes=len(notes))
    return Plan(objective=objective, steps=steps, notes=notes)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _style(theme: Any, name: str, text: str) -> str:
    """Apply a theme color method if available; plain text otherwise (fail-soft)."""
    if theme is None:
        return text
    fn = getattr(theme, name, None)
    if not callable(fn):
        return text
    try:
        return str(fn(text))
    except Exception:  # noqa: BLE001 — styling is cosmetic, never break rendering
        return text


def render_plan(plan: Plan, theme: Any = None) -> str:
    """Render a :class:`Plan` as pretty, scannable multi-line text.

    Numbered steps with an indented one-line detail, then an optional Notes
    block. ``theme`` is an optional :class:`cli.theme.Theme`; when omitted (or a
    method is missing) the output degrades to plain text. Never raises.
    """
    try:
        steps = list(getattr(plan, "steps", []) or [])
        notes = list(getattr(plan, "notes", []) or [])
        objective = str(getattr(plan, "objective", "") or "").strip()

        lines: list[str] = []
        header = f"Plan · {objective}" if objective else "Plan"
        lines.append(_style(theme, "bold", header))

        if not steps:
            error = str(getattr(plan, "error", "") or "").strip()
            if error:
                lines.append("  " + _style(theme, "red", f"draft failed: {error}"))
            else:
                lines.append("  " + _style(theme, "gray", "(no steps)"))
        for i, step in enumerate(steps, 1):
            title = str(getattr(step, "title", "") or "").strip()
            detail = str(getattr(step, "detail", "") or "").strip()
            num = _style(theme, "cyan", f"{i:>2}.")
            lines.append(f"  {num} {_style(theme, 'bold', title)}")
            if detail:
                lines.append("      " + _style(theme, "gray", detail))

        if notes:
            lines.append("")
            lines.append(_style(theme, "gray", "Notes:"))
            for note in notes:
                lines.append("  " + _style(theme, "dim", f"- {str(note).strip()}"))

        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — rendering must never crash the cockpit
        try:
            return f"Plan · {plan.objective}"
        except Exception:  # noqa: BLE001
            return "Plan"


__all__ = [
    "PlanStep",
    "Plan",
    "parse_plan_text",
    "parse_plan_notes",
    "build_plan_prompt",
    "draft_plan",
    "render_plan",
]
