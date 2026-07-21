"""Persisted manuscript-stage repair context.

When a vertical's terminal deterministic verifier fails (see the Manager's hard
gate in ``argus_skill/manager/_core.py`` and ``run_stage_shell_checks`` in
``argus_skill/tools/stage_check.py``), the Manager records the EXACT failure list
here so that:

* the next manuscript-stage agent round receives the failures verbatim (the
  physics ``role_banner`` embeds :func:`render_repair_block` when this file
  exists), and
* a stall — no drop in the failure count across consecutive rounds — can be
  detected and reported BLOCKED instead of spinning to the mission timeout.

The state lives in a SEPARATE file (``research/MANUSCRIPT_REPAIR.json``), never in
the Manager-owned ``PIPELINE_STATE.json``, so stage authority is untouched. This
module is discipline-agnostic; it only ever stores an opaque failure-string list.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Repair-context file, relative to the project root.
REPAIR_REL = "research/MANUSCRIPT_REPAIR.json"

#: Consecutive rounds without a drop in the failure count before we call it stalled.
STALL_THRESHOLD = 2


def _path(project_root: object) -> Path:
    return Path(str(project_root or ".")) / REPAIR_REL


def read_repair_state(project_root: object) -> dict | None:
    """Return the persisted repair state, or ``None`` if absent/unreadable."""
    try:
        data = json.loads(_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def update_repair_state(
    project_root: object,
    failures: list[str],
    *,
    now_iso: str | None = None,
) -> dict:
    """Record a failing terminal-gate round and return the new state.

    Tracks ``round``, ``prev_failure_count``, ``failure_count`` and a
    ``no_drop_streak``; sets ``stalled`` once the count has failed to drop for
    :data:`STALL_THRESHOLD` consecutive rounds.
    """
    prev = read_repair_state(project_root)
    count = len(failures)
    if prev is None:
        rnd, prev_count, no_drop = 1, None, 0
    else:
        rnd = int(prev.get("round", 0) or 0) + 1
        prev_count = prev.get("failure_count")
        no_drop = int(prev.get("no_drop_streak", 0) or 0)
        if isinstance(prev_count, int) and count >= prev_count:
            no_drop += 1
        else:
            no_drop = 0
    stalled = no_drop >= STALL_THRESHOLD
    state: dict = {
        "kind": "manuscript",
        "round": rnd,
        "prev_failure_count": prev_count,
        "failure_count": count,
        "no_drop_streak": no_drop,
        "stalled": stalled,
        "status": "manuscript_repair_stalled" if stalled else "manuscript_repair_required",
        "failures": list(failures),
    }
    if now_iso:
        state["updated_utc"] = now_iso
    p = _path(project_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)
    return state


def clear_repair_state(project_root: object) -> None:
    """Remove the repair context (called once the verifier passes)."""
    try:
        _path(project_root).unlink()
    except OSError:
        pass


def render_repair_block(state: dict | None) -> str:
    """Prompt text embedding the exact failure list + forced repair instructions."""
    if not state or not state.get("failures"):
        return ""
    failures = list(state.get("failures", []))
    listing = "\n".join(f"  {i}. {f}" for i, f in enumerate(failures, 1))
    block = (
        "## MANUSCRIPT REPAIR REQUIRED (deterministic verifier failed)\n"
        f"The last manuscript attempt failed `manuscript check --layer all` with "
        f"{state.get('failure_count', len(failures))} deterministic failure(s) "
        f"(repair round {state.get('round', 1)}). You MUST eliminate EVERY item below "
        "— one concrete edit per failure — then re-run "
        "`python -m argus_skill.verticals.physics.manuscript check --layer all` and "
        "confirm it prints 'satisfied'. Do NOT merely rewrite the abstract or pad the "
        "text with filler in place of clearing these, and do NOT claim the manuscript "
        "stage done until the checker passes clean. Exact failures to eliminate:\n"
        + listing
    )
    if state.get("stalled"):
        block += (
            "\n\nSTALL DETECTED: the deterministic failure count has not dropped for "
            f"{state.get('no_drop_streak')} consecutive rounds. If you cannot clear these "
            "failures deterministically, STOP and report BLOCKED with the specific "
            "obstacle — do not keep re-submitting a manuscript that fails the same checks."
        )
    return block


__all__ = [
    "REPAIR_REL",
    "STALL_THRESHOLD",
    "read_repair_state",
    "update_repair_state",
    "clear_repair_state",
    "render_repair_block",
]
