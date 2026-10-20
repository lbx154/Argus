"""What a holder of a mission would see, and which holders already saw it.

A role called again on the facts it last held can only repeat itself
(principle 4). The facts are the workspace (minus the files running work is
still writing), the registered background work, and the operator's context
revision: plain values compared with ``==`` and stored as readable JSON.

The ledger records, per lineage (a mission's ``node_key``, else its id), the
facts each role last held, so resuming or replanning the same work does not
forget them.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from ..core.operator_context import OperatorContextStore
from ..core.portable_filename import portable_filename_component
from ..core.workspace_facts import workspace_facts
from .external_work import ExternalWorkStatus, external_work_rows, scan_external_work

LEDGER_FILENAME = "holders.json"


def change_facts(
    workdir: Path | str,
    *,
    state_root: Path | str | None = None,
    statuses: Iterable[ExternalWorkStatus] | None = None,
) -> dict[str, Any]:
    """The facts a mission's next holder would read, from one registry scan."""
    work = list(scan_external_work(workdir) if statuses is None else statuses)
    try:
        operator = OperatorContextStore(state_root).revision if state_root else 0
    except Exception:  # noqa: BLE001 - unknown, see ``holds_unchanged``
        operator = -1
    return {
        "workspace": workspace_facts(
            workdir,
            ignore=[path for status in work if status.waitable for path in status.activity_paths],
        ),
        "external": external_work_rows(work),
        "operator_revision": operator,
    }


def lineage_ledger(handoffs_root: Path | str, lineage: str) -> Path:
    return (
        Path(handoffs_root) / f"lineage-{portable_filename_component(lineage)}" / LEDGER_FILENAME
    )


def mission_lineage(context_packet_path: str | os.PathLike[str] | None) -> str:
    """The lineage of the mission whose context packet is ``context_packet_path``, or ""."""
    if not context_packet_path:
        return ""
    try:
        mission = json.loads(Path(context_packet_path).expanduser().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(mission, dict):
        return ""
    return str(mission.get("node_key") or "").strip() or str(mission.get("mission_id") or "").strip()


def mission_ledger(context_packet_path: str | os.PathLike[str] | None) -> Path | None:
    """The ledger of the mission whose context packet is ``context_packet_path``."""
    lineage = mission_lineage(context_packet_path)
    if not lineage:
        return None
    return lineage_ledger(Path(context_packet_path).expanduser().parent.parent, lineage)


def _read(ledger: Path) -> dict[str, Any]:
    try:
        value = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def held_facts(ledger: Path | None, role: str) -> Any:
    """The facts ``role`` last held on this lineage, or ``None``."""
    entry = _read(ledger).get(role) if ledger is not None else None
    return entry.get("facts") if isinstance(entry, dict) else None


def holds_unchanged(ledger: Path | None, role: str, facts: dict[str, Any]) -> bool:
    """Whether ``role`` already held exactly ``facts``.

    Facts that could not be read are unknown, so they never match: one more
    call costs less than a holder skipped on news nobody could see.
    """
    known = facts["operator_revision"] != -1 and "unavailable" not in facts["workspace"]
    return known and facts == held_facts(ledger, role)


def record_holder(ledger: Path | None, role: str, facts: Any, **context: Any) -> None:
    """Record that ``role`` has now held the work on ``facts``."""
    if ledger is None:
        return
    entries = _read(ledger)
    entries[role] = {"facts": facts, "at": time.time(), **context}
    ledger.parent.mkdir(parents=True, exist_ok=True)
    tmp = ledger.with_name(f"{ledger.name}.tmp-{os.getpid()}-{time.time_ns()}")
    try:
        tmp.write_text(json.dumps(entries, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, ledger)
    finally:
        tmp.unlink(missing_ok=True)


__all__ = [
    "LEDGER_FILENAME",
    "change_facts",
    "held_facts",
    "holds_unchanged",
    "lineage_ledger",
    "mission_ledger",
    "mission_lineage",
    "record_holder",
]
