"""What the Planner last held, kept on disk so a restart does not forget it.

``handoffs/lineage-planning/holders.json`` maps a role to the facts it last
held: plain values compared with ``==``. A missing or unreadable file holds
nothing.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..context_packet import _atomic_write_json


def lineage_ledger(handoffs_root: Path | str, lineage: str) -> Path:
    return Path(handoffs_root) / f"lineage-{lineage}" / "holders.json"


def _read(ledger: Path) -> dict[str, Any]:
    try:
        value = json.loads(ledger.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def held_facts(ledger: Path, role: str) -> Any:
    """The facts ``role`` last held, or ``None``."""
    entry = _read(ledger).get(role)
    return entry.get("facts") if isinstance(entry, dict) else None


def record_holder(ledger: Path, role: str, facts: Any) -> None:
    """Record that ``role`` now holds ``facts``."""
    _atomic_write_json(ledger, {**_read(ledger), role: {"facts": facts, "at": time.time()}})
