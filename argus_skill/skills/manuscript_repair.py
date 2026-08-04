"""Read and render legacy manuscript-stage repair context.

Current missions no longer write ``MANUSCRIPT_REPAIR.json``. The read path is
retained so projects created by older releases still surface their exact pending
failures to the physics role banner.
"""
from __future__ import annotations

import json
from pathlib import Path

#: Repair-context file, relative to the project root.
REPAIR_REL = "research/MANUSCRIPT_REPAIR.json"

def _path(project_root: object) -> Path:
    return Path(str(project_root or ".")) / REPAIR_REL


def read_repair_state(project_root: object) -> dict | None:
    """Return the persisted repair state, or ``None`` if absent/unreadable."""
    try:
        data = json.loads(_path(project_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


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
    "read_repair_state",
    "render_repair_block",
]
