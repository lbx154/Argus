"""Process-written method-freeze and confirmation-run facts."""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from ...core.manuscript_snapshot import manuscript_sha256

FREEZE_PATH = Path("research/METHOD_FREEZE.json")
CONFIRMATION_RESULT_PATH = Path("research/confirmation_result.json")
SCHEMA_VERSION = 1


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def declare_method_freeze(
    project_root: Path | str,
    *,
    method_identity: str,
    method_description: str,
    confirmation_command: str,
    data_split_identity: str,
    frozen_at: str | None = None,
) -> dict[str, Any]:
    """Write the declared method and its one planned confirmation run."""
    fields = {
        "method_identity": method_identity,
        "method_description": method_description,
        "confirmation_command": confirmation_command,
        "data_split_identity": data_split_identity,
    }
    normalized = {key: str(value or "").strip() for key, value in fields.items()}
    missing = [key for key, value in normalized.items() if not value]
    if missing:
        raise ValueError("method freeze requires " + ", ".join(missing))
    root = Path(project_root).resolve()
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "method": {
            "identity": normalized["method_identity"],
            "description": normalized["method_description"],
        },
        "frozen_at": frozen_at or datetime.now(UTC).isoformat(),
        "manuscript_sha256_at_freeze": manuscript_sha256(root),
        "planned_confirmation_run": {
            "command": normalized["confirmation_command"],
            "data_split_identity": normalized["data_split_identity"],
        },
    }
    _write_json(root / FREEZE_PATH, payload)
    return payload


def load_method_freeze(project_root: Path | str) -> dict[str, Any] | None:
    try:
        payload = json.loads((Path(project_root) / FREEZE_PATH).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def record_confirmation_result(
    project_root: Path | str,
    *,
    result: Mapping[str, Any],
    completed_at: str | None = None,
) -> dict[str, Any]:
    """Write the result emitted by the declared confirmation-run process."""
    root = Path(project_root).resolve()
    freeze = load_method_freeze(root)
    if freeze is None:
        raise ValueError("cannot record confirmation result before METHOD_FREEZE.json")
    planned = freeze.get("planned_confirmation_run")
    if not isinstance(planned, dict):
        raise ValueError("METHOD_FREEZE.json has no planned_confirmation_run")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "method_identity": str((freeze.get("method") or {}).get("identity") or ""),
        "freeze_frozen_at": str(freeze.get("frozen_at") or ""),
        "confirmation_run": {
            "command": str(planned.get("command") or ""),
            "data_split_identity": str(planned.get("data_split_identity") or ""),
            "completed_at": completed_at or datetime.now(UTC).isoformat(),
        },
        "result": dict(result),
    }
    _write_json(root / CONFIRMATION_RESULT_PATH, payload)
    return payload


def research_review_prompt_block(project_root: Path | str) -> str:
    """Render freeze facts for ordinary Reviewer judgment; absence is silent."""
    root = Path(project_root)
    freeze = load_method_freeze(root)
    if freeze is None:
        return ""
    method = freeze.get("method") if isinstance(freeze.get("method"), dict) else {}
    planned = (
        freeze.get("planned_confirmation_run")
        if isinstance(freeze.get("planned_confirmation_run"), dict)
        else {}
    )
    lines = [
        "## Declared method freeze (process-written facts)",
        f"- method identity: {method.get('identity') or '<missing>'}",
        f"- method description: {method.get('description') or '<missing>'}",
        f"- frozen at: {freeze.get('frozen_at') or '<missing>'}",
        "- manuscript SHA-256 at freeze: "
        + str(freeze.get("manuscript_sha256_at_freeze") or "<missing>"),
        f"- planned confirmation command: {planned.get('command') or '<missing>'}",
        "- planned confirmation data split: "
        + str(planned.get("data_split_identity") or "<missing>"),
    ]
    try:
        confirmation = json.loads(
            (root / CONFIRMATION_RESULT_PATH).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError):
        confirmation = None
    if isinstance(confirmation, dict):
        lines.append("- confirmation result: " + json.dumps(
            confirmation, ensure_ascii=False, sort_keys=True
        ))
    else:
        lines.append("- confirmation result: not recorded")
    lines.extend([
        "Headline numbers may change only from this declared confirmation run. ",
        "Further exploration variants belong to the next paper, not this manuscript.",
        "As part of ordinary review judgment, compare every headline number with "
        "research/confirmation_result.json and report any inconsistency.",
    ])
    return "\n".join(lines) + "\n\n"


__all__ = [
    "CONFIRMATION_RESULT_PATH",
    "FREEZE_PATH",
    "declare_method_freeze",
    "load_method_freeze",
    "record_confirmation_result",
    "research_review_prompt_block",
]
