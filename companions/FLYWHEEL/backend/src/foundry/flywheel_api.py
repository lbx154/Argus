from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .api import (
    verify_conditioned_campaign_integrity,
    verify_training_campaign_provenance,
)
from .db import Database, decode_row, decode_rows, utc_now
from .flywheel_models import (
    DatasetSelection,
    DatasetSnapshotCreate,
    EpisodeCreate,
    EpisodeSealRequest,
    OpenReviewFetchRequest,
    PdfReviewPayload,
    ReviewImportConfirm,
    ReviewImportCreate,
    ReviewImportDiscard,
    TeamIntakeConfirmRequest,
    TeamIntakeExtractRequest,
)
from .services.flywheel_data import (
    EpisodeService,
    FlywheelDataError,
    assert_no_secrets,
    canonical_json,
    selection_preview,
    sha256_text,
)

OPENREVIEW_API_ORIGIN = "https://api2.openreview.net"
OPENREVIEW_FETCH_TIMEOUT_SECONDS = 10
OPENREVIEW_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
OPENREVIEW_USER_AGENT = "ARGUS-FLYWHEEL/1.0 (+public-review-import)"
_RESERVED_LINEAGE_RELATIONS = frozenset(
    {
        "execution",
        "selected_candidate",
        "ideation_source",
        "training_source",
        "rebuttal_source",
    }
)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _openreview_http_open(request: urllib.request.Request, *, timeout: int):
    """Open without ambient proxies, cookies, credentials, or redirect following."""

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    return opener.open(request, timeout=timeout)


def _db(request: Request) -> Database:
    return request.app.state.db


def _service(request: Request) -> EpisodeService:
    return EpisodeService(
        _db(request),
        request.app.state.settings.data_dir / "data-vault" / "objects",
        data_dir=request.app.state.settings.data_dir,
    )


def _not_found_or_conflict(exc: FlywheelDataError) -> HTTPException:
    status = 404 if "not found" in str(exc).lower() else 409
    return HTTPException(status, str(exc))


def _reject_secrets(value: Any) -> None:
    try:
        assert_no_secrets(value)
    except FlywheelDataError as exc:
        raise HTTPException(422, str(exc)) from exc


def _require_row(db: Database, table: str, entity_id: Any) -> None:
    allowed = {
        "team_profiles",
        "venues",
        "deadlines",
        "campaigns",
        "ideation_runs",
        "research_episodes",
        "review_import_batches",
        "dataset_snapshots",
    }
    if table not in allowed:
        raise RuntimeError("invalid internal table")
    if not db.fetch_one(f"SELECT id FROM {table} WHERE id=?", (entity_id,)):
        raise HTTPException(404, f"{table} entity not found: {entity_id}")


def _extract_team_profile(raw_text: str) -> tuple[dict[str, Any], list[str]]:
    """Transparent deterministic extraction; the human-confirm endpoint owns truth."""

    lowered = raw_text.lower()
    gpu_models = sorted(
        set(
            match.upper().replace("GEFORCE ", "")
            for match in re.findall(
                r"(?i)\b(?:geforce\s+)?(h100|h200|a100|a800|a6000|l40s?|rtx\s*4090|rtx\s*5090|4090|5090)\b",
                raw_text,
            )
        )
    )
    gpu_count_match = re.search(
        r"(?i)(\d{1,3})\s*(?:x|×|张|块|台)?\s*(?:h100|h200|a100|a800|a6000|l40s?|rtx\s*4090|rtx\s*5090|4090|5090)",
        raw_text,
    )
    token_match = re.search(
        r"(?i)(\d+(?:\.\d+)?)\s*(万|百万|千万|亿|k|m|b)?\s*(?:tokens?|令牌)", raw_text
    )
    time_match = re.search(r"(?i)(\d+(?:\.\d+)?)\s*(天|周|个月|月|days?|weeks?|months?)", raw_text)
    expertise_terms = [
        term
        for term in (
            "机器学习",
            "深度学习",
            "大语言模型",
            "强化学习",
            "计算机视觉",
            "自然语言处理",
            "机器人",
            "系统",
            "安全",
            "数据库",
            "网络",
            "HCI",
            "LLM",
            "NLP",
            "CV",
            "RL",
        )
        if term.lower() in lowered
    ]
    profile: dict[str, Any] = {
        "schema_version": "flywheel.team-profile/1",
        "expertise": expertise_terms,
        "methods": [],
        "data_access": [],
        "constraints": {
            "gpu_count": int(gpu_count_match.group(1)) if gpu_count_match else None,
            "gpu_models": gpu_models,
            "token_budget_text": token_match.group(0) if token_match else None,
            "time_budget_text": time_match.group(0) if time_match else None,
        },
        "goals": {},
        "policy": {
            "requires_human_confirmation": True,
            "extraction_mode": "local_deterministic_v1",
        },
    }
    uncertainties: list[str] = []
    if not expertise_terms:
        uncertainties.append("expertise_not_detected")
    if not gpu_models:
        uncertainties.append("gpu_model_not_detected")
    if not token_match:
        uncertainties.append("token_budget_not_detected")
    if not time_match:
        uncertainties.append("time_budget_not_detected")
    return profile, uncertainties


