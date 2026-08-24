"""Team-conditioned ideation, labeling, outcomes and dataset-loop API.

This router is separate from the Argus lifecycle router so personalization and
training-data collection cannot bypass the normal campaign Start gate.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Mapping

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

from .api import (
    verify_conditioned_ideation_candidate_provenance,
    verify_training_campaign_provenance,
)
from .db import Database, decode_row, decode_rows, utc_now
from .services.campaign_binding import build_campaign_binding
from .services.candidate_import import (
    CandidateImportError,
    import_human_candidate_payload,
)
from .services.flywheel_data import FlywheelDataError, assert_no_secrets
from .services.ideation import (
    compile_candidate_research_objective,
    compile_ideation_objective,
    write_immutable_candidate_objective,
    write_immutable_objective,
)

_SAFE_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,79}$")
_DIMENSIONS = (
    "novelty_evidence",
    "falsifiability",
    "resource_fit",
    "venue_fit",
    "methodological_soundness",
    "integrity_risk",
    "expected_information_gain",
)
class TeamProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    expertise: list[str] = Field(default_factory=list, max_length=64)
    methods: list[str] = Field(default_factory=list, max_length=64)
    data_access: list[str] = Field(default_factory=list, max_length=64)
    constraints: dict[str, Any] = Field(default_factory=dict)
    goals: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    training_consent: StrictBool = False
    license_basis: str = Field(default="", max_length=1_000)
    enabled: StrictBool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "license_basis")
    @classmethod
    def clean_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("expertise", "methods", "data_access")
    @classmethod
    def clean_lists(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        for value in values:
            clean = value.strip()
            if not clean or len(clean) > 1_000:
                raise ValueError("entries must be nonblank and at most 1000 characters")
            if clean not in result:
                result.append(clean)
        return result

    @field_validator("constraints", "goals", "policy", "metadata")
    @classmethod
    def bound_objects(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(_canonical_json(value)) > 64 * 1024:
            raise ValueError("object exceeds 64 KiB")
        return value

    @field_validator("license_basis")
    @classmethod
    def require_license_for_training(cls, value: str, info: Any) -> str:
        # Cross-field enforcement is repeated in the route after full model
        # validation because field ordering should never decide eligibility.
        return value


class TeamProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    expertise: list[str] | None = Field(default=None, max_length=64)
    methods: list[str] | None = Field(default=None, max_length=64)
    data_access: list[str] | None = Field(default=None, max_length=64)
    constraints: dict[str, Any] | None = None
    goals: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    training_consent: StrictBool | None = None
    license_basis: str | None = Field(default=None, max_length=1_000)
    enabled: StrictBool | None = None
    metadata: dict[str, Any] | None = None


class IdeationRunIn(BaseModel):
    team_profile_id: str = Field(min_length=1, max_length=100)
    venue_key: str = Field(min_length=1, max_length=100)
    deadline_id: int | None = None
    resource_id: str | None = Field(default=None, max_length=100)
    connection_id: str | None = Field(default=None, max_length=100)
    candidate_count: int = Field(default=10, ge=3, le=20)
    finalist_count: int = Field(default=5, ge=1, le=20)
    completion_target: str = Field(default="", max_length=4_000)
    source_snapshot_ref: str = Field(default="", max_length=2_048)
    source_snapshot_sha256: str = Field(default="", max_length=64)
    create_campaign: StrictBool = True
    preflight_attestations: dict[str, StrictBool] = Field(default_factory=dict)

    @field_validator("completion_target", "source_snapshot_ref", "source_snapshot_sha256")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_source_snapshot_binding(self) -> "IdeationRunIn":
        has_ref = bool(self.source_snapshot_ref)
        has_digest = bool(self.source_snapshot_sha256)
        if has_ref != has_digest:
            raise ValueError(
                "source_snapshot_ref and source_snapshot_sha256 must be supplied together"
            )
        if has_digest and not re.fullmatch(r"[0-9a-fA-F]{64}", self.source_snapshot_sha256):
            raise ValueError("source_snapshot_sha256 must be exactly 64 hexadecimal characters")
        return self


class CandidateImportIn(BaseModel):
    candidates: list[dict[str, Any]] = Field(min_length=1, max_length=20)
    # This is the public/operator route.  Only the coordinator's verified
    # allowlist/download path may assign ``argus_artifact`` provenance.
    imported_from: Literal["human_entered"] = "human_entered"
    artifact_sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    manifest: dict[str, Any]


class CandidateCampaignIn(BaseModel):
    completion_target: str = Field(default="", max_length=4_000)
    stop_criteria: list[str] = Field(default_factory=list, max_length=32)
    title: str = Field(default="", max_length=500)

    @field_validator("completion_target", "title")
    @classmethod
    def clean_candidate_campaign_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("stop_criteria")
    @classmethod
    def clean_candidate_stops(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in values:
            value = raw.strip()
            if not value or len(value) > 1_000:
                raise ValueError("stop criteria must be nonblank and at most 1000 characters")
            if value not in cleaned:
                cleaned.append(value)
        return cleaned


class CandidateLabelIn(BaseModel):
    labeler_alias: str = Field(min_length=1, max_length=80)
    decision: Literal["shortlist", "revise", "reject", "abstain"]
    dimensions: dict[str, float | None]
    rationale_redacted: str = Field(min_length=1, max_length=20_000)
    redaction_confirmed: StrictBool
    training_consent: StrictBool = False
    license_basis: str = Field(default="", max_length=1_000)

    @field_validator("labeler_alias")
    @classmethod
    def valid_alias(cls, value: str) -> str:
        return _alias(value)

    @field_validator("rationale_redacted", "license_basis")
    @classmethod
    def clean_label_text(cls, value: str) -> str:
        return value.strip()


class PairwiseLabelIn(BaseModel):
    left_candidate_id: str
    right_candidate_id: str
    winner: Literal["left", "right", "tie", "abstain"]
    labeler_alias: str = Field(min_length=1, max_length=80)
    rationale_redacted: str = Field(min_length=1, max_length=20_000)
    redaction_confirmed: StrictBool
    training_consent: StrictBool = False
    license_basis: str = Field(default="", max_length=1_000)

    @field_validator("labeler_alias")
    @classmethod
    def valid_alias(cls, value: str) -> str:
        return _alias(value)


class ReviewerFeedbackIn(BaseModel):
    reviewer: str = Field(min_length=1, max_length=80)
    score: float | None = Field(default=None, ge=-100, le=100)
    score_label: str = Field(default="", max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=100)
    recommendation: str = Field(default="", max_length=500)
    opinion_redacted: str = Field(min_length=1, max_length=30_000)
    questions: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("reviewer")
    @classmethod
    def valid_reviewer(cls, value: str) -> str:
        return _alias(value)

    @field_validator("opinion_redacted", "score_label", "recommendation")
    @classmethod
    def clean_review_text(cls, value: str) -> str:
        return value.strip()


class SubmissionIn(BaseModel):
    campaign_id: str
    submission_version: str = Field(min_length=1, max_length=200)
    submission_ref: str = Field(default="", max_length=500)
    submitted_at: str | None = None
    reviewer_feedback: list[ReviewerFeedbackIn] = Field(min_length=1, max_length=30)
    decision: Literal["accept", "reject", "withdraw", "withdrawn", "pending", "other"] = "pending"
    consent_to_training_export: StrictBool = False
    review_license_confirmed: StrictBool = False
    redaction_confirmed: StrictBool = False

    @field_validator("submission_version", "submission_ref")
    @classmethod
    def clean_submission_text(cls, value: str) -> str:
        return value.strip()


class FollowUpIn(BaseModel):
    actor: str = Field(min_length=1, max_length=500)
    approval_reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("actor", "approval_reason")
    @classmethod
    def clean_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


def create_workflow_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/team-profiles")
    def list_profiles(request: Request, include_disabled: bool = False) -> list[dict[str, Any]]:
        where = "" if include_disabled else " WHERE enabled=1"
        return decode_rows(
            _db(request).fetch_all(f"SELECT * FROM team_profiles{where} ORDER BY updated_at DESC")
        )

    @router.post("/team-profiles", status_code=201)
    def create_profile(body: TeamProfileIn, request: Request) -> dict[str, Any]:
        _validate_training_license(body.training_consent, body.license_basis)
        _assert_no_secrets_http(body.model_dump(mode="json"))
        profile_id = str(uuid.uuid4())
        now = utc_now()
        db = _db(request)
        db.execute(
            "INSERT INTO team_profiles(id,name,expertise_json,methods_json,data_access_json,"
            "constraints_json,goals_json,policy_json,training_consent,license_basis,enabled,"
            "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                profile_id,
                body.name,
                _json(body.expertise),
                _json(body.methods),
                _json(body.data_access),
                _json(body.constraints),
                _json(body.goals),
                _json(body.policy),
                int(body.training_consent),
                body.license_basis,
                int(body.enabled),
                _json(body.metadata),
                now,
                now,
            ),
        )
        db.append_event(
            "ideation",
            "team_profile.created",
            entity_type="team_profile",
            entity_id=profile_id,
            payload={"name": body.name, "training_consent": body.training_consent},
        )
        return _profile(db, profile_id)

    @router.patch("/team-profiles/{profile_id}")
    def patch_profile(
        profile_id: str, body: TeamProfilePatch, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        current = _profile(db, profile_id)
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            return current
        try:
            normalized = TeamProfileIn(
                name=changes.get("name", current["name"]),
                expertise=changes.get("expertise", current["expertise"]),
                methods=changes.get("methods", current["methods"]),
                data_access=changes.get("data_access", current["data_access"]),
                constraints=changes.get("constraints", current["constraints"]),
                goals=changes.get("goals", current["goals"]),
                policy=changes.get("policy", current["policy"]),
                training_consent=changes.get(
                    "training_consent", current["training_consent"]
                ),
                license_basis=changes.get("license_basis", current["license_basis"]),
                enabled=changes.get("enabled", current["enabled"]),
                metadata=changes.get("metadata", current["metadata"]),
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        _validate_training_license(normalized.training_consent, normalized.license_basis)
        normalized_values = normalized.model_dump(mode="json")
        _assert_no_secrets_http(normalized_values)
        json_fields = {
            "expertise": "expertise_json",
            "methods": "methods_json",
            "data_access": "data_access_json",
            "constraints": "constraints_json",
            "goals": "goals_json",
            "policy": "policy_json",
            "metadata": "metadata_json",
        }
        columns: dict[str, Any] = {}
        for key in changes:
            value = normalized_values[key]
            if key in json_fields:
                columns[json_fields[key]] = _json(value)
            elif key in {"training_consent", "enabled"}:
                columns[key] = int(value)
            elif key in {"name", "license_basis"}:
                columns[key] = value.strip() if isinstance(value, str) else value
        columns["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in columns)
        db.execute(
            f"UPDATE team_profiles SET {assignments} WHERE id=?",
            [*columns.values(), profile_id],
        )
        db.append_event(
            "ideation",
            "team_profile.updated",
            entity_type="team_profile",
            entity_id=profile_id,
            payload={"fields": sorted(changes)},
        )
        return _profile(db, profile_id)

    @router.get("/ideation/runs")
    def list_runs(
        request: Request,
        team_profile_id: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if team_profile_id:
            where = " WHERE ir.team_profile_id=?"
            params.append(team_profile_id)
        params.append(limit)
        rows = _db(request).fetch_all(
            "SELECT ir.*,tp.name AS team_name,v.venue_key,v.display_name AS venue_name "
            "FROM ideation_runs ir JOIN team_profiles tp ON tp.id=ir.team_profile_id "
            "JOIN venues v ON v.id=ir.venue_id"
            f"{where} ORDER BY ir.created_at DESC LIMIT ?",
            params,
        )
        return decode_rows(rows)

    @router.post("/ideation/runs", status_code=201)
    def create_run(body: IdeationRunIn, request: Request) -> dict[str, Any]:
        db = _db(request)
        profile = _profile(db, body.team_profile_id)
        if not profile["enabled"]:
            raise HTTPException(409, "Team profile is disabled")
        team_origin = _team_origin(db, profile)
        venue_raw = db.fetch_one("SELECT * FROM venues WHERE venue_key=?", (body.venue_key,))
        if not venue_raw:
            raise HTTPException(404, f"Venue not found: {body.venue_key}")
        venue = decode_row(venue_raw) or {}
        deadline = _resolve_deadline(db, venue["id"], body.deadline_id)
        resource = _optional_row(db, "resources", body.resource_id)
        _optional_row(db, "connections", body.connection_id)
        if body.finalist_count > body.candidate_count:
            raise HTTPException(422, "finalist_count cannot exceed candidate_count")
        try:
            compiled = compile_ideation_objective(
                team_profile=profile,
                venue=venue,
                deadline=deadline,
                resource=resource,
                team_origin=team_origin,
                run_options={
                    "candidate_count": body.candidate_count,
                    "finalist_count": body.finalist_count,
                    "completion_target": body.completion_target
                    or "Produce a falsifiable, evidence-backed portfolio for human selection; NO_WINNER is valid.",
                    "source_snapshot_ref": body.source_snapshot_ref,
                    "source_snapshot_sha256": body.source_snapshot_sha256,
                    "preflight_attestations": body.preflight_attestations,
                },
            )
            objective_path = write_immutable_objective(
                request.app.state.settings.data_dir / "ideation-objectives", compiled
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        run_id = str(uuid.uuid4())
        campaign_id = str(uuid.uuid4()) if body.create_campaign else None
        now = utc_now()
        training_consent = bool(profile["training_consent"])
        license_basis = str(profile["license_basis"] or "")
        with db.transaction() as transaction:
            duplicate = transaction.execute(
                "SELECT id FROM ideation_runs WHERE objective_sha256=?",
                (compiled.objective_sha256,),
            ).fetchone()
            if duplicate:
                raise HTTPException(
                    409,
                    "An identical frozen ideation run already exists: " + str(duplicate["id"]),
                )
            # ``ideation_runs.campaign_id`` is a real foreign key.  Insert the
            # optional idle campaign first, then bind the immutable run to it
            # in the same transaction.
            if campaign_id:
                config = {
                    "campaign_kind": "conditioned_ideation",
                    "ideation_run_id": run_id,
                    "team_profile_id": profile["id"],
                    "condition_sha256": compiled.condition_sha256,
                    "objective_sha256": compiled.objective_sha256,
                    "preflight_attestations": body.preflight_attestations,
                    "backend": "connection-default",
                    "oral_is_aspiration_only": True,
                    "automatic_submission_allowed": False,
                }
                transaction.execute(
                    "INSERT INTO campaigns(id,venue_id,idea_id,deadline_id,connection_id,resource_id,"
                    "title,objective,schedule_state,execution_state,science_state,review_state,"
                    "integrity_state,deadline_state,progress,last_summary,viewer_score,"
                    "reviewer_scores_json,config_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        campaign_id,
                        venue["id"],
                        None,
                        deadline.get("id") if deadline else None,
                        body.connection_id,
                        body.resource_id,
                        f"{venue['venue_key']} · {profile['name']} conditioned ideation",
                        compiled.objective,
                        "manual",
                        "idle",
                        "candidate",
                        "not_requested",
                        "unchecked",
                        "on_track",
                        0,
                        "Conditioned objective frozen; explicit Start approval is still required.",
                        None,
                        "[]",
                        _json(config),
                        now,
                        now,
                    ),
                )
            transaction.execute(
                "INSERT INTO ideation_runs(id,team_profile_id,venue_id,deadline_id,resource_id,"
                "connection_id,campaign_id,state,condition_schema_version,condition_snapshot_json,"
                "condition_sha256,objective_sha256,objective_path,source_snapshot_ref,source_snapshot_sha256,"
                "training_consent,license_basis,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    profile["id"],
                    venue["id"],
                    deadline.get("id") if deadline else None,
                    body.resource_id,
                    body.connection_id,
                    campaign_id,
                    "campaign_created" if campaign_id else "objective_ready",
                    compiled.condition_snapshot["schema_version"],
                    _json(compiled.condition_snapshot),
                    compiled.condition_sha256,
                    compiled.objective_sha256,
                    str(objective_path),
                    body.source_snapshot_ref,
                    body.source_snapshot_sha256.lower(),
                    int(training_consent),
                    license_basis,
                    now,
                    now,
                ),
            )
            transaction.execute(
                "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    "ideation",
                    "ideation.run_created",
                    "info",
                    "ideation_run",
                    run_id,
                    _json(
                        {
                            "team_profile_id": profile["id"],
                            "venue_key": venue["venue_key"],
                            "condition_sha256": compiled.condition_sha256,
                            "objective_sha256": compiled.objective_sha256,
                            "campaign_id": campaign_id,
                            "launch_triggered": False,
                        }
                    ),
                    now,
                ),
            )
        result = _run_detail(db, run_id)
        result["objective"] = compiled.objective
        result["condition_sha256"] = compiled.condition_sha256
        result["launch_triggered"] = False
        return result

    @router.get("/ideation/runs/{run_id}")
    def run_detail(run_id: str, request: Request) -> dict[str, Any]:
        return _run_detail(_db(request), run_id)

    @router.post("/ideation/runs/{run_id}/candidates", status_code=201)
    def import_candidates(
        run_id: str, body: CandidateImportIn, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        if body.artifact_sha256.lower() != str(body.manifest.get("candidates_sha256") or "").lower():
            raise HTTPException(
                409,
                "artifact_sha256 must match manifest.candidates_sha256",
            )
        try:
            receipt = import_human_candidate_payload(
                db,
                run_id=run_id,
                candidates=body.candidates,
                manifest=body.manifest,
            )
        except CandidateImportError as exc:
            raise HTTPException(
                exc.status_code, detail={"code": exc.code, "message": str(exc)}
            ) from exc
        if not receipt.imported:
            raise HTTPException(
                409,
                "Candidates are already frozen with the same artifact binding",
            )
        return _run_detail(db, run_id)

    @router.post("/ideation/candidates/{candidate_id}/campaign", status_code=201)
    def create_candidate_campaign(
        candidate_id: str, body: CandidateCampaignIn, request: Request
    ) -> dict[str, Any]:
        """Freeze one generated direction into its own idle, non-seed campaign."""

        db = _db(request)
        candidate_row = decode_row(
            db.fetch_one(
                "SELECT * FROM generated_idea_candidates WHERE id=?", (candidate_id,)
            )
        )
        if not candidate_row:
            raise HTTPException(404, f"Conditioned candidate not found: {candidate_id}")
        run = _run_detail(db, candidate_row["ideation_run_id"])
        manifest = run.get("candidate_manifest")
        if not isinstance(manifest, Mapping) or not manifest:
            raise HTTPException(409, "Candidate artifact manifest is not frozen")
        expected_bindings = {
            "condition_sha256": run["condition_sha256"],
            "objective_sha256": run["objective_sha256"],
            "candidates_sha256": run["candidate_artifact_sha256"],
        }
        mismatches = [
            key
            for key, expected in expected_bindings.items()
            if str(manifest.get(key) or "").lower() != str(expected or "").lower()
        ]
        if mismatches or candidate_row.get("artifact_sha256") != run.get(
            "candidate_artifact_sha256"
        ):
            raise HTTPException(
                409,
                "Candidate provenance binding failed: "
                + ", ".join(mismatches or ["candidate_artifact_sha256"]),
            )
        try:
            compiled = compile_candidate_research_objective(
                ideation_run_id=run["id"],
                condition_snapshot=run["condition_snapshot"],
                condition_sha256=run["condition_sha256"],
                parent_objective_sha256=run["objective_sha256"],
                candidate_id=candidate_id,
                candidate_artifact_sha256=run["candidate_artifact_sha256"],
                candidate=candidate_row["candidate"],
                completion_target=body.completion_target or None,
                stop_criteria=body.stop_criteria,
            )
            objective_path = write_immutable_candidate_objective(
                request.app.state.settings.data_dir / "candidate-objectives", compiled
            )
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            namespace = uuid.UUID(candidate_id)
        except ValueError as exc:  # candidate importer owns this invariant
            raise HTTPException(409, "Conditioned candidate identity is invalid") from exc
        campaign_id = str(uuid.uuid5(namespace, compiled.prompt_sha256))
        now = utc_now()
        condition_snapshot = run["condition_snapshot"]
        resource_snapshot = condition_snapshot.get("resource")
        resource_capacity = (
            resource_snapshot.get("capacity")
            if isinstance(resource_snapshot, Mapping)
            and isinstance(resource_snapshot.get("capacity"), Mapping)
            else {}
        )
        config = {
            "campaign_kind": "conditioned_candidate_research",
            "ideation_run_id": run["id"],
            "candidate_id": candidate_id,
            "team_profile_id": run["team_profile_id"],
            "condition_sha256": run["condition_sha256"],
            "parent_objective_sha256": run["objective_sha256"],
            "candidate_artifact_sha256": run["candidate_artifact_sha256"],
            "candidate_record_sha256": compiled.candidate_sha256,
            "candidate_input_sha256": compiled.input_sha256,
            "candidate_prompt_sha256": compiled.prompt_sha256,
            "candidate_objective_path": str(objective_path),
            "condition_snapshot_bound": True,
            "conditioned_candidate_binding": True,
            "seed_catalog_source": False,
            "candidate_selection_recorded": True,
            "launch_triggered": False,
            "preflight_attestations": condition_snapshot.get(
                "preflight_attestations", {}
            ),
            "wall_clock_deadline": resource_capacity.get("wall_clock_deadline"),
            "backend": "connection-default",
            "positive_result_required": False,
            "oral_is_aspiration_only": True,
            "automatic_submission_allowed": False,
        }
        binding = build_campaign_binding(
            campaign_id=campaign_id,
            ideation_run_id=run["id"],
            candidate_id=candidate_id,
            condition_sha256=run["condition_sha256"],
            parent_objective_sha256=run["objective_sha256"],
            candidate_artifact_sha256=run["candidate_artifact_sha256"],
            candidate_record_sha256=compiled.candidate_sha256,
            candidate_input_sha256=compiled.input_sha256,
            candidate_prompt_sha256=compiled.prompt_sha256,
            objective_path=str(objective_path),
        )
        config["binding_receipt_sha256"] = binding["receipt_sha256"]
        title = body.title or f"{candidate_row['title']} · conditioned research"
        idempotent = False
        with db.transaction() as transaction:
            raced = transaction.execute(
                "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            if raced:
                raced_config = json.loads(raced["config_json"] or "{}")
                immutable_config = {
                    key: value
                    for key, value in config.items()
                    if key
                    in {
                        "campaign_kind",
                        "ideation_run_id",
                        "candidate_id",
                        "team_profile_id",
                        "condition_sha256",
                        "parent_objective_sha256",
                        "candidate_artifact_sha256",
                        "candidate_record_sha256",
                        "candidate_input_sha256",
                        "candidate_prompt_sha256",
                        "candidate_objective_path",
                        "binding_receipt_sha256",
                    }
                }
                mismatched = [
                    key
                    for key, value in immutable_config.items()
                    if raced_config.get(key) not in (value, None)
                    or (key != "binding_receipt_sha256" and raced_config.get(key) != value)
                ]
                if (
                    raced["objective"] != compiled.objective
                    or raced["venue_id"] != run["venue_id"]
                    or raced["deadline_id"] != run.get("deadline_id")
                    or raced["connection_id"] != run.get("connection_id")
                    or raced["resource_id"] != run.get("resource_id")
                    or mismatched
                ):
                    raise HTTPException(
                        409,
                        "Existing conditioned campaign no longer matches its frozen source",
                    )
                if raced_config.get("binding_receipt_sha256") is None:
                    raced_config["binding_receipt_sha256"] = binding["receipt_sha256"]
                    transaction.execute(
                        "UPDATE campaigns SET config_json=?,updated_at=? WHERE id=?",
                        (_json(raced_config), now, campaign_id),
                    )
                idempotent = True
            else:
                transaction.execute(
                    "INSERT INTO campaigns(id,venue_id,idea_id,deadline_id,connection_id,resource_id,"
                    "title,objective,schedule_state,execution_state,science_state,review_state,"
                    "integrity_state,deadline_state,progress,last_summary,viewer_score,"
                    "reviewer_scores_json,config_json,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        campaign_id,
                        run["venue_id"],
                        None,
                        run.get("deadline_id"),
                        run.get("connection_id"),
                        run.get("resource_id"),
                        title,
                        compiled.objective,
                        "manual",
                        "idle",
                        "candidate",
                        "not_requested",
                        "unchecked",
                        "on_track",
                        0,
                        "Candidate-specific objective frozen; explicit Start approval is required.",
                        None,
                        "[]",
                        _json(config),
                        now,
                        now,
                    ),
                )
            receipt = transaction.execute(
                "SELECT * FROM conditioned_campaign_bindings WHERE campaign_id=?",
                (campaign_id,),
            ).fetchone()
            if receipt:
                if any(receipt[key] != value for key, value in binding.items()):
                    raise HTTPException(
                        409, "Conditioned campaign binding receipt does not match frozen source"
                    )
            else:
                transaction.execute(
                    "INSERT INTO conditioned_campaign_bindings("
                    "campaign_id,schema_version,ideation_run_id,candidate_id,condition_sha256,"
                    "parent_objective_sha256,candidate_artifact_sha256,candidate_record_sha256,"
                    "candidate_input_sha256,candidate_prompt_sha256,objective_path,receipt_sha256,created_at"
                    ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        binding["campaign_id"],
                        binding["schema_version"],
                        binding["ideation_run_id"],
                        binding["candidate_id"],
                        binding["condition_sha256"],
                        binding["parent_objective_sha256"],
                        binding["candidate_artifact_sha256"],
                        binding["candidate_record_sha256"],
                        binding["candidate_input_sha256"],
                        binding["candidate_prompt_sha256"],
                        binding["objective_path"],
                        binding["receipt_sha256"],
                        now,
                    ),
                )
            if not raced:
                transaction.execute(
                    "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        "ideation",
                        "ideation.candidate_campaign_created",
                        "info",
                        "campaign",
                        campaign_id,
                        _json(
                            {
                                "ideation_run_id": run["id"],
                                "candidate_id": candidate_id,
                                "condition_sha256": run["condition_sha256"],
                                "candidate_prompt_sha256": compiled.prompt_sha256,
                                "launch_triggered": False,
                            }
                        ),
                        now,
                    ),
                )
        created = decode_row(db.fetch_one("SELECT * FROM campaigns WHERE id=?", (campaign_id,))) or {}
        created["candidate_prompt_sha256"] = compiled.prompt_sha256
        created["launch_triggered"] = False
        created["idempotent"] = idempotent
        return created

    @router.post("/ideation/candidates/{candidate_id}/labels", status_code=201)
    def label_candidate(
        candidate_id: str, body: CandidateLabelIn, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        candidate = db.fetch_one(
            "SELECT * FROM generated_idea_candidates WHERE id=?", (candidate_id,)
        )
        if not candidate:
            raise HTTPException(404, f"Candidate not found: {candidate_id}")
        candidate_provenance = _candidate_ideation_provenance(
            db, candidate_id, request.app.state.settings.data_dir
        )
        dimensions = _validated_dimensions(body.dimensions)
        _validate_training_record(
            body.training_consent,
            body.license_basis,
            body.redaction_confirmed,
        )
        label_id = str(uuid.uuid4())
        now = utc_now()
        try:
            db.execute(
                "INSERT INTO idea_labels(id,candidate_id,labeler_alias,decision,dimensions_json,"
                "rationale_redacted,redaction_confirmed,training_consent,license_basis,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    label_id,
                    candidate_id,
                    body.labeler_alias,
                    body.decision,
                    _json(dimensions),
                    body.rationale_redacted.strip(),
                    int(body.redaction_confirmed),
                    int(body.training_consent),
                    body.license_basis.strip(),
                    now,
                    now,
                ),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(409, "This alias already labeled the candidate") from exc
            raise
        db.append_event(
            "ideation",
            "ideation.candidate_labeled",
            entity_type="candidate",
            entity_id=candidate_id,
            payload={
                "label_id": label_id,
                "decision": body.decision,
                "training_eligible": _training_eligible(
                    body.training_consent, body.license_basis, body.redaction_confirmed
                )
                and candidate_provenance is not None,
            },
        )
        run_id = candidate["ideation_run_id"]
        totals = db.fetch_one(
            "SELECT COUNT(*) AS candidates,(SELECT COUNT(DISTINCT l.candidate_id) "
            "FROM idea_labels l JOIN generated_idea_candidates cc ON cc.id=l.candidate_id "
            "WHERE cc.ideation_run_id=?) AS labeled "
            "FROM generated_idea_candidates c WHERE c.ideation_run_id=?",
            (run_id, run_id),
        ) or {"candidates": 0, "labeled": 0}
        if totals["candidates"] and totals["candidates"] == totals["labeled"]:
            db.execute(
                "UPDATE ideation_runs SET state='labeled',updated_at=? WHERE id=?",
                (utc_now(), run_id),
            )
        result = decode_row(
            db.fetch_one("SELECT * FROM idea_labels WHERE id=?", (label_id,))
        ) or {}
        result["training_export_eligible"] = bool(
            _training_eligible(
                body.training_consent, body.license_basis, body.redaction_confirmed
            )
            and candidate_provenance is not None
        )
        result["training_lineage"] = candidate_provenance
        return result

    @router.post("/ideation/runs/{run_id}/pairwise", status_code=201)
    def pairwise_label(
        run_id: str, body: PairwiseLabelIn, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        _run(db, run_id)
        if body.left_candidate_id == body.right_candidate_id:
            raise HTTPException(422, "Pairwise candidates must be distinct")
        rows = db.fetch_all(
            "SELECT id,ideation_run_id FROM generated_idea_candidates WHERE id IN (?,?)",
            (body.left_candidate_id, body.right_candidate_id),
        )
        if len(rows) != 2 or any(row["ideation_run_id"] != run_id for row in rows):
            raise HTTPException(422, "Both candidates must belong to this ideation run")
        pair_provenance = {
            candidate_id: _candidate_ideation_provenance(
                db, candidate_id, request.app.state.settings.data_dir
            )
            for candidate_id in (body.left_candidate_id, body.right_candidate_id)
        }
        _validate_training_record(
            body.training_consent,
            body.license_basis,
            body.redaction_confirmed,
        )
        left_id, right_id, winner = (
            (body.left_candidate_id, body.right_candidate_id, body.winner)
            if body.left_candidate_id < body.right_candidate_id
            else (
                body.right_candidate_id,
                body.left_candidate_id,
                {"left": "right", "right": "left"}.get(body.winner, body.winner),
            )
        )
        preference_id = str(uuid.uuid4())
        now = utc_now()
        try:
            db.execute(
                "INSERT INTO idea_pairwise_preferences(id,ideation_run_id,left_candidate_id,"
                "right_candidate_id,winner,labeler_alias,rationale_redacted,redaction_confirmed,"
                "training_consent,license_basis,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    preference_id,
                    run_id,
                    left_id,
                    right_id,
                    winner,
                    body.labeler_alias,
                    body.rationale_redacted.strip(),
                    int(body.redaction_confirmed),
                    int(body.training_consent),
                    body.license_basis.strip(),
                    now,
                ),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(409, "This pair was already labeled by the alias") from exc
            raise
        result = decode_row(
            db.fetch_one("SELECT * FROM idea_pairwise_preferences WHERE id=?", (preference_id,))
        ) or {}
        result["training_export_eligible"] = bool(
            _training_eligible(
                body.training_consent, body.license_basis, body.redaction_confirmed
            )
            and all(pair_provenance.values())
        )
        result["training_lineage"] = pair_provenance
        return result

    @router.get("/outcomes/submissions")
    def list_submissions(
        request: Request, limit: int = Query(default=100, ge=1, le=500)
    ) -> dict[str, Any]:
        db = _db(request)
        rows = decode_rows(
            db.fetch_all(
                "SELECT sr.*,c.title AS campaign_title,v.venue_key,v.display_name AS venue_name "
                "FROM submission_records sr JOIN campaigns c ON c.id=sr.campaign_id "
                "JOIN venues v ON v.id=sr.venue_id ORDER BY sr.updated_at DESC LIMIT ?",
                (limit,),
            )
        )
        for row in rows:
            row["submission_version"] = row.pop("paper_version")
            row["reviewer_feedback"] = _submission_reviews(db, row["id"])
            _decorate_training_state(
                row, db, request.app.state.settings.data_dir
            )
            row["rebuttals"] = decode_rows(
                db.fetch_all(
                    "SELECT * FROM rebuttal_versions WHERE submission_id=? ORDER BY version DESC",
                    (row["id"],),
                )
            )
            if row["rebuttals"]:
                latest = row["rebuttals"][0]
                row["follow_up_campaign_id"] = latest.get("campaign_id")
                row["rebuttal_objective_sha256"] = latest.get("objective_sha256")
        return {"items": rows, "total": len(rows)}

    @router.post("/outcomes/submissions", status_code=201)
    def create_submission(body: SubmissionIn, request: Request) -> dict[str, Any]:
        db = _db(request)
        campaign = decode_row(
            db.fetch_one("SELECT * FROM campaigns WHERE id=?", (body.campaign_id,))
        )
        if not campaign:
            raise HTTPException(404, f"Campaign not found: {body.campaign_id}")
        training_provenance: dict[str, Any] | None = None
        try:
            training_provenance = verify_training_campaign_provenance(
                db, campaign, request.app.state.settings.data_dir
            )
        except HTTPException:
            # Historical/manual outcomes may still be recorded, but the absence
            # of verified lineage is frozen and makes export fail closed.
            training_provenance = None
        if not body.redaction_confirmed:
            raise HTTPException(
                422, "Recording reviewer text requires explicit redaction confirmation"
            )
        if body.consent_to_training_export and not body.review_license_confirmed:
            raise HTTPException(422, "Training export consent requires confirmed review-use rights")
        if body.consent_to_training_export and not body.redaction_confirmed:
            raise HTTPException(422, "Training export consent requires explicit redaction confirmation")
        submitted_at = _optional_datetime(body.submitted_at, "submitted_at")
        submission_id = str(uuid.uuid4())
        now = utc_now()
        decision = "withdraw" if body.decision == "withdrawn" else body.decision
        status = "decided" if decision != "pending" else "under_review"
        license_basis = (
            "operator_confirmed_authorized_review_use"
            if body.review_license_confirmed
            else ""
        )
        aliases = [review.reviewer for review in body.reviewer_feedback]
        if len(set(aliases)) != len(aliases):
            raise HTTPException(422, "Reviewer aliases must be unique within a submission")
        try:
            with db.transaction() as transaction:
                transaction.execute(
                    "INSERT INTO submission_records(id,campaign_id,venue_id,paper_version,submission_ref,"
                    "status,decision,submitted_at,decided_at,training_consent,license_basis,pseudonymized,"
                    "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        submission_id,
                        body.campaign_id,
                        campaign["venue_id"],
                        body.submission_version,
                        body.submission_ref,
                        status,
                        decision,
                        submitted_at,
                        now if decision != "pending" else None,
                        int(body.consent_to_training_export),
                        license_basis,
                        int(body.redaction_confirmed),
                        _json(
                            {
                                "review_license_confirmed": body.review_license_confirmed,
                                "automatic_training": False,
                                "automatic_submission": False,
                                "training_provenance": training_provenance,
                            }
                        ),
                        now,
                        now,
                    ),
                )
                for review in body.reviewer_feedback:
                    transaction.execute(
                        "INSERT INTO external_reviews(id,submission_id,reviewer_alias,score,score_label,"
                        "confidence,recommendation,feedback_redacted,questions_json,redaction_confirmed,"
                        "source_kind,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid.uuid4()),
                            submission_id,
                            review.reviewer,
                            review.score,
                            review.score_label,
                            review.confidence,
                            review.recommendation,
                            review.opinion_redacted,
                            _json(review.questions),
                            int(body.redaction_confirmed),
                            "human_entered",
                            now,
                            now,
                        ),
                    )
                transaction.execute(
                    "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        "outcomes",
                        "submission.outcome_recorded",
                        "info",
                        "submission",
                        submission_id,
                        _json(
                            {
                                "campaign_id": body.campaign_id,
                                "decision": decision,
                                "review_count": len(body.reviewer_feedback),
                                "training_export_eligible": bool(training_provenance)
                                and bool(body.consent_to_training_export)
                                and bool(body.review_license_confirmed)
                                and bool(body.redaction_confirmed),
                            }
                        ),
                        now,
                    ),
                )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise HTTPException(409, "This campaign paper version already exists") from exc
            raise
        return _submission_detail(
            db, submission_id, request.app.state.settings.data_dir
        )

    @router.post("/outcomes/submissions/{submission_id}/follow-up", status_code=201)
    def create_follow_up(
        submission_id: str, body: FollowUpIn, request: Request, response: Response
    ) -> dict[str, Any]:
        db = _db(request)
        submission = _submission_detail(
            db, submission_id, request.app.state.settings.data_dir
        )
        if not submission["training_lineage_verified"]:
            raise HTTPException(
                409,
                "Rebuttal follow-up requires verified conditioned-candidate lineage: "
                + "; ".join(submission["training_lineage_ineligibility_reasons"]),
            )
        if submission["training_provenance"]["campaign_kind"] != "conditioned_candidate_research":
            raise HTTPException(
                409,
                "Rebuttal follow-up must be rooted directly in a conditioned candidate campaign",
            )
        campaign = db.fetch_one("SELECT * FROM campaigns WHERE id=?", (submission["campaign_id"],))
        if not campaign:
            raise HTTPException(409, "Source campaign no longer exists")
        reviews = submission["reviewer_feedback"]
        objective = _rebuttal_objective(submission, campaign, reviews, body)
        objective_sha = hashlib.sha256(objective.encode("utf-8")).hexdigest()
        existing = decode_row(
            db.fetch_one(
                "SELECT * FROM rebuttal_versions WHERE submission_id=? AND objective_sha256=?",
                (submission_id, objective_sha),
            )
        )
        if existing:
            response.status_code = 200
            return {
                **existing,
                "idempotent": True,
                "campaign_id": existing.get("campaign_id"),
                "rebuttal_objective": objective,
                "launch_triggered": False,
                "submission_triggered": False,
            }
        path = _write_rebuttal_objective(
            request.app.state.settings.data_dir / "rebuttal-objectives",
            objective_sha,
            objective,
        )
        version_row = db.fetch_one(
            "SELECT COALESCE(MAX(version),0)+1 AS next_version FROM rebuttal_versions WHERE submission_id=?",
            (submission_id,),
        )
        version = int(version_row["next_version"])
        # Keep the historical UUID-v5 namespace stable so migrated records retain
        # idempotent identifiers even though public export schemas use FLYWHEEL.
        followup_campaign_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"argus-foundry:rebuttal:{submission_id}:{objective_sha}")
        )
        rebuttal_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"argus-foundry:rebuttal-version:{submission_id}:{objective_sha}")
        )
        now = utc_now()
        source_config = json.loads(campaign.get("config_json") or "{}")
        config = {
            "campaign_kind": "rebuttal_follow_up",
            "source_campaign_id": campaign["id"],
            "submission_id": submission_id,
            "rebuttal_objective_sha256": objective_sha,
            "preflight_attestations": source_config.get("preflight_attestations", {}),
            "backend": "connection-default",
            "automatic_submission_allowed": False,
        }
        with db.transaction() as transaction:
            transaction.execute(
                "INSERT INTO campaigns(id,venue_id,idea_id,deadline_id,connection_id,resource_id,title,"
                "objective,schedule_state,execution_state,science_state,review_state,integrity_state,"
                "deadline_state,progress,last_summary,reviewer_scores_json,config_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    followup_campaign_id,
                    campaign["venue_id"],
                    campaign["idea_id"],
                    campaign["deadline_id"],
                    campaign["connection_id"],
                    campaign["resource_id"],
                    f"{campaign['title']} · Rebuttal v{version}",
                    objective,
                    "manual",
                    "idle",
                    "candidate",
                    "human_review",
                    "unchecked",
                    "on_track",
                    0,
                    "Rebuttal objective frozen; human Start approval is still required.",
                    "[]",
                    _json(config),
                    now,
                    now,
                ),
            )
            transaction.execute(
                "INSERT INTO rebuttal_versions(id,submission_id,version,state,objective_sha256,"
                "objective_path,campaign_id,human_notes,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    rebuttal_id,
                    submission_id,
                    version,
                    "campaign_created",
                    objective_sha,
                    str(path),
                    followup_campaign_id,
                    f"Created by {body.actor}: {body.approval_reason}",
                    now,
                    now,
                ),
            )
            transaction.execute(
                "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    "outcomes",
                    "rebuttal.follow_up_created",
                    "attention",
                    "campaign",
                    followup_campaign_id,
                    _json(
                        {
                            "submission_id": submission_id,
                            "source_campaign_id": campaign["id"],
                            "objective_sha256": objective_sha,
                            "actor": body.actor,
                            "approval_reason": body.approval_reason,
                            "launch_triggered": False,
                            "submission_triggered": False,
                        }
                    ),
                    now,
                ),
            )
        return {
            **(decode_row(db.fetch_one("SELECT * FROM rebuttal_versions WHERE id=?", (rebuttal_id,))) or {}),
            "idempotent": False,
            "campaign_id": followup_campaign_id,
            "rebuttal_objective": objective,
            "launch_triggered": False,
            "submission_triggered": False,
        }

    @router.get("/outcomes/training-export")
    def training_export(request: Request) -> Response:
        lines = _training_records(
            _db(request), request.app.state.settings.data_dir
        )
        payload = "".join(_json(record, canonical=True) + "\n" for record in lines)
        return Response(
            payload,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": 'attachment; filename="argus-flywheel-training.jsonl"',
                "X-Training-Record-Count": str(len(lines)),
                "X-Automatic-Training": "false",
            },
        )

    @router.get("/outcomes/submissions/{submission_id}/training-export")
    def submission_training_export(submission_id: str, request: Request) -> Response:
        submission = _submission_detail(
            _db(request), submission_id, request.app.state.settings.data_dir
        )
        if not submission["training_export_eligible"]:
            raise HTTPException(
                409,
                "Submission is not eligible for training export: "
                + "; ".join(submission["ineligibility_reasons"]),
            )
        lines = _training_records(
            _db(request),
            request.app.state.settings.data_dir,
            submission_id=submission_id,
        )
        payload = "".join(_json(record, canonical=True) + "\n" for record in lines)
        return Response(
            payload,
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="argus-flywheel-submission-{submission_id}.jsonl"'
                ),
                "X-Training-Record-Count": str(len(lines)),
                "X-Automatic-Training": "false",
            },
        )

    @router.get("/datasets/training-export")
    def all_training_export(request: Request) -> Response:
        # Alias exposes that the same consent-gated export also contains
        # conditioned-idea scalar labels and pairwise preferences.
        return training_export(request)

    return router


def _db(request: Request) -> Database:
    return request.app.state.db


def _assert_no_secrets_http(value: Any) -> None:
    try:
        assert_no_secrets(value)
    except FlywheelDataError as exc:
        raise HTTPException(422, str(exc)) from exc


def _profile(db: Database, profile_id: str) -> dict[str, Any]:
    row = decode_row(db.fetch_one("SELECT * FROM team_profiles WHERE id=?", (profile_id,)))
    if not row:
        raise HTTPException(404, f"Team profile not found: {profile_id}")
    return row


def _team_origin(db: Database, profile: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve a confirmed one-sentence intake without trusting mutable UI state."""

    metadata = profile.get("metadata")
    intake_id = str(metadata.get("source_team_intake_id") or "") if isinstance(metadata, Mapping) else ""
    if not intake_id:
        return {"kind": "structured_profile"}
    row = db.fetch_one(
        "SELECT id,state,raw_text,extracted_json,uncertainties_json,team_profile_id "
        "FROM team_intakes WHERE id=? AND team_profile_id=?",
        (intake_id, profile["id"]),
    )
    if not row or row.get("state") != "confirmed":
        raise HTTPException(
            409,
            "Team profile references an unavailable or unconfirmed original intake",
        )
    try:
        extraction = json.loads(row.get("extracted_json") or "{}")
        uncertainties = json.loads(row.get("uncertainties_json") or "[]")
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "Confirmed team intake provenance is invalid") from exc
    if not isinstance(extraction, dict) or not isinstance(uncertainties, list):
        raise HTTPException(409, "Confirmed team intake provenance has an invalid shape")
    return {
        "kind": "confirmed_operator_intake",
        "source_intake_id": intake_id,
        "operator_statement": row.get("raw_text") or "",
        "extraction": extraction,
        "uncertainties": [str(item) for item in uncertainties],
    }


