"""Software-engineering vertical; Manager independently chooses execution mode."""

from __future__ import annotations

STAGE_ORDER = ["delivery"]
CHECKLIST_STAGE_ORDER = tuple(STAGE_ORDER)
CHECKLIST_OPTIONAL_STAGES = ("delivery",)
CHECKLIST_ITEMS: dict[str, tuple[object, ...]] = {"delivery": ()}
completion_gate = "none"
# Safe fallback for old callers that have not yet read the Manager-persisted
# workflow_mode. Runtime orchestration uses resolve_workflow_mode(project_root).
WORKFLOW_MODE = "staged"


def role_banner(role: str) -> str:
    return (
        "SOFTWARE VERTICAL: implement the requested repository change using the "
        "matched skills and project wiki, preserve unrelated work, and verify the "
        "result with task-native tests. Execution topology is Manager-owned and "
        "is not part of this capability classification."
    )