def _review_draft(body: ReviewImportCreate) -> tuple[Any, dict[str, Any]]:
    if body.raw_text is not None:
        raw = body.raw_text.strip()
        parsed: Any = {
            "mode": "text_draft",
            "character_count": len(raw),
            "line_count": len(raw.splitlines()),
            "preview": raw[:500],
        }
        raw_payload = {
            "media_type": "text/plain; charset=utf-8",
            "content_utf8": raw,
        }
    elif body.source_kind == "pdf":
        pdf = PdfReviewPayload.model_validate(body.payload)
        data = pdf.decoded_bytes()
        parsed = {
            "mode": "pdf_draft",
            "filename": pdf.filename,
            "byte_length": len(data),
            "needs_text_extraction": True,
        }
        raw_payload = {
            "media_type": "application/pdf",
            "filename": pdf.filename,
            "content_base64": pdf.content_base64,
        }
    else:
        parsed = body.payload
        raw_payload = {
            "media_type": "application/json",
            "content_utf8": canonical_json(body.payload),
        }
    return parsed, raw_payload


def _staged_review_bytes(raw_payload: dict[str, Any]) -> tuple[bytes, str, str | None]:
    media_type = raw_payload.get("media_type")
    if not isinstance(media_type, str) or not media_type:
        raise FlywheelDataError("staged media_type is missing")
    if "content_base64" in raw_payload:
        pdf = PdfReviewPayload.model_validate(
            {
                "filename": raw_payload.get("filename"),
                "mime_type": media_type,
                "content_base64": raw_payload.get("content_base64"),
            }
        )
        return pdf.decoded_bytes(), "application/pdf", pdf.filename
    content = raw_payload.get("content_utf8")
    if not isinstance(content, str):
        raise FlywheelDataError("staged UTF-8 content is missing")
    return content.encode("utf-8"), media_type, None


