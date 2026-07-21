"""Compact, task-agnostic reminder to work from reality."""
from __future__ import annotations

GROUND_TRUTH_RELPATH = "research/GROUND_TRUTH.md"

_MODE_LINES = {
    "staged": (
        f"Inspect the real code, data, logs, and measurements. If binding facts are "
        f"still unknown, record only those facts in `{GROUND_TRUTH_RELPATH}` before "
        "acting; never fabricate or treat a summary as a conclusion."
    ),
    "direct": (
        f"Verify only facts material to the requested deliverable. Use "
        f"`{GROUND_TRUTH_RELPATH}` only when a shared factual record helps; do not "
        "create extra research scaffolding."
    ),
    "proportional": (
        "Verify the current claim or delta and reuse reviewed evidence unless a "
        "dependency changed or conflicts. Do not rebuild snapshots, manifests, or "
        "audit packets by default."
    ),
}

_ROLE_LINES = {
    "planner": "Plan from the actual constraint; investigate it first when unknown.",
    "engineer": "Act on observed causes and report only results actually produced.",
    "reviewer": "Check a material doubt independently; otherwise judge the result.",
}


def ground_truth_mandate(role: str = "", *, workflow_mode: str = "staged") -> str:
    """Return one compact reality reminder plus an optional role sentence."""
    mode = (workflow_mode or "").strip().lower()
    block = _MODE_LINES.get(mode, _MODE_LINES["staged"])
    role_line = _ROLE_LINES.get((role or "").strip().lower(), "")
    if role_line:
        block += " " + role_line
    return "## Reality check\n" + block + "\n\n"


__all__ = ["GROUND_TRUTH_RELPATH", "ground_truth_mandate"]
