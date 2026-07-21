"""The ordinary Markdown checkpoint shared by fresh Engineer/Reviewer turns."""

from __future__ import annotations

from pathlib import Path

SHARED_CHECKPOINT_TEMPLATE = """# Goal

# Current State

# Verified Done

# Failed / Dead Ends

# Open Questions / Blockers

# Relevant Files and Evidence
"""


def ensure_shared_checkpoint(path: Path | None) -> Path | None:
    """Create the shared note once; agents own every later edit."""
    if path is None:
        return None
    checkpoint = Path(path).expanduser().resolve()
    try:
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        if not checkpoint.exists():
            checkpoint.write_text(SHARED_CHECKPOINT_TEMPLATE, encoding="utf-8")
    except OSError:
        return None
    return checkpoint


def shared_checkpoint_instructions(path: Path | None, *, role: str) -> str:
    """Tell one role how to take its turn editing the shared note."""
    if path is None:
        return ""
    checkpoint = str(Path(path).expanduser().resolve())
    packet = str(Path(path).expanduser().resolve().parent / "latest.json")
    if role == "reviewer":
        action = (
            "The Engineer already edited it this round. Verify against artifacts, "
            "then directly correct it before your verdict; you are the final editor."
        )
    else:
        action = (
            "Read it first; the previous Reviewer edited it last. Before ending, "
            "directly update current state, evidence paths, blockers, and open questions."
        )
    return (
        "## Shared checkpoint — edit the file directly\n"
        f"Canonical context packet: `{packet}`\n"
        f"Human-editable projection: `{checkpoint}`\n\n"
        "Read the index first, open `handoff.path` for the latest sealed role "
        "decision, then open `mission.path` for the immutable objective/acceptance "
        "contract. Open only referenced artifacts unless new evidence requires "
        "expanding the search.\n\n"
        f"{action}\n\n"
        "It is current state, not a log: rewrite stale text. Actually edit the file; "
        "do not duplicate the verdict rationale or next-round instruction here; do "
        "not emit checkpoint JSON or merely describe an edit."
    )


__all__ = [
    "SHARED_CHECKPOINT_TEMPLATE",
    "ensure_shared_checkpoint",
    "shared_checkpoint_instructions",
]