def _fetch_openreview_forum(forum_id: str) -> tuple[Any, bytes, str]:
    query = urllib.parse.urlencode({"forum": forum_id})
    source_ref = f"{OPENREVIEW_API_ORIGIN}/notes?{query}"
    parsed_url = urllib.parse.urlsplit(source_ref)
    if parsed_url.scheme != "https" or parsed_url.hostname != "api2.openreview.net":
        raise FlywheelDataError("OpenReview URL escaped the fixed public API allowlist")
    request = urllib.request.Request(
        source_ref,
        headers={
            "Accept": "application/json",
            "User-Agent": OPENREVIEW_USER_AGENT,
        },
        method="GET",
    )
    try:
        with _openreview_http_open(
            request, timeout=OPENREVIEW_FETCH_TIMEOUT_SECONDS
        ) as response:
            final_url = urllib.parse.urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "api2.openreview.net":
                raise FlywheelDataError("OpenReview response URL is not allowlisted")
            status = getattr(response, "status", 200)
            if status != 200:
                raise FlywheelDataError(f"OpenReview returned HTTP {status}")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > OPENREVIEW_MAX_RESPONSE_BYTES:
                        raise FlywheelDataError("OpenReview response exceeds the 2 MiB limit")
                except ValueError as exc:
                    raise FlywheelDataError("OpenReview returned an invalid Content-Length") from exc
            media_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if media_type not in {"application/json", "application/vnd.api+json"}:
                raise FlywheelDataError("OpenReview response is not JSON")
            raw_bytes = response.read(OPENREVIEW_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise FlywheelDataError(f"OpenReview public fetch failed with HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise FlywheelDataError(f"OpenReview public fetch failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise FlywheelDataError("OpenReview public fetch timed out") from exc
    if len(raw_bytes) > OPENREVIEW_MAX_RESPONSE_BYTES:
        raise FlywheelDataError("OpenReview response exceeds the 2 MiB limit")
    try:
        raw_text = raw_bytes.decode("utf-8")
        parsed = json.loads(raw_text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FlywheelDataError("OpenReview returned invalid UTF-8 JSON") from exc
    if not isinstance(parsed, (dict, list)):
        raise FlywheelDataError("OpenReview JSON root must be an object or array")
    assert_no_secrets(parsed)
    return parsed, raw_bytes, source_ref


def _snapshot_verify(db: Database, service: EpisodeService, snapshot_id: str) -> dict[str, Any]:
    snapshot = db.fetch_one("SELECT * FROM dataset_snapshots WHERE id=?", (snapshot_id,))
    if not snapshot:
        raise HTTPException(404, f"dataset snapshot not found: {snapshot_id}")
    members = db.fetch_all(
        "SELECT * FROM dataset_snapshot_members WHERE snapshot_id=? ORDER BY episode_id,revision_id",
        (snapshot_id,),
    )
    checks = [
        {
            "name": "manifest_sha256",
            "valid": sha256_text(snapshot["manifest_json"]) == snapshot["manifest_sha256"],
            "detail": snapshot["manifest_sha256"],
        }
    ]
    manifest = json.loads(snapshot["manifest_json"])
    selection = json.loads(snapshot["selection_json"])
    manifest_members = manifest.get("members")
    frozen_members = (
        manifest_members
        if isinstance(manifest_members, list)
        and all(isinstance(member, dict) for member in manifest_members)
        else []
    )
    selection_material = {
        "schema_version": "flywheel.dataset-selection/1",
        "require_training_consent": bool(selection.get("require_training_consent", True)),
        "members": frozen_members,
    }
    checks.append(
        {
            "name": "selection_sha256",
            "valid": sha256_text(canonical_json(selection_material)) == snapshot["selection_sha256"],
            "detail": snapshot["selection_sha256"],
        }
    )
    checks.append(
        {
            "name": "member_count",
            "valid": (
                len(manifest.get("members", [])) == len(members) == snapshot["member_count"]
            ),
            "detail": str(len(members)),
        }
    )
    checks.append(
        {
            "name": "manifest_members_are_structured",
            "valid": (
                isinstance(manifest_members, list)
                and len(frozen_members) == len(manifest_members)
            ),
            "detail": str(len(manifest_members) if isinstance(manifest_members, list) else 0),
        }
    )
    db_member_keys = {
        (member["episode_id"], member["revision_id"], member["manifest_sha256"])
        for member in members
    }
    frozen_member_keys = {
        (
            str(member.get("episode_id") or ""),
            str(member.get("revision_id") or ""),
            str(member.get("manifest_sha256") or ""),
        )
        for member in frozen_members
    }
    checks.append(
        {
            "name": "database_member_projection",
            "valid": db_member_keys == frozen_member_keys and len(frozen_members) == len(members),
            "detail": str(len(db_member_keys)),
        }
    )
    for member in frozen_members:
        episode_id = str(member.get("episode_id") or "")
        revision_id = str(member.get("revision_id") or "")
        revision = db.fetch_one("SELECT * FROM episode_revisions WHERE id=?", (revision_id,))
        valid = bool(
            revision
            and revision["episode_id"] == episode_id
            and revision["manifest_sha256"] == member.get("manifest_sha256")
            and revision["chain_sha256"] == member.get("chain_sha256")
            and service.verify_revision_prefix(
                episode_id, revision_id
            )["valid"]
        )
        checks.append(
            {
                "name": f"member_{revision_id}",
                "valid": valid,
                "detail": episode_id,
            }
        )
        episode = decode_row(
            db.fetch_one("SELECT * FROM research_episodes WHERE id=?", (episode_id,))
        )
        expected_provenance = member.get("training_provenance")
        provenance_status = (
            service.training_provenance_status(
                episode,
                revision,
                expected_provenance=(
                    expected_provenance
                    if isinstance(expected_provenance, dict)
                    else None
                ),
            )
            if episode is not None and revision is not None
            else {
                "verified": False,
                "reasons": ["dataset_member_source_missing"],
            }
        )
        checks.append(
            {
                "name": f"member_{revision_id}_training_lineage",
                "valid": bool(provenance_status["verified"]),
                "detail": ",".join(provenance_status["reasons"]) or "verified",
            }
        )
    return {
        "id": snapshot_id,
        "valid": all(check["valid"] for check in checks),
        "checks": checks,
        "manifest_sha256": snapshot["manifest_sha256"],
        "member_count": len(members),
    }


def create_flywheel_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["Research Data Flywheel"])

    @router.get("/episodes")
    def list_episodes(request: Request) -> dict[str, Any]:
        return {"items": _service(request).list_episode_summaries()}

    @router.post("/episodes", status_code=201)
    def create_episode(body: EpisodeCreate, request: Request) -> dict[str, Any]:
        db = _db(request)
        _reject_secrets(body.model_dump())
        forbidden_relations = sorted(
            {
                link.relation.strip().lower()
                for link in body.links
                if link.relation.strip().lower() in _RESERVED_LINEAGE_RELATIONS
            }
        )
        if forbidden_relations:
            raise HTTPException(
                422,
                "system lineage relations cannot be supplied by clients: "
                + ", ".join(forbidden_relations),
            )
        run: dict[str, Any] | None = None
        candidate: dict[str, Any] | None = None
        conditioned_binding: dict[str, str] | None = None
        ideation_campaign_id: str | None = None
        if body.ideation_run_id:
            run = decode_row(
                db.fetch_one("SELECT * FROM ideation_runs WHERE id=?", (body.ideation_run_id,))
            )
            if not run:
                raise HTTPException(404, f"ideation run not found: {body.ideation_run_id}")
            if not body.candidate_id:
                raise HTTPException(422, "candidate_id is required when ideation_run_id is set")
            candidate = decode_row(
                db.fetch_one(
                    "SELECT * FROM generated_idea_candidates WHERE id=?", (body.candidate_id,)
                )
            )
            if not candidate:
                raise HTTPException(404, f"candidate not found: {body.candidate_id}")
            if candidate["ideation_run_id"] != body.ideation_run_id:
                raise HTTPException(409, "selected candidate does not belong to ideation run")
            run_digest = str(run.get("candidate_artifact_sha256") or "")
            candidate_digest = str(candidate.get("artifact_sha256") or "")
            if not run_digest or run_digest != candidate_digest:
                raise HTTPException(409, "selected candidate artifact binding is missing or inconsistent")
        elif body.candidate_id:
            raise HTTPException(422, "candidate_id requires ideation_run_id")

        effective_team_profile_id = body.team_profile_id
        effective_venue_id = body.venue_id
        effective_deadline_id = body.deadline_id
        effective_campaign_id = body.campaign_id
        training_provenance: dict[str, Any] | None = None
        if run:
            bindings = (
                ("team_profile_id", body.team_profile_id, run.get("team_profile_id")),
                ("venue_id", body.venue_id, run.get("venue_id")),
                ("deadline_id", body.deadline_id, run.get("deadline_id")),
            )
            for field, supplied, frozen in bindings:
                if supplied is not None and supplied != frozen:
                    raise HTTPException(
                        409,
                        f"episode {field} conflicts with the frozen ideation run binding",
                    )
            effective_team_profile_id = run.get("team_profile_id")
            effective_venue_id = run.get("venue_id")
            effective_deadline_id = run.get("deadline_id")
            ideation_campaign_id = run.get("campaign_id")
            if body.campaign_id is None:
                raise HTTPException(
                    422,
                    "campaign_id must identify the conditioned candidate execution campaign; "
                    "the ideation campaign is provenance only",
                )
            campaign = decode_row(
                db.fetch_one("SELECT * FROM campaigns WHERE id=?", (body.campaign_id,))
            )
            if not campaign:
                raise HTTPException(404, f"campaign not found: {body.campaign_id}")
            # The join is intentional: an existing campaign is not an execution
            # campaign unless it owns an immutable conditioned-candidate receipt.
            joined_binding = db.fetch_one(
                "SELECT b.* FROM campaigns c "
                "JOIN conditioned_campaign_bindings b ON b.campaign_id=c.id "
                "WHERE c.id=?",
                (body.campaign_id,),
            )
            if not joined_binding:
                raise HTTPException(
                    409,
                    "campaign_id is not a conditioned candidate execution campaign; "
                    "an ideation campaign cannot be recorded as execution",
                )
            conditioned_binding = verify_conditioned_campaign_integrity(
                db,
                campaign,
                request.app.state.settings.data_dir,
            )
            if conditioned_binding is None:
                raise HTTPException(
                    409, "campaign_id has no conditioned candidate execution binding"
                )
            if conditioned_binding["ideation_run_id"] != run["id"]:
                raise HTTPException(
                    409,
                    "conditioned execution campaign does not belong to the selected ideation run",
                )
            if conditioned_binding["candidate_id"] != candidate["id"]:
                raise HTTPException(
                    409,
                    "conditioned execution campaign does not belong to the selected candidate",
                )
            if any(
                str(joined_binding.get(key) or "") != value
                for key, value in conditioned_binding.items()
            ):
                raise HTTPException(
                    409, "conditioned execution binding changed during episode creation"
                )
            effective_campaign_id = body.campaign_id

        # A generic/manual Episode remains useful as an archive, but it cannot
        # claim training lineage.  When a real candidate or rebuttal Campaign is
        # supplied, derive team/venue/deadline from its verified root receipt and
        # freeze the complete digest set.  A caller cannot provide these values.
        if effective_campaign_id is not None:
            provenance_campaign = decode_row(
                db.fetch_one("SELECT * FROM campaigns WHERE id=?", (effective_campaign_id,))
            )
            if provenance_campaign is None:
                raise HTTPException(404, f"campaign not found: {effective_campaign_id}")
            try:
                training_provenance = verify_training_campaign_provenance(
                    db,
                    provenance_campaign,
                    request.app.state.settings.data_dir,
                )
            except HTTPException:
                if run is not None:
                    raise
                training_provenance = None
            if training_provenance is not None:
                provenance_run = decode_row(
                    db.fetch_one(
                        "SELECT * FROM ideation_runs WHERE id=?",
                        (training_provenance["ideation_run_id"],),
                    )
                )
                if provenance_run is None:
                    raise HTTPException(
                        409, "verified training lineage no longer has its ideation run"
                    )
                provenance_bindings = (
                    (
                        "team_profile_id",
                        body.team_profile_id,
                        provenance_run.get("team_profile_id"),
                    ),
                    ("venue_id", body.venue_id, provenance_run.get("venue_id")),
                    ("deadline_id", body.deadline_id, provenance_run.get("deadline_id")),
                )
                for field, supplied, frozen in provenance_bindings:
                    if supplied is not None and supplied != frozen:
                        raise HTTPException(
                            409,
                            f"episode {field} conflicts with verified training lineage",
                        )
                effective_team_profile_id = provenance_run.get("team_profile_id")
                effective_venue_id = provenance_run.get("venue_id")
                effective_deadline_id = provenance_run.get("deadline_id")

        for table, entity_id in (
            ("team_profiles", effective_team_profile_id),
            ("venues", effective_venue_id),
            ("deadlines", effective_deadline_id),
            ("campaigns", effective_campaign_id),
        ):
            if entity_id is not None:
                _require_row(db, table, entity_id)
        frozen_metadata = dict(body.metadata)
        # Prevent caller-controlled lookalikes.  Only this route writes reserved
        # lineage metadata after verifying the source campaign and frozen files.
        frozen_metadata.pop("training_provenance", None)
        frozen_metadata.pop("selected_candidate", None)
        if training_provenance is not None:
            frozen_metadata["training_provenance"] = training_provenance
        if run and candidate and conditioned_binding:
            frozen_metadata["selected_candidate"] = {
                "schema_version": "argus-flywheel/selected-candidate-v2",
                "candidate_id": candidate["id"],
                "candidate_key": candidate["candidate_key"],
                "ideation_run_id": run["id"],
                "execution_campaign_id": effective_campaign_id,
                "ideation_campaign_id": ideation_campaign_id,
                "condition_sha256": conditioned_binding["condition_sha256"],
                "artifact_sha256": candidate["artifact_sha256"],
                "candidate_artifact_sha256": conditioned_binding[
                    "candidate_artifact_sha256"
                ],
                "candidate_record_sha256": conditioned_binding[
                    "candidate_record_sha256"
                ],
                "candidate_input_sha256": conditioned_binding[
                    "candidate_input_sha256"
                ],
                "candidate_prompt_sha256": conditioned_binding[
                    "candidate_prompt_sha256"
                ],
                "candidate_snapshot": candidate["candidate"],
                "run_objective_sha256": run["objective_sha256"],
                "binding_receipt": conditioned_binding,
            }
        _reject_secrets(frozen_metadata)
        episode_id = str(uuid.uuid4())
        now = utc_now()
        automatic_links: list[tuple[str, Any, str, dict[str, Any]]] = []
        if body.ideation_run_id:
            automatic_links.append(
                (
                    "ideation_run",
                    body.ideation_run_id,
                    "ideation_source",
                    {
                        "condition_sha256": conditioned_binding["condition_sha256"],
                        "objective_sha256": conditioned_binding[
                            "parent_objective_sha256"
                        ],
                        "binding_receipt_sha256": conditioned_binding["receipt_sha256"],
                    },
                )
            )
        if body.candidate_id:
            automatic_links.append(
                (
                    "generated_idea_candidate",
                    body.candidate_id,
                    "selected_candidate",
                    {
                        "candidate_artifact_sha256": conditioned_binding[
                            "candidate_artifact_sha256"
                        ],
                        "candidate_record_sha256": conditioned_binding[
                            "candidate_record_sha256"
                        ],
                        "binding_receipt_sha256": conditioned_binding["receipt_sha256"],
                    },
                )
            )
        if ideation_campaign_id and ideation_campaign_id != effective_campaign_id:
            automatic_links.append(
                (
                    "campaign",
                    ideation_campaign_id,
                    "ideation_source",
                    {
                        "condition_sha256": conditioned_binding["condition_sha256"],
                        "objective_sha256": conditioned_binding[
                            "parent_objective_sha256"
                        ],
                        "execution_campaign_id": effective_campaign_id,
                    },
                )
            )
        if effective_campaign_id:
            automatic_links.append(
                (
                    "campaign",
                    effective_campaign_id,
                    "execution" if training_provenance is not None else "associated_campaign",
                    {
                        "condition_sha256": conditioned_binding[
                            "condition_sha256"
                        ] if conditioned_binding else None,
                        "candidate_artifact_sha256": conditioned_binding[
                            "candidate_artifact_sha256"
                        ] if conditioned_binding else None,
                        "candidate_record_sha256": conditioned_binding[
                            "candidate_record_sha256"
                        ] if conditioned_binding else None,
                        "candidate_input_sha256": conditioned_binding[
                            "candidate_input_sha256"
                        ] if conditioned_binding else None,
                        "candidate_prompt_sha256": conditioned_binding[
                            "candidate_prompt_sha256"
                        ] if conditioned_binding else None,
                        "binding_receipt": conditioned_binding,
                    }
                    if conditioned_binding
                    else (
                        {"training_provenance": training_provenance}
                        if training_provenance is not None
                        else {}
                    ),
                )
            )
        with db.transaction() as connection:
            connection.execute(
                "INSERT INTO research_episodes"
                "(id,title,objective,state,team_profile_id,venue_id,deadline_id,campaign_id,"
                "training_consent,license_basis,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,'active',?,?,?,?,?,?,?,?,?)",
                (
                    episode_id,
                    body.title,
                    body.objective,
                    effective_team_profile_id,
                    effective_venue_id,
                    effective_deadline_id,
                    effective_campaign_id,
                    int(body.training_consent),
                    body.license_basis,
                    canonical_json(frozen_metadata),
                    now,
                    now,
                ),
            )
            for entity_type, entity_id, relation, link_metadata in automatic_links:
                connection.execute(
                    "INSERT INTO episode_entity_links"
                    "(id,episode_id,entity_type,entity_id,relation,metadata_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        episode_id,
                        entity_type,
                        entity_id,
                        relation,
                        canonical_json(link_metadata),
                        now,
                    ),
                )
            for link in body.links:
                connection.execute(
                    "INSERT INTO episode_entity_links"
                    "(id,episode_id,entity_type,entity_id,relation,metadata_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        str(uuid.uuid4()),
                        episode_id,
                        link.entity_type,
                        link.entity_id,
                        link.relation,
                        canonical_json(link.metadata),
                        now,
                    ),
                )
        db.append_event(
            "flywheel.episode",
            "episode_created",
            entity_type="research_episode",
            entity_id=episode_id,
            payload={"title": body.title},
        )
        return _service(request).episode_detail(episode_id)

    @router.get("/episodes/{episode_id}")
    def episode_detail(episode_id: str, request: Request) -> dict[str, Any]:
        try:
            return _service(request).episode_detail(episode_id)
        except FlywheelDataError as exc:
            raise _not_found_or_conflict(exc) from exc

    @router.post("/episodes/{episode_id}/seal", status_code=201)
    def seal_episode(
        episode_id: str, body: EpisodeSealRequest, request: Request
    ) -> dict[str, Any]:
        try:
            revision = _service(request).seal(
                episode_id,
                actor=body.actor,
                reason=body.reason,
                terminal_state=body.terminal_state,
            )
        except FlywheelDataError as exc:
            raise _not_found_or_conflict(exc) from exc
        _db(request).append_event(
            "flywheel.episode",
            "episode_revision_sealed",
            entity_type="research_episode",
            entity_id=episode_id,
            payload={"revision_id": revision["id"], "manifest_sha256": revision["manifest_sha256"]},
        )
        return revision

    @router.get("/episodes/{episode_id}/verify")
    @router.post("/episodes/{episode_id}/verify")
    def verify_episode(episode_id: str, request: Request) -> dict[str, Any]:
        try:
            return _service(request).verify_episode(episode_id)
        except FlywheelDataError as exc:
            raise _not_found_or_conflict(exc) from exc

    @router.post("/team-intakes/extract", status_code=201)
    def extract_team_intake(
        body: TeamIntakeExtractRequest, request: Request
    ) -> dict[str, Any]:
        _reject_secrets(body.raw_text)
        extracted, uncertainties = _extract_team_profile(body.raw_text)
        intake_id = str(uuid.uuid4())
        now = utc_now()
        _db(request).execute(
            "INSERT INTO team_intakes"
            "(id,state,raw_text,extracted_json,uncertainties_json,created_at,updated_at) "
            "VALUES(?,'draft',?,?,?,?,?)",
            (
                intake_id,
                body.raw_text,
                canonical_json(extracted),
                canonical_json(uncertainties),
                now,
                now,
            ),
        )
        return {
            "id": intake_id,
            "state": "draft",
            "raw_text": body.raw_text,
            "extracted": extracted,
            "uncertainties": uncertainties,
        }

    @router.post("/team-intakes/{intake_id}/confirm")
    def confirm_team_intake(
        intake_id: str, body: TeamIntakeConfirmRequest, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        _reject_secrets(body.model_dump())
        expertise = body.profile.get("expertise", [])
        methods = body.profile.get("methods", [])
        data_access = body.profile.get("data_access", [])
        constraints = body.profile.get("constraints", {})
        goals = body.profile.get("goals", {})
        policy = body.profile.get("policy", {})
        if not all(isinstance(item, list) for item in (expertise, methods, data_access)):
            raise HTTPException(422, "expertise, methods and data_access must be arrays")
        if not all(isinstance(item, dict) for item in (constraints, goals, policy)):
            raise HTTPException(422, "constraints, goals and policy must be objects")
        team_profile_id = str(uuid.uuid4())
        now = utc_now()
        with db.transaction() as connection:
            # BEGIN IMMEDIATE is acquired by Database.transaction before this read.
            # The state check and both writes therefore form one one-shot CAS.
            row = connection.execute(
                "SELECT state FROM team_intakes WHERE id=?", (intake_id,)
            ).fetchone()
            if not row:
                raise HTTPException(404, f"team intake not found: {intake_id}")
            if row["state"] != "draft":
                raise HTTPException(409, "team intake is already confirmed")
            connection.execute(
                "INSERT INTO team_profiles"
                "(id,name,expertise_json,methods_json,data_access_json,constraints_json,goals_json,"
                "policy_json,training_consent,license_basis,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?, ?,?)",
                (
                    team_profile_id,
                    body.name or f"Flywheel team {intake_id[:8]}",
                    canonical_json(expertise),
                    canonical_json(methods),
                    canonical_json(data_access),
                    canonical_json(constraints),
                    canonical_json(goals),
                    canonical_json(policy),
                    int(body.training_consent),
                    body.license_basis,
                    canonical_json({"source_team_intake_id": intake_id}),
                    now,
                    now,
                ),
            )
            updated = connection.execute(
                "UPDATE team_intakes SET state='confirmed',confirmed_profile_json=?,team_profile_id=?,"
                "confirmed_by=?,confirmed_at=?,updated_at=? WHERE id=? AND state='draft'",
                (canonical_json(body.profile), team_profile_id, body.actor, now, now, intake_id),
            )
            if updated.rowcount != 1:
                raise HTTPException(409, "team intake confirmation changed concurrently")
        return {
            "id": intake_id,
            "state": "confirmed",
            "team_profile_id": team_profile_id,
            "profile": body.profile,
            "confirmed_by": body.actor,
            "confirmed_at": now,
        }

    @router.post("/episodes/{episode_id}/review-imports", status_code=201)
    def create_review_import(
        episode_id: str, body: ReviewImportCreate, request: Request
    ) -> dict[str, Any]:
        service = _service(request)
        try:
            service.require_episode(episode_id)
        except FlywheelDataError as exc:
            raise _not_found_or_conflict(exc) from exc
        _reject_secrets(body.model_dump())
        parsed, raw_payload = _review_draft(body)
        batch_id = str(uuid.uuid4())
        now = utc_now()
        _db(request).execute(
            "INSERT INTO review_import_batches"
            "(id,episode_id,source_kind,source_ref,state,raw_payload_json,parsed_json,created_at,updated_at) "
            "VALUES(?,?,?,?, 'draft',?,?,?,?)",
            (
                batch_id,
                episode_id,
                body.source_kind,
                body.source_ref or "",
                canonical_json(raw_payload),
                canonical_json(parsed),
                now,
                now,
            ),
        )
        return {
            "id": batch_id,
            "episode_id": episode_id,
            "state": "draft",
            "source_kind": body.source_kind,
            "source_ref": body.source_ref or "",
            "raw_object_sha256": None,
            "parsed": parsed,
            "needs_human_confirmation": True,
            "manual_redaction_required": body.source_kind == "pdf",
            "fetch_performed": False,
        }

    @router.post("/episodes/{episode_id}/review-imports/openreview", status_code=201)
    def fetch_openreview_import(
        episode_id: str, body: OpenReviewFetchRequest, request: Request
    ) -> dict[str, Any]:
        service = _service(request)
        try:
            service.require_episode(episode_id)
        except FlywheelDataError as exc:
            raise _not_found_or_conflict(exc) from exc
        _reject_secrets(body.model_dump())
        try:
            parsed, raw_bytes, source_ref = _fetch_openreview_forum(body.forum_id)
        except FlywheelDataError as exc:
            raise HTTPException(502, str(exc)) from exc
        batch_id = str(uuid.uuid4())
        now = utc_now()
        _db(request).execute(
            "INSERT INTO review_import_batches"
            "(id,episode_id,source_kind,source_ref,state,raw_payload_json,parsed_json,created_at,updated_at) "
            "VALUES(?,?, 'openreview',?, 'draft',?,?,?,?)",
            (
                batch_id,
                episode_id,
                source_ref,
                canonical_json(
                    {
                        "media_type": "application/json",
                        "content_utf8": raw_bytes.decode("utf-8"),
                    }
                ),
                canonical_json(parsed),
                now,
                now,
            ),
        )
        return {
            "id": batch_id,
            "episode_id": episode_id,
            "state": "draft",
            "source_kind": "openreview",
            "source_ref": source_ref,
            "raw_object_sha256": None,
            "parsed": parsed,
            "needs_human_confirmation": True,
            "manual_redaction_required": False,
            "fetch_performed": True,
        }

    @router.post("/review-imports/{batch_id}/confirm")
    def confirm_review_import(
        batch_id: str, body: ReviewImportConfirm, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        row = db.fetch_one("SELECT * FROM review_import_batches WHERE id=?", (batch_id,))
        if not row:
            raise HTTPException(404, f"review import not found: {batch_id}")
        if row["state"] != "draft":
            raise HTTPException(409, f"review import is already {row['state']}")
        parsed = body.parsed if body.parsed is not None else json.loads(row["parsed_json"])
        _reject_secrets(body.model_dump())
        _reject_secrets(parsed)
        raw_payload = json.loads(row["raw_payload_json"])
        try:
            raw_bytes, media_type, filename = _staged_review_bytes(raw_payload)
            obj = _service(request).objects.put_bytes(
                raw_bytes,
                media_type=media_type,
                metadata={
                    "source_kind": row["source_kind"],
                    "source_ref": row["source_ref"],
                    "filename": filename,
                    "stage": "review_import_confirmed",
                },
            )
        except (KeyError, TypeError, ValueError, FlywheelDataError) as exc:
            raise HTTPException(422, f"review import payload cannot be sealed: {exc}") from exc
        now = utc_now()
        with db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE review_import_batches SET state='confirmed',parsed_json=?,redaction_confirmed=1,"
                "raw_object_sha256=?,training_consent=?,license_basis=?,confirmed_by=?,confirmed_at=?,updated_at=? "
                "WHERE id=? AND state='draft'",
                (
                    canonical_json(parsed),
                    obj.sha256,
                    int(body.training_consent),
                    body.license_basis,
                    body.actor,
                    now,
                    now,
                    batch_id,
                ),
            )
            if cursor.rowcount != 1:
                raise FlywheelDataError("review import confirmation race detected")
            connection.execute(
                "INSERT OR IGNORE INTO episode_entity_links"
                "(id,episode_id,entity_type,entity_id,relation,metadata_json,created_at) "
                "VALUES(?,?,?,?,?,'{}',?)",
                (
                    str(uuid.uuid4()),
                    row["episode_id"],
                    "content_object",
                    obj.sha256,
                    "review_evidence",
                    now,
                ),
            )
        return {
            "id": batch_id,
            "episode_id": row["episode_id"],
            "state": "confirmed",
            "source_kind": row["source_kind"],
            "raw_object_sha256": obj.sha256,
            "parsed": parsed,
            "redaction_confirmed": True,
            "redaction_scan_state": obj.redaction_scan_state,
            "manual_redaction_required": obj.manual_redaction_required,
            "manual_redaction_confirmed": bool(
                obj.manual_redaction_required and body.redaction_confirmed
            ),
            "training_consent": body.training_consent,
            "license_basis": body.license_basis,
            "confirmed_by": body.actor,
            "confirmed_at": now,
        }

    @router.post("/review-imports/{batch_id}/discard")
    def discard_review_import(
        batch_id: str, body: ReviewImportDiscard, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        row = db.fetch_one("SELECT * FROM review_import_batches WHERE id=?", (batch_id,))
        if not row:
            raise HTTPException(404, f"review import not found: {batch_id}")
        if row["state"] != "draft":
            raise HTTPException(409, f"only draft review imports can be discarded; state={row['state']}")
        _reject_secrets(body.model_dump())
        now = utc_now()
        with db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE review_import_batches SET state='discarded',discarded_by=?,"
                "discarded_at=?,discard_reason=?,updated_at=? WHERE id=? AND state='draft'",
                (body.actor, now, body.reason, now, batch_id),
            )
            if cursor.rowcount != 1:
                raise FlywheelDataError("review import discard race detected")
        db.append_event(
            "flywheel.review",
            "review_import_discarded",
            entity_type="review_import_batch",
            entity_id=batch_id,
            payload={"episode_id": row["episode_id"], "discarded_by": body.actor},
        )
        return {
            "id": batch_id,
            "episode_id": row["episode_id"],
            "state": "discarded",
            "discarded_by": body.actor,
            "discard_reason": body.reason,
            "discarded_at": now,
        }

    @router.post("/dataset-snapshots/preview")
    def preview_dataset(body: DatasetSelection, request: Request) -> dict[str, Any]:
        return selection_preview(
            _service(request),
            episode_ids=body.episode_ids,
            require_training_consent=body.require_training_consent,
        )

    @router.get("/dataset-snapshots")
    def list_dataset_snapshots(request: Request) -> dict[str, Any]:
        rows = decode_rows(
            _db(request).fetch_all("SELECT * FROM dataset_snapshots ORDER BY created_at DESC,id")
        )
        for row in rows:
            count = _db(request).fetch_one(
                "SELECT COUNT(*) AS count FROM dataset_snapshot_members WHERE snapshot_id=?",
                (row["id"],),
            )
            row["member_count"] = count["count"] if count else 0
        return {"items": rows}

    @router.post("/dataset-snapshots", status_code=201)
    def create_dataset_snapshot(
        body: DatasetSnapshotCreate, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        service = _service(request)
        snapshot_id = str(uuid.uuid4())
        now = utc_now()
        with db.transaction() as connection:
            # Hold the SQLite writer reservation while eligibility is recomputed
            # and persisted. A review confirmation/reseal cannot interleave
            # between the digest comparison and immutable member insertion.
            preview = selection_preview(
                service,
                episode_ids=body.episode_ids,
                require_training_consent=body.require_training_consent,
            )
            if preview["selection_sha256"] != body.expected_selection_sha256:
                raise HTTPException(409, "dataset selection changed; preview again before sealing")
            if not preview["eligible"]:
                raise HTTPException(409, "dataset snapshot requires at least one eligible revision")
            selection = {
                "schema_version": "flywheel.dataset-selection/1",
                "require_training_consent": body.require_training_consent,
                "requested_episode_ids": sorted(set(body.episode_ids)),
                "excluded": preview["excluded"],
            }
            manifest = {
                "schema_version": "flywheel.dataset-snapshot/1",
                "id": snapshot_id,
                "name": body.name,
                "selection_sha256": preview["selection_sha256"],
                "members": preview["eligible"],
                "license_basis": body.license_basis,
                "created_by": body.actor,
                "created_at": now,
                "training_started": False,
            }
            _reject_secrets(manifest)
            manifest_json = canonical_json(manifest)
            manifest_sha256 = sha256_text(manifest_json)
            connection.execute(
                "INSERT INTO dataset_snapshots"
                "(id,name,selection_json,selection_sha256,manifest_json,manifest_sha256,member_count,"
                "license_basis,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot_id,
                    body.name,
                    canonical_json(selection),
                    preview["selection_sha256"],
                    manifest_json,
                    manifest_sha256,
                    len(preview["eligible"]),
                    body.license_basis,
                    body.actor,
                    now,
                ),
            )
            for member in preview["eligible"]:
                connection.execute(
                    "INSERT INTO dataset_snapshot_members"
                    "(snapshot_id,revision_id,episode_id,manifest_sha256,created_at) VALUES(?,?,?,?,?)",
                    (
                        snapshot_id,
                        member["revision_id"],
                        member["episode_id"],
                        member["manifest_sha256"],
                        now,
                    ),
                )
        return {
            "id": snapshot_id,
            "name": body.name,
            "selection_sha256": preview["selection_sha256"],
            "manifest_sha256": manifest_sha256,
            "member_count": len(preview["eligible"]),
            "manifest": manifest,
            "immutable": True,
            "training_started": False,
        }

    @router.get("/dataset-snapshots/{snapshot_id}")
    def dataset_snapshot_detail(snapshot_id: str, request: Request) -> dict[str, Any]:
        db = _db(request)
        row = db.fetch_one("SELECT * FROM dataset_snapshots WHERE id=?", (snapshot_id,))
        if not row:
            raise HTTPException(404, f"dataset snapshot not found: {snapshot_id}")
        output = decode_row(row) or {}
        output["members"] = db.fetch_all(
            "SELECT * FROM dataset_snapshot_members WHERE snapshot_id=? ORDER BY episode_id,revision_id",
            (snapshot_id,),
        )
        return output

    @router.get("/dataset-snapshots/{snapshot_id}/verify")
    @router.post("/dataset-snapshots/{snapshot_id}/verify")
    def verify_dataset_snapshot(snapshot_id: str, request: Request) -> dict[str, Any]:
        return _snapshot_verify(_db(request), _service(request), snapshot_id)

    return router