def _run(db: Database, run_id: str) -> dict[str, Any]:
    row = decode_row(db.fetch_one("SELECT * FROM ideation_runs WHERE id=?", (run_id,)))
    if not row:
        raise HTTPException(404, f"Ideation run not found: {run_id}")
    return row


def _run_detail(db: Database, run_id: str) -> dict[str, Any]:
    run = _run(db, run_id)
    objective_path = Path(run["objective_path"])
    try:
        objective_bytes = objective_path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            409, f"Frozen objective is unavailable for ideation run {run_id}: {exc}"
        ) from exc
    objective_sha256 = hashlib.sha256(objective_bytes).hexdigest()
    if objective_sha256 != run["objective_sha256"]:
        raise HTTPException(
            409,
            "Frozen objective integrity check failed for ideation run "
            f"{run_id}: expected {run['objective_sha256']}, got {objective_sha256}",
        )
    try:
        run["objective"] = objective_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            409, f"Frozen objective is not valid UTF-8 for ideation run {run_id}"
        ) from exc
    computed_condition_sha256 = hashlib.sha256(
        _canonical_json(run["condition_snapshot"])
    ).hexdigest()
    if computed_condition_sha256 != run.get("condition_sha256"):
        raise HTTPException(
            409,
            "Frozen condition integrity check failed for ideation run "
            f"{run_id}: expected {run.get('condition_sha256')}, got {computed_condition_sha256}",
        )
    candidates = decode_rows(
        db.fetch_all(
            "SELECT * FROM generated_idea_candidates WHERE ideation_run_id=? ORDER BY created_at",
            (run_id,),
        )
    )
    for candidate in candidates:
        candidate["labels"] = decode_rows(
            db.fetch_all(
                "SELECT * FROM idea_labels WHERE candidate_id=? ORDER BY created_at",
                (candidate["id"],),
            )
        )
    run["candidates"] = candidates
    run["pairwise_preferences"] = decode_rows(
        db.fetch_all(
            "SELECT * FROM idea_pairwise_preferences WHERE ideation_run_id=? ORDER BY created_at",
            (run_id,),
        )
    )
    return run


