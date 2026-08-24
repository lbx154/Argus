from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import Database, decode_row, utc_now
from ..integrations.argus_webapi import ArgusWebApiClient
from .episode_artifact_manifest import head_artifact_membership
from .flywheel_data import (
    ContentObjectStore,
    FlywheelDataError,
    assert_no_secrets,
    canonical_json,
    sha256_text,
)

MAX_INDEX_ITEMS = 2_048
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
MAX_DRAFT_BYTES_PER_EPISODE = 128 * 1024 * 1024
MAX_DRAFTS_PER_EPISODE = 20
MAX_PREVIEW_BYTES = 64 * 1024
MAX_INDEX_FIELD_CHARS = 4_096
_TEXT_KINDS = frozenset({"text", "markdown", "json", "table", "csv"})
_SAFE_ENTRY_FIELDS = (
    "path",
    "kind",
    "exists",
    "size",
    "sha256",
    "content_type",
    "media_type",
    "name",
    "modified_at",
)
_TEXT_ENTRY_FIELDS = frozenset(
    {"path", "kind", "content_type", "media_type", "name", "modified_at"}
)


@dataclass(frozen=True, slots=True)
class EpisodeArgusBinding:
    episode: dict[str, Any]
    campaign: dict[str, Any]
    connection: dict[str, Any]


