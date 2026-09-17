"""The ordinary Markdown checkpoint shared by fresh Engineer/Reviewer turns."""

from __future__ import annotations

from pathlib import Path


def resolve_shared_checkpoint(path: Path | None) -> Path | None:
    """Resolve the optional handoff path without creating it."""
    if path is None:
        return None
    return Path(path).expanduser().resolve()


def shared_checkpoint_instructions(
    path: Path | None,
    *,
    role: str,
    continuation: bool = True,
) -> str:
    """Tell one role how to take its turn editing the shared note."""
    if path is None:
        return ""
    checkpoint = str(Path(path).expanduser().resolve())
    if role == "reviewer" and not continuation:
        return (
            "## Shared checkpoint\n"
            f"If your verdict is not `done`, update `{checkpoint}` with only the "
            "remaining state, blocker, and next action before returning. For a "
            "`done` verdict, do not read or edit the checkpoint merely for ceremony."
        )
    packet = str(Path(path).expanduser().resolve().parent / "latest.json")
    if role == "reviewer":
        action = (
            "Reviewer is the final editor: correct it only when another round needs "
            "remaining state or a next action."
        )
    else:
        action = (
            "Create or rewrite it only when another round needs current state, evidence "
            "paths, a blocker, or one next action."
        )
    return (
        "## Shared checkpoint\n"
        f"Role-state index: `{packet}`\n"
        f"Continuation note: `{checkpoint}`\n"
        "The current prompt already contains the mission contract. Read the index only "
        "to resolve a contradiction or continue prior work; never create worktree "
        "copies of these state files. "
        f"{action} Keep it as current state, not a log or JSON verdict."
    )


__all__ = [
    "resolve_shared_checkpoint",
    "shared_checkpoint_instructions",
]