def _resolve_deadline(
    db: Database, venue_id: int, deadline_id: int | None
) -> dict[str, Any] | None:
    if deadline_id is not None:
        row = decode_row(db.fetch_one("SELECT * FROM deadlines WHERE id=?", (deadline_id,)))
        if not row:
            raise HTTPException(404, f"Deadline not found: {deadline_id}")
        if row["venue_id"] != venue_id:
            raise HTTPException(422, "Deadline does not belong to the selected venue")
        return row
    today = datetime.now(UTC).date().isoformat()
    row = db.fetch_one(
        "SELECT * FROM deadlines WHERE venue_id=? AND deadline_date>=? "
        "ORDER BY deadline_date LIMIT 1",
        (venue_id, today),
    )
    return decode_row(row)


def _optional_row(db: Database, table: str, row_id: str | None) -> dict[str, Any] | None:
    if row_id is None:
        return None
    if table not in {"resources", "connections"}:
        raise RuntimeError("invalid internal table")
    row = decode_row(db.fetch_one(f"SELECT * FROM {table} WHERE id=?", (row_id,)))
    if not row:
        raise HTTPException(404, f"{table[:-1].capitalize()} not found: {row_id}")
    return row


def _submission_reviews(db: Database, submission_id: str) -> list[dict[str, Any]]:
    reviews = decode_rows(
        db.fetch_all(
            "SELECT * FROM external_reviews WHERE submission_id=? ORDER BY created_at",
            (submission_id,),
        )
    )
    for review in reviews:
        review["reviewer"] = review.pop("reviewer_alias")
        review["opinion_redacted"] = review.pop("feedback_redacted")
    return reviews