def normalize_artifact_entry(raw: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key in _SAFE_ENTRY_FIELDS:
        value = raw.get(key)
        if key == "exists":
            output[key] = value is True
        elif key == "size":
            output[key] = (
                value
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                else None
            )
        elif key == "sha256":
            normalized = value.lower() if isinstance(value, str) else ""
            output[key] = (
                normalized
                if len(normalized) == 64
                and all(character in "0123456789abcdef" for character in normalized)
                else None
            )
        elif key in _TEXT_ENTRY_FIELDS:
            output[key] = value if isinstance(value, str) else None
        else:
            output[key] = None
    output["path"] = str(output.get("path") or "")
    output["kind"] = str(output.get("kind") or "artifact")
    return output


def artifact_entry_sha256(entry: dict[str, Any]) -> str:
    return sha256_text(canonical_json(entry))


class ArgusArtifactImportService:
    def __init__(self, db: Database, *, staging_root: Path, object_root: Path) -> None:
        self.db = db
        self.staging_root = staging_root.resolve()
        self.objects = ContentObjectStore(db, object_root)

    def resolve_binding(self, episode_id: str) -> EpisodeArgusBinding:
        episode = decode_row(
            self.db.fetch_one("SELECT * FROM research_episodes WHERE id=?", (episode_id,))
        )
        if not episode:
            raise FlywheelDataError(f"episode not found: {episode_id}")
        campaign_id = episode.get("campaign_id")
        if not campaign_id:
            raise FlywheelDataError("Episode is not bound to an Argus Campaign")
        campaign = decode_row(
            self.db.fetch_one("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        )
        if not campaign:
            raise FlywheelDataError(f"campaign not found: {campaign_id}")
        connection_id = campaign.get("connection_id")
        project_id = campaign.get("argus_project_id")
        if not connection_id or not project_id:
            raise FlywheelDataError(
                "Episode Campaign has no attached Argus connection/project"
            )
        connection = decode_row(
            self.db.fetch_one("SELECT * FROM connections WHERE id=?", (connection_id,))
        )
        if not connection:
            raise FlywheelDataError(f"connection not found: {connection_id}")
        return EpisodeArgusBinding(episode, campaign, connection)

    def remote_index(
        self, binding: EpisodeArgusBinding, client: ArgusWebApiClient
    ) -> list[dict[str, Any]]:
        rows = client.artifacts(str(binding.campaign["argus_project_id"]))
        if len(rows) > MAX_INDEX_ITEMS:
            raise FlywheelDataError(
                f"Argus artifact index exceeds the {MAX_INDEX_ITEMS}-item limit"
            )
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for ordinal, raw in enumerate(rows, start=1):
            entry = normalize_artifact_entry(raw)
            path = entry["path"]
            try:
                ArgusWebApiClient._validate_artifact_path(path)
            except ValueError:
                continue
            oversized_fields = [
                key
                for key, value in entry.items()
                if isinstance(value, str) and len(value) > MAX_INDEX_FIELD_CHARS
            ]
            if oversized_fields:
                raise FlywheelDataError(
                    "Argus artifact index entry "
                    f"{ordinal} contains oversized metadata fields: "
                    f"{', '.join(sorted(oversized_fields))}"
                )
            try:
                assert_no_secrets(entry, path="argus_artifact_index")
            except FlywheelDataError as exc:
                raise FlywheelDataError(
                    f"Argus artifact index entry {ordinal} contains unsafe metadata: {exc}"
                ) from exc
            if path in seen:
                raise FlywheelDataError(
                    f"Argus artifact index entry {ordinal} duplicates an earlier path"
                )
            seen.add(path)
            entry["entry_sha256"] = artifact_entry_sha256(entry)
            normalized.append(entry)
        return sorted(normalized, key=lambda item: item["path"])

    def stage(
        self,
        episode_id: str,
        *,
        artifact_path: str,
        role: str,
        expected_entry_sha256: str,
        idempotency_key: str,
        client: ArgusWebApiClient,
    ) -> tuple[dict[str, Any], bool]:
        binding = self.resolve_binding(episode_id)
        existing = self.db.fetch_one(
            "SELECT * FROM argus_artifact_imports WHERE episode_id=? AND idempotency_key=?",
            (episode_id, idempotency_key),
        )
        if existing:
            if not (
                existing["artifact_path"] == artifact_path
                and existing["role"] == role
                and existing["source_entry_sha256"] == expected_entry_sha256
            ):
                raise FlywheelDataError(
                    "idempotency_key is already bound to a different artifact request"
                )
            return self.public_import(existing), True

        index = self.remote_index(binding, client)
        matches = [entry for entry in index if entry["path"] == artifact_path]
        if not matches or matches[0].get("exists") is not True:
            raise FlywheelDataError(
                "artifact_path is not a current allowlisted Argus artifact"
            )
        entry = matches[0]
        if entry["entry_sha256"] != expected_entry_sha256:
            raise FlywheelDataError(
                "Argus artifact index entry changed; refresh before staging"
            )
        declared_size = entry.get("size")
        if isinstance(declared_size, int) and declared_size > MAX_ARTIFACT_BYTES:
            raise FlywheelDataError(
                f"artifact exceeds the {MAX_ARTIFACT_BYTES}-byte staging limit"
            )
        draft_usage = self.db.fetch_one(
            "SELECT COUNT(*) AS count,COALESCE(SUM(source_byte_length),0) AS bytes "
            "FROM argus_artifact_imports WHERE episode_id=? AND state='draft'",
            (episode_id,),
        ) or {"count": 0, "bytes": 0}
        if int(draft_usage["count"]) >= MAX_DRAFTS_PER_EPISODE:
            raise FlywheelDataError(
                "Episode staged artifact count limit would be exceeded "
                f"({MAX_DRAFTS_PER_EPISODE})"
            )
        if isinstance(declared_size, int) and (
            int(draft_usage["bytes"]) + declared_size > MAX_DRAFT_BYTES_PER_EPISODE
        ):
            raise FlywheelDataError("Episode staged artifact byte budget would be exceeded")

        project_id = str(binding.campaign["argus_project_id"])
        download = client.download_artifact(
            project_id, artifact_path, max_bytes=MAX_ARTIFACT_BYTES
        )
        if isinstance(declared_size, int) and download.size != declared_size:
            raise FlywheelDataError(
                "downloaded artifact length differs from the fresh Argus index"
            )
        reported_sha = entry.get("sha256")
        if isinstance(reported_sha, str) and reported_sha and download.sha256 != reported_sha:
            raise FlywheelDataError(
                "downloaded artifact SHA-256 differs from the fresh Argus index"
            )
        if int(draft_usage["bytes"]) + download.size > MAX_DRAFT_BYTES_PER_EPISODE:
            raise FlywheelDataError("Episode staged artifact byte budget would be exceeded")

        media_type = self._media_type(entry, download.content_type)
        scan_state, manual_redaction_required = self._scan_staged(
            download.content, media_type=media_type, kind=str(entry.get("kind") or "")
        )
        import_id = str(uuid.uuid4())
        staging_key = f"{import_id[:2]}/{import_id}.stage"
        target = self._staging_path(staging_key)
        now = utc_now()
        idempotent_row: dict[str, Any] | None = None
        try:
            with self.db.transaction() as connection:
                concurrent = connection.execute(
                    "SELECT * FROM argus_artifact_imports "
                    "WHERE episode_id=? AND idempotency_key=?",
                    (episode_id, idempotency_key),
                ).fetchone()
                if concurrent is not None:
                    concurrent_row = dict(concurrent)
                    if not (
                        concurrent_row["artifact_path"] == artifact_path
                        and concurrent_row["role"] == role
                        and concurrent_row["source_entry_sha256"]
                        == expected_entry_sha256
                    ):
                        raise FlywheelDataError(
                            "idempotency_key is already bound to a different artifact request"
                        )
                    idempotent_row = concurrent_row
                else:
                    locked_usage = connection.execute(
                        "SELECT COUNT(*) AS count,COALESCE(SUM(source_byte_length),0) AS bytes "
                        "FROM argus_artifact_imports WHERE episode_id=? AND state='draft'",
                        (episode_id,),
                    ).fetchone()
                    if locked_usage is None:  # pragma: no cover - aggregate invariant
                        raise RuntimeError("artifact staging usage query returned no row")
                    if int(locked_usage["count"]) >= MAX_DRAFTS_PER_EPISODE:
                        raise FlywheelDataError(
                            "Episode staged artifact count limit would be exceeded "
                            f"({MAX_DRAFTS_PER_EPISODE})"
                        )
                    if int(locked_usage["bytes"]) + download.size > MAX_DRAFT_BYTES_PER_EPISODE:
                        raise FlywheelDataError(
                            "Episode staged artifact byte budget would be exceeded"
                        )
                    self._atomic_stage(target, download.content)
                    connection.execute(
                        "INSERT INTO argus_artifact_imports("
                        "id,episode_id,campaign_id,connection_id,argus_project_id,artifact_path,role,"
                        "state,idempotency_key,source_entry_json,source_entry_sha256,source_sha256,"
                        "source_byte_length,media_type,staging_key,scan_state,manual_redaction_required,"
                        "created_at,updated_at) VALUES(?,?,?,?,?,?,?,'draft',?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            import_id,
                            episode_id,
                            binding.campaign["id"],
                            binding.connection["id"],
                            project_id,
                            artifact_path,
                            role,
                            idempotency_key,
                            canonical_json(
                                {key: value for key, value in entry.items() if key != "entry_sha256"}
                            ),
                            expected_entry_sha256,
                            download.sha256,
                            download.size,
                            media_type,
                            staging_key,
                            scan_state,
                            int(manual_redaction_required),
                            now,
                            now,
                        ),
                    )
        except FlywheelDataError:
            target.unlink(missing_ok=True)
            raise
        except sqlite3.IntegrityError as exc:
            target.unlink(missing_ok=True)
            raced = self.db.fetch_one(
                "SELECT * FROM argus_artifact_imports WHERE episode_id=? AND idempotency_key=?",
                (episode_id, idempotency_key),
            )
            if raced and (
                raced["artifact_path"] == artifact_path
                and raced["role"] == role
                and raced["source_entry_sha256"] == expected_entry_sha256
            ):
                return self.public_import(raced), True
            raise FlywheelDataError("artifact staging race or integrity conflict") from exc
        if idempotent_row is not None:
            return self.public_import(idempotent_row), True
        self.db.append_event(
            "flywheel.artifact",
            "argus_artifact_staged",
            entity_type="argus_artifact_import",
            entity_id=import_id,
            payload={
                "episode_id": episode_id,
                "campaign_id": binding.campaign["id"],
                "artifact_path": artifact_path,
                "role": role,
                "source_sha256": download.sha256,
                "source_byte_length": download.size,
            },
        )
        row = self.db.fetch_one("SELECT * FROM argus_artifact_imports WHERE id=?", (import_id,))
        if not row:  # pragma: no cover - committed insert invariant
            raise RuntimeError("artifact staging insert disappeared")
        return self.public_import(row, include_preview=True), False

    def confirm(
        self,
        import_id: str,
        *,
        actor: str,
        expected_source_sha256: str,
        redaction_confirmed: bool,
        manual_redaction_confirmed: bool,
        training_consent: bool,
        license_basis: str,
        disposition: str,
        replacement_text: str | None,
    ) -> dict[str, Any]:
        row = self._require_import(import_id)
        if row["state"] != "draft":
            raise FlywheelDataError(f"artifact import is already {row['state']}")
        if row["source_sha256"] != expected_source_sha256:
            raise FlywheelDataError("staged artifact SHA-256 changed; refresh before confirming")
        if redaction_confirmed is not True:
            raise FlywheelDataError("redaction confirmation is required")
        if bool(row["manual_redaction_required"]) and manual_redaction_confirmed is not True:
            raise FlywheelDataError("binary artifact requires explicit manual redaction review")
        source = self._read_staged(row)
        if disposition == "replace_text":
            if bool(row["manual_redaction_required"]):
                raise FlywheelDataError(
                    "binary artifacts cannot be replaced with text; stage a sanitized Argus artifact"
                )
            final_bytes = str(replacement_text or "").encode("utf-8")
            media_type = (
                row["media_type"]
                if str(row["media_type"]).lower().startswith("text/")
                else "text/plain; charset=utf-8"
            )
        else:
            if row["scan_state"] == "requires_redaction":
                raise FlywheelDataError(
                    "staged text contains a probable credential; use a sanitized replacement"
                )
            final_bytes = source
            media_type = row["media_type"]
        now = utc_now()
        with self.db.transaction() as connection:
            locked = connection.execute(
                "SELECT * FROM argus_artifact_imports WHERE id=?", (import_id,)
            ).fetchone()
            if locked is None or locked["state"] != "draft":
                raise FlywheelDataError("artifact confirmation race detected")
            obj = self.objects.put_bytes(
                final_bytes,
                media_type=media_type,
                metadata={
                    "source_kind": "argus_artifact",
                    "argus_project_id": row["argus_project_id"],
                    "artifact_path": row["artifact_path"],
                    "role": row["role"],
                    "source_sha256": row["source_sha256"],
                    "redaction_mode": disposition,
                },
                connection=connection,
            )
            cursor = connection.execute(
                "UPDATE argus_artifact_imports SET state='confirmed',content_object_sha256=?,"
                "redaction_mode=?,redaction_confirmed=1,manual_redaction_confirmed=?,"
                "training_consent=?,license_basis=?,"
                "confirmed_by=?,confirmed_at=?,updated_at=? WHERE id=? AND state='draft'",
                (
                    obj.sha256,
                    disposition,
                    int(manual_redaction_confirmed),
                    int(training_consent),
                    license_basis,
                    actor,
                    now,
                    now,
                    import_id,
                ),
            )
            if cursor.rowcount != 1:
                raise FlywheelDataError("artifact confirmation race detected")
            connection.execute(
                "INSERT OR IGNORE INTO episode_entity_links"
                "(id,episode_id,entity_type,entity_id,relation,metadata_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    row["episode_id"],
                    "content_object",
                    obj.sha256,
                    f"argus_artifact:{row['role']}",
                    canonical_json(
                        {
                            "argus_artifact_import_id": import_id,
                            "source_sha256": row["source_sha256"],
                        }
                    ),
                    now,
                ),
            )
        self._purge_staging(row)
        self.db.append_event(
            "flywheel.artifact",
            "argus_artifact_confirmed",
            entity_type="argus_artifact_import",
            entity_id=import_id,
            payload={
                "episode_id": row["episode_id"],
                "role": row["role"],
                "content_object_sha256": obj.sha256,
                "training_consent": bool(training_consent),
            },
        )
        return self.public_import(self._require_import(import_id))

    def discard(self, import_id: str, *, actor: str, reason: str) -> dict[str, Any]:
        row = self._require_import(import_id)
        if row["state"] != "draft":
            raise FlywheelDataError(
                f"only draft artifact imports can be discarded; state={row['state']}"
            )
        now = utc_now()
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE argus_artifact_imports SET state='discarded',discarded_by=?,"
                "discarded_at=?,discard_reason=?,updated_at=? WHERE id=? AND state='draft'",
                (actor, now, reason, now, import_id),
            )
            if cursor.rowcount != 1:
                raise FlywheelDataError("artifact discard race detected")
        self._purge_staging(row)
        self.db.append_event(
            "flywheel.artifact",
            "argus_artifact_discarded",
            entity_type="argus_artifact_import",
            entity_id=import_id,
            payload={"episode_id": row["episode_id"], "discarded_by": actor},
        )
        return self.public_import(self._require_import(import_id))

    def list_imports(self, episode_id: str) -> list[dict[str, Any]]:
        self.resolve_binding(episode_id)
        return [
            self.public_import(row)
            for row in self.db.fetch_all(
                "SELECT * FROM argus_artifact_imports WHERE episode_id=? ORDER BY created_at,id",
                (episode_id,),
            )
        ]

    def import_detail(self, import_id: str) -> dict[str, Any]:
        return self.public_import(self._require_import(import_id), include_preview=True)

    def public_import(
        self, row: dict[str, Any], *, include_preview: bool = False
    ) -> dict[str, Any]:
        output = decode_row(row) or {}
        output.pop("staging_key", None)
        source_entry = output.pop("source_entry_json", None)
        if isinstance(source_entry, str):
            try:
                output["source_entry"] = json.loads(source_entry)
            except json.JSONDecodeError:
                output["source_entry"] = {}
        elif "source_entry" not in output:
            output["source_entry"] = {}
        for key in (
            "manual_redaction_required",
            "manual_redaction_confirmed",
            "redaction_confirmed",
            "training_consent",
        ):
            output[key] = bool(output.get(key))
        episode = self.db.fetch_one(
            "SELECT head_revision_id FROM research_episodes WHERE id=?", (row["episode_id"],)
        )
        head = (
            self.db.fetch_one(
                "SELECT id,manifest_json FROM episode_revisions WHERE id=?",
                (episode["head_revision_id"],),
            )
            if episode and episode.get("head_revision_id")
            else None
        )
        membership = head_artifact_membership(head)
        sealed = bool(
            row["state"] == "confirmed"
            and membership.get(row["id"]) == row.get("content_object_sha256")
        )
        output["sealed_in_head"] = sealed
        output["sealed_revision_id"] = head["id"] if sealed and head else None
        if include_preview:
            output["preview"] = self._preview(row)
        return output

    def _require_import(self, import_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM argus_artifact_imports WHERE id=?", (import_id,))
        if not row:
            raise FlywheelDataError(f"Argus artifact import not found: {import_id}")
        return row

    def _read_staged(self, row: dict[str, Any]) -> bytes:
        path = self._staging_path(str(row["staging_key"]))
        if not path.is_file():
            raise FlywheelDataError("staged artifact bytes are missing")
        data = path.read_bytes()
        if len(data) != int(row["source_byte_length"]):
            raise FlywheelDataError("staged artifact byte length mismatch")
        if hashlib.sha256(data).hexdigest() != row["source_sha256"]:
            raise FlywheelDataError("staged artifact SHA-256 mismatch")
        return data

    def _preview(self, row: dict[str, Any]) -> dict[str, Any]:
        if row["state"] != "draft" or bool(row["manual_redaction_required"]):
            return {
                "available": False,
                "text": "",
                "truncated": False,
                "max_bytes": MAX_PREVIEW_BYTES,
            }
        data = self._read_staged(row)
        bounded = data[:MAX_PREVIEW_BYTES]
        return {
            "available": True,
            "text": bounded.decode("utf-8", errors="replace"),
            "truncated": len(data) > MAX_PREVIEW_BYTES,
            "max_bytes": MAX_PREVIEW_BYTES,
        }

    def _staging_path(self, staging_key: str) -> Path:
        path = (self.staging_root / staging_key).resolve()
        try:
            path.relative_to(self.staging_root)
        except ValueError as exc:
            raise FlywheelDataError("staging key escapes the artifact staging root") from exc
        return path

    @staticmethod
    def _atomic_stage(target: Path, content: bytes) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary_name = tempfile.mkstemp(prefix=".incoming-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            target.chmod(stat.S_IRUSR | stat.S_IWUSR)
        finally:
            temporary.unlink(missing_ok=True)

    def _purge_staging(self, row: dict[str, Any]) -> None:
        try:
            self._staging_path(str(row["staging_key"])).unlink(missing_ok=True)
        except OSError as exc:
            self.db.append_event(
                "flywheel.artifact",
                "argus_artifact_staging_cleanup_failed",
                severity="attention",
                entity_type="argus_artifact_import",
                entity_id=row["id"],
                payload={"error_type": type(exc).__name__},
            )

    @staticmethod
    def _media_type(entry: dict[str, Any], downloaded: str | None) -> str:
        candidate = str(
            downloaded
            or entry.get("media_type")
            or entry.get("content_type")
            or ""
        ).strip()
        if candidate:
            return candidate[:500]
        kind = str(entry.get("kind") or "").lower()
        if kind == "markdown":
            return "text/markdown; charset=utf-8"
        if kind == "json":
            return "application/json"
        if kind in {"text", "table", "csv"}:
            return "text/plain; charset=utf-8"
        return "application/octet-stream"

    @staticmethod
    def _scan_staged(
        content: bytes, *, media_type: str, kind: str
    ) -> tuple[str, bool]:
        normalized_media = media_type.lower()
        text_candidate = (
            normalized_media.startswith("text/")
            or normalized_media.startswith("application/json")
            or "+json" in normalized_media
            or kind.lower() in _TEXT_KINDS
        )
        if not text_candidate:
            return "not_scannable_binary", True
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError:
            return "not_scannable_binary", True
        try:
            assert_no_secrets(decoded, path="staged_argus_artifact")
        except FlywheelDataError:
            return "requires_redaction", False
        return "passed", False


def artifact_limits() -> dict[str, int]:
    return {
        "max_index_items": MAX_INDEX_ITEMS,
        "max_artifact_bytes": MAX_ARTIFACT_BYTES,
        "max_draft_bytes_per_episode": MAX_DRAFT_BYTES_PER_EPISODE,
        "max_drafts_per_episode": MAX_DRAFTS_PER_EPISODE,
        "max_preview_bytes": MAX_PREVIEW_BYTES,
    }
