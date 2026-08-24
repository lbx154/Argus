from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from ..db import Database, decode_row, decode_rows, utc_now
from .episode_artifact_manifest import (
    confirmed_argus_artifact_manifest,
    head_argus_artifacts_are_current,
    head_artifact_membership,
)

SCHEMA_VERSION = "flywheel.episode/2"
SUPPORTED_SCHEMA_VERSIONS = frozenset({"flywheel.episode/1", SCHEMA_VERSION})
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "access_token",
    "auth_token",
    "bearer_token",
    "password",
    "passwd",
    "secret",
    "client_secret",
    "credential",
    "credentials",
    "private_key",
}
_SAFE_SECRET_LIKE_KEYS = {
    # Resource/accounting vocabulary is part of a TeamProfile, not a credential.
    "token_budget",
    "token_budgets",
    "token_limit",
    "token_limits",
    "token_count",
    "token_counts",
    "token_cost",
    "token_usage",
    "token_budget_text",
    "token_limit_text",
    "max_tokens",
    "min_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_tokens",
    "completion_tokens",
    "api_budget",
    "api_cost",
}
_SECRET_KEY_SUFFIXES = (
    "apikey",
    "accesstoken",
    "authtoken",
    "bearertoken",
    "refreshtoken",
    "idtoken",
    "clientsecret",
    "webhooksecret",
    "signingsecret",
    "privatekey",
    "password",
    "passwd",
    "credential",
    "credentials",
    "token",
    "secret",
)
_SECRET_KEY_PREFIXES = (
    "api_key_",
    "access_token_",
    "auth_token_",
    "bearer_token_",
    "refresh_token_",
    "client_secret_",
    "private_key_",
    "password_",
    "passwd_",
    "credential_",
    "credentials_",
    "secret_",
    "token_",
)
_PROVIDER_SECRET_KEY = re.compile(
    r"(?:^|_)(?:openai|anthropic|github|gitlab|google|gemini|azure|aws|slack|"
    r"huggingface|hf)(?:_|$).*(?:api_?key|key|token|secret|credential)s?$"
)
_SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|client[_-]?secret)"
        r"\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=-]{12,}"
    ),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
)