def _submission_detail(
    db: Database, submission_id: str, data_dir: Path
) -> dict[str, Any]:
    row = decode_row(db.fetch_one("SELECT * FROM submission_records WHERE id=?", (submission_id,)))
    if not row:
        raise HTTPException(404, f"Submission not found: {submission_id}")
    row["submission_version"] = row.pop("paper_version")
    row["reviewer_feedback"] = _submission_reviews(db, submission_id)
    _decorate_training_state(row, db, data_dir)
    return row


def _frozen_training_provenance_state(
    db: Database, row: Mapping[str, Any], data_dir: Path
) -> tuple[dict[str, Any] | None, list[str]]:
    reasons: list[str] = []
    campaign_id = row.get("campaign_id")
    if not isinstance(campaign_id, str) or not campaign_id.strip():
        return None, ["source campaign is absent"]
    campaign = decode_row(db.fetch_one("SELECT * FROM campaigns WHERE id=?", (campaign_id,)))
    if campaign is None:
        return None, ["source campaign is unavailable"]
    try:
        current = verify_training_campaign_provenance(db, campaign, data_dir)
    except HTTPException as exc:
        return None, [f"verified training lineage failed: {exc.detail}"]
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    frozen = metadata.get("training_provenance")
    if not isinstance(frozen, Mapping):
        reasons.append("verified training lineage was not frozen at record creation")
    elif _json(frozen, canonical=True) != _json(current, canonical=True):
        reasons.append("frozen training lineage no longer matches its source receipts")
    return current, reasons


