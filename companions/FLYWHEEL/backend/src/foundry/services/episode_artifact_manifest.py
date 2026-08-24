from __future__ import annotations

import json
from typing import Any, Iterable

from ..db import Database, decode_rows


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def confirmed_argus_artifact_manifest(
    rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in rows:
        if row.get("state") != "confirmed":
            continue
        source_entry = row.get("source_entry")
        if source_entry is None:
            try:
                source_entry = json.loads(str(row.get("source_entry_json") or "{}"))
            except json.JSONDecodeError:
                source_entry = {}
        records.append(
            {
                "id": row["id"],
                "campaign_id": row["campaign_id"],
                "connection_id": row["connection_id"],
                "argus_project_id": row["argus_project_id"],
                "artifact_path": row["artifact_path"],
                "role": row["role"],
                "source_entry": source_entry,
                "source_entry_sha256": row["source_entry_sha256"],
                "source_sha256": row["source_sha256"],
                "source_byte_length": int(row["source_byte_length"]),
                "media_type": row["media_type"],
                "content_object_sha256": row["content_object_sha256"],
                "redaction_mode": row["redaction_mode"],
                "redaction_confirmed": bool(row["redaction_confirmed"]),
                "manual_redaction_required": bool(row["manual_redaction_required"]),
                "manual_redaction_confirmed": bool(
                    row.get("manual_redaction_confirmed")
                ),
                "training_consent": bool(row["training_consent"]),
                "license_basis": row["license_basis"],
                "confirmed_by": row["confirmed_by"],
                "confirmed_at": row["confirmed_at"],
            }
        )
    return sorted(records, key=lambda item: (item["role"], item["artifact_path"], item["id"]))


def current_confirmed_argus_artifacts(
    db: Database, episode_id: str
) -> list[dict[str, Any]]:
    return confirmed_argus_artifact_manifest(
        decode_rows(
            db.fetch_all(
                "SELECT * FROM argus_artifact_imports WHERE episode_id=? "
                "ORDER BY role,artifact_path,id",
                (episode_id,),
            )
        )
    )


def head_argus_artifacts_are_current(
    db: Database,
    episode_id: str,
    head_revision: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not head_revision:
        return False, "no sealed head revision"
    try:
        manifest = json.loads(str(head_revision["manifest_json"]))
        sealed = manifest.get("argus_artifact_imports", [])
    except (KeyError, TypeError, json.JSONDecodeError):
        return False, "head manifest is not valid JSON"
    if not isinstance(sealed, list):
        return False, "head manifest argus_artifact_imports is not an array"
    current = current_confirmed_argus_artifacts(db, episode_id)
    if _canonical_json(sealed) != _canonical_json(current):
        return False, "current confirmed Argus artifacts are not exactly sealed in the head manifest"
    return True, "current confirmed Argus artifacts exactly match the head manifest"


def head_artifact_membership(
    head_revision: dict[str, Any] | None,
) -> dict[str, str]:
    if not head_revision:
        return {}
    try:
        manifest = json.loads(str(head_revision["manifest_json"]))
        rows = manifest.get("argus_artifact_imports", [])
    except (KeyError, TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(rows, list):
        return {}
    output: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        import_id = row.get("id")
        object_sha = row.get("content_object_sha256")
        if isinstance(import_id, str) and isinstance(object_sha, str):
            output[import_id] = object_sha
    return output