class FlywheelDataError(ValueError):
    """Expected, user-correctable data-flywheel error."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def assert_no_secrets(value: Any, *, path: str = "payload") -> None:
    """Reject likely credentials without rejecting ordinary token-budget descriptions."""

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
            compact = normalized.replace("_", "")
            key_is_secret_bearing = (
                normalized in _SECRET_KEYS
                or (
                    normalized not in _SAFE_SECRET_LIKE_KEYS
                    and (
                        any(compact.endswith(suffix) for suffix in _SECRET_KEY_SUFFIXES)
                        or normalized.startswith(_SECRET_KEY_PREFIXES)
                        or _PROVIDER_SECRET_KEY.search(normalized) is not None
                    )
                )
            )
            if key_is_secret_bearing:
                raise FlywheelDataError(f"secret-bearing field is not allowed at {path}.{key}")
            assert_no_secrets(child, path=f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            assert_no_secrets(child, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        for pattern in _SECRET_PATTERNS:
            if pattern.search(value):
                raise FlywheelDataError(f"probable credential detected at {path}")


@dataclass(frozen=True, slots=True)
class StoredObject:
    sha256: str
    media_type: str
    byte_length: int
    storage_path: str
    redaction_scan_state: str
    manual_redaction_required: bool


class ContentObjectStore:
    def __init__(self, db: Database, root: Path) -> None:
        self.db = db
        self.root = root.resolve()

    def put_json(self, value: Any, *, metadata: dict[str, Any] | None = None) -> StoredObject:
        assert_no_secrets(value)
        return self.put_bytes(
            canonical_json(value).encode("utf-8"),
            media_type="application/json",
            metadata=metadata,
        )

    def put_text(
        self,
        value: str,
        *,
        media_type: str = "text/plain; charset=utf-8",
        metadata: dict[str, Any] | None = None,
    ) -> StoredObject:
        assert_no_secrets(value)
        return self.put_bytes(value.encode("utf-8"), media_type=media_type, metadata=metadata)

    def put_bytes(
        self,
        value: bytes,
        *,
        media_type: str,
        metadata: dict[str, Any] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> StoredObject:
        metadata = metadata or {}
        assert_no_secrets(metadata)
        try:
            decoded = value.decode("utf-8")
        except UnicodeDecodeError:
            decoded = None
        normalized_media_type = media_type.strip().lower()
        text_scannable = bool(
            decoded is not None
            and (
                normalized_media_type.startswith("text/")
                or normalized_media_type.startswith("application/json")
                or "+json" in normalized_media_type
            )
        )
        if text_scannable and decoded is not None:
            assert_no_secrets(decoded, path="object_bytes")
        redaction_scan_state = "passed" if text_scannable else "not_scannable_binary"
        manual_redaction_required = not text_scannable
        digest = hashlib.sha256(value).hexdigest()
        relative = Path(digest[:2]) / digest[2:4] / digest
        destination = self.root / relative
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise FlywheelDataError("existing content-addressed object failed verification")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary_name = tempfile.mkstemp(prefix=".incoming-", dir=destination.parent)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(value)
                    stream.flush()
                    os.fsync(stream.fileno())
                Path(temporary_name).replace(destination)
            finally:
                temporary = Path(temporary_name)
                if temporary.exists():
                    temporary.unlink()
        now = utc_now()

        def register(target: sqlite3.Connection) -> sqlite3.Row:
            target.execute(
                "INSERT OR IGNORE INTO content_objects"
                "(sha256,media_type,byte_length,storage_path,secret_scan_state,metadata_json,"
                "redaction_scan_state,manual_redaction_required,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    digest,
                    media_type,
                    len(value),
                    relative.as_posix(),
                    redaction_scan_state,
                    canonical_json(metadata),
                    redaction_scan_state,
                    int(manual_redaction_required),
                    now,
                ),
            )
            row = target.execute(
                "SELECT * FROM content_objects WHERE sha256=?", (digest,)
            ).fetchone()
            if row is None:
                raise FlywheelDataError("content object registry insert failed")
            return row

        if connection is None:
            with self.db.transaction() as owned_connection:
                row = register(owned_connection)
        else:
            # Callers that already hold BEGIN IMMEDIATE can atomically register
            # the object together with the state transition that references it.
            row = register(connection)
        if not row:
            raise FlywheelDataError("content object registry insert failed")
        return StoredObject(
            digest,
            media_type,
            len(value),
            relative.as_posix(),
            str(row["redaction_scan_state"]),
            bool(row["manual_redaction_required"]),
        )

    def verify(self, digest: str) -> tuple[bool, str]:
        row = self.db.fetch_one("SELECT * FROM content_objects WHERE sha256=?", (digest,))
        if not row:
            return False, "registry row missing"
        path = (self.root / row["storage_path"]).resolve()
        try:
            path.relative_to(self.root)
        except ValueError:
            return False, "storage path escapes object root"
        if not path.is_file():
            return False, "object file missing"
        data = path.read_bytes()
        if len(data) != row["byte_length"]:
            return False, "byte length mismatch"
        if hashlib.sha256(data).hexdigest() != digest:
            return False, "sha256 mismatch"
        return True, "verified"


def _chain_hash(parent_chain_sha256: str | None, manifest_sha256: str) -> str:
    return sha256_text(
        canonical_json(
            {
                "manifest_sha256": manifest_sha256,
                "parent_chain_sha256": parent_chain_sha256,
            }
        )
    )


def _decode_revision(row: dict[str, Any]) -> dict[str, Any]:
    return decode_row(row) or {}


def _confirmed_review_manifest(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": row["id"],
            "source_kind": row["source_kind"],
            "source_ref": row["source_ref"],
            "raw_object_sha256": row["raw_object_sha256"],
            "parsed": row.get("parsed", {}),
            "redaction_confirmed": bool(row["redaction_confirmed"]),
            "training_consent": bool(row["training_consent"]),
            "license_basis": row["license_basis"],
        }
        for row in rows
        if row["state"] == "confirmed"
    ]


class EpisodeService:
    def __init__(
        self, db: Database, object_root: Path, *, data_dir: Path | None = None
    ) -> None:
        self.db = db
        self.objects = ContentObjectStore(db, object_root)
        # ``object_root`` is normally <data_dir>/data-vault/objects.  Keep the
        # explicit argument for tests/custom layouts and the derivation for
        # backwards-compatible direct construction.
        self.data_dir = (
            data_dir.resolve()
            if data_dir is not None
            else object_root.resolve().parent.parent
        )

    def require_episode(self, episode_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM research_episodes WHERE id=?", (episode_id,))
        if not row:
            raise FlywheelDataError(f"episode not found: {episode_id}")
        return decode_row(row) or {}

    def _annotate_review_redaction(self, rows: list[dict[str, Any]]) -> None:
        """Attach immutable object-scan truth to review import projections."""

        digests = sorted(
            {
                str(row.get("raw_object_sha256") or "")
                for row in rows
                if row.get("state") == "confirmed" and row.get("raw_object_sha256")
            }
        )
        objects: dict[str, dict[str, Any]] = {}
        if digests:
            placeholders = ",".join("?" for _ in digests)
            objects = {
                str(item["sha256"]): dict(item)
                for item in self.db.fetch_all(
                    f"SELECT sha256,secret_scan_state,redaction_scan_state,"
                    f"manual_redaction_required FROM content_objects WHERE sha256 IN ({placeholders})",
                    digests,
                )
            }
        for row in rows:
            obj = objects.get(str(row.get("raw_object_sha256") or ""))
            manual_required = bool(obj and obj.get("manual_redaction_required"))
            human_confirmed = bool(row.get("redaction_confirmed"))
            row["redaction_scan_state"] = (
                str(obj.get("redaction_scan_state") or "") if obj else "unverified"
            )
            row["manual_redaction_required"] = manual_required
            # Review imports use their existing explicit redaction confirmation
            # as the human binary/PDF confirmation. The immutable content object
            # decides whether that stronger confirmation was required.
            row["manual_redaction_confirmed"] = bool(manual_required and human_confirmed)
            row["redaction_ready"] = bool(obj and human_confirmed)

    def list_episode_summaries(self) -> list[dict[str, Any]]:
        """Return one-query list metadata; content/hash verification stays lazy."""

        rows = decode_rows(
            self.db.fetch_all(
                "SELECT e.*,hr.revision_number AS head_revision_number,"
                "hr.manifest_sha256 AS head_manifest_sha256,"
                "(SELECT COUNT(*) FROM episode_revisions er WHERE er.episode_id=e.id) "
                "AS revision_count,"
                "(SELECT COUNT(*) FROM review_import_batches rb "
                " WHERE rb.episode_id=e.id AND rb.state='draft') AS pending_review_count,"
                "(SELECT COUNT(*) FROM review_import_batches rb "
                " WHERE rb.episode_id=e.id AND rb.state='confirmed') AS confirmed_review_count,"
                "(SELECT COUNT(*) FROM review_import_batches rb "
                " WHERE rb.episode_id=e.id AND rb.state='discarded') AS discarded_review_count "
                ", (SELECT COUNT(*) FROM argus_artifact_imports ai "
                " WHERE ai.episode_id=e.id AND ai.state='draft') AS pending_argus_artifact_count "
                ", (SELECT COUNT(*) FROM argus_artifact_imports ai "
                " WHERE ai.episode_id=e.id AND ai.state='confirmed') AS confirmed_argus_artifact_count "
                "FROM research_episodes e "
                "LEFT JOIN episode_revisions hr ON hr.id=e.head_revision_id "
                "ORDER BY e.updated_at DESC,e.id"
            )
        )
        for row in rows:
            row["summary_only"] = True
            row["head_integrity_valid"] = None
            row["gates"] = {
                "objective_present": bool(str(row.get("objective") or "").strip()),
                "team_confirmed": bool(row.get("team_profile_id")),
                "reviews_confirmed": int(row.get("pending_review_count") or 0) == 0,
                "argus_artifacts_confirmed": int(
                    row.get("pending_argus_artifact_count") or 0
                ) == 0,
                "revision_sealed": bool(row.get("head_revision_id")),
                "integrity_verified": None,
            }
            row["data_eligibility"] = {
                "eligible": None,
                "verification_required": True,
                "reason": "load episode detail or verify endpoint for content/hash eligibility",
            }
        return rows

    def head_reviews_are_current(
        self, episode_id: str, head_revision: dict[str, Any] | None = None
    ) -> tuple[bool, str]:
        head = head_revision
        if head is None:
            episode = self.require_episode(episode_id)
            if episode.get("head_revision_id"):
                head = self.db.fetch_one(
                    "SELECT * FROM episode_revisions WHERE id=?",
                    (episode["head_revision_id"],),
                )
        if not head:
            return False, "no sealed head revision"
        imports = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM review_import_batches WHERE episode_id=? "
                "ORDER BY created_at,id",
                (episode_id,),
            )
        )
        current = _confirmed_review_manifest(imports)
        try:
            manifest = json.loads(head["manifest_json"])
            sealed = manifest["review_imports"]
        except (KeyError, TypeError, json.JSONDecodeError):
            return False, "head manifest does not contain a valid review_imports array"
        if canonical_json(sealed) != canonical_json(current):
            return False, "current confirmed reviews are not exactly sealed in the head manifest"
        return True, "current confirmed reviews exactly match the head manifest"

    def head_argus_artifacts_are_current(
        self, episode_id: str, head_revision: dict[str, Any] | None = None
    ) -> tuple[bool, str]:
        head = head_revision
        if head is None:
            episode = self.require_episode(episode_id)
            if episode.get("head_revision_id"):
                head = self.db.fetch_one(
                    "SELECT * FROM episode_revisions WHERE id=?",
                    (episode["head_revision_id"],),
                )
        return head_argus_artifacts_are_current(self.db, episode_id, head)

    def training_provenance_status(
        self,
        episode: dict[str, Any],
        revision: dict[str, Any] | None = None,
        *,
        expected_provenance: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Re-authenticate the immutable candidate lineage behind an Episode.

        The sealed revision, not mutable request metadata, is the source of the
        frozen receipt.  Current campaign/source files are then re-verified so a
        later database or filesystem mutation cannot silently remain eligible.
        """

        reasons: list[str] = []
        frozen_episode = episode
        if revision is not None:
            try:
                revision_manifest = json.loads(str(revision["manifest_json"]))
                frozen_episode = revision_manifest["episode"]
            except (KeyError, TypeError, json.JSONDecodeError):
                return {
                    "verified": False,
                    "data_eligible": False,
                    "reasons": ["sealed_revision_training_lineage_invalid"],
                    "provenance": None,
                }
        frozen_metadata = frozen_episode.get("metadata")
        if not isinstance(frozen_metadata, dict):
            frozen_metadata = {}
        frozen_provenance = frozen_metadata.get("training_provenance")
        if not isinstance(frozen_provenance, dict):
            reasons.append("verified_training_lineage_not_frozen")
        campaign_id = frozen_episode.get("campaign_id")
        if not isinstance(campaign_id, str) or not campaign_id.strip():
            reasons.append("source_campaign_missing")
            return {
                "verified": False,
                "data_eligible": False,
                "reasons": reasons,
                "provenance": None,
            }
        if episode.get("campaign_id") != campaign_id:
            reasons.append("episode_campaign_changed_since_seal")
        campaign = decode_row(
            self.db.fetch_one("SELECT * FROM campaigns WHERE id=?", (campaign_id,))
        )
        current_provenance: dict[str, Any] | None = None
        if campaign is None:
            reasons.append("source_campaign_not_found")
        else:
            # Imported lazily to avoid an import cycle: api.py also imports this
            # service module for the normal router implementation.
            from ..api import verify_training_campaign_provenance

            try:
                current_provenance = verify_training_campaign_provenance(
                    self.db, campaign, self.data_dir
                )
            except Exception as exc:
                detail = getattr(exc, "detail", None)
                reasons.append(
                    "training_lineage_verification_failed:"
                    + str(detail or exc).replace(",", ";")
                )
        if current_provenance is not None:
            if not isinstance(frozen_provenance, dict) or canonical_json(
                frozen_provenance
            ) != canonical_json(current_provenance):
                reasons.append("frozen_training_lineage_mismatch")
            if expected_provenance is not None and canonical_json(
                expected_provenance
            ) != canonical_json(current_provenance):
                reasons.append("dataset_member_training_lineage_mismatch")
        elif expected_provenance is not None:
            reasons.append("dataset_member_training_lineage_unverifiable")
        return {
            "verified": not reasons,
            "data_eligible": not reasons,
            "reasons": reasons,
            "provenance": current_provenance,
        }

    def episode_detail(self, episode_id: str) -> dict[str, Any]:
        episode = self.require_episode(episode_id)
        revisions = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM episode_revisions WHERE episode_id=? ORDER BY revision_number",
                (episode_id,),
            )
        )
        links = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM episode_entity_links WHERE episode_id=? ORDER BY created_at,id",
                (episode_id,),
            )
        )
        review_imports = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM review_import_batches WHERE episode_id=? ORDER BY created_at,id",
                (episode_id,),
            )
        )
        self._annotate_review_redaction(review_imports)
        for review_import in review_imports:
            review_import.pop("raw_payload", None)
        argus_artifact_imports = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM argus_artifact_imports WHERE episode_id=? ORDER BY created_at,id",
                (episode_id,),
            )
        )
        for artifact_import in argus_artifact_imports:
            artifact_import.pop("staging_key", None)
        intake_confirmed = bool(episode.get("team_profile_id"))
        pending_reviews = [row for row in review_imports if row["state"] == "draft"]
        all_reviews_confirmed = not pending_reviews
        pending_artifacts = [
            row for row in argus_artifact_imports if row["state"] == "draft"
        ]
        all_artifacts_confirmed = not pending_artifacts
        head_row = (
            self.db.fetch_one(
                "SELECT * FROM episode_revisions WHERE id=?", (episode.get("head_revision_id"),)
            )
            if episode.get("head_revision_id")
            else None
        )
        artifact_membership = head_artifact_membership(head_row)
        for artifact_import in argus_artifact_imports:
            sealed = bool(
                artifact_import["state"] == "confirmed"
                and artifact_membership.get(artifact_import["id"])
                == artifact_import.get("content_object_sha256")
            )
            artifact_import["sealed_in_head"] = sealed
            artifact_import["sealed_revision_id"] = (
                head_row["id"] if sealed and head_row else None
            )
        head_reviews_current, head_reviews_detail = self.head_reviews_are_current(
            episode_id,
            head_row,
        )
        head_artifacts_current, head_artifacts_detail = (
            self.head_argus_artifacts_are_current(episode_id, head_row)
        )
        head_verification = self.verify_episode(episode_id) if revisions else {
            "valid": False,
            "checks": [{"name": "head_revision", "valid": False, "detail": "no sealed revision"}],
            "head_revision": None,
            "manifest_sha256": None,
        }
        provenance_status = self.training_provenance_status(episode, head_row)
        gates = {
            "objective_present": bool(episode.get("objective", "").strip()),
            "team_confirmed": intake_confirmed,
            "reviews_confirmed": all_reviews_confirmed,
            "reviews_sealed_in_head": head_reviews_current,
            "argus_artifacts_confirmed": all_artifacts_confirmed,
            "argus_artifacts_sealed_in_head": head_artifacts_current,
            "revision_sealed": bool(revisions),
            "integrity_verified": bool(head_verification["valid"]),
            "training_lineage_verified": provenance_status["verified"],
        }
        review_rights_ready = all(
            bool(row.get("training_consent")) and bool(row.get("license_basis", "").strip())
            and bool(row.get("redaction_ready"))
            for row in review_imports
            if row["state"] == "confirmed"
        )
        artifact_rights_ready = all(
            bool(row.get("training_consent"))
            and bool(row.get("license_basis", "").strip())
            and bool(row.get("redaction_confirmed"))
            and (
                not bool(row.get("manual_redaction_required"))
                or bool(row.get("manual_redaction_confirmed"))
            )
            for row in argus_artifact_imports
            if row["state"] == "confirmed"
        )
        ineligibility_reasons = list(provenance_status["reasons"])
        eligibility_checks = (
            ("training_consent_missing", not bool(episode.get("training_consent"))),
            (
                "license_basis_missing",
                not bool(episode.get("license_basis", "").strip()),
            ),
            ("no_sealed_revision", not bool(revisions)),
            ("review_import_confirmation_pending", not all_reviews_confirmed),
            ("confirmed_reviews_not_sealed_in_head", not head_reviews_current),
            ("review_training_rights_missing", not review_rights_ready),
            ("artifact_import_confirmation_pending", not all_artifacts_confirmed),
            (
                "confirmed_argus_artifacts_not_sealed_in_head",
                not head_artifacts_current,
            ),
            ("artifact_training_rights_missing", not artifact_rights_ready),
            ("integrity_verification_failed", not bool(head_verification["valid"])),
        )
        ineligibility_reasons.extend(
            reason for reason, failed in eligibility_checks if failed
        )
        ineligibility_reasons = list(dict.fromkeys(ineligibility_reasons))
        data_eligibility = {
            "eligible": not ineligibility_reasons,
            "training_consent": bool(episode.get("training_consent")),
            "license_basis_present": bool(episode.get("license_basis", "").strip()),
            "all_review_imports_confirmed": all_reviews_confirmed,
            "confirmed_reviews_sealed_in_head": head_reviews_current,
            "confirmed_reviews_sealed_in_head_detail": head_reviews_detail,
            "pending_review_count": len(pending_reviews),
            "review_rights_ready": review_rights_ready,
            "review_redaction_ready": all(
                bool(row.get("redaction_ready"))
                for row in review_imports
                if row["state"] == "confirmed"
            ),
            "all_argus_artifact_imports_confirmed": all_artifacts_confirmed,
            "confirmed_argus_artifacts_sealed_in_head": head_artifacts_current,
            "confirmed_argus_artifacts_sealed_in_head_detail": head_artifacts_detail,
            "pending_argus_artifact_count": len(pending_artifacts),
            "argus_artifact_rights_ready": artifact_rights_ready,
            "head_integrity_valid": bool(head_verification["valid"]),
            "training_lineage_verified": provenance_status["verified"],
            "training_lineage": provenance_status["provenance"],
            "ineligibility_reasons": ineligibility_reasons,
        }
        return {
            **episode,
            "revisions": revisions,
            "links": links,
            "review_imports": review_imports,
            "argus_artifact_imports": argus_artifact_imports,
            "gates": gates,
            "data_eligibility": data_eligibility,
        }

    def seal(
        self,
        episode_id: str,
        *,
        actor: str,
        reason: str,
        terminal_state: str | None,
    ) -> dict[str, Any]:
        episode = self.require_episode(episode_id)
        if not episode.get("objective", "").strip():
            raise FlywheelDataError("cannot seal an episode without a human-visible objective")
        if not episode.get("team_profile_id"):
            raise FlywheelDataError("cannot seal an episode before its team profile is confirmed")
        state = terminal_state or episode["state"]
        links = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM episode_entity_links WHERE episode_id=? ORDER BY entity_type,entity_id,relation",
                (episode_id,),
            )
        )
        imports = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM review_import_batches WHERE episode_id=? ORDER BY created_at,id",
                (episode_id,),
            )
        )
        self._annotate_review_redaction(imports)
        artifact_imports = decode_rows(
            self.db.fetch_all(
                "SELECT * FROM argus_artifact_imports WHERE episode_id=? "
                "ORDER BY role,artifact_path,id",
                (episode_id,),
            )
        )
        draft_imports = [row["id"] for row in imports if row["state"] == "draft"]
        if draft_imports:
            raise FlywheelDataError(
                "cannot seal while review imports await human confirmation: " + ", ".join(draft_imports)
            )
        unsafe_reviews = [
            row["id"]
            for row in imports
            if row["state"] == "confirmed" and not bool(row.get("redaction_ready"))
        ]
        if unsafe_reviews:
            raise FlywheelDataError(
                "cannot seal confirmed review evidence before redaction/manual binary review: "
                + ", ".join(unsafe_reviews)
            )
        draft_artifacts = [
            row["id"] for row in artifact_imports if row["state"] == "draft"
        ]
        if draft_artifacts:
            raise FlywheelDataError(
                "cannot seal while Argus artifact imports await human confirmation: "
                + ", ".join(draft_artifacts)
            )
        parent = self.db.fetch_one(
            "SELECT * FROM episode_revisions WHERE episode_id=? ORDER BY revision_number DESC LIMIT 1",
            (episode_id,),
        )
        revision_number = int(parent["revision_number"]) + 1 if parent else 1
        sealed_at = utc_now()
        object_refs = sorted(
            {
                (row["raw_object_sha256"], f"review_import:{row['source_kind']}")
                for row in imports
                if row["state"] == "confirmed"
            }
            | {
                (row["content_object_sha256"], f"argus_artifact:{row['role']}")
                for row in artifact_imports
                if row["state"] == "confirmed"
            }
        )
        for digest, _ in object_refs:
            valid, detail = self.objects.verify(digest)
            if not valid:
                raise FlywheelDataError(f"content object {digest} cannot be sealed: {detail}")
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "episode": {
                "id": episode_id,
                "title": episode["title"],
                "objective": episode["objective"],
                "state": state,
                "team_profile_id": episode.get("team_profile_id"),
                "venue_id": episode.get("venue_id"),
                "deadline_id": episode.get("deadline_id"),
                "campaign_id": episode.get("campaign_id"),
                "training_consent": bool(episode.get("training_consent")),
                "license_basis": episode.get("license_basis", ""),
                "metadata": episode.get("metadata", {}),
            },
            "entity_links": [
                {
                    "entity_type": link["entity_type"],
                    "entity_id": link["entity_id"],
                    "relation": link["relation"],
                    "metadata": link.get("metadata", {}),
                }
                for link in links
            ],
            "review_imports": _confirmed_review_manifest(imports),
            "argus_artifact_imports": confirmed_argus_artifact_manifest(
                artifact_imports
            ),
            "objects": [
                {"sha256": digest, "role": role} for digest, role in object_refs
            ],
            "provenance": {
                "parent_revision_id": parent["id"] if parent else None,
                "parent_chain_sha256": parent["chain_sha256"] if parent else None,
                "revision_number": revision_number,
                "reason": reason,
                "sealed_by": actor,
                "sealed_at": sealed_at,
            },
        }
        assert_no_secrets(manifest)
        manifest_json = canonical_json(manifest)
        manifest_sha256 = sha256_text(manifest_json)
        chain_sha256 = _chain_hash(parent["chain_sha256"] if parent else None, manifest_sha256)
        revision_id = str(uuid.uuid4())
        with self.db.transaction() as connection:
            connection.execute(
                "INSERT INTO episode_revisions"
                "(id,episode_id,revision_number,parent_revision_id,manifest_json,manifest_sha256,"
                "chain_sha256,object_count,reason,sealed_by,sealed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    revision_id,
                    episode_id,
                    revision_number,
                    parent["id"] if parent else None,
                    manifest_json,
                    manifest_sha256,
                    chain_sha256,
                    len(object_refs),
                    reason,
                    actor,
                    sealed_at,
                ),
            )
            for digest, role in object_refs:
                connection.execute(
                    "INSERT INTO episode_revision_objects"
                    "(revision_id,object_sha256,role,created_at) VALUES(?,?,?,?)",
                    (revision_id, digest, role, sealed_at),
                )
            connection.execute(
                "UPDATE research_episodes SET state=?,head_revision_id=?,updated_at=? WHERE id=?",
                (state, revision_id, sealed_at, episode_id),
            )
        return _decode_revision(
            self.db.fetch_one("SELECT * FROM episode_revisions WHERE id=?", (revision_id,)) or {}
        )

    def _verify_revision_rows(
        self, episode_id: str, revisions: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Verify one immutable chain prefix without consulting mutable Episode state."""

        checks: list[dict[str, Any]] = []
        parent_id: str | None = None
        parent_chain: str | None = None
        for expected_number, revision in enumerate(revisions, 1):
            manifest_digest = sha256_text(revision["manifest_json"])
            checks.append(
                {
                    "name": f"revision_{expected_number}_manifest",
                    "valid": manifest_digest == revision["manifest_sha256"],
                    "detail": revision["id"],
                }
            )
            chain_digest = _chain_hash(parent_chain, revision["manifest_sha256"])
            checks.append(
                {
                    "name": f"revision_{expected_number}_chain",
                    "valid": (
                        revision["revision_number"] == expected_number
                        and revision["parent_revision_id"] == parent_id
                        and chain_digest == revision["chain_sha256"]
                    ),
                    "detail": revision["id"],
                }
            )
            refs = self.db.fetch_all(
                "SELECT object_sha256,role FROM episode_revision_objects WHERE revision_id=?",
                (revision["id"],),
            )
            for ref in refs:
                valid, detail = self.objects.verify(ref["object_sha256"])
                checks.append(
                    {
                        "name": f"object_{ref['object_sha256']}",
                        "valid": valid,
                        "detail": detail,
                    }
                )
            try:
                manifest = json.loads(revision["manifest_json"])
                provenance = manifest["provenance"]
                manifest_refs = sorted(
                    (item["sha256"], item["role"]) for item in manifest.get("objects", [])
                )
                row_refs = sorted((item["object_sha256"], item["role"]) for item in refs)
                schema_version = manifest.get("schema_version")
                artifact_rows = manifest.get("argus_artifact_imports", [])
                artifact_refs = (
                    sorted(
                        {
                            (
                                item["content_object_sha256"],
                                f"argus_artifact:{item['role']}",
                            )
                            for item in artifact_rows
                        }
                    )
                    if isinstance(artifact_rows, list)
                    and all(isinstance(item, dict) for item in artifact_rows)
                    else None
                )
                row_artifact_refs = sorted(
                    {
                        (item["object_sha256"], item["role"])
                        for item in refs
                        if str(item["role"]).startswith("argus_artifact:")
                    }
                )
                artifact_structure_valid = bool(
                    artifact_refs is not None
                    and (
                        (schema_version == "flywheel.episode/1" and not row_artifact_refs)
                        or (
                            schema_version == SCHEMA_VERSION
                            and artifact_refs == row_artifact_refs
                        )
                    )
                )
                structure_valid = bool(
                    schema_version in SUPPORTED_SCHEMA_VERSIONS
                    and manifest.get("episode", {}).get("id") == episode_id
                    and provenance.get("parent_revision_id") == parent_id
                    and provenance.get("parent_chain_sha256") == parent_chain
                    and provenance.get("revision_number") == expected_number
                    and manifest_refs == row_refs
                    and revision["object_count"] == len(row_refs)
                    and artifact_structure_valid
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                structure_valid = False
            checks.append(
                {
                    "name": f"revision_{expected_number}_structure",
                    "valid": structure_valid,
                    "detail": revision["id"],
                }
            )
            parent_id = revision["id"]
            parent_chain = revision["chain_sha256"]
        head = revisions[-1] if revisions else None
        return checks, head

    def verify_revision_prefix(self, episode_id: str, revision_id: str) -> dict[str, Any]:
        """Verify the exact revision frozen by a dataset snapshot.

        Later Episode revisions and newly staged/confirmed reviews are append-only
        activity and must not invalidate an older immutable snapshot member.
        """

        self.require_episode(episode_id)
        revisions = self.db.fetch_all(
            "SELECT * FROM episode_revisions WHERE episode_id=? ORDER BY revision_number",
            (episode_id,),
        )
        target_index = next(
            (index for index, revision in enumerate(revisions) if revision["id"] == revision_id),
            None,
        )
        if target_index is None:
            return {
                "valid": False,
                "checks": [
                    {
                        "name": "target_revision",
                        "valid": False,
                        "detail": f"revision not found in episode: {revision_id}",
                    }
                ],
                "head_revision": None,
                "manifest_sha256": None,
            }
        checks, target = self._verify_revision_rows(
            episode_id, revisions[: target_index + 1]
        )
        checks.append(
            {
                "name": "target_revision",
                "valid": bool(target and target["id"] == revision_id),
                "detail": revision_id,
            }
        )
        return {
            "valid": bool(checks) and all(check["valid"] for check in checks),
            "checks": checks,
            "head_revision": _decode_revision(target) if target else None,
            "manifest_sha256": target["manifest_sha256"] if target else None,
        }

    def verify_episode(self, episode_id: str) -> dict[str, Any]:
        episode = self.require_episode(episode_id)
        revisions = self.db.fetch_all(
            "SELECT * FROM episode_revisions WHERE episode_id=? ORDER BY revision_number",
            (episode_id,),
        )
        checks, head = self._verify_revision_rows(episode_id, revisions)
        checks.append(
            {
                "name": "head_revision",
                "valid": bool(head and episode.get("head_revision_id") == head["id"]),
                "detail": head["id"] if head else "no revisions",
            }
        )
        reviews_current, reviews_detail = self.head_reviews_are_current(
            episode_id, head
        )
        checks.append(
            {
                "name": "head_confirmed_reviews_current",
                "valid": reviews_current,
                "detail": reviews_detail,
            }
        )
        artifacts_current, artifacts_detail = self.head_argus_artifacts_are_current(
            episode_id, head
        )
        checks.append(
            {
                "name": "head_confirmed_argus_artifacts_current",
                "valid": artifacts_current,
                "detail": artifacts_detail,
            }
        )
        return {
            "valid": bool(checks) and all(check["valid"] for check in checks),
            "checks": checks,
            "head_revision": _decode_revision(head) if head else None,
            "manifest_sha256": head["manifest_sha256"] if head else None,
        }


def selection_preview(
    service: EpisodeService,
    *,
    episode_ids: Iterable[str],
    require_training_consent: bool,
) -> dict[str, Any]:
    requested = sorted(set(episode_ids))
    if requested:
        placeholders = ",".join("?" for _ in requested)
        rows = service.db.fetch_all(
            f"SELECT * FROM research_episodes WHERE id IN ({placeholders}) ORDER BY id", requested
        )
        found = {row["id"] for row in rows}
        missing = [episode_id for episode_id in requested if episode_id not in found]
    else:
        rows = service.db.fetch_all("SELECT * FROM research_episodes ORDER BY id")
        missing = []
    members: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = [
        {"episode_id": episode_id, "reason": "episode_not_found"} for episode_id in missing
    ]
    for raw in rows:
        episode = decode_row(raw) or {}
        revision = service.db.fetch_one(
            "SELECT * FROM episode_revisions WHERE id=?", (episode.get("head_revision_id"),)
        ) if episode.get("head_revision_id") else None
        reasons: list[str] = []
        if not revision:
            reasons.append("no_sealed_revision")
        if require_training_consent and not episode.get("training_consent"):
            reasons.append("training_consent_missing")
        if not episode.get("license_basis", "").strip():
            reasons.append("license_basis_missing")
        drafts = service.db.fetch_one(
            "SELECT COUNT(*) AS count FROM review_import_batches WHERE episode_id=? AND state='draft'",
            (episode["id"],),
        )
        if drafts and drafts["count"]:
            reasons.append("review_import_confirmation_pending")
        review_rights = service.db.fetch_one(
            "SELECT COUNT(*) AS count FROM review_import_batches WHERE episode_id=? AND state='confirmed' "
            "AND (training_consent=0 OR trim(license_basis)='' OR redaction_confirmed=0)",
            (episode["id"],),
        )
        if require_training_consent and review_rights and review_rights["count"]:
            reasons.append("review_training_rights_missing")
        review_redaction = service.db.fetch_one(
            "SELECT COUNT(*) AS count FROM review_import_batches r "
            "LEFT JOIN content_objects o ON o.sha256=r.raw_object_sha256 "
            "WHERE r.episode_id=? AND r.state='confirmed' "
            "AND (r.redaction_confirmed=0 OR r.raw_object_sha256 IS NULL OR o.sha256 IS NULL)",
            (episode["id"],),
        )
        if review_redaction and review_redaction["count"]:
            reasons.append("review_redaction_or_manual_binary_confirmation_missing")
        artifact_drafts = service.db.fetch_one(
            "SELECT COUNT(*) AS count FROM argus_artifact_imports "
            "WHERE episode_id=? AND state='draft'",
            (episode["id"],),
        )
        if artifact_drafts and artifact_drafts["count"]:
            reasons.append("artifact_import_confirmation_pending")
        artifact_rights = service.db.fetch_one(
            "SELECT COUNT(*) AS count FROM argus_artifact_imports "
            "WHERE episode_id=? AND state='confirmed' "
            "AND (training_consent=0 OR trim(license_basis)='' OR redaction_confirmed=0 "
            "OR (manual_redaction_required=1 AND manual_redaction_confirmed=0))",
            (episode["id"],),
        )
        if require_training_consent and artifact_rights and artifact_rights["count"]:
            reasons.append("artifact_training_rights_missing")
        reviews_current, _ = service.head_reviews_are_current(episode["id"], revision)
        if revision and not reviews_current:
            reasons.append("confirmed_reviews_not_sealed_in_head")
        artifacts_current, _ = service.head_argus_artifacts_are_current(
            episode["id"], revision
        )
        if revision and not artifacts_current:
            reasons.append("confirmed_argus_artifacts_not_sealed_in_head")
        verification = service.verify_episode(episode["id"]) if revision else {"valid": False}
        if (
            revision
            and not verification["valid"]
            and reviews_current
            and artifacts_current
        ):
            reasons.append("integrity_verification_failed")
        provenance_status = service.training_provenance_status(episode, revision)
        reasons.extend(provenance_status["reasons"])
        if reasons:
            excluded.append(
                {
                    "episode_id": episode["id"],
                    "reason": ",".join(dict.fromkeys(reasons)),
                }
            )
            continue
        members.append(
            {
                "episode_id": episode["id"],
                "revision_id": revision["id"],
                "manifest_sha256": revision["manifest_sha256"],
                "chain_sha256": revision["chain_sha256"],
                "training_provenance": provenance_status["provenance"],
            }
        )
    selection = {
        "schema_version": "flywheel.dataset-selection/1",
        "require_training_consent": require_training_consent,
        "members": members,
    }
    return {
        "eligible": members,
        "excluded": excluded,
        "counts": {"eligible": len(members), "excluded": len(excluded)},
        "selection_sha256": sha256_text(canonical_json(selection)),
    }