def _decorate_training_state(
    row: dict[str, Any], db: Database, data_dir: Path
) -> None:
    reasons: list[str] = []
    if not row.get("training_consent"):
        reasons.append("explicit training-export consent is absent")
    if not str(row.get("license_basis") or "").strip():
        reasons.append("review-use license basis is absent")
    if not row.get("pseudonymized"):
        reasons.append("redaction/pseudonymization confirmation is absent")
    provenance, provenance_reasons = _frozen_training_provenance_state(
        db, row, data_dir
    )
    reasons.extend(provenance_reasons)
    row["consent_to_training_export"] = bool(row.get("training_consent"))
    row["review_license_confirmed"] = bool(str(row.get("license_basis") or "").strip())
    row["training_lineage_verified"] = not provenance_reasons
    row["training_lineage_ineligibility_reasons"] = provenance_reasons
    row["training_provenance"] = provenance if not provenance_reasons else None
    row["training_export_eligible"] = not reasons
    row["ineligibility_reasons"] = reasons
    row["training_export_ineligibility_reasons"] = reasons


def _rebuttal_objective(
    submission: Mapping[str, Any],
    campaign: Mapping[str, Any],
    reviews: list[Mapping[str, Any]],
    body: FollowUpIn,
) -> str:
    review_packet = [
        {
            "reviewer_alias": review.get("reviewer"),
            "score": review.get("score"),
            "score_label": review.get("score_label"),
            "confidence": review.get("confidence"),
            "recommendation": review.get("recommendation"),
            "feedback_redacted": review.get("opinion_redacted"),
            "questions": review.get("questions") or [],
        }
        for review in reviews
    ]
    return f"""# ARGUS REBUTTAL FOLLOW-UP OBJECTIVE

Source campaign: `{campaign['id']}`
Submission record: `{submission['id']}`
Paper version: `{submission['submission_version']}`
Decision state: `{submission.get('decision') or 'pending'}`
Human authorizing draft preparation: `{body.actor}`
Reason: {body.approval_reason}

## Boundary

Prepare evidence-grounded rebuttal material for human review.  Do not submit,
contact reviewers, infer reviewer identity, promise score changes, invent new
experiments, or treat this authorization as permission to start the campaign.
The follow-up campaign remains idle until a separate explicit Start approval.

## Frozen redacted review packet

```json
{json.dumps(review_packet, ensure_ascii=False, sort_keys=True, indent=2)}
```

## Debate and review procedure

1. `RESPONSE_ADVOCATE` maps every reviewer point to the exact manuscript claim,
   existing artifact, or a clearly identified bounded new analysis.
2. `RESPONSE_SKEPTIC` independently checks whether each proposed answer is
   actually supported, responsive, statistically valid, within resource/time
   limits, and free of overclaiming.  It must surface contradictions among
   reviewers rather than averaging them away.
3. `REBUTTAL_ARBITER` produces an issue ledger with status `answered`,
   `partially_answered`, `unsupported`, `requires_human_input`, or `out_of_scope`.
4. Independent fresh-context reviewers evaluate clarity, evidence sufficiency,
   methods/statistics, venue policy, and integrity.  Missing evidence means
   `score: null`, never a guessed score.

Required artifacts: `REVIEW_ISSUE_LEDGER.json`, `CLAIM_EVIDENCE_DELTA.json`,
`REBUTTAL_DRAFT.md`, `SKEPTIC_REPORT.md`, `INDEPENDENT_REVIEW_PANEL.json`, and
`HUMAN_INPUT_REQUESTS.md`.

Legal terminal states: `REBUTTAL_READY_FOR_HUMAN_REVIEW`,
`REQUIRES_HUMAN_INPUT`, `INSUFFICIENT_EVIDENCE`, `POLICY_BLOCKED`, and `KILLED`.
Only a human may decide whether and how to submit the final response.
"""


