"""Export normalized ledger inputs; the Node reader owns their cost projection.

Storage validation, call-ID deduplication and token-price reconciliation remain
with UsageLedger; its shared call contributions also own model-receipt
deduplication. Never fold lifecycle events or read raw JSONL in TypeScript.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any


def cost_input_frames(
    root: Path, *, limit: int, max_records: int, validate_id: Callable[[Any], str],
) -> Iterator[dict[str, Any]]:
    from ..core.session import list_sessions
    from ..core.usage import UsageLedger, usage_call_contributions
    from .project_state import project_life_dir

    records_sent = 0
    projects_sent = 0
    for meta in list_sessions(root, include_empty=False):
        sid = validate_id(meta.id)
        life_dir = project_life_dir(sid, global_root=root)
        if life_dir is None:
            raise ValueError("project disappeared or is outside the configured root")
        yield {"kind": "cost_project", "project_id": sid}
        # The existing records() method may reconcile incomplete token pricing.
        # Read one complete, validated project before emitting any of its records.
        for record in usage_call_contributions(UsageLedger(life_dir, migrate_legacy=False).records()):
            if records_sent >= max_records:
                raise CostInputLimitError("cost record limit exceeded")
            records_sent += 1
            yield {"kind": "cost_record", "record": record}
        try:
            updated_at = (life_dir / "usage.jsonl").stat().st_mtime
        except OSError:
            updated_at = 0.0
        yield {"kind": "cost_project_end", "project_id": sid, "updated_at": updated_at}
        projects_sent += 1
        if projects_sent >= limit:
            break


class CostInputLimitError(ValueError):
    pass
