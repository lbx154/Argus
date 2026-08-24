"""Verified import of team-conditioned ideation artifacts.

The Argus artifact index is the only remote file authority used here.  A
candidate portfolio is never inferred from a stopped daemon and never imported
from an unbound JSON array: ``CANDIDATES.json`` and its manifest must both be
allowlisted, byte-verified, and bound to the frozen condition/objective hashes.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from ..db import Database, utc_now
from ..integrations.argus_webapi import ArgusWebApiClient
from .argus_artifact_ingest import MAX_INDEX_FIELD_CHARS, MAX_INDEX_ITEMS, normalize_artifact_entry
from .flywheel_data import FlywheelDataError, assert_no_secrets

CANDIDATES_PATH = "CANDIDATES.json"
CANDIDATES_MANIFEST_PATH = "CANDIDATES_MANIFEST.json"
CANDIDATES_MANIFEST_SCHEMA = "flywheel.ideation-candidates/1"
MAX_CANDIDATES_BYTES = 4 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_CANDIDATES = 20
MAX_CANDIDATE_BYTES = 128 * 1024
MAX_CANDIDATE_TEXT_CHARS = 16_000
MAX_CANDIDATE_LIST_ITEMS = 100
MAX_CANDIDATE_LIST_ITEM_CHARS = 4_000
MAX_RESOURCE_PLAN_BYTES = 32 * 1024
_HEX = frozenset("0123456789abcdef")
_CANDIDATE_FIELDS = (
    "candidate_key",
    "title",
    "problem_gap",
    "core_hypothesis",
    "mechanism",
    "closest_work",
    "differentiation_claim",
    "public_or_authorized_data",
    "method",
    "strongest_baselines",
    "decisive_experiments",
    "falsifier",
    "estimated_resources",
    "elapsed_time_plan",
    "venue_fit",
    "risks",
    "ethics_and_license",
    "expected_information_gain",
    "terminal_recommendation",
    "team_specific_advantage",
    "condition_fit_counterfactual",
    "novelty_collision_test",
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "condition_sha256",
        "objective_sha256",
        "candidates_sha256",
        "candidate_count",
    }
)
_REQUIRED_TEXT_FIELDS = {
    "problem_gap": MAX_CANDIDATE_TEXT_CHARS,
    "core_hypothesis": MAX_CANDIDATE_TEXT_CHARS,
    "mechanism": MAX_CANDIDATE_TEXT_CHARS,
    "differentiation_claim": MAX_CANDIDATE_TEXT_CHARS,
    "method": MAX_CANDIDATE_TEXT_CHARS,
    "falsifier": MAX_CANDIDATE_TEXT_CHARS,
    "elapsed_time_plan": 8_000,
    "venue_fit": 8_000,
    "ethics_and_license": 8_000,
    "expected_information_gain": 8_000,
    "team_specific_advantage": 4_000,
    "condition_fit_counterfactual": 4_000,
}
_REQUIRED_TEXT_LIST_FIELDS = (
    "closest_work",
    "public_or_authorized_data",
    "strongest_baselines",
    "decisive_experiments",
    "risks",
)
# These are recommendations, not claimed experimental outcomes.  Negative and
# no-winner paths are first-class so the importer never pressures Argus to
# fabricate a positive candidate.  Case and ``_``/``-`` spelling are normalized
# only for validation; the immutable artifact itself is not rewritten.
_TERMINAL_RECOMMENDATIONS = frozenset(
    {
        "shortlist",
        "revise",
        "reject",
        "no-winner",
        "negative-result",
        "novelty-collision",
        "resource-infeasible",
        "insufficient-evidence",
        "killed",
        "deferred",
        "needs-human-decision",
        "blocked",
    }
)


class CandidateImportError(ValueError):
    """A fail-closed candidate artifact or binding error."""

    def __init__(self, code: str, message: str, *, status_code: int = 422) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class CandidateImportResult:
    imported: bool
    candidate_count: int
    candidates_sha256: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _DownloadedJson:
    value: Any
    path: str
    size: int
    sha256: str
    index_size: int
    index_sha256: str


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in _HEX for character in value.lower()
    )


def _run_row(db: Database, run_id: str) -> dict[str, Any]:
    row = db.fetch_one("SELECT * FROM ideation_runs WHERE id=?", (run_id,))
    if row is None:
        raise CandidateImportError("run_not_found", f"ideation run not found: {run_id}", status_code=404)
    return dict(row)


def _expected_condition_sha256(run: Mapping[str, Any]) -> str:
    persisted = run.get("condition_sha256")
    if _is_sha256(persisted):
        return str(persisted).lower()
    try:
        snapshot = json.loads(str(run["condition_snapshot_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise CandidateImportError(
            "condition_snapshot_invalid", "frozen condition snapshot is not valid JSON", status_code=409
        ) from exc
    return sha256_bytes(canonical_bytes(snapshot))


def _verify_objective_file(run: Mapping[str, Any]) -> None:
    expected = str(run.get("objective_sha256") or "").lower()
    if not _is_sha256(expected):
        raise CandidateImportError(
            "objective_binding_invalid", "frozen objective SHA-256 is missing or invalid", status_code=409
        )
    path = Path(str(run.get("objective_path") or ""))
    try:
        actual = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise CandidateImportError(
            "objective_unavailable", "frozen objective file cannot be verified", status_code=409
        ) from exc
    if actual != expected:
        raise CandidateImportError(
            "objective_tampered", "frozen objective bytes no longer match objective_sha256", status_code=409
        )


def _validated_manifest(
    run: Mapping[str, Any], candidates: list[dict[str, Any]], manifest: Mapping[str, Any]
) -> tuple[dict[str, Any], str, str]:
    try:
        assert_no_secrets(manifest, path="candidate_manifest")
        assert_no_secrets(candidates, path="candidates")
    except FlywheelDataError as exc:
        raise CandidateImportError("secret_detected", str(exc)) from exc
    normalized = dict(manifest)
    if set(normalized) != _MANIFEST_FIELDS:
        missing = sorted(_MANIFEST_FIELDS - set(normalized))
        unexpected = sorted(set(normalized) - _MANIFEST_FIELDS)
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if unexpected:
            detail.append("unexpected=" + ",".join(unexpected))
        raise CandidateImportError(
            "manifest_fields_invalid",
            "candidate manifest must contain exactly the five binding fields"
            + (": " + "; ".join(detail) if detail else ""),
        )
    if normalized.get("schema_version") != CANDIDATES_MANIFEST_SCHEMA:
        raise CandidateImportError(
            "manifest_schema_mismatch",
            f"candidate manifest schema must be {CANDIDATES_MANIFEST_SCHEMA}",
        )
    expected_condition = _expected_condition_sha256(run)
    received_condition = str(normalized.get("condition_sha256") or "").lower()
    if received_condition != expected_condition:
        raise CandidateImportError(
            "condition_binding_mismatch",
            "candidate manifest condition_sha256 does not match the frozen run",
            status_code=409,
        )
    expected_objective = str(run.get("objective_sha256") or "").lower()
    received_objective = str(normalized.get("objective_sha256") or "").lower()
    if received_objective != expected_objective:
        raise CandidateImportError(
            "objective_binding_mismatch",
            "candidate manifest objective_sha256 does not match the frozen run",
            status_code=409,
        )
    candidate_bytes = canonical_bytes(candidates)
    candidates_sha256 = sha256_bytes(candidate_bytes)
    received_candidates_sha256 = str(normalized.get("candidates_sha256") or "").lower()
    if received_candidates_sha256 != candidates_sha256:
        raise CandidateImportError(
            "candidate_digest_mismatch",
            "candidate manifest candidates_sha256 does not match canonical CANDIDATES.json",
            status_code=409,
        )
    count = normalized.get("candidate_count")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(candidates):
        raise CandidateImportError(
            "candidate_count_mismatch", "candidate manifest candidate_count is inconsistent"
        )
    manifest_sha256 = sha256_bytes(canonical_bytes(normalized))
    return normalized, candidates_sha256, manifest_sha256


def _required_text(
    candidate: Mapping[str, Any],
    *,
    field: str,
    position: int,
    max_chars: int,
    code: str = "candidate_text_invalid",
) -> str:
    value = candidate.get(field)
    if not isinstance(value, str) or not value.strip() or len(value) > max_chars:
        raise CandidateImportError(
            code,
            f"candidate {position} {field} must be a nonblank string of at most "
            f"{max_chars} characters",
        )
    return value


def _required_text_list(
    candidate: Mapping[str, Any],
    *,
    field: str,
    position: int,
    max_items: int = MAX_CANDIDATE_LIST_ITEMS,
    max_item_chars: int = MAX_CANDIDATE_LIST_ITEM_CHARS,
    code: str = "candidate_list_invalid",
) -> list[str]:
    value = candidate.get(field)
    if not isinstance(value, list) or not 1 <= len(value) <= max_items:
        raise CandidateImportError(
            code,
            f"candidate {position} {field} must be a nonempty array of at most "
            f"{max_items} strings",
        )
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip() or len(item) > max_item_chars:
            raise CandidateImportError(
                code,
                f"candidate {position} {field} entries must be nonblank strings of at most "
                f"{max_item_chars} characters",
            )
        clean = item.strip()
        if clean in normalized:
            raise CandidateImportError(
                code,
                f"candidate {position} {field} must not contain duplicate entries",
            )
        normalized.append(clean)
    return value


def _validate_meaningful_json(
    value: Any,
    *,
    position: int,
    path: str,
    depth: int = 0,
    nonnegative_numbers: bool = False,
    code: str = "estimated_resources_invalid",
) -> None:
    """Reject placeholder/null resource metadata while remaining domain-neutral."""

    if depth > 8:
        raise CandidateImportError(
            code,
            f"candidate {position} {path} exceeds the maximum nesting depth",
        )
    if value is None:
        raise CandidateImportError(
            code,
            f"candidate {position} {path} must not contain null placeholders",
        )
    if isinstance(value, str):
        if not value.strip() or len(value) > MAX_CANDIDATE_LIST_ITEM_CHARS:
            raise CandidateImportError(
                code,
                f"candidate {position} {path} strings must be nonblank and bounded",
            )
        return
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise CandidateImportError(
                code,
                f"candidate {position} {path} numbers must be finite",
            )
        if nonnegative_numbers and value < 0:
            raise CandidateImportError(
                code,
                f"candidate {position} {path} resource quantities must not be negative",
            )
        return
    if isinstance(value, list):
        if not 1 <= len(value) <= MAX_CANDIDATE_LIST_ITEMS:
            raise CandidateImportError(
                code,
                f"candidate {position} {path} arrays must be nonempty and bounded",
            )
        for index, item in enumerate(value):
            _validate_meaningful_json(
                item,
                position=position,
                path=f"{path}[{index}]",
                depth=depth + 1,
                nonnegative_numbers=nonnegative_numbers,
                code=code,
            )
        return
    if isinstance(value, dict):
        if not 1 <= len(value) <= MAX_CANDIDATE_LIST_ITEMS:
            raise CandidateImportError(
                code,
                f"candidate {position} {path} objects must be nonempty and bounded",
            )
        for key, item in value.items():
            if not isinstance(key, str) or not key.strip() or len(key) > 200:
                raise CandidateImportError(
                    code,
                    f"candidate {position} {path} object keys must be nonblank and bounded",
                )
            _validate_meaningful_json(
                item,
                position=position,
                path=f"{path}.{key}",
                depth=depth + 1,
                nonnegative_numbers=nonnegative_numbers,
                code=code,
            )
        return
    raise CandidateImportError(
        code,
        f"candidate {position} {path} contains an unsupported value type",
    )


def _normalized_candidates(candidates: list[dict[str, Any]], run_id: str) -> list[dict[str, Any]]:
    if not 1 <= len(candidates) <= MAX_CANDIDATES:
        raise CandidateImportError(
            "candidate_count_out_of_range", f"candidate count must be between 1 and {MAX_CANDIDATES}"
        )
    try:
        namespace = uuid.UUID(run_id)
    except ValueError as exc:
        raise CandidateImportError("run_id_invalid", "ideation run id is not a UUID", status_code=409) from exc
    output: list[dict[str, Any]] = []
    keys: set[str] = set()
    for position, candidate in enumerate(candidates):
        if not isinstance(candidate, dict):
            raise CandidateImportError(
                "candidate_not_object", f"candidate {position} must be a JSON object"
            )
        if len(canonical_bytes(candidate)) > MAX_CANDIDATE_BYTES:
            raise CandidateImportError(
                "candidate_too_large", f"candidate {position} exceeds {MAX_CANDIDATE_BYTES} bytes"
            )
        missing = [field for field in _CANDIDATE_FIELDS if field not in candidate]
        if missing:
            raise CandidateImportError(
                "candidate_fields_missing",
                f"candidate {position} missing fields: {', '.join(missing)}",
            )
        key = _required_text(
            candidate,
            field="candidate_key",
            position=position,
            max_chars=100,
            code="candidate_identity_invalid",
        ).strip()
        title = _required_text(
            candidate,
            field="title",
            position=position,
            max_chars=500,
            code="candidate_identity_invalid",
        ).strip()
        if key in keys:
            raise CandidateImportError("candidate_key_duplicate", f"duplicate candidate_key: {key}")
        keys.add(key)
        for field, max_chars in _REQUIRED_TEXT_FIELDS.items():
            _required_text(
                candidate,
                field=field,
                position=position,
                max_chars=max_chars,
                code=(
                    "condition_fit_invalid"
                    if field in {"team_specific_advantage", "condition_fit_counterfactual"}
                    else "candidate_text_invalid"
                ),
            )
        validated_lists = {
            field: _required_text_list(candidate, field=field, position=position)
            for field in _REQUIRED_TEXT_LIST_FIELDS
        }
        evidence_refs = validated_lists["closest_work"]
        resources = candidate.get("estimated_resources")
        if not isinstance(resources, dict):
            raise CandidateImportError(
                "estimated_resources_invalid",
                f"candidate {position} estimated_resources must be a nonempty object",
            )
        if len(canonical_bytes(resources)) > MAX_RESOURCE_PLAN_BYTES:
            raise CandidateImportError(
                "estimated_resources_invalid",
                f"candidate {position} estimated_resources exceeds {MAX_RESOURCE_PLAN_BYTES} bytes",
            )
        _validate_meaningful_json(
            resources,
            position=position,
            path="estimated_resources",
            nonnegative_numbers=True,
        )
        recommendation = candidate.get("terminal_recommendation")
        if not isinstance(recommendation, str) or not recommendation.strip():
            raise CandidateImportError(
                "terminal_recommendation_invalid",
                f"candidate {position} terminal_recommendation must be a supported value",
            )
        normalized_recommendation = recommendation.strip().lower().replace("_", "-")
        if normalized_recommendation not in _TERMINAL_RECOMMENDATIONS:
            allowed = ", ".join(sorted(_TERMINAL_RECOMMENDATIONS))
            raise CandidateImportError(
                "terminal_recommendation_invalid",
                f"candidate {position} terminal_recommendation must be one of: {allowed}",
            )
        collision = candidate.get("novelty_collision_test")
        if not isinstance(collision, dict):
            raise CandidateImportError(
                "novelty_collision_test_invalid",
                f"candidate {position} novelty_collision_test must be an object",
            )
        for field, max_chars in (("search_cutoff", 256), ("falsifier", 8_000)):
            _required_text(
                collision,
                field=field,
                position=position,
                max_chars=max_chars,
                code="novelty_collision_test_invalid",
            )
        _required_text_list(
            collision,
            field="closest_source_ids",
            position=position,
            max_item_chars=2_048,
            code="novelty_collision_test_invalid",
        )
        for field, value in collision.items():
            if field in {"search_cutoff", "falsifier", "closest_source_ids"}:
                continue
            _validate_meaningful_json(
                value,
                position=position,
                path=f"novelty_collision_test.{field}",
                code="novelty_collision_test_invalid",
            )
        output.append(
            {
                "id": str(uuid.uuid5(namespace, key)),
                "key": key,
                "title": title,
                "candidate": candidate,
                "evidence_refs": evidence_refs[:100],
            }
        )
    return output


def _freeze_candidate_payload(
    db: Database,
    *,
    run_id: str,
    candidates: list[dict[str, Any]],
    manifest: dict[str, Any],
    imported_from: Literal["argus_artifact", "human_entered"],
    provenance_receipt: Mapping[str, Any] | None = None,
) -> CandidateImportResult:
    """Validate one portfolio and atomically freeze it into an ideation run."""

    if imported_from == "argus_artifact" and not provenance_receipt:
        raise CandidateImportError(
            "argus_provenance_missing",
            "Argus artifact candidates require a verified allowlist/download receipt",
            status_code=409,
        )
    if imported_from == "human_entered" and provenance_receipt is not None:
        raise CandidateImportError(
            "candidate_provenance_invalid",
            "Human-entered candidates cannot attach an Argus artifact receipt",
            status_code=409,
        )
    if provenance_receipt is not None:
        try:
            assert_no_secrets(provenance_receipt, path="candidate_provenance_receipt")
        except FlywheelDataError as exc:
            raise CandidateImportError("secret_detected", str(exc)) from exc
    run = _run_row(db, run_id)
    _verify_objective_file(run)
    normalized_manifest, candidates_sha256, manifest_sha256 = _validated_manifest(
        run, candidates, manifest
    )
    normalized = _normalized_candidates(candidates, run_id)
    now = utc_now()
    manifest_json = canonical_bytes(normalized_manifest).decode("utf-8").rstrip("\n")
    with db.transaction() as transaction:
        locked = transaction.execute("SELECT * FROM ideation_runs WHERE id=?", (run_id,)).fetchone()
        if locked is None:  # pragma: no cover - writer reservation invariant
            raise CandidateImportError("run_not_found", f"ideation run not found: {run_id}", status_code=404)
        existing = transaction.execute(
            "SELECT COUNT(*) AS count FROM generated_idea_candidates WHERE ideation_run_id=?",
            (run_id,),
        ).fetchone()
        existing_count = int(existing["count"] if existing is not None else 0)
        if existing_count:
            existing_digest = str(locked["candidate_artifact_sha256"] or "").lower()
            existing_manifest = (
                str(locked["candidate_manifest_json"] or "")
                if "candidate_manifest_json" in locked.keys()
                else ""
            )
            existing_manifest_sha256 = ""
            if existing_manifest:
                try:
                    existing_manifest_sha256 = sha256_bytes(
                        canonical_bytes(json.loads(existing_manifest))
                    )
                except (TypeError, json.JSONDecodeError):
                    existing_manifest_sha256 = "invalid"
            if existing_digest == candidates_sha256 and (
                not existing_manifest or existing_manifest_sha256 == manifest_sha256
            ):
                return CandidateImportResult(
                    imported=False,
                    candidate_count=existing_count,
                    candidates_sha256=candidates_sha256,
                    manifest_sha256=manifest_sha256,
                )
            raise CandidateImportError(
                "immutable_candidates_conflict",
                "candidates are already frozen with a different artifact binding",
                status_code=409,
            )
        for item in normalized:
            transaction.execute(
                "INSERT INTO generated_idea_candidates(id,ideation_run_id,candidate_key,title,"
                "candidate_json,evidence_refs_json,imported_from,artifact_sha256,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    item["id"],
                    run_id,
                    item["key"],
                    item["title"],
                    json.dumps(item["candidate"], ensure_ascii=False),
                    json.dumps(item["evidence_refs"], ensure_ascii=False),
                    imported_from,
                    candidates_sha256,
                    now,
                    now,
                ),
            )
        if "candidate_manifest_json" in locked.keys():
            transaction.execute(
                "UPDATE ideation_runs SET state='awaiting_labels',candidate_artifact_sha256=?,"
                "candidate_manifest_json=?,updated_at=? WHERE id=?",
                (candidates_sha256, manifest_json, now, run_id),
            )
        else:  # Compatibility for a database migrating concurrently from v7.
            transaction.execute(
                "UPDATE ideation_runs SET state='awaiting_labels',candidate_artifact_sha256=?,"
                "updated_at=? WHERE id=?",
                (candidates_sha256, now, run_id),
            )
        transaction.execute(
            "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
            "VALUES('ideation','ideation.candidates_imported','info','ideation_run',?,?,?)",
            (
                run_id,
                json.dumps(
                    {
                        "count": len(normalized),
                        "imported_from": imported_from,
                        "artifact_sha256": candidates_sha256,
                        "manifest_sha256": manifest_sha256,
                        "provenance_receipt": dict(provenance_receipt or {}),
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
    return CandidateImportResult(
        imported=True,
        candidate_count=len(normalized),
        candidates_sha256=candidates_sha256,
        manifest_sha256=manifest_sha256,
    )


def import_human_candidate_payload(
    db: Database,
    *,
    run_id: str,
    candidates: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> CandidateImportResult:
    """Freeze a public/operator-supplied portfolio with honest provenance."""

    return _freeze_candidate_payload(
        db,
        run_id=run_id,
        candidates=candidates,
        manifest=manifest,
        imported_from="human_entered",
    )


def _artifact_entries(index: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    rows = list(index)
    if len(rows) > MAX_INDEX_ITEMS:
        raise CandidateImportError(
            "artifact_index_too_large", f"Argus artifact index exceeds {MAX_INDEX_ITEMS} items"
        )
    output: dict[str, dict[str, Any]] = {}
    for ordinal, raw in enumerate(rows, start=1):
        entry = normalize_artifact_entry(raw)
        path = entry["path"]
        oversized = [
            key
            for key, value in entry.items()
            if isinstance(value, str) and len(value) > MAX_INDEX_FIELD_CHARS
        ]
        if oversized:
            raise CandidateImportError(
                "artifact_index_invalid",
                f"artifact index entry {ordinal} has oversized fields: {', '.join(oversized)}",
            )
        try:
            ArgusWebApiClient._validate_artifact_path(path)
            assert_no_secrets(entry, path="candidate_artifact_index")
        except (ValueError, FlywheelDataError) as exc:
            raise CandidateImportError(
                "artifact_index_invalid", f"artifact index entry {ordinal} is unsafe: {exc}"
            ) from exc
        if path in output:
            raise CandidateImportError(
                "artifact_index_duplicate", f"artifact index contains duplicate path: {path}"
            )
        output[path] = entry
    return output


def _download_json(
    client: ArgusWebApiClient,
    *,
    project_id: str,
    entry: Mapping[str, Any],
    path: str,
    max_bytes: int,
) -> _DownloadedJson:
    if entry.get("exists") is not True:
        raise CandidateImportError("artifact_missing", f"Argus artifact is unavailable: {path}")
    declared_size = entry.get("size")
    reported_sha = entry.get("sha256")
    if not isinstance(declared_size, int) or isinstance(declared_size, bool) or not _is_sha256(
        reported_sha
    ):
        raise CandidateImportError(
            "artifact_receipt_missing",
            f"Argus artifact index must provide valid size and SHA-256 for {path}",
        )
    if declared_size > max_bytes:
        raise CandidateImportError("artifact_too_large", f"{path} exceeds the {max_bytes}-byte limit")
    download = client.download_artifact(project_id, path, max_bytes=max_bytes)
    if download.size != declared_size:
        raise CandidateImportError(
            "artifact_length_mismatch", f"downloaded {path} length differs from its fresh index"
        )
    if download.sha256 != str(reported_sha).lower():
        raise CandidateImportError(
            "artifact_transport_digest_mismatch",
            f"downloaded {path} SHA-256 differs from its fresh index",
            status_code=409,
        )
    try:
        value = json.loads(download.content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateImportError("artifact_json_invalid", f"{path} is not valid UTF-8 JSON") from exc
    return _DownloadedJson(
        value=value,
        path=path,
        size=download.size,
        sha256=download.sha256,
        index_size=declared_size,
        index_sha256=str(reported_sha).lower(),
    )


def import_argus_candidate_artifacts(
    db: Database,
    *,
    run_id: str,
    client: ArgusWebApiClient,
    artifact_index: Iterable[dict[str, Any]],
) -> CandidateImportResult:
    """Download, bind, and freeze the allowlisted final Argus candidate packet."""

    run = _run_row(db, run_id)
    campaign_id = str(run.get("campaign_id") or "")
    campaign = db.fetch_one("SELECT * FROM campaigns WHERE id=?", (campaign_id,)) if campaign_id else None
    project_id = str(campaign["argus_project_id"] or "") if campaign is not None else ""
    if not project_id:
        raise CandidateImportError(
            "argus_binding_missing",
            "ideation run has no attached Argus project",
            status_code=409,
        )
    entries = _artifact_entries(artifact_index)
    missing = [
        path
        for path in (CANDIDATES_PATH, CANDIDATES_MANIFEST_PATH)
        if path not in entries
    ]
    if missing:
        raise CandidateImportError(
            "candidate_artifacts_missing",
            "required allowlisted Argus artifacts are missing: " + ", ".join(missing),
        )
    candidates_download = _download_json(
        client,
        project_id=project_id,
        entry=entries[CANDIDATES_PATH],
        path=CANDIDATES_PATH,
        max_bytes=MAX_CANDIDATES_BYTES,
    )
    manifest_download = _download_json(
        client,
        project_id=project_id,
        entry=entries[CANDIDATES_MANIFEST_PATH],
        path=CANDIDATES_MANIFEST_PATH,
        max_bytes=MAX_MANIFEST_BYTES,
    )
    raw_candidates = candidates_download.value
    raw_manifest = manifest_download.value
    if not isinstance(raw_candidates, list) or not all(
        isinstance(candidate, dict) for candidate in raw_candidates
    ):
        raise CandidateImportError(
            "candidates_shape_invalid", "CANDIDATES.json must be an array of objects"
        )
    if not isinstance(raw_manifest, dict):
        raise CandidateImportError(
            "manifest_shape_invalid", "CANDIDATES_MANIFEST.json must be an object"
        )
    # The manifest binds the canonical CANDIDATES bytes.  Requiring the raw
    # streamed digest to match as well proves Argus emitted that canonical file
    # rather than a logically equivalent but unbound serialization.
    if str(raw_manifest.get("candidates_sha256") or "").lower() != candidates_download.sha256:
        raise CandidateImportError(
            "candidate_transport_binding_mismatch",
            "downloaded CANDIDATES.json bytes do not match manifest.candidates_sha256",
            status_code=409,
        )
    receipt = {
        "transport": "argus_allowlisted_artifact_download",
        "argus_project_id": project_id,
        "artifact_index_sha256": sha256_bytes(
            canonical_bytes(
                {
                    CANDIDATES_PATH: entries[CANDIDATES_PATH],
                    CANDIDATES_MANIFEST_PATH: entries[CANDIDATES_MANIFEST_PATH],
                }
            )
        ),
        "artifacts": [
            {
                "path": item.path,
                "size": item.size,
                "sha256": item.sha256,
                "index_size": item.index_size,
                "index_sha256": item.index_sha256,
            }
            for item in (candidates_download, manifest_download)
        ],
    }
    return _freeze_candidate_payload(
        db,
        run_id=run_id,
        candidates=raw_candidates,
        manifest=raw_manifest,
        imported_from="argus_artifact",
        provenance_receipt=receipt,
    )


__all__ = [
    "CANDIDATES_MANIFEST_PATH",
    "CANDIDATES_MANIFEST_SCHEMA",
    "CANDIDATES_PATH",
    "CandidateImportError",
    "CandidateImportResult",
    "canonical_bytes",
    "import_argus_candidate_artifacts",
    "import_human_candidate_payload",
]