def _candidate_ideation_provenance(
    db: Database, candidate_id: str, data_dir: Path
) -> dict[str, Any] | None:
    try:
        return verify_conditioned_ideation_candidate_provenance(
            db, candidate_id, data_dir
        )
    except HTTPException:
        return None


def _training_records(
    db: Database, data_dir: Path, *, submission_id: str | None = None
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    submission_filter = " AND sr.id=?" if submission_id else ""
    submission_params: tuple[Any, ...] = (submission_id,) if submission_id else ()
    submissions = decode_rows(
        db.fetch_all(
            "SELECT sr.*,v.venue_key,c.id AS source_campaign_id FROM submission_records sr "
            "JOIN venues v ON v.id=sr.venue_id JOIN campaigns c ON c.id=sr.campaign_id "
            "WHERE sr.training_consent=1 AND sr.pseudonymized=1 "
            "AND LENGTH(TRIM(sr.license_basis))>0"
            + submission_filter,
            submission_params,
        )
    )
    for submission in submissions:
        training_provenance, provenance_reasons = _frozen_training_provenance_state(
            db, submission, data_dir
        )
        if provenance_reasons or training_provenance is None:
            continue
        reviews = _submission_reviews(db, submission["id"])
        group = f"campaign:{submission['source_campaign_id']}"
        split = _group_split(group)
        for review in reviews:
            if review.get("redaction_confirmed") is not True:
                continue
            records.append(
                {
                    "schema": "argus-flywheel/outcome-review/v2",
                    "record_id": f"review:{review['id']}",
                    "group_id": group,
                    "split": split,
                    "venue_key": submission["venue_key"],
                    "paper_version": submission["paper_version"],
                    "reviewer_alias": review["reviewer"],
                    "score": review.get("score"),
                    "score_label": review.get("score_label"),
                    "confidence": review.get("confidence"),
                    "recommendation": review.get("recommendation"),
                    "feedback_redacted": review["opinion_redacted"],
                    "questions": review.get("questions") or [],
                    "decision": submission.get("decision"),
                    "license_basis": submission["license_basis"],
                    "provenance": "human_entered_redacted_review",
                    "training_lineage": training_provenance,
                }
            )
    label_rows = [] if submission_id else db.fetch_all(
        "SELECT l.*,c.candidate_json,c.artifact_sha256,c.ideation_run_id,"
        "r.condition_snapshot_json,r.license_basis AS run_license "
        "FROM idea_labels l JOIN generated_idea_candidates c ON c.id=l.candidate_id "
        "JOIN ideation_runs r ON r.id=c.ideation_run_id "
        "WHERE l.training_consent=1 AND l.redaction_confirmed=1 "
        "AND LENGTH(TRIM(l.license_basis))>0 AND r.training_consent=1 "
        "AND LENGTH(TRIM(r.license_basis))>0 AND length(c.artifact_sha256)=64"
    )
    for row in label_rows:
        training_provenance = _candidate_ideation_provenance(
            db, row["candidate_id"], data_dir
        )
        if training_provenance is None:
            continue
        group = f"ideation:{row['ideation_run_id']}"
        records.append(
            {
                "schema": "argus-flywheel/conditioned-idea-label/v2",
                "record_id": f"idea-label:{row['id']}",
                "group_id": group,
                "split": _group_split(group),
                "condition_snapshot": json.loads(row["condition_snapshot_json"]),
                "candidate": json.loads(row["candidate_json"]),
                "artifact_sha256": row["artifact_sha256"],
                "labeler_alias": row["labeler_alias"],
                "decision": row["decision"],
                "dimensions": json.loads(row["dimensions_json"]),
                "rationale_redacted": row["rationale_redacted"],
                "license_basis": row["license_basis"],
                "provenance": "human_conditioned_idea_label",
                "training_lineage": training_provenance,
            }
        )
    pair_rows = [] if submission_id else db.fetch_all(
        "SELECT p.*,l.candidate_json AS left_json,rr.candidate_json AS right_json,"
        "l.artifact_sha256 AS left_artifact_sha256,"
        "rr.artifact_sha256 AS right_artifact_sha256,"
        "r.condition_snapshot_json,r.license_basis AS run_license "
        "FROM idea_pairwise_preferences p "
        "JOIN generated_idea_candidates l ON l.id=p.left_candidate_id "
        "JOIN generated_idea_candidates rr ON rr.id=p.right_candidate_id "
        "JOIN ideation_runs r ON r.id=p.ideation_run_id "
        "WHERE p.training_consent=1 AND p.redaction_confirmed=1 "
        "AND LENGTH(TRIM(p.license_basis))>0 AND r.training_consent=1 "
        "AND LENGTH(TRIM(r.license_basis))>0 "
        "AND length(l.artifact_sha256)=64 AND l.artifact_sha256=rr.artifact_sha256"
    )
    for row in pair_rows:
        left_provenance = _candidate_ideation_provenance(
            db, row["left_candidate_id"], data_dir
        )
        right_provenance = _candidate_ideation_provenance(
            db, row["right_candidate_id"], data_dir
        )
        if left_provenance is None or right_provenance is None:
            continue
        group = f"ideation:{row['ideation_run_id']}"
        records.append(
            {
                "schema": "argus-flywheel/conditioned-idea-preference/v2",
                "record_id": f"idea-pair:{row['id']}",
                "group_id": group,
                "split": _group_split(group),
                "condition_snapshot": json.loads(row["condition_snapshot_json"]),
                "left": json.loads(row["left_json"]),
                "right": json.loads(row["right_json"]),
                "artifact_sha256": row["left_artifact_sha256"],
                "left_artifact_sha256": row["left_artifact_sha256"],
                "right_artifact_sha256": row["right_artifact_sha256"],
                "winner": row["winner"],
                "labeler_alias": row["labeler_alias"],
                "rationale_redacted": row["rationale_redacted"],
                "license_basis": row["license_basis"],
                "provenance": "human_pairwise_preference",
                "left_training_lineage": left_provenance,
                "right_training_lineage": right_provenance,
            }
        )
    return sorted(records, key=lambda item: item["record_id"])


def _validated_dimensions(value: Mapping[str, float | None]) -> dict[str, float | None]:
    missing = [key for key in _DIMENSIONS if key not in value]
    unknown = sorted(set(value) - set(_DIMENSIONS))
    if missing or unknown:
        raise HTTPException(
            422,
            "dimensions must contain exactly "
            + ", ".join(_DIMENSIONS)
            + (f"; missing={missing}" if missing else "")
            + (f"; unknown={unknown}" if unknown else ""),
        )
    result: dict[str, float | None] = {}
    for key in _DIMENSIONS:
        score = value[key]
        if score is not None and not 0 <= float(score) <= 10:
            raise HTTPException(422, f"dimension {key} must be null or between 0 and 10")
        result[key] = None if score is None else float(score)
    return result


def _validate_training_license(consent: bool, license_basis: str) -> None:
    if consent and not str(license_basis).strip():
        raise HTTPException(422, "Training consent requires a nonblank license basis")


def _validate_training_record(consent: bool, license_basis: str, redacted: bool) -> None:
    _validate_training_license(consent, license_basis)
    if consent and not redacted:
        raise HTTPException(422, "Training consent requires explicit redaction confirmation")


def _training_eligible(consent: bool, license_basis: str, redacted: bool) -> bool:
    return bool(consent and str(license_basis).strip() and redacted)


def _alias(value: str) -> str:
    clean = value.strip()
    if not _SAFE_ALIAS.fullmatch(clean) or "@" in clean or "http" in clean.lower():
        raise ValueError("use a pseudonymous alias such as Reviewer 1; names/emails/URLs are forbidden")
    return clean


def _optional_datetime(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(422, f"{field} must include an explicit UTC offset")
    return parsed.astimezone(UTC).isoformat()


def _group_split(group_id: str) -> str:
    bucket = int(hashlib.sha256(group_id.encode("utf-8")).hexdigest()[:8], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def _write_rebuttal_objective(root: Path, digest: str, objective: str) -> Path:
    target = root.resolve() / digest / "REBUTTAL_OBJECTIVE.md"
    content = objective.encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != content:
            raise HTTPException(500, "content-addressed rebuttal collision")
        return target
    temporary = target.parent / f".tmp-{uuid.uuid4().hex[:8]}"
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(500, "failed to freeze rebuttal objective") from exc
    target.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return target


def _canonical_json(value: Any) -> bytes:
    return (_json(value, canonical=True) + "\n").encode("utf-8")


def _json(value: Any, *, canonical: bool = False) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=canonical,
        separators=(",", ":") if canonical else None,
    )
