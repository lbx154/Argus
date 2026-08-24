"""Immutable, content-addressed evidence packets for independent Viewer runs."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

_PREVIEW_KINDS = frozenset({"text", "markdown", "json", "table"})
_SAFE_REMOTE_PATH = re.compile(r"^[^\\\x00-\x1f]{1,4096}$")


class ArtifactPreviewClient(Protocol):
    def artifact(self, sid: str, path: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class EvidenceSnapshot:
    state: str
    path: Path
    sha256: str
    artifact_count: int
    document: Mapping[str, Any]

    def viewer_request_fields(self) -> dict[str, Any]:
        """Return a self-contained, verified payload suitable for the queue."""
        return {
            "evidence_snapshot_state": self.state,
            "evidence_snapshot_sha256": self.sha256,
            "evidence_snapshot_path": str(self.path),
            # Inline evidence prevents an evaluator adapter from having to
            # trust and open a user-supplied filesystem path.
            "evidence_snapshot": dict(self.document),
            "evidence_refs": [
                f"argus://{row['project']}/{row['path']}"
                for row in self.document.get("artifacts", [])
                if isinstance(row, Mapping)
            ],
        }


def validate_evidence_snapshot(
    document: Mapping[str, Any], expected_sha256: str
) -> tuple[str, tuple[str, ...]]:
    """Authenticate an inline snapshot before an evaluator can rely on it."""
    encoded = _canonical_json(document)
    actual = hashlib.sha256(encoded).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or actual != expected_sha256:
        raise ValueError("evidence snapshot hash mismatch")
    state = document.get("state")
    if state not in {"evidence", "empty"}:
        raise ValueError("evidence snapshot state is invalid")
    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("evidence snapshot artifacts are invalid")
    refs: list[str] = []
    for row in artifacts:
        if not isinstance(row, Mapping):
            raise ValueError("evidence snapshot artifact is invalid")
        project, path, preview = row.get("project"), row.get("path"), row.get("preview")
        if not isinstance(project, str) or not project or not isinstance(path, str) or not _safe_remote_path(path):
            raise ValueError("evidence snapshot source is invalid")
        if not isinstance(preview, str):
            raise ValueError("evidence snapshot preview is invalid")
        preview_hash = hashlib.sha256(preview.encode("utf-8")).hexdigest()
        if row.get("sha256") != preview_hash:
            raise ValueError("evidence artifact hash mismatch")
        refs.append(f"argus://{project}/{path}")
    if state == "empty" and artifacts:
        raise ValueError("empty evidence snapshot contains artifacts")
    if state == "evidence" and not artifacts:
        raise ValueError("evidence snapshot contains no artifacts")
    return str(state), tuple(refs)


def build_evidence_snapshot(
    campaign: Mapping[str, Any],
    client: ArtifactPreviewClient,
    artifact_index: Sequence[Mapping[str, Any]],
    *,
    output_root: Path,
    max_artifacts: int = 24,
    max_preview_bytes: int = 64 * 1024,
    max_total_preview_bytes: int = 512 * 1024,
) -> EvidenceSnapshot:
    """Fetch bounded previews only through Argus' allowlisted artifact API.

    No path in ``campaign`` or ``artifact_index`` is ever opened locally.  The
    only local write is a canonical JSON document beneath ``output_root``.
    """
    if not 1 <= max_artifacts <= 64:
        raise ValueError("max_artifacts must be between 1 and 64")
    if not 1 <= max_preview_bytes <= 128 * 1024:
        raise ValueError("max_preview_bytes must be between 1 and 131072")
    if not max_preview_bytes <= max_total_preview_bytes <= 2 * 1024 * 1024:
        raise ValueError("max_total_preview_bytes is outside the safe bound")
    project_id = str(campaign.get("argus_project_id") or "")
    if not project_id:
        raise ValueError("campaign has no Argus project id")

    candidates: list[str] = []
    seen: set[str] = set()
    for row in artifact_index:
        path = str(row.get("path") or "")
        kind = str(row.get("kind") or "")
        if (
            kind not in _PREVIEW_KINDS
            or row.get("exists") is not True
            or not _safe_remote_path(path)
            or path in seen
        ):
            continue
        seen.add(path)
        candidates.append(path)
        if len(candidates) >= max_artifacts:
            break

    artifacts: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    remaining = max_total_preview_bytes
    for path in candidates:
        if remaining <= 0:
            skipped.append({"project": project_id, "path": path, "reason": "total_preview_limit"})
            continue
        try:
            detail = client.artifact(project_id, path)
        except Exception:  # remote transport detail is intentionally not persisted
            skipped.append({"project": project_id, "path": path, "reason": "unavailable"})
            continue
        if (
            str(detail.get("path") or "") != path
            or detail.get("exists") is not True
            or str(detail.get("kind") or "") not in _PREVIEW_KINDS
        ):
            skipped.append({"project": project_id, "path": path, "reason": "contract_mismatch"})
            continue
        preview = detail.get("preview")
        if not isinstance(preview, str):
            skipped.append({"project": project_id, "path": path, "reason": "preview_missing"})
            continue
        bounded, locally_truncated, used = _bounded_utf8(
            preview, min(max_preview_bytes, remaining)
        )
        remaining -= used
        size = detail.get("size")
        normalized_size = (
            size if isinstance(size, int) and not isinstance(size, bool) and size >= 0 else None
        )
        artifacts.append({
            "project": project_id,
            "path": path,
            "size": normalized_size,
            "truncated": bool(detail.get("truncated")) or locally_truncated,
            "preview": bounded,
            "sha256": hashlib.sha256(bounded.encode("utf-8")).hexdigest(),
        })

    state = "evidence" if artifacts else "empty"
    document: dict[str, Any] = {
        "protocol_version": 1,
        "state": state,
        "score": None,
        "detail": (
            "Bounded, allowlisted Argus evidence is available; no score is implied."
            if artifacts
            else "No eligible allowlisted text evidence was available; no score was produced."
        ),
        "campaign_id": str(campaign.get("id") or ""),
        "source_project": project_id,
        "limits": {
            "max_artifacts": max_artifacts,
            "max_preview_bytes": max_preview_bytes,
            "max_total_preview_bytes": max_total_preview_bytes,
        },
        "artifacts": artifacts,
        "skipped": skipped,
    }
    encoded = _canonical_json(document)
    digest = hashlib.sha256(encoded).hexdigest()
    target = output_root.resolve() / digest / "EVIDENCE_SNAPSHOT.json"
    _write_immutable(target, encoded)
    return EvidenceSnapshot(
        state=state,
        path=target,
        sha256=digest,
        artifact_count=len(artifacts),
        document=document,
    )


def _safe_remote_path(path: str) -> bool:
    if not _SAFE_REMOTE_PATH.fullmatch(path) or path.startswith("/"):
        return False
    return all(segment not in {"", ".", ".."} for segment in path.split("/"))


def _bounded_utf8(value: str, limit: int) -> tuple[str, bool, int]:
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value, False, len(encoded)
    bounded = encoded[:limit].decode("utf-8", errors="ignore")
    return bounded, True, len(bounded.encode("utf-8"))


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_immutable(target: Path, content: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != content:
            raise RuntimeError("content-addressed evidence snapshot collision")
    else:
        # Keep the temporary basename short: the content-addressed parent path
        # can already be deep enough to hit Windows' legacy MAX_PATH handling.
        temporary = target.parent / f".tmp-{uuid.uuid4().hex[:8]}"
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
