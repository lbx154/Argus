from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)

from .db import Database, decode_row, decode_rows, utc_now
from .integrations.argus_webapi import (
    ArgusDaemonCommandError,
    ArgusWebApiClient,
    ArgusWebApiError,
    argus_connection_metadata,
    assess_argus_connection,
    require_argus_daemon_command_applied,
)
from .integrations.release_monitor import ReleaseRegistryError
from .integrations.release_stager import ReleaseStageError
from .models import (
    ApprovalDecision,
    CampaignAction,
    CampaignCreate,
    CampaignStartRequest,
    CampaignUpdate,
    ConnectionCreate,
    ConnectionUpdate,
    LockedContractRequest,
    ReleaseStageRequest,
    ReminderCreate,
    ResourceCreate,
    ResourceUpdate,
    ReviewPanelRequest,
    ReviewRequest,
    SettingsPatch,
)
from .seeding import seed_database
from .serialization import connection_public, connections_public
from .services import (
    ViewerQueue,
    build_evidence_snapshot,
    build_pipeline,
    compile_prompt,
    differentiate_idea,
    inspect_release,
    probe_resources,
    record_release_inspection,
    stage_release,
)
from .services import (
    sync_sources as sync_external_sources,
)
from .services.calendar_export import build_ical_calendar
from .services.campaign_binding import (
    CampaignBindingError,
    canonical_bytes,
    validate_campaign_binding,
)
from .services.ideation import CANDIDATE_MANIFEST_SCHEMA, CANDIDATE_RESEARCH_SCHEMA


def _db(request: Request) -> Database:
    return request.app.state.db


def _canonical_websocket_origin(value: str) -> str | None:
    """Return a comparable browser origin or fail closed for malformed values."""
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    hostname = hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def _require(db: Database, table: str, entity_id: str | int) -> dict[str, Any]:
    if table not in {"venues", "ideas", "campaigns", "connections", "resources", "deadlines"}:
        raise RuntimeError("invalid internal table")
    row = db.fetch_one(f"SELECT * FROM {table} WHERE id=?", (entity_id,))
    if not row:
        raise HTTPException(404, f"{table[:-1].capitalize()} not found: {entity_id}")
    return row


def _extract_project_id(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("sid", "project_id", "session_id"):
            if payload.get(key):
                return str(payload[key])
        for value in payload.values():
            found = _extract_project_id(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _extract_project_id(value)
            if found:
                return found
    return None


def _extract_remote_workdir(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("workdir", "launch_cwd", "workspace"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        for value in payload.values():
            found = _extract_remote_workdir(value)
            if found:
                return found
    if isinstance(payload, list):
        for value in payload:
            found = _extract_remote_workdir(value)
            if found:
                return found
    return None


def _client(request: Request, connection: dict[str, Any]) -> ArgusWebApiClient:
    _validate_connection_token_ref(request, connection)
    token = request.app.state.secrets.resolve(
        connection["id"],
        connection.get("token_ref"),
        endpoint=connection.get("base_url"),
    )
    return ArgusWebApiClient(connection["base_url"], token=token)


def _validate_connection_token_env(
    request: Request, token_env: str | None, endpoint: str
) -> None:
    """Allow persisted env references only to the server-owned Argus token name.

    A connection base URL is operator-controlled. Letting the same request name
    an arbitrary process environment variable would turn the connection probe
    into a credential exfiltration primitive. Literal one-run tokens remain in
    the process-local vault and never pass through this path.
    """

    if token_env is None:
        return
    allowed = str(request.app.state.settings.argus_token_env or "").strip()
    if not allowed or token_env != allowed:
        raise HTTPException(
            422,
            "token_env must equal the server-managed Argus token environment name",
        )
    try:
        request.app.state.secrets.validate_env_reference(token_env, endpoint)
    except ValueError as exc:
        raise HTTPException(
            422,
            "token_env may only be used with the server-managed Argus base URL",
        ) from exc


def _validate_connection_token_ref(request: Request, connection: dict[str, Any]) -> None:
    """Reject legacy/forged arbitrary env references before any network call."""

    reference = str(connection.get("token_ref") or "")
    if reference.startswith("env:"):
        _validate_connection_token_env(
            request, reference[4:], str(connection.get("base_url") or "")
        )


async def _freeze_viewer_evidence(
    campaign: dict[str, Any], request: Request
) -> Any:
    """Freeze one bounded, allowlisted evidence snapshot for a review batch."""

    if not campaign.get("argus_project_id") or not campaign.get("connection_id"):
        raise HTTPException(
            409,
            "Independent review requires an attached Argus project and connection",
        )
    connection = _require(_db(request), "connections", campaign["connection_id"])
    client = _client(request, connection)
    last_snapshot = (
        campaign.get("last_snapshot")
        if isinstance(campaign.get("last_snapshot"), dict)
        else {}
    )
    artifact_index = last_snapshot.get("foundry_artifacts")
    if not isinstance(artifact_index, list):
        try:
            artifact_index = await asyncio.to_thread(
                client.artifacts, campaign["argus_project_id"]
            )
        except (ArgusWebApiError, ValueError) as exc:
            raise HTTPException(
                502,
                "Could not freeze an Argus evidence index for independent review: "
                + str(exc),
            ) from exc
    try:
        return await asyncio.to_thread(
            build_evidence_snapshot,
            campaign,
            client,
            artifact_index,
            output_root=(
                request.app.state.settings.data_dir
                / "viewer"
                / "evidence-snapshots"
            ),
        )
    except (ArgusWebApiError, ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(
            502, "Could not freeze bounded Viewer evidence: " + str(exc)
        ) from exc


def _enqueue_independent_review(
    *,
    campaign: dict[str, Any],
    reviewer_kind: str,
    rubric: dict[str, Any],
    approval: dict[str, Any],
    evidence: Any,
    request: Request,
) -> dict[str, Any]:
    """Persist and launch one independent Viewer request from frozen evidence."""

    db = _db(request)
    review_id = str(uuid.uuid4())
    now = utc_now()
    db.execute(
        "INSERT INTO reviews(id,campaign_id,reviewer_kind,state,feedback_json,created_at,updated_at) "
        "VALUES(?,?,?,'queued',?,?,?)",
        (
            review_id,
            campaign["id"],
            reviewer_kind,
            json.dumps(
                {"rubric": rubric, "human_review_approval": approval},
                ensure_ascii=False,
            ),
            now,
            now,
        ),
    )
    db.execute(
        "UPDATE campaigns SET review_state='queued',updated_at=? WHERE id=?",
        (now, campaign["id"]),
    )
    queue = ViewerQueue(request.app.state.settings.data_dir / "viewer")
    viewer_response = queue.enqueue(
        {
            "request_id": review_id,
            "campaign_id": campaign["id"],
            "reviewer_kind": reviewer_kind,
            "venue": {"name": campaign["venue_name"], "key": campaign["venue_key"]},
            "rubric_weights": rubric.get("weights", {}),
            "human_review_approval": approval,
            **evidence.viewer_request_fields(),
        }
    )
    viewer_argv = [
        sys.executable,
        "-m",
        "foundry.workers.viewer_worker",
        "--queue-dir",
        str(request.app.state.settings.data_dir / "viewer"),
        "--once",
    ]
    if request.app.state.settings.viewer_evaluator_command:
        viewer_argv.extend(
            [
                "--evaluator-command-json",
                json.dumps(request.app.state.settings.viewer_evaluator_command),
                "--evaluator-work-root",
                str(request.app.state.settings.data_dir / "viewer" / "evaluator-work"),
                "--evaluator-timeout",
                str(request.app.state.settings.viewer_evaluator_timeout_seconds),
            ]
        )
    try:
        viewer_process = subprocess.Popen(
            viewer_argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
        )
    except OSError as exc:
        db.execute(
            "UPDATE reviews SET state='failed',feedback_json=?,updated_at=? WHERE id=?",
            (
                json.dumps(
                    {
                        "rubric": rubric,
                        "human_review_approval": approval,
                        "worker_start_error": str(exc),
                    },
                    ensure_ascii=False,
                ),
                utc_now(),
                review_id,
            ),
        )
        raise HTTPException(500, "Viewer worker could not be started") from exc
    db.append_event(
        "reviews",
        "review.queued",
        entity_type="campaign",
        entity_id=campaign["id"],
        payload={
            "review_id": review_id,
            "reviewer_kind": reviewer_kind,
            "viewer_worker_pid": viewer_process.pid,
            "evaluator_configured": bool(
                request.app.state.settings.viewer_evaluator_command
            ),
            "evidence_snapshot_sha256": evidence.sha256,
            "evidence_snapshot_state": evidence.state,
            "evidence_artifact_count": evidence.artifact_count,
            "human_review_approval": approval,
        },
    )
    return {
        "review_id": review_id,
        "reviewer_kind": reviewer_kind,
        "viewer_worker_pid": viewer_process.pid,
        "evaluator_configured": bool(
            request.app.state.settings.viewer_evaluator_command
        ),
        "evidence_snapshot_sha256": evidence.sha256,
        "evidence_snapshot_state": evidence.state,
        "evidence_artifact_count": evidence.artifact_count,
        **viewer_response,
    }


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    # Write the exact bytes whose SHA-256 is recorded in our manifests.  On
    # Windows, Path.write_text/newline=None translates LF to CRLF, which makes
    # the on-disk objective differ from prompt_sha256 even though read_text()
    # later hides the difference through universal-newline decoding.
    temporary.write_bytes(content.encode("utf-8"))
    temporary.replace(path)


def _read_utf8_bytes(path: Path) -> tuple[bytes, str]:
    payload = path.read_bytes()
    return payload, payload.decode("utf-8")


_BASE_PREFLIGHT_ATTESTATIONS = (
    "compute_inventory_and_capacity_verified",
    "data_access_and_license_reviewed",
    "non_compute_prerequisites_reviewed",
)
_DOMAIN_PREFLIGHT_ATTESTATIONS = {
    "HI": ("human_subjects_and_ethics_path_reviewed",),
    "SC": ("dual_use_and_disclosure_path_reviewed",),
    "CT": ("proof_expertise_and_checker_plan_reviewed",),
}

_FULL_COMMIT_SHA = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
_CONNECTION_PROBE_METADATA_KEYS = frozenset(
    {
        "argus_revision",
        "argus_release_id",
        "argus_package_version",
        "argus_worktree",
        "protocol",
        "protocol_compatible",
        "snapshot_schema_version",
        "snapshot_contract",
        "capabilities",
        "feature_support",
        "backend_ready",
        "system_doctor",
        "launch_compatible",
        "missing_capabilities",
    }
)


def _validated_preflight(config: dict[str, Any], category_id: str) -> dict[str, bool]:
    raw = config.get("preflight_attestations")
    attestations = raw if isinstance(raw, dict) else {}
    required = (
        *_BASE_PREFLIGHT_ATTESTATIONS,
        *_DOMAIN_PREFLIGHT_ATTESTATIONS.get(category_id, ()),
    )
    missing = [key for key in required if attestations.get(key) is not True]
    if missing:
        raise HTTPException(
            409,
            "Preflight attestations are incomplete: " + "; ".join(missing),
        )
    return {key: True for key in required}


def _campaign_id_from_approval_id(approval_id: str) -> str:
    prefix = "approval-"
    if not approval_id.startswith(prefix):
        raise HTTPException(422, "approval_id must use the form approval-<campaign UUID>")
    candidate = approval_id[len(prefix):]
    try:
        parsed = uuid.UUID(candidate)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(422, "approval_id must contain a valid campaign UUID") from exc
    canonical = str(parsed)
    if candidate != canonical:
        raise HTTPException(422, "approval_id must contain a canonical lowercase campaign UUID")
    return canonical


def _with_campaign_release_state(
    campaign: dict[str, Any], data_dir: Path
) -> dict[str, Any]:
    """Expose a truthful release reference without inferring a pin from telemetry."""

    result = dict(campaign)
    config = result.get("config") if isinstance(result.get("config"), dict) else {}
    configured = str(config.get("argus_release_sha") or "").strip()
    reference = configured or None
    source = "campaign_config_reference" if configured else None
    pinned = False

    manifest_path = Path(data_dir) / "campaigns" / str(result.get("id", "")) / "manifest.json"
    objective_path = manifest_path.with_name("OBJECTIVE.md")
    if result.get("launch_command_id") and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            objective_sha = hashlib.sha256(objective_path.read_bytes()).hexdigest()
        except (OSError, UnicodeError, json.JSONDecodeError):
            manifest = None
            objective_sha = None
        if (
            isinstance(manifest, dict)
            and manifest.get("campaign_id") == result.get("id")
            and manifest.get("launch_command_id") == result.get("launch_command_id")
            and manifest.get("prompt_sha256") == objective_sha
        ):
            manifest_sha = str(manifest.get("argus_release_sha") or "").strip()
            manifest_pinned = manifest.get("release_pinned") is True
            if manifest_sha:
                reference = manifest_sha
                source = manifest.get("release_reference_source") or "launch_manifest_reference"
            pinned = bool(
                manifest_pinned
                and manifest_sha
                and _FULL_COMMIT_SHA.fullmatch(manifest_sha)
                and manifest.get("release_pin_source") == "launch_compatible_target_probe"
            )

    result.update(
        {
            "release_pinned": pinned,
            "release_reference": reference,
            "release_reference_source": source,
        }
    )
    return result


def _load_frozen_launch_packet(
    campaign_dir: Path,
    workspace: Path,
    campaign: dict[str, Any],
    connection: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Load and validate an immutable launch packet for an idempotent retry."""
    objective_path = campaign_dir / "OBJECTIVE.md"
    manifest_path = campaign_dir / "manifest.json"
    try:
        objective_bytes, objective = _read_utf8_bytes(objective_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            409,
            "Frozen launch packet is missing or corrupt; manual recovery is required",
        ) from exc
    if not isinstance(manifest, dict):
        raise HTTPException(409, "Frozen launch manifest must be a JSON object")
    command_id = campaign.get("launch_command_id")
    if manifest.get("campaign_id") != campaign["id"]:
        raise HTTPException(409, "Frozen launch manifest belongs to a different campaign")
    if manifest.get("launch_command_id") != command_id:
        raise HTTPException(409, "Frozen launch command does not match the campaign receipt")
    actual_prompt_hash = hashlib.sha256(objective_bytes).hexdigest()
    if manifest.get("prompt_sha256") != actual_prompt_hash:
        raise HTTPException(409, "Frozen objective hash does not match the launch manifest")
    frozen_connection = manifest.get("connection")
    if not isinstance(frozen_connection, dict):
        raise HTTPException(409, "Frozen launch manifest has no connection identity")
    if (
        frozen_connection.get("id") != connection["id"]
        or frozen_connection.get("kind") != connection["kind"]
        or str(frozen_connection.get("base_url", "")).rstrip("/")
        != str(connection["base_url"]).rstrip("/")
    ):
        raise HTTPException(
            409,
            "Argus connection changed after launch; the frozen receipt cannot be retried safely",
        )
    launch = manifest.get("launch")
    if not isinstance(launch, dict) or not isinstance(launch.get("name"), str):
        raise HTTPException(409, "Frozen launch manifest has no immutable launch arguments")
    if connection["kind"] == "remote":
        if (
            launch.get("workdir") != ""
            or launch.get("launch_cwd") != ""
            or launch.get("workspace_mode") != "target_argus_default"
        ):
            raise HTTPException(409, "Frozen remote launch must use target-side workspace allocation")
    else:
        expected_workspace = str(workspace)
        if (
            launch.get("workdir") != expected_workspace
            or launch.get("launch_cwd") != expected_workspace
            or launch.get("workspace_mode") != "foundry_local_isolated"
        ):
            raise HTTPException(409, "Frozen launch paths no longer match the campaign workspace")
    return objective, manifest


def _utc_schedule(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"{field} must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise HTTPException(422, f"{field} must include an explicit UTC offset")
    return parsed.astimezone(UTC).isoformat()


def _domain_profile(request: Request, category_id: str, idea: dict[str, Any]) -> dict[str, Any]:
    path = request.app.state.settings.seed_data_dir / "domain_evidence.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        profile = (document.get("domains") or {}).get(category_id)
        if isinstance(profile, dict) and profile.get("evidence_requirements"):
            return profile
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "name": category_id or "Research domain",
        "evidence_requirements": [
            idea.get("decisive_experiments") or "freeze a decisive falsifier",
            idea.get("public_data_or_tasks") or "use traceable public evidence",
        ],
    }


def _resource_contract(
    resource: dict[str, Any] | None,
    deadline: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    capacity = (resource or {}).get("capacity") or {}
    overrides = config or {}
    gpu_count = capacity.get("gpu_count")
    if gpu_count is None:
        gpu_count = len(capacity.get("devices") or [])
    wall_clock_deadline = overrides.get("wall_clock_deadline") or capacity.get(
        "wall_clock_deadline"
    )
    conservative_cutoff = None
    deadline_basis = "operator_internal_cutoff"
    if deadline:
        if deadline.get("evidence_status") == "official_confirmed":
            conservative_cutoff = deadline.get("deadline_date")
            deadline_basis = "official_deadline_date"
        else:
            conservative_cutoff = deadline.get("forecast_window_start") or deadline.get(
                "deadline_date"
            )
            deadline_basis = "forecast_window_start"
    gpu_model = capacity.get("gpu_model")
    if gpu_count == 0 and (not isinstance(gpu_model, str) or not gpu_model.strip()):
        gpu_model = "API-only"
    values = {
        "gpu_count": gpu_count,
        "gpu_model": gpu_model,
        "gpu_hours": overrides.get("gpu_hours", capacity.get("gpu_hours", "REQUIRED_BEFORE_LAUNCH")),
        # Never infer a clock time from a bare conference date.  The operator must
        # choose an explicit, timezone-aware cutoff for a reproducible launch.
        "wall_clock_deadline": wall_clock_deadline or "REQUIRED_BEFORE_LAUNCH",
        "planning_cutoff_date": conservative_cutoff,
        "planning_cutoff_basis": deadline_basis,
        "max_parallel_jobs": overrides.get(
            "max_parallel_jobs", capacity.get("max_parallel_jobs", 1)
        ),
        "api_budget": overrides.get("api_budget", capacity.get("api_budget", "REQUIRED_BEFORE_LAUNCH")),
    }
    missing: list[str] = []
    if not resource or resource.get("resource_type") == "unconfigured" or not capacity.get("configured"):
        missing.append("configured resource profile")
    if (
        gpu_count is None
        or isinstance(gpu_count, bool)
        or not isinstance(gpu_count, int)
        or gpu_count < 0
    ):
        missing.append("gpu_count (use 0 for API-only)")
    if not isinstance(values["gpu_model"], str) or not values["gpu_model"].strip():
        missing.append("gpu_model")
    gpu_hours = values["gpu_hours"]
    gpu_hours_valid = (
        not isinstance(gpu_hours, bool)
        and isinstance(gpu_hours, (int, float))
        and math.isfinite(float(gpu_hours))
        and gpu_hours >= 0
        and (not isinstance(gpu_count, int) or gpu_count <= 0 or gpu_hours > 0)
    )
    if not gpu_hours_valid:
        missing.append("gpu_hours")
    api_budget = values["api_budget"]
    if (
        not isinstance(api_budget, str)
        or not api_budget.strip()
        or api_budget.strip().lower() in {"unlimited", "unbounded", "infinite", "no limit"}
    ):
        missing.append("api_budget (use 'none' when no paid/model API is allowed)")
    max_parallel_jobs = values["max_parallel_jobs"]
    if (
        isinstance(max_parallel_jobs, bool)
        or not isinstance(max_parallel_jobs, int)
        or max_parallel_jobs < 1
    ):
        missing.append("max_parallel_jobs (strictly positive integer required)")
    if values["wall_clock_deadline"] == "REQUIRED_BEFORE_LAUNCH":
        missing.append("wall_clock_deadline (timezone-aware ISO-8601 with explicit UTC offset required)")
    else:
        try:
            wall_clock = datetime.fromisoformat(str(values["wall_clock_deadline"]))
        except (TypeError, ValueError):
            missing.append("wall_clock_deadline (invalid ISO-8601 datetime)")
        else:
            if wall_clock.tzinfo is None or wall_clock.utcoffset() is None:
                missing.append("wall_clock_deadline (explicit UTC offset required)")
            elif conservative_cutoff:
                try:
                    cutoff_date = date.fromisoformat(str(conservative_cutoff))
                except ValueError:
                    missing.append("conference planning cutoff date (invalid seed data)")
                else:
                    # Compare the date in the operator's stated offset.  This is
                    # intentionally conservative and avoids a +14/-12 conversion
                    # silently moving a run beyond the conference planning day.
                    if wall_clock.date() > cutoff_date:
                        missing.append(
                            "wall_clock_deadline (must be on or before "
                            f"{conservative_cutoff}, based on {deadline_basis})"
                        )
    return values, missing


def _require_future_launch_cutoff(resource_contract: dict[str, Any]) -> None:
    raw = resource_contract.get("wall_clock_deadline")
    try:
        wall_clock = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError) as exc:
        raise HTTPException(409, "Launch wall_clock_deadline is invalid") from exc
    if wall_clock.tzinfo is None or wall_clock.utcoffset() is None:
        raise HTTPException(409, "Launch wall_clock_deadline requires an explicit UTC offset")
    if wall_clock.astimezone(UTC) <= datetime.now(UTC):
        raise HTTPException(409, "Launch wall_clock_deadline must be in the future")


def _rolling_submission_model(venue: dict[str, Any]) -> dict[str, Any] | None:
    metadata = venue.get("metadata") if isinstance(venue.get("metadata"), dict) else {}
    model = (
        metadata.get("special_submission_model")
        if isinstance(metadata.get("special_submission_model"), dict)
        else {}
    )
    if model.get("status") != "official_rolling_no_fixed_deadline":
        return None
    return model


def _rolling_submission_snapshot(
    venue: dict[str, Any], resource_contract: dict[str, Any]
) -> dict[str, Any] | None:
    """Return an explicit non-deadline snapshot for an official rolling venue."""
    model = _rolling_submission_model(venue)
    if model is None:
        return None
    return {
        "kind": "rolling_venue_internal_cutoff",
        "venue_key": venue.get("venue_key"),
        "has_fixed_submission_deadline": False,
        "official_submission_deadline": None,
        "evidence_status": "official_rolling_no_fixed_deadline",
        "source_url": model.get("source_url"),
        "official_model_reason": model.get("reason"),
        "operator_internal_cutoff": resource_contract.get("wall_clock_deadline"),
        "internal_cutoff_is_official_deadline": False,
        "truth_notice": (
            "Operator cutoff for research planning only; this venue has no fixed official "
            "submission deadline and the cutoff must never be represented as one."
        ),
    }


def _submission_snapshot(
    venue: dict[str, Any],
    deadline: dict[str, Any] | None,
    resource_contract: dict[str, Any],
) -> dict[str, Any]:
    if deadline is not None:
        return dict(deadline)
    rolling = _rolling_submission_snapshot(venue, resource_contract)
    if rolling is None:
        raise HTTPException(409, "A fixed-deadline venue requires an associated deadline")
    return rolling


def _compile_idea_packet(
    idea: dict[str, Any],
    venue: dict[str, Any],
    deadline: dict[str, Any] | None,
    resource: dict[str, Any] | None,
    request: Request,
    config: dict[str, Any] | None = None,
    *,
    phase: str = "portfolio",
    locked_contract: dict[str, Any] | None = None,
) -> tuple[Any, dict[str, Any], list[str]]:
    resources, missing = _resource_contract(resource, deadline, config)
    rolling_snapshot = _rolling_submission_snapshot(venue, resources) if deadline is None else None
    evidence = (
        (deadline or {}).get("evidence_status")
        or (rolling_snapshot or {}).get("evidence_status")
        or "unconfirmed"
    )
    source = (
        (deadline or {}).get("source_url")
        or (rolling_snapshot or {}).get("source_url")
        or "official source must be supplied before launch"
    )
    if evidence == "official_confirmed":
        deadline_text = (
            f"{(deadline or {}).get('deadline_date') or 'TBA'} "
            f"{(deadline or {}).get('timezone') or ''} [official_confirmed]"
        ).strip()
    elif deadline:
        forecast_start = deadline.get("forecast_window_start") or deadline.get("deadline_date")
        forecast_end = deadline.get("forecast_window_end") or deadline.get("deadline_date")
        deadline_text = (
            f"point estimate {deadline.get('deadline_date') or 'TBA'} "
            f"{deadline.get('timezone') or ''} [forecast, not an official fact]; "
            f"planning interval {forecast_start}..{forecast_end}; schedule against "
            f"the conservative start {forecast_start}"
        ).strip()
    else:
        deadline_text = "rolling/TBA; operator-supplied internal cutoff"
    prompt_idea = {
        "title": idea["title_zh"],
        "problem_gap": idea["problem_gap"],
        "mechanism_hypothesis": idea["core_hypothesis"],
        "kill_criterion": idea["kill_criterion"],
        "method_seed": idea["method"],
        "public_data_or_tasks": idea.get("public_data_or_tasks"),
        "decisive_experiment": idea.get("decisive_experiments"),
        "predicted_observation": (
            "必须在检索与 pilot 设计后预注册；当前 seed 未声称任何方向或实验结果"
        ),
        "baseline_candidates": [idea["strongest_baselines"]],
        "oral_aspiration": True,
        "source_requirements": [
            "目标会议往届官方 proceedings/OpenReview accepted papers",
            "截至任务启动日的 arXiv/出版社一手论文，并记录检索截止时间",
            "作者或组织的官方 GitHub 仓库及固定 commit",
        ],
    }
    if locked_contract:
        prompt_idea.update(
            {
                "primary_claim": locked_contract["primary_claim"],
                "primary_metric": locked_contract["primary_metric"],
                "minimum_effect": locked_contract["minimum_effect"],
                "data_split": locked_contract["data_split"],
                "confirmatory_seeds": locked_contract["confirmatory_seeds"],
                "strongest_baselines": locked_contract["strongest_baselines"],
                "baseline_candidates": locked_contract["strongest_baselines"],
            }
        )
    compiled = compile_prompt(
        idea=prompt_idea,
        venue={
            "name": venue["display_name"],
            "edition": str((deadline or {}).get("conference_year") or "rolling"),
            "track": (deadline or {}).get("round_note") or "Full/Regular Paper",
            "deadline": deadline_text,
            "scope": idea["venue_fit_reason"],
            "policies": [
                f"Deadline evidence is {evidence}; source: {source}",
                "启动前从会议官方页面复核匿名、AI 使用、伦理、页数与 artifact 规则",
            ],
        },
        domain=_domain_profile(request, venue.get("category_id", ""), idea),
        resources=resources,
        phase=phase,
    )
    return compiled, resources, missing


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_locked_contract_packet(
    campaign_dir: Path,
    campaign: dict[str, Any],
    summary: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Read and authenticate one immutable locked contract from its campaign root."""
    relative_directory = summary.get("directory")
    if not isinstance(relative_directory, str) or not relative_directory:
        raise HTTPException(409, "Locked contract registry has no immutable directory")
    contracts_root = (campaign_dir / "contracts").resolve()
    contract_dir = (campaign_dir / relative_directory).resolve()
    try:
        contract_dir.relative_to(contracts_root)
    except ValueError as exc:
        raise HTTPException(409, "Locked contract registry points outside the campaign root") from exc
    try:
        objective_bytes, objective = _read_utf8_bytes(contract_dir / "OBJECTIVE.md")
        manifest = json.loads((contract_dir / "MANIFEST.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HTTPException(409, "Locked contract packet is missing or corrupt") from exc
    if not isinstance(manifest, dict) or manifest.get("kind") != "locked_campaign_contract":
        raise HTTPException(409, "Locked contract manifest has an invalid kind")
    if manifest.get("campaign_id") != campaign["id"]:
        raise HTTPException(409, "Locked contract belongs to a different campaign")
    bindings = manifest.get("bindings")
    expected_bindings = {
        "venue_id": campaign.get("venue_id"),
        "idea_id": campaign.get("idea_id"),
        "deadline_id": campaign.get("deadline_id"),
        "resource_id": campaign.get("resource_id"),
    }
    if not isinstance(bindings, dict) or any(
        bindings.get(key) != value for key, value in expected_bindings.items()
    ):
        raise HTTPException(409, "Locked contract bindings no longer match the campaign")
    prompt_sha256 = hashlib.sha256(objective_bytes).hexdigest()
    if manifest.get("prompt_sha256") != prompt_sha256:
        raise HTTPException(409, "Locked objective hash does not match its manifest")
    for key in ("version", "contract_sha256", "request_sha256", "prompt_sha256"):
        if summary.get(key) != manifest.get(key):
            raise HTTPException(409, f"Locked contract registry mismatch: {key}")
    return objective, manifest


def _locked_contract_history(config: dict[str, Any]) -> list[dict[str, Any]]:
    value = config.get("locked_contract_history")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _existing_locked_packet(
    campaign_dir: Path,
    campaign: dict[str, Any],
    summary: dict[str, Any],
    expected_manifest: dict[str, Any],
    expected_objective: str,
) -> tuple[str, dict[str, Any]]:
    """Authenticate and adopt a complete packet left before a DB commit.

    Files are renamed into their content-addressed final directory before the
    SQLite transaction commits.  A process crash in that narrow interval must
    be recoverable, but an unrelated or partial directory must never be
    overwritten or silently adopted.
    """

    objective, manifest = _load_locked_contract_packet(campaign_dir, campaign, summary)
    if objective != expected_objective:
        raise HTTPException(409, "Existing locked packet objective does not match this request")
    for key, expected in expected_manifest.items():
        if key == "created_at":
            continue
        actual = manifest.get(key)
        if key == "prompt_manifest" and isinstance(actual, dict) and isinstance(expected, dict):
            actual = {item: value for item, value in actual.items() if item != "generated_at"}
            expected = {
                item: value for item, value in expected.items() if item != "generated_at"
            }
        if key == "human_science_gate" and isinstance(actual, dict) and isinstance(expected, dict):
            # ``recorded_at`` is frozen by the writer that won the filesystem
            # race.  A retry after a database rollback necessarily compiles a
            # new wall-clock value, so authenticate every semantic gate field
            # here and validate the frozen timestamp against top-level
            # ``created_at`` below instead of comparing it to retry time.
            actual = {item: value for item, value in actual.items() if item != "recorded_at"}
            expected = {
                item: value for item, value in expected.items() if item != "recorded_at"
            }
        if actual != expected:
            raise HTTPException(
                409,
                f"Existing locked packet cannot be reconciled: manifest field {key}",
            )
    if not isinstance(manifest.get("created_at"), str) or not manifest["created_at"]:
        raise HTTPException(409, "Existing locked packet has no creation timestamp")
    human_gate = manifest.get("human_science_gate")
    if not isinstance(human_gate, dict) or human_gate.get("recorded_at") != manifest["created_at"]:
        raise HTTPException(409, "Existing locked packet has an inconsistent human gate timestamp")
    return objective, manifest


def _reject_reserved_connection_metadata(metadata: dict[str, Any]) -> None:
    reserved = sorted(_CONNECTION_PROBE_METADATA_KEYS.intersection(metadata))
    if reserved:
        raise HTTPException(
            422,
            "Connection compatibility metadata is probe-owned and cannot be supplied: "
            + ", ".join(reserved),
        )


def _without_connection_probe_truth(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if key not in _CONNECTION_PROBE_METADATA_KEYS
    }


def _release_pin_truth(
    campaign_config: dict[str, Any],
    connection: dict[str, Any],
    connection_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Separate a user-supplied reference from a probed, full commit identity."""

    supplied: Any = campaign_config.get("argus_release_sha")
    reference_source: str | None = None
    if supplied is not None:
        reference_source = "campaign_config_reference"
    elif connection_metadata.get("release_sha") is not None:
        supplied = connection_metadata.get("release_sha")
        reference_source = "connection_metadata_reference"
    reference = str(supplied).strip() if supplied is not None else None
    observed_value = connection_metadata.get("argus_revision")
    observed = str(observed_value).strip() if observed_value is not None else None
    observed_is_full_sha = bool(observed and _FULL_COMMIT_SHA.fullmatch(observed))
    reference_matches_observed = (
        reference is None
        or (
            bool(_FULL_COMMIT_SHA.fullmatch(reference))
            and reference.lower() == str(observed).lower()
        )
    )
    probe_is_current = (
        connection.get("status") == "online"
        and connection_metadata.get("launch_compatible") is True
        and bool(connection.get("last_checked_at"))
    )
    pinned = observed_is_full_sha and reference_matches_observed and probe_is_current
    pinned_sha = observed.lower() if pinned and observed is not None else None
    return {
        "argus_release_sha": pinned_sha or reference,
        "argus_release_reference": reference,
        "release_reference_source": reference_source,
        "argus_release_observed_revision": observed,
        "release_pinned": pinned,
        "release_pin_source": "launch_compatible_target_probe" if pinned else None,
    }


def _strict_positive_limit(value: Any, label: str) -> int:
    if value is None:
        return 1
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise HTTPException(409, f"{label} must be a strictly positive integer")
    return value


def _campaign_row(db: Database, campaign_id: str) -> dict[str, Any]:
    row = db.fetch_one(
        """
        SELECT c.*,v.venue_key,v.display_name AS venue_name,v.category_id,
               i.title_zh AS idea_title,
               d.deadline_date,d.timezone,d.evidence_status,
               cn.name AS connection_name,r.name AS resource_name
        FROM campaigns c JOIN venues v ON v.id=c.venue_id
        LEFT JOIN ideas i ON i.id=c.idea_id
        LEFT JOIN deadlines d ON d.id=c.deadline_id
        LEFT JOIN connections cn ON cn.id=c.connection_id
        LEFT JOIN resources r ON r.id=c.resource_id
        WHERE c.id=?
        """,
        (campaign_id,),
    )
    if not row:
        raise HTTPException(404, f"Campaign not found: {campaign_id}")
    return decode_row(row) or {}


def verify_conditioned_campaign_integrity(
    db: Database, campaign: dict[str, Any], data_dir: Path
) -> dict[str, str] | None:
    """Fail closed when a conditioned candidate loses any frozen provenance edge."""

    config = campaign.get("config") if isinstance(campaign.get("config"), dict) else {}
    receipt_row = db.fetch_one(
        "SELECT * FROM conditioned_campaign_bindings WHERE campaign_id=?",
        (campaign["id"],),
    )
    declares_conditioned = config.get("campaign_kind") == "conditioned_candidate_research"
    if receipt_row is None and not declares_conditioned:
        return None
    if receipt_row is None:
        raise HTTPException(
            409, "Conditioned campaign integrity check failed: immutable binding receipt is missing"
        )
    try:
        binding = validate_campaign_binding(receipt_row)
    except CampaignBindingError as exc:
        raise HTTPException(
            409, f"Conditioned campaign integrity check failed: {exc}"
        ) from exc
    if not declares_conditioned:
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: campaign config was removed or retyped",
        )
    critical_config = {
        "ideation_run_id": binding["ideation_run_id"],
        "candidate_id": binding["candidate_id"],
        "condition_sha256": binding["condition_sha256"],
        "parent_objective_sha256": binding["parent_objective_sha256"],
        "candidate_artifact_sha256": binding["candidate_artifact_sha256"],
        "candidate_record_sha256": binding["candidate_record_sha256"],
        "candidate_input_sha256": binding["candidate_input_sha256"],
        "candidate_prompt_sha256": binding["candidate_prompt_sha256"],
        "candidate_objective_path": binding["objective_path"],
        "binding_receipt_sha256": binding["receipt_sha256"],
    }
    config_mismatches = [
        key for key, expected in critical_config.items() if config.get(key) != expected
    ]
    boolean_contract = {
        "condition_snapshot_bound": True,
        "conditioned_candidate_binding": True,
        "candidate_selection_recorded": True,
        "launch_triggered": False,
        "seed_catalog_source": False,
        "positive_result_required": False,
        "automatic_submission_allowed": False,
    }
    config_mismatches.extend(
        key for key, expected in boolean_contract.items() if config.get(key) is not expected
    )
    if config_mismatches:
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: config binding mismatch ("
            + ", ".join(sorted(config_mismatches))
            + ")",
        )
    run = db.fetch_one("SELECT * FROM ideation_runs WHERE id=?", (binding["ideation_run_id"],))
    candidate = db.fetch_one(
        "SELECT * FROM generated_idea_candidates WHERE id=?", (binding["candidate_id"],)
    )
    if run is None or candidate is None:
        raise HTTPException(
            409, "Conditioned campaign integrity check failed: frozen source row is unavailable"
        )
    try:
        condition_snapshot = json.loads(run["condition_snapshot_json"])
        candidate_payload = json.loads(candidate["candidate_json"])
        candidate_manifest = json.loads(run["candidate_manifest_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            409, "Conditioned campaign integrity check failed: frozen source JSON is invalid"
        ) from exc
    if not all(
        isinstance(value, dict)
        for value in (condition_snapshot, candidate_payload, candidate_manifest)
    ):
        raise HTTPException(
            409, "Conditioned campaign integrity check failed: frozen source JSON is invalid"
        )
    computed_condition_sha = hashlib.sha256(canonical_bytes(condition_snapshot)).hexdigest()
    computed_candidate_sha = hashlib.sha256(canonical_bytes(candidate_payload)).hexdigest()
    source_mismatches: list[str] = []
    checks = (
        (run.get("condition_sha256"), binding["condition_sha256"], "condition_sha256"),
        (computed_condition_sha, binding["condition_sha256"], "condition_snapshot"),
        (run.get("objective_sha256"), binding["parent_objective_sha256"], "parent_objective"),
        (candidate.get("ideation_run_id"), binding["ideation_run_id"], "candidate_run"),
        (candidate.get("artifact_sha256"), binding["candidate_artifact_sha256"], "candidate_artifact"),
        (computed_candidate_sha, binding["candidate_record_sha256"], "candidate_record"),
        (run.get("candidate_artifact_sha256"), binding["candidate_artifact_sha256"], "run_artifact"),
        (candidate_manifest.get("condition_sha256"), binding["condition_sha256"], "manifest_condition"),
        (
            candidate_manifest.get("objective_sha256"),
            binding["parent_objective_sha256"],
            "manifest_objective",
        ),
        (
            candidate_manifest.get("candidates_sha256"),
            binding["candidate_artifact_sha256"],
            "manifest_candidates",
        ),
        (candidate_manifest.get("schema_version"), CANDIDATE_MANIFEST_SCHEMA, "manifest_schema"),
        (config.get("team_profile_id"), run.get("team_profile_id"), "team_profile_id"),
        (campaign.get("idea_id"), None, "idea_id"),
        (campaign.get("venue_id"), run.get("venue_id"), "venue_id"),
        (campaign.get("deadline_id"), run.get("deadline_id"), "deadline_id"),
        (campaign.get("connection_id"), run.get("connection_id"), "connection_id"),
        (campaign.get("resource_id"), run.get("resource_id"), "resource_id"),
        (
            config.get("preflight_attestations"),
            condition_snapshot.get("preflight_attestations", {}),
            "preflight_attestations",
        ),
    )
    source_mismatches.extend(label for actual, expected, label in checks if actual != expected)
    candidate_count = db.fetch_one(
        "SELECT COUNT(*) AS count FROM generated_idea_candidates WHERE ideation_run_id=?",
        (binding["ideation_run_id"],),
    )
    if (
        isinstance(candidate_manifest.get("candidate_count"), bool)
        or not isinstance(candidate_manifest.get("candidate_count"), int)
        or candidate_manifest.get("candidate_count") != int((candidate_count or {}).get("count", 0))
    ):
        source_mismatches.append("candidate_count")
    candidate_rows = db.fetch_all(
        "SELECT candidate_json FROM generated_idea_candidates "
        "WHERE ideation_run_id=? ORDER BY rowid",
        (binding["ideation_run_id"],),
    )
    try:
        candidate_portfolio = [json.loads(row["candidate_json"]) for row in candidate_rows]
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: frozen candidate portfolio is invalid",
        ) from exc
    if hashlib.sha256(canonical_bytes(candidate_portfolio)).hexdigest() != binding[
        "candidate_artifact_sha256"
    ]:
        source_mismatches.append("candidate_portfolio")
    if source_mismatches:
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: frozen source mismatch ("
            + ", ".join(sorted(source_mismatches))
            + ")",
        )
    allowed_root = (data_dir / "candidate-objectives").resolve()
    allowed_parent_root = (data_dir / "ideation-objectives").resolve()
    objective_path = Path(binding["objective_path"])
    try:
        resolved_objective_path = objective_path.resolve(strict=True)
        resolved_objective_path.relative_to(allowed_root)
        objective_bytes = resolved_objective_path.read_bytes()
        contract = json.loads(
            resolved_objective_path.with_name("CANDIDATE_CONTRACT.json").read_bytes()
        )
        resolved_parent_path = Path(run["objective_path"]).resolve(strict=True)
        resolved_parent_path.relative_to(allowed_parent_root)
        parent_bytes = resolved_parent_path.read_bytes()
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: immutable objective file is unavailable",
        ) from exc
    objective_sha = hashlib.sha256(objective_bytes).hexdigest()
    parent_sha = hashlib.sha256(parent_bytes).hexdigest()
    if (
        objective_sha != binding["candidate_prompt_sha256"]
        or parent_sha != binding["parent_objective_sha256"]
        or objective_bytes != str(campaign.get("objective") or "").encode("utf-8")
    ):
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: objective bytes or SHA changed",
        )
    expected_contract_binding = {
        "schema_version": CANDIDATE_RESEARCH_SCHEMA,
        "ideation_run_id": binding["ideation_run_id"],
        "condition_sha256": binding["condition_sha256"],
        "parent_objective_sha256": binding["parent_objective_sha256"],
        "candidate_id": binding["candidate_id"],
        "candidate_artifact_sha256": binding["candidate_artifact_sha256"],
        "candidate_sha256": binding["candidate_record_sha256"],
    }
    if not isinstance(contract, dict) or contract.get("binding") != expected_contract_binding:
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: candidate contract binding changed",
        )
    input_document = {
        "schema_version": CANDIDATE_RESEARCH_SCHEMA,
        "binding": expected_contract_binding,
        "condition_snapshot": condition_snapshot,
        "candidate": candidate_payload,
        "goal_contract": contract,
    }
    if hashlib.sha256(canonical_bytes(input_document)).hexdigest() != binding[
        "candidate_input_sha256"
    ]:
        raise HTTPException(
            409,
            "Conditioned campaign integrity check failed: candidate input SHA changed",
        )
    return binding


def _verify_conditioned_ideation_integrity(
    db: Database, campaign: dict[str, Any], data_dir: Path
) -> dict[str, str]:
    """Re-authenticate a frozen team-conditioned ideation objective at Start."""

    config = campaign.get("config") if isinstance(campaign.get("config"), dict) else {}
    if config.get("campaign_kind") != "conditioned_ideation":
        raise HTTPException(409, "Conditioned ideation campaign type is missing")
    run_id = str(config.get("ideation_run_id") or "")
    run = db.fetch_one("SELECT * FROM ideation_runs WHERE id=?", (run_id,))
    if run is None:
        raise HTTPException(
            409, "Conditioned ideation integrity check failed: frozen run is unavailable"
        )
    try:
        condition_snapshot = json.loads(run["condition_snapshot_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            409, "Conditioned ideation integrity check failed: condition snapshot is invalid"
        ) from exc
    if not isinstance(condition_snapshot, dict):
        raise HTTPException(
            409, "Conditioned ideation integrity check failed: condition snapshot is invalid"
        )
    condition_bytes = canonical_bytes(condition_snapshot)
    condition_sha = hashlib.sha256(condition_bytes).hexdigest()
    objective_bytes = str(campaign.get("objective") or "").encode("utf-8")
    objective_sha = hashlib.sha256(objective_bytes).hexdigest()
    source_checks = (
        (run.get("campaign_id"), campaign["id"], "run_campaign_id"),
        (run.get("team_profile_id"), config.get("team_profile_id"), "team_profile_id"),
        (run.get("venue_id"), campaign.get("venue_id"), "venue_id"),
        (run.get("deadline_id"), campaign.get("deadline_id"), "deadline_id"),
        (run.get("connection_id"), campaign.get("connection_id"), "connection_id"),
        (run.get("resource_id"), campaign.get("resource_id"), "resource_id"),
        (campaign.get("idea_id"), None, "idea_id"),
        (run.get("condition_sha256"), condition_sha, "condition_sha256"),
        (config.get("condition_sha256"), condition_sha, "config_condition_sha256"),
        (run.get("objective_sha256"), objective_sha, "objective_sha256"),
        (config.get("objective_sha256"), objective_sha, "config_objective_sha256"),
        (
            run.get("condition_schema_version"),
            condition_snapshot.get("schema_version"),
            "condition_schema_version",
        ),
        (
            config.get("preflight_attestations"),
            condition_snapshot.get("preflight_attestations", {}),
            "preflight_attestations",
        ),
        (config.get("backend"), "connection-default", "backend"),
        (config.get("automatic_submission_allowed"), False, "automatic_submission_allowed"),
    )
    mismatches = [label for actual, expected, label in source_checks if actual != expected]
    if mismatches:
        raise HTTPException(
            409,
            "Conditioned ideation integrity check failed: frozen source mismatch ("
            + ", ".join(sorted(mismatches))
            + ")",
        )
    allowed_root = (data_dir / "ideation-objectives").resolve()
    try:
        objective_path = Path(str(run["objective_path"])).resolve(strict=True)
        objective_path.relative_to(allowed_root)
        frozen_objective = objective_path.read_bytes()
        frozen_condition = objective_path.with_name("CONDITION_SNAPSHOT.json").read_bytes()
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(
            409,
            "Conditioned ideation integrity check failed: immutable objective files are unavailable",
        ) from exc
    if (
        frozen_objective != objective_bytes
        or hashlib.sha256(frozen_objective).hexdigest() != objective_sha
        or frozen_condition != condition_bytes
        or objective_path.parent.name != objective_sha
    ):
        raise HTTPException(
            409,
            "Conditioned ideation integrity check failed: objective, condition, hash, or path changed",
        )
    return {
        "campaign_kind": "conditioned_ideation",
        "ideation_run_id": run_id,
        "condition_sha256": condition_sha,
        "objective_sha256": objective_sha,
        "objective_path": str(objective_path),
    }


def _verify_rebuttal_follow_up_integrity(
    db: Database, campaign: dict[str, Any], data_dir: Path
) -> dict[str, str]:
    """Verify the immutable submission/review lineage behind a rebuttal campaign."""

    config = campaign.get("config") if isinstance(campaign.get("config"), dict) else {}
    submission_id = str(config.get("submission_id") or "")
    source_campaign_id = str(config.get("source_campaign_id") or "")
    objective_sha = str(config.get("rebuttal_objective_sha256") or "").lower()
    submission = db.fetch_one("SELECT * FROM submission_records WHERE id=?", (submission_id,))
    source_raw = db.fetch_one("SELECT * FROM campaigns WHERE id=?", (source_campaign_id,))
    rebuttal = db.fetch_one(
        "SELECT * FROM rebuttal_versions WHERE campaign_id=?", (campaign["id"],)
    )
    if submission is None or source_raw is None or rebuttal is None:
        raise HTTPException(
            409, "Rebuttal integrity check failed: immutable source binding is unavailable"
        )
    source = decode_row(source_raw) or {}
    source_config = (
        source.get("config") if isinstance(source.get("config"), dict) else {}
    )
    source_binding = verify_conditioned_campaign_integrity(db, source, data_dir)
    if source_binding is None:
        raise HTTPException(
            409, "Rebuttal integrity check failed: source campaign is not conditioned"
        )
    objective_bytes = str(campaign.get("objective") or "").encode("utf-8")
    computed_sha = hashlib.sha256(objective_bytes).hexdigest()
    checks = (
        (submission.get("campaign_id"), source_campaign_id, "submission_campaign_id"),
        (rebuttal.get("submission_id"), submission_id, "rebuttal_submission_id"),
        (str(rebuttal.get("objective_sha256") or "").lower(), objective_sha, "rebuttal_sha"),
        (objective_sha, computed_sha, "objective_sha"),
        (campaign.get("idea_id"), source.get("idea_id"), "idea_id"),
        (campaign.get("venue_id"), source.get("venue_id"), "venue_id"),
        (campaign.get("deadline_id"), source.get("deadline_id"), "deadline_id"),
        (campaign.get("connection_id"), source.get("connection_id"), "connection_id"),
        (campaign.get("resource_id"), source.get("resource_id"), "resource_id"),
        (
            config.get("preflight_attestations"),
            source_config.get("preflight_attestations", {}),
            "preflight_attestations",
        ),
        (config.get("backend"), "connection-default", "backend"),
        (config.get("automatic_submission_allowed"), False, "automatic_submission_allowed"),
    )
    mismatches = [label for actual, expected, label in checks if actual != expected]
    if mismatches:
        raise HTTPException(
            409,
            "Rebuttal integrity check failed: frozen source mismatch ("
            + ", ".join(sorted(mismatches))
            + ")",
        )
    allowed_root = (data_dir / "rebuttal-objectives").resolve()
    try:
        objective_path = Path(str(rebuttal["objective_path"])).resolve(strict=True)
        objective_path.relative_to(allowed_root)
        frozen_objective = objective_path.read_bytes()
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(
            409, "Rebuttal integrity check failed: immutable objective file is unavailable"
        ) from exc
    if (
        frozen_objective != objective_bytes
        or hashlib.sha256(frozen_objective).hexdigest() != objective_sha
        or objective_path.parent.name != objective_sha
        or objective_path.name != "REBUTTAL_OBJECTIVE.md"
    ):
        raise HTTPException(
            409, "Rebuttal integrity check failed: objective bytes, hash, or path changed"
        )
    return {
        "campaign_kind": "rebuttal_follow_up",
        "source_campaign_id": source_campaign_id,
        "submission_id": submission_id,
        "objective_sha256": objective_sha,
        "objective_path": str(objective_path),
    }


TRAINING_PROVENANCE_SCHEMA = "argus-flywheel/verified-training-lineage-v1"
IDEATION_LABEL_PROVENANCE_SCHEMA = (
    "argus-flywheel/verified-conditioned-ideation-lineage-v1"
)


def verify_conditioned_ideation_candidate_provenance(
    db: Database, candidate_id: str, data_dir: Path
) -> dict[str, Any]:
    """Verify a real conditioned candidate without requiring its execution.

    Human scalar/pairwise decisions are valuable negative-selection data.  They
    may therefore precede a candidate execution, but they must still originate
    from a fully frozen conditioned-ideation run rather than a seed/manual idea.
    """

    candidate = db.fetch_one(
        "SELECT * FROM generated_idea_candidates WHERE id=?", (candidate_id,)
    )
    if candidate is None:
        raise HTTPException(409, "Conditioned ideation lineage failed: candidate is missing")
    run = db.fetch_one(
        "SELECT * FROM ideation_runs WHERE id=?", (candidate["ideation_run_id"],)
    )
    if run is None or not run.get("campaign_id"):
        raise HTTPException(
            409,
            "Conditioned ideation lineage failed: frozen run campaign is missing",
        )
    if not bool(run.get("training_consent")) or not str(
        run.get("license_basis") or ""
    ).strip():
        raise HTTPException(
            409,
            "Conditioned ideation lineage failed: frozen run training consent or license is absent",
        )
    campaign = decode_row(
        db.fetch_one("SELECT * FROM campaigns WHERE id=?", (run["campaign_id"],))
    )
    if campaign is None:
        raise HTTPException(
            409, "Conditioned ideation lineage failed: run campaign is unavailable"
        )
    ideation = _verify_conditioned_ideation_integrity(db, campaign, data_dir)
    try:
        condition_snapshot = json.loads(run["condition_snapshot_json"])
        candidate_payload = json.loads(candidate["candidate_json"])
        manifest = json.loads(run["candidate_manifest_json"])
        candidate_rows = db.fetch_all(
            "SELECT candidate_json FROM generated_idea_candidates "
            "WHERE ideation_run_id=? ORDER BY rowid",
            (run["id"],),
        )
        portfolio = [json.loads(row["candidate_json"]) for row in candidate_rows]
    except (TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            409, "Conditioned ideation lineage failed: frozen JSON is invalid"
        ) from exc
    if not all(
        isinstance(value, dict)
        for value in (condition_snapshot, candidate_payload, manifest)
    ) or not all(isinstance(value, dict) for value in portfolio):
        raise HTTPException(
            409, "Conditioned ideation lineage failed: frozen JSON shape is invalid"
        )
    condition_sha = hashlib.sha256(canonical_bytes(condition_snapshot)).hexdigest()
    record_sha = hashlib.sha256(canonical_bytes(candidate_payload)).hexdigest()
    portfolio_sha = hashlib.sha256(canonical_bytes(portfolio)).hexdigest()
    manifest_sha = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    checks = (
        (candidate.get("ideation_run_id"), run["id"], "candidate_run"),
        (run.get("condition_sha256"), condition_sha, "condition_sha256"),
        (ideation.get("condition_sha256"), condition_sha, "ideation_condition"),
        (ideation.get("objective_sha256"), run.get("objective_sha256"), "objective_sha256"),
        (candidate.get("artifact_sha256"), portfolio_sha, "candidate_artifact"),
        (run.get("candidate_artifact_sha256"), portfolio_sha, "run_candidate_artifact"),
        (manifest.get("schema_version"), CANDIDATE_MANIFEST_SCHEMA, "manifest_schema"),
        (manifest.get("condition_sha256"), condition_sha, "manifest_condition"),
        (
            manifest.get("objective_sha256"),
            run.get("objective_sha256"),
            "manifest_objective",
        ),
        (manifest.get("candidates_sha256"), portfolio_sha, "manifest_candidates"),
        (manifest.get("candidate_count"), len(portfolio), "manifest_candidate_count"),
    )
    mismatches = [label for actual, expected, label in checks if actual != expected]
    if mismatches:
        raise HTTPException(
            409,
            "Conditioned ideation lineage failed: frozen source mismatch ("
            + ", ".join(sorted(mismatches))
            + ")",
        )
    return {
        "schema_version": IDEATION_LABEL_PROVENANCE_SCHEMA,
        "campaign_kind": "conditioned_ideation",
        "ideation_campaign_id": campaign["id"],
        "ideation_run_id": run["id"],
        "candidate_id": candidate["id"],
        "condition_sha256": condition_sha,
        "objective_sha256": str(run["objective_sha256"]),
        "candidate_artifact_sha256": portfolio_sha,
        "candidate_record_sha256": record_sha,
        "candidate_manifest_sha256": manifest_sha,
        "candidate_import_provenance": str(candidate.get("imported_from") or ""),
    }


def verify_training_campaign_provenance(
    db: Database, campaign: dict[str, Any], data_dir: Path
) -> dict[str, Any]:
    """Return one canonical, re-verifiable training lineage or fail closed.

    A Campaign being present, runnable, or completed is not sufficient evidence for
    the research-data flywheel.  Training lineage starts only at an immutable
    team-conditioned candidate receipt.  A rebuttal is admitted only when its
    submission and frozen objective trace back to such a receipt.
    """

    decoded = decode_row(campaign) or {}
    config = decoded.get("config") if isinstance(decoded.get("config"), dict) else {}
    kind = config.get("campaign_kind")
    if kind == "conditioned_candidate_research":
        binding = verify_conditioned_campaign_integrity(db, decoded, data_dir)
        if binding is None:
            raise HTTPException(
                409,
                "Training lineage is unverified: conditioned candidate binding is missing",
            )
        return {
            "schema_version": TRAINING_PROVENANCE_SCHEMA,
            "campaign_kind": "conditioned_candidate_research",
            "campaign_id": decoded["id"],
            "source_candidate_campaign_id": decoded["id"],
            "ideation_run_id": binding["ideation_run_id"],
            "candidate_id": binding["candidate_id"],
            "condition_sha256": binding["condition_sha256"],
            "parent_objective_sha256": binding["parent_objective_sha256"],
            "candidate_artifact_sha256": binding["candidate_artifact_sha256"],
            "candidate_record_sha256": binding["candidate_record_sha256"],
            "candidate_input_sha256": binding["candidate_input_sha256"],
            "candidate_prompt_sha256": binding["candidate_prompt_sha256"],
            "binding_receipt_sha256": binding["receipt_sha256"],
            "binding_receipt": binding,
        }
    if kind == "rebuttal_follow_up":
        rebuttal = _verify_rebuttal_follow_up_integrity(db, decoded, data_dir)
        source_campaign_id = rebuttal["source_campaign_id"]
        source = decode_row(
            db.fetch_one("SELECT * FROM campaigns WHERE id=?", (source_campaign_id,))
        )
        if source is None:
            raise HTTPException(
                409, "Training lineage is unverified: rebuttal source campaign is missing"
            )
        binding = verify_conditioned_campaign_integrity(db, source, data_dir)
        if binding is None:
            raise HTTPException(
                409,
                "Training lineage is unverified: rebuttal source is not a conditioned candidate",
            )
        return {
            "schema_version": TRAINING_PROVENANCE_SCHEMA,
            "campaign_kind": "rebuttal_follow_up",
            "campaign_id": decoded["id"],
            "source_candidate_campaign_id": source_campaign_id,
            "submission_id": rebuttal["submission_id"],
            "rebuttal_objective_sha256": rebuttal["objective_sha256"],
            "ideation_run_id": binding["ideation_run_id"],
            "candidate_id": binding["candidate_id"],
            "condition_sha256": binding["condition_sha256"],
            "parent_objective_sha256": binding["parent_objective_sha256"],
            "candidate_artifact_sha256": binding["candidate_artifact_sha256"],
            "candidate_record_sha256": binding["candidate_record_sha256"],
            "candidate_input_sha256": binding["candidate_input_sha256"],
            "candidate_prompt_sha256": binding["candidate_prompt_sha256"],
            "binding_receipt_sha256": binding["receipt_sha256"],
            "binding_receipt": binding,
        }
    if kind == "conditioned_ideation":
        raise HTTPException(
            409,
            "Training lineage is unverified: conditioned ideation is a pre-execution source",
        )
    if decoded.get("idea_id") is not None:
        raise HTTPException(
            409, "Training lineage is unverified: seed catalogue campaigns are not executions"
        )
    raise HTTPException(
        409,
        "Training lineage is unverified: campaign is manual, unbound, or has no immutable candidate receipt",
    )


def _verify_campaign_launch_provenance(
    db: Database, campaign: dict[str, Any], data_dir: Path, settings: Any
) -> dict[str, str] | None:
    """Admit only provenance-authenticated production campaign types."""

    config = campaign.get("config") if isinstance(campaign.get("config"), dict) else {}
    kind = config.get("campaign_kind")
    binding_row = db.fetch_one(
        "SELECT campaign_id FROM conditioned_campaign_bindings WHERE campaign_id=?",
        (campaign["id"],),
    )
    if binding_row is not None or kind == "conditioned_candidate_research":
        binding = verify_conditioned_campaign_integrity(db, campaign, data_dir)
        if binding is None:
            raise HTTPException(409, "Conditioned candidate binding is missing")
        return {**binding, "campaign_kind": "conditioned_candidate_research"}
    if kind == "conditioned_ideation":
        return _verify_conditioned_ideation_integrity(db, campaign, data_dir)
    if kind == "rebuttal_follow_up":
        return _verify_rebuttal_follow_up_integrity(db, campaign, data_dir)
    if settings.allow_unbound_campaign_launch_for_tests:
        return None
    if campaign.get("idea_id") is not None:
        raise HTTPException(
            409,
            "Seed catalog campaigns are not executable. Create a TeamProfile, run "
            "conditioned ideation, import its bound candidate artifact, and create a "
            "candidate-specific campaign.",
        )
    raise HTTPException(
        409,
        "Campaign has no verified conditioned or immutable formal provenance and is not executable",
    )


def _campaign_launch_eligibility(
    db: Database, campaign: dict[str, Any], data_dir: Path, settings: Any
) -> dict[str, Any]:
    """Project the same provenance gate used by Start without mutating state.

    ``launch_eligible`` intentionally covers immutable provenance plus the
    campaign's execution/receipt state. Connection, resource, deadline and
    preflight readiness remain request-time Start gates.
    """

    provenance: dict[str, str] | None = None
    provenance_reason: str | None = None
    try:
        provenance = _verify_campaign_launch_provenance(
            db, campaign, data_dir, settings
        )
    except HTTPException as exc:
        detail = exc.detail
        provenance_reason = (
            detail
            if isinstance(detail, str)
            else json.dumps(detail, ensure_ascii=False, sort_keys=True)
        )

    provenance_valid = provenance_reason is None
    state = str(campaign.get("execution_state") or "")
    command_id = str(campaign.get("launch_command_id") or "").strip()
    project_id = str(campaign.get("argus_project_id") or "").strip()
    state_reason: str | None = None
    if state in {"starting", "running", "draining", "completed"}:
        state_reason = f"Campaign execution_state={state} is not start-eligible"
    elif project_id:
        state_reason = "Campaign already has an attached Argus project"
    elif command_id and state not in {"failed", "needs_attention"}:
        state_reason = (
            "Existing launch receipt can only be reconciled from "
            "failed/needs_attention"
        )

    state_eligible = state_reason is None
    eligible = provenance_valid and state_eligible
    provenance_kind = (
        str(provenance.get("campaign_kind") or "")
        if provenance is not None
        else (
            "test_unbound"
            if provenance_valid and settings.allow_unbound_campaign_launch_for_tests
            else None
        )
    )
    return {
        "launch_eligible": eligible,
        "launch_ineligibility_reason": provenance_reason or state_reason,
        "launch_provenance_valid": provenance_valid,
        "launch_provenance_kind": provenance_kind,
        "launch_provenance_ineligibility_reason": provenance_reason,
        "launch_state_eligible": state_eligible,
        "launch_state_ineligibility_reason": state_reason,
        "launch_eligibility_scope": "provenance_and_execution_state",
    }


def _with_campaign_launch_eligibility(
    db: Database, campaign: dict[str, Any], data_dir: Path, settings: Any
) -> dict[str, Any]:
    result = dict(campaign)
    result.update(_campaign_launch_eligibility(db, result, data_dir, settings))
    return result


def create_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health")
    def health(request: Request) -> dict[str, Any]:
        db = _db(request)
        migration = db.fetch_one("SELECT MAX(version) AS version FROM schema_migrations")
        connection_rows = decode_rows(db.fetch_all("SELECT * FROM connections WHERE enabled=1"))
        argus_online = any(row.get("status") == "online" for row in connection_rows)
        argus_ready = any(
            row.get("status") == "online"
            and isinstance(row.get("metadata"), dict)
            and row["metadata"].get("launch_compatible") is True
            and row["metadata"].get("backend_ready") is True
            for row in connection_rows
        )
        return {
            "ok": True,
            "service": "argus-research-flywheel",
            "schema_version": (migration or {}).get("version", 0),
            "time": utc_now(),
            "viewer_evaluator_configured": bool(
                request.app.state.settings.viewer_evaluator_command
            ),
            "argus_control_plane_configured": bool(
                request.app.state.settings.argus_base_url
            ),
            "argus_online": argus_online,
            "argus_backend_ready": argus_ready,
            "research_chain_ready": argus_ready,
        }

    @router.get("/dashboard")
    def dashboard(request: Request) -> dict[str, Any]:
        db = _db(request)
        today = date.today().isoformat()
        counts = {
            "venues": db.fetch_one("SELECT COUNT(*) AS n FROM venues")["n"],
            "ideas": db.fetch_one("SELECT COUNT(*) AS n FROM ideas")["n"],
            "campaigns": db.fetch_one("SELECT COUNT(*) AS n FROM campaigns")["n"],
            "running_campaigns": db.fetch_one(
                "SELECT COUNT(*) AS n FROM campaigns WHERE execution_state='running'"
            )["n"],
            "needs_attention": db.fetch_one(
                "SELECT COUNT(*) AS n FROM campaigns WHERE execution_state='needs_attention' "
                "OR integrity_state IN ('failed','blocked')"
            )["n"],
            "pending_approvals": db.fetch_one(
                "SELECT COUNT(*) AS n FROM campaigns WHERE schedule_state='awaiting_approval' "
                "OR review_state='human_review'"
            )["n"],
        }
        upcoming = decode_rows(
            db.fetch_all(
                """
                SELECT d.*,v.venue_key,v.display_name,v.category_id,
                  v.official_name,v.category_zh,
                  CAST(julianday(
                    CASE WHEN d.evidence_status='official_confirmed' THEN d.deadline_date
                         ELSE COALESCE(d.forecast_window_start,d.deadline_date) END
                  )-julianday(?) AS INTEGER) AS days_remaining,
                  (SELECT COUNT(*) FROM ideas i WHERE i.venue_id=v.id) AS idea_count
                FROM deadlines d JOIN venues v ON v.id=d.venue_id
                ORDER BY d.deadline_date LIMIT 200
                """,
                (today,),
            )
        )
        campaigns = decode_rows(
            db.fetch_all(
                """
                SELECT c.*,v.venue_key,v.display_name AS venue_name,i.title_zh AS idea_title
                FROM campaigns c JOIN venues v ON v.id=c.venue_id
                LEFT JOIN ideas i ON i.id=c.idea_id
                ORDER BY c.updated_at DESC LIMIT 12
                """
            )
        )
        campaigns = [
            _with_campaign_launch_eligibility(
                db,
                campaign,
                request.app.state.settings.data_dir,
                request.app.state.settings,
            )
            for campaign in campaigns
        ]
        recent_events = decode_rows(
            db.fetch_all("SELECT * FROM events ORDER BY id DESC LIMIT 30")
        )
        resources = decode_rows(db.fetch_all("SELECT * FROM resources ORDER BY name"))
        venue_catalog = decode_rows(db.fetch_all(
            "SELECT v.*,COUNT(DISTINCT i.id) AS idea_count,COUNT(DISTINCT d.id) AS deadline_count "
            "FROM venues v LEFT JOIN ideas i ON i.venue_id=v.id "
            "LEFT JOIN deadlines d ON d.venue_id=v.id GROUP BY v.id "
            "ORDER BY v.category_id,v.display_name"
        ))
        return {
            "generated_at": utc_now(),
            "counts": counts,
            "upcoming_deadlines": upcoming,
            "venue_catalog": venue_catalog,
            "campaigns": campaigns,
            "resources": resources,
            "recent_events": recent_events,
        }

    @router.get("/venues")
    def venues(
        request: Request,
        category_id: str | None = None,
        query: str | None = Query(default=None, max_length=100),
    ) -> dict[str, Any]:
        sql = (
            "SELECT v.*,COUNT(DISTINCT i.id) AS idea_count,COUNT(DISTINCT d.id) AS deadline_count,"
            "MIN(d.deadline_date) AS next_deadline FROM venues v "
            "LEFT JOIN ideas i ON i.venue_id=v.id LEFT JOIN deadlines d ON d.venue_id=v.id WHERE 1=1"
        )
        params: list[Any] = []
        if category_id:
            sql += " AND v.category_id=?"
            params.append(category_id)
        if query:
            sql += " AND (v.venue_key LIKE ? OR v.display_name LIKE ? OR v.official_name LIKE ?)"
            needle = f"%{query}%"
            params.extend([needle, needle, needle])
        sql += " GROUP BY v.id ORDER BY v.category_id,v.display_name"
        items = decode_rows(_db(request).fetch_all(sql, params))
        return {"items": items, "total": len(items)}

    @router.get("/venues/{venue_key}")
    def venue_detail(venue_key: str, request: Request) -> dict[str, Any]:
        db = _db(request)
        venue = decode_row(db.fetch_one("SELECT * FROM venues WHERE venue_key=?", (venue_key,)))
        if not venue:
            raise HTTPException(404, f"Venue not found: {venue_key}")
        venue["deadlines"] = decode_rows(
            db.fetch_all("SELECT * FROM deadlines WHERE venue_id=? ORDER BY deadline_date", (venue["id"],))
        )
        venue["ideas"] = decode_rows(
            db.fetch_all("SELECT * FROM ideas WHERE venue_id=? ORDER BY rank", (venue["id"],))
        )
        return venue

    @router.get("/ideas")
    def ideas(
        request: Request,
        venue_key: str | None = None,
        risk_level: str | None = None,
        freshness_state: str | None = None,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        if venue_key:
            where.append("v.venue_key=?")
            params.append(venue_key)
        if risk_level:
            where.append("i.risk_level=?")
            params.append(risk_level)
        if freshness_state:
            where.append("i.freshness_state=?")
            params.append(freshness_state)
        clause = " AND ".join(where)
        db = _db(request)
        total = db.fetch_one(
            f"SELECT COUNT(*) AS n FROM ideas i JOIN venues v ON v.id=i.venue_id WHERE {clause}", params
        )["n"]
        items = decode_rows(
            db.fetch_all(
                f"SELECT i.*,v.venue_key,v.display_name AS venue_name FROM ideas i "
                f"JOIN venues v ON v.id=i.venue_id WHERE {clause} ORDER BY v.venue_key,i.rank LIMIT ? OFFSET ?",
                [*params, limit, offset],
            )
        )
        return {"items": items, "total": total, "limit": limit, "offset": offset}

    @router.get("/ideas/{idea_id}/prompt")
    def idea_prompt(
        idea_id: int,
        request: Request,
        deadline_id: int | None = None,
        resource_id: str | None = None,
    ) -> dict[str, Any]:
        db = _db(request)
        idea = decode_row(_require(db, "ideas", idea_id)) or {}
        venue = decode_row(_require(db, "venues", idea["venue_id"])) or {}
        deadline = (
            decode_row(_require(db, "deadlines", deadline_id))
            if deadline_id
            else decode_row(
                db.fetch_one(
                    "SELECT * FROM deadlines WHERE venue_id=? ORDER BY deadline_date LIMIT 1",
                    (idea["venue_id"],),
                )
            )
        )
        resource = (
            decode_row(_require(db, "resources", resource_id))
            if resource_id
            else decode_row(db.fetch_one(
                "SELECT * FROM resources WHERE enabled=1 "
                "ORDER BY CASE WHEN resource_type='unconfigured' THEN 1 ELSE 0 END,name LIMIT 1"
            ))
        )
        compiled, resources, missing = _compile_idea_packet(
            idea, venue, deadline, resource, request
        )
        return {
            "idea_id": idea_id,
            "venue_key": venue["venue_key"],
            "prompt": compiled.prompt,
            "prompt_sha256": compiled.prompt_sha256,
            "manifest": compiled.manifest,
            "resource_contract": resources,
            "execution_ready": False,
            "missing_before_launch": [
                "Select a TeamProfile and generate a conditioned ideation candidate",
                *missing,
            ],
            "compiler": "prompt-compiler/v2",
            "warning": "Seed baseline preview only; it cannot be launched or treated as a personalized research direction.",
        }

    @router.get("/campaigns")
    def campaigns(
        request: Request,
        venue_key: str | None = None,
        execution_state: str | None = None,
        limit: int = Query(100, ge=1, le=500),
    ) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        if venue_key:
            where.append("v.venue_key=?")
            params.append(venue_key)
        if execution_state:
            where.append("c.execution_state=?")
            params.append(execution_state)
        rows = _db(request).fetch_all(
            "SELECT c.*,v.venue_key,v.display_name AS venue_name,i.title_zh AS idea_title,"
            "d.deadline_date,d.timezone,"
            "(SELECT r.feedback_json FROM reviews r WHERE r.campaign_id=c.id AND r.score IS NOT NULL ORDER BY r.updated_at DESC LIMIT 1) AS latest_review_feedback_json,"
            "(SELECT r.recommendation FROM reviews r WHERE r.campaign_id=c.id AND r.score IS NOT NULL ORDER BY r.updated_at DESC LIMIT 1) AS latest_review_recommendation,"
            "(SELECT r.updated_at FROM reviews r WHERE r.campaign_id=c.id AND r.score IS NOT NULL ORDER BY r.updated_at DESC LIMIT 1) AS latest_review_updated_at "
            "FROM campaigns c JOIN venues v ON v.id=c.venue_id "
            "LEFT JOIN ideas i ON i.id=c.idea_id LEFT JOIN deadlines d ON d.id=c.deadline_id "
            f"WHERE {' AND '.join(where)} ORDER BY c.updated_at DESC LIMIT ?",
            [*params, limit],
        )
        items = []
        for item in decode_rows(rows):
            encoded_feedback = item.pop("latest_review_feedback_json", None)
            try:
                item["latest_review_feedback"] = json.loads(encoded_feedback or "null")
            except (TypeError, json.JSONDecodeError):
                item["latest_review_feedback"] = None
            items.append(
                _with_campaign_launch_eligibility(
                    _db(request),
                    _with_campaign_release_state(
                        item, request.app.state.settings.data_dir
                    ),
                    request.app.state.settings.data_dir,
                    request.app.state.settings,
                )
            )
        return {"items": items, "total": len(items)}

    @router.post("/approvals/{approval_id}")
    def decide_approval(
        approval_id: str, body: ApprovalDecision, request: Request
    ) -> dict[str, Any]:
        """Record one human decision without launching work or submitting anything."""
        db = _db(request)
        campaign_id = _campaign_id_from_approval_id(approval_id)
        target_schedule = "admitted" if body.decision == "approve" else "deferred"
        target_review = "approved" if body.decision == "approve" else "rejected"
        event_type = (
            "campaign.approval_approved"
            if body.decision == "approve"
            else "campaign.approval_rejected"
        )
        now = utc_now()
        idempotent = False
        with db.transaction() as connection:
            row = connection.execute(
                "SELECT id,schedule_state,review_state,execution_state,argus_project_id "
                "FROM campaigns WHERE id=?",
                (campaign_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, f"Campaign not found: {campaign_id}")
            campaign_state = dict(row)
            prior_decision = connection.execute(
                "SELECT event_type FROM events WHERE entity_type='campaign' AND entity_id=? "
                "AND event_type IN ('campaign.approval_approved','campaign.approval_rejected') "
                "ORDER BY id DESC LIMIT 1",
                (campaign_id,),
            ).fetchone()
            if prior_decision is not None:
                prior_event = str(prior_decision["event_type"])
                if prior_event == event_type and campaign_state["schedule_state"] == target_schedule:
                    idempotent = True
                else:
                    prior_label = "approve" if prior_event.endswith("approved") else "reject"
                    raise HTTPException(
                        409,
                        f"Approval is already finalized as {prior_label}; decisions are immutable",
                    )
            elif not (
                campaign_state["schedule_state"] == "awaiting_approval"
                or campaign_state["review_state"] == "human_review"
            ):
                raise HTTPException(409, "Campaign is not awaiting a human approval decision")
            else:
                next_review = (
                    target_review
                    if campaign_state["review_state"] == "human_review"
                    else campaign_state["review_state"]
                )
                cursor = connection.execute(
                    "UPDATE campaigns SET schedule_state=?,review_state=?,updated_at=? WHERE id=? "
                    "AND (schedule_state='awaiting_approval' OR review_state='human_review')",
                    (target_schedule, next_review, now, campaign_id),
                )
                if cursor.rowcount != 1:
                    raise HTTPException(409, "Approval state changed concurrently; refresh and retry")
                connection.execute(
                    "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        "campaigns",
                        event_type,
                        "info" if body.decision == "approve" else "attention",
                        "campaign",
                        campaign_id,
                        json.dumps(
                            {
                                "approval_id": approval_id,
                                "decision": body.decision,
                                "reason": body.reason,
                                "previous": {
                                    "schedule_state": campaign_state["schedule_state"],
                                    "review_state": campaign_state["review_state"],
                                },
                                "next": {
                                    "schedule_state": target_schedule,
                                    "review_state": next_review,
                                },
                                "launch_triggered": False,
                                "submission_triggered": False,
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
        result = _campaign_row(db, campaign_id)
        result["approval"] = {
            "id": approval_id,
            "decision": body.decision,
            "idempotent": idempotent,
        }
        return result

    @router.post("/campaigns", status_code=201)
    def create_campaign(body: CampaignCreate, request: Request) -> dict[str, Any]:
        db = _db(request)
        venue = db.fetch_one("SELECT * FROM venues WHERE venue_key=?", (body.venue_key,))
        if not venue:
            raise HTTPException(404, f"Venue not found: {body.venue_key}")
        idea = _require(db, "ideas", body.idea_id) if body.idea_id else None
        if idea and idea["venue_id"] != venue["id"]:
            raise HTTPException(422, "Idea does not belong to the selected venue")
        if body.deadline_id:
            deadline = _require(db, "deadlines", body.deadline_id)
            if deadline["venue_id"] != venue["id"]:
                raise HTTPException(422, "Deadline does not belong to the selected venue")
        if body.connection_id:
            _require(db, "connections", body.connection_id)
        if body.resource_id:
            _require(db, "resources", body.resource_id)
        campaign_id = str(uuid.uuid4())
        now = utc_now()
        scheduled_for = _utc_schedule(body.scheduled_for, "scheduled_for")
        title = body.title or (idea or {}).get("title_zh") or f"{body.venue_key} research campaign"
        db.execute(
            """
            INSERT INTO campaigns(id,venue_id,idea_id,deadline_id,connection_id,resource_id,title,
              objective,schedule_state,config_json,scheduled_for,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                campaign_id, venue["id"], body.idea_id, body.deadline_id, body.connection_id,
                body.resource_id, title, body.objective,
                "scheduled" if scheduled_for else "manual",
                json.dumps(body.config, ensure_ascii=False), scheduled_for, now, now,
            ),
        )
        db.append_event(
            "campaigns", "campaign.created", entity_type="campaign", entity_id=campaign_id,
            payload={"title": title, "venue_key": body.venue_key},
        )
        return _campaign_row(db, campaign_id)

    @router.get("/campaigns/{campaign_id}")
    def campaign_detail(campaign_id: str, request: Request) -> dict[str, Any]:
        db = _db(request)
        campaign = _with_campaign_release_state(
            _campaign_row(db, campaign_id), request.app.state.settings.data_dir
        )
        campaign = _with_campaign_launch_eligibility(
            db,
            campaign,
            request.app.state.settings.data_dir,
            request.app.state.settings,
        )
        campaign["reviews"] = decode_rows(
            db.fetch_all("SELECT * FROM reviews WHERE campaign_id=? ORDER BY created_at DESC", (campaign_id,))
        )
        campaign["events"] = decode_rows(
            db.fetch_all(
                "SELECT * FROM events WHERE entity_type='campaign' AND entity_id=? ORDER BY id DESC LIMIT 100",
                (campaign_id,),
            )
        )
        return campaign

    @router.post("/campaigns/{campaign_id}/locked-contract", status_code=201)
    def create_locked_contract(
        campaign_id: str,
        body: LockedContractRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        """Freeze a confirmatory contract without contacting or launching Argus."""
        db = _db(request)
        source_campaign = _campaign_row(db, campaign_id)
        campaign = source_campaign
        venue = decode_row(_require(db, "venues", campaign["venue_id"])) or {}
        if not all(campaign.get(key) for key in ("idea_id", "resource_id")):
            raise HTTPException(
                409,
                "Locked contract requires an associated idea and resource profile",
            )
        if not campaign.get("deadline_id") and _rolling_submission_model(venue) is None:
            raise HTTPException(
                409,
                "Locked contract for a fixed-deadline venue requires an associated deadline",
            )
        if campaign.get("execution_state") in {"starting", "running", "draining"}:
            raise HTTPException(409, "An active campaign must be paused before promotion")
        promoted_from_campaign_id = (
            campaign_id
            if (
                campaign.get("execution_state") != "idle"
                or campaign.get("launch_command_id")
                or campaign.get("argus_project_id")
                or campaign.get("started_at")
            )
            else None
        )
        frozen_fields = body.model_dump(mode="json")
        request_sha256 = _canonical_sha256(
            {"schema_version": 1, "locked_contract_request": frozen_fields}
        )
        config = campaign.get("config") if isinstance(campaign.get("config"), dict) else {}
        source_config = config
        lineage_source_campaign_id = (
            promoted_from_campaign_id or config.get("promoted_from_campaign_id")
        )
        source_campaign_dir = (
            request.app.state.settings.data_dir / "campaigns" / campaign_id
        )
        for summary in _locked_contract_history(config):
            if summary.get("request_sha256") != request_sha256:
                continue
            objective, manifest = _load_locked_contract_packet(
                source_campaign_dir, campaign, summary
            )
            campaign["promoted_from_campaign_id"] = config.get(
                "promoted_from_campaign_id"
            )
            campaign["target_campaign_id"] = campaign_id
            campaign["locked_contract"] = {
                **summary,
                "idempotent": True,
                "objective": objective,
                "manifest": manifest,
            }
            response.status_code = 200
            return campaign

        # Idempotent promotion lookup is event-backed so the source Portfolio row
        # and its launch receipt/workspace remain untouched.
        if promoted_from_campaign_id:
            promotion_events = db.fetch_all(
                "SELECT payload_json FROM events WHERE entity_type='campaign' AND entity_id=? "
                "AND event_type='campaign.contract_promoted' ORDER BY id DESC",
                (campaign_id,),
            )
            for event in promotion_events:
                try:
                    payload = json.loads(event["payload_json"] or "{}")
                except json.JSONDecodeError:
                    continue
                if payload.get("request_sha256") != request_sha256:
                    continue
                target_id = payload.get("target_campaign_id")
                if not isinstance(target_id, str):
                    continue
                target = _campaign_row(db, target_id)
                target_config = target.get("config") or {}
                summary = next(
                    (
                        item
                        for item in _locked_contract_history(target_config)
                        if item.get("request_sha256") == request_sha256
                    ),
                    None,
                )
                if not isinstance(summary, dict):
                    raise HTTPException(
                        409,
                        "Promoted child has no locked contract version for this request receipt",
                    )
                target_dir = request.app.state.settings.data_dir / "campaigns" / target_id
                objective, manifest = _load_locked_contract_packet(
                    target_dir, target, summary
                )
                target["promoted_from_campaign_id"] = campaign_id
                target["target_campaign_id"] = target_id
                target["locked_contract"] = {
                    **summary,
                    "idempotent": True,
                    "objective": objective,
                    "manifest": manifest,
                }
                response.status_code = 200
                return target

        idea = decode_row(_require(db, "ideas", campaign["idea_id"])) or {}
        deadline = (
            decode_row(_require(db, "deadlines", campaign["deadline_id"]))
            if campaign.get("deadline_id")
            else None
        )
        resource = decode_row(_require(db, "resources", campaign["resource_id"])) or {}
        if not resource.get("enabled"):
            raise HTTPException(409, "Selected resource profile is disabled")
        preflight = _validated_preflight(config, str(campaign.get("category_id") or ""))
        resource_contract, missing_resources = _resource_contract(resource, deadline, config)
        if missing_resources:
            raise HTTPException(
                409,
                "Resource contract is incomplete: " + "; ".join(missing_resources),
            )
        submission_snapshot = _submission_snapshot(venue, deadline, resource_contract)

        try:
            compiled, compiled_resources, missing_compiled = _compile_idea_packet(
                idea,
                venue,
                deadline,
                resource,
                request,
                config,
                phase="locked",
                locked_contract=frozen_fields,
            )
        except (KeyError, ValueError) as exc:
            raise HTTPException(422, f"Locked contract cannot be compiled: {exc}") from exc
        if missing_compiled:
            raise HTTPException(
                409,
                "Resource contract is incomplete: " + "; ".join(missing_compiled),
            )
        if compiled_resources != resource_contract:
            raise HTTPException(409, "Resource contract changed while compiling; retry")

        # A promotion child is deterministic for the source/request pair.  This
        # lets a retry find and authenticate a complete packet even if the
        # process died after the filesystem rename but before SQLite committed.
        target_campaign_id = (
            str(
                uuid.uuid5(
                    uuid.UUID(campaign_id),
                    f"locked-contract-promotion:{request_sha256}",
                )
            )
            if promoted_from_campaign_id
            else campaign_id
        )
        if promoted_from_campaign_id:
            config = {
                key: value
                for key, value in source_config.items()
                if key
                not in {
                    "locked_contract",
                    "locked_contract_sha256",
                    "locked_contract_version",
                    "locked_contract_history",
                }
            }
            config["promoted_from_campaign_id"] = promoted_from_campaign_id
            config["promotion_request_sha256"] = request_sha256
            campaign = {
                **source_campaign,
                "id": target_campaign_id,
                "objective": "",
                "schedule_state": "admitted",
                "execution_state": "idle",
                "science_state": "candidate",
                "argus_project_id": None,
                "launch_command_id": None,
                "started_at": None,
                "completed_at": None,
                "config": config,
            }
        campaign_dir = (
            request.app.state.settings.data_dir / "campaigns" / target_campaign_id
        )

        bindings = {
            "venue_id": campaign["venue_id"],
            "idea_id": campaign["idea_id"],
            "deadline_id": campaign["deadline_id"],
            "resource_id": campaign["resource_id"],
        }
        contract_sha256 = _canonical_sha256(
            {
                "schema_version": 1,
                "campaign_id": target_campaign_id,
                "bindings": bindings,
                "request_sha256": request_sha256,
                "prompt_sha256": compiled.prompt_sha256,
                "prompt_input_sha256": compiled.manifest.get("input_sha256"),
                "resource_contract": resource_contract,
                "preflight_attestations": preflight,
            }
        )
        created_at = utc_now()
        created_summary: dict[str, Any] | None = None
        idempotent_summary: dict[str, Any] | None = None
        idempotent_manifest: dict[str, Any] | None = None
        idempotent_objective: str | None = None
        with db.transaction() as transaction:
            source_raw = transaction.execute(
                "SELECT * FROM campaigns WHERE id=?", (campaign_id,)
            ).fetchone()
            if source_raw is None:
                raise HTTPException(404, f"Campaign not found: {campaign_id}")
            current_source = decode_row(dict(source_raw)) or {}
            if current_source.get("execution_state") in {"starting", "running", "draining"}:
                raise HTTPException(409, "Campaign became active while locking")
            current_is_promoted = bool(
                current_source.get("execution_state") != "idle"
                or current_source.get("launch_command_id")
                or current_source.get("argus_project_id")
                or current_source.get("started_at")
            )
            if current_is_promoted != bool(promoted_from_campaign_id):
                raise HTTPException(409, "Campaign lifecycle changed while locking; retry")
            if any(current_source.get(key) != value for key, value in bindings.items()):
                raise HTTPException(409, "Campaign bindings changed while locking; retry")
            current_source_config = (
                current_source.get("config")
                if isinstance(current_source.get("config"), dict)
                else {}
            )
            current_preflight = _validated_preflight(
                current_source_config, str(source_campaign.get("category_id") or "")
            )
            if current_preflight != preflight or current_source_config != source_config:
                raise HTTPException(409, "Campaign configuration changed while locking; retry")
            current_resource_row = transaction.execute(
                "SELECT * FROM resources WHERE id=?", (current_source["resource_id"],)
            ).fetchone()
            current_deadline_row = (
                transaction.execute(
                    "SELECT * FROM deadlines WHERE id=?", (current_source["deadline_id"],)
                ).fetchone()
                if current_source.get("deadline_id")
                else None
            )
            if current_resource_row is None or (
                current_deadline_row is None and _rolling_submission_model(venue) is None
            ):
                raise HTTPException(409, "Locked contract bindings disappeared while locking")
            current_resource = decode_row(dict(current_resource_row)) or {}
            current_deadline = (
                decode_row(dict(current_deadline_row)) if current_deadline_row is not None else None
            )
            if not current_resource.get("enabled"):
                raise HTTPException(409, "Selected resource profile was disabled while locking")
            current_resources, current_missing = _resource_contract(
                current_resource, current_deadline, current_source_config
            )
            if current_missing or current_resources != resource_contract:
                raise HTTPException(409, "Resource contract changed while locking; retry")
            current = current_source
            current_config = current_source_config
            if promoted_from_campaign_id:
                raced_child_id: str | None = None
                promotion_rows = transaction.execute(
                    "SELECT payload_json FROM events WHERE entity_type='campaign' AND entity_id=? "
                    "AND event_type='campaign.contract_promoted' ORDER BY id DESC",
                    (campaign_id,),
                ).fetchall()
                for promotion_row in promotion_rows:
                    try:
                        promotion_payload = json.loads(promotion_row["payload_json"] or "{}")
                    except json.JSONDecodeError:
                        continue
                    if promotion_payload.get("request_sha256") == request_sha256:
                        candidate = promotion_payload.get("target_campaign_id")
                        if isinstance(candidate, str):
                            raced_child_id = candidate
                            break
                if raced_child_id:
                    child_raw = transaction.execute(
                        "SELECT * FROM campaigns WHERE id=?", (raced_child_id,)
                    ).fetchone()
                    if child_raw is None:
                        raise HTTPException(409, "Promotion receipt points to a missing child")
                    current = decode_row(dict(child_raw)) or {}
                    current_config = (
                        current.get("config")
                        if isinstance(current.get("config"), dict)
                        else {}
                    )
                    target_campaign_id = raced_child_id
                    campaign = {**source_campaign, **current}
                    campaign_dir = (
                        request.app.state.settings.data_dir
                        / "campaigns"
                        / target_campaign_id
                    )
                else:
                    transaction.execute(
                        "INSERT INTO campaigns(id,venue_id,idea_id,deadline_id,connection_id,"
                        "resource_id,title,objective,schedule_state,execution_state,science_state,"
                        "config_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            target_campaign_id,
                            current_source["venue_id"],
                            current_source["idea_id"],
                            current_source["deadline_id"],
                            current_source.get("connection_id"),
                            current_source["resource_id"],
                            f"{source_campaign['title']} · Locked promotion",
                            "",
                            "admitted",
                            "idle",
                            "candidate",
                            json.dumps(config, ensure_ascii=False),
                            created_at,
                            created_at,
                        ),
                    )
                    current = campaign
                    current_config = config
            history = _locked_contract_history(current_config)
            for summary in history:
                if summary.get("request_sha256") == request_sha256:
                    idempotent_objective, idempotent_manifest = _load_locked_contract_packet(
                        campaign_dir, campaign, summary
                    )
                    idempotent_summary = summary
                    break
            if idempotent_summary is None:
                versions = [
                    item.get("version")
                    for item in history
                    if isinstance(item.get("version"), int) and item["version"] > 0
                ]
                version = max(versions, default=0) + 1
                directory_name = f"locked-v{version}-{contract_sha256}"
                relative_directory = (Path("contracts") / directory_name).as_posix()
                created_summary = {
                    "version": version,
                    "contract_sha256": contract_sha256,
                    "request_sha256": request_sha256,
                    "prompt_sha256": compiled.prompt_sha256,
                    "directory": relative_directory,
                    "created_at": created_at,
                }
                manifest = {
                    "schema_version": 1,
                    "kind": "locked_campaign_contract",
                    "immutable": True,
                    "campaign_id": target_campaign_id,
                    "venue_key": campaign["venue_key"],
                    "promoted_from_campaign_id": lineage_source_campaign_id,
                    "version": version,
                    "contract_sha256": contract_sha256,
                    "request_sha256": request_sha256,
                    "prompt_sha256": compiled.prompt_sha256,
                    "prompt_manifest": dict(compiled.manifest),
                    "bindings": bindings,
                    "frozen_contract": frozen_fields,
                    "deadline_snapshot": submission_snapshot,
                    "resource_contract": resource_contract,
                    "preflight_attestations": preflight,
                    "human_science_gate": {
                        "human_approved": True,
                        "approval_reason": frozen_fields["approval_reason"],
                        "recorded_at": created_at,
                        "source": "explicit_locked_contract_request",
                    },
                    "created_at": created_at,
                    "launch_triggered": False,
                    "submission": False,
                    "submission_triggered": False,
                }
                contracts_root = campaign_dir / "contracts"
                contracts_root.mkdir(parents=True, exist_ok=True)
                staging = contracts_root / f".locked-staging-{uuid.uuid4()}"
                final_directory = contracts_root / directory_name
                if final_directory.exists():
                    _, recovered_manifest = _existing_locked_packet(
                        campaign_dir,
                        campaign,
                        created_summary,
                        manifest,
                        compiled.prompt,
                    )
                    # Preserve the timestamp already frozen on disk.  Every
                    # other field was authenticated above before DB adoption.
                    created_at = recovered_manifest["created_at"]
                    created_summary["created_at"] = created_at
                    manifest = recovered_manifest
                else:
                    try:
                        staging.mkdir(parents=False, exist_ok=False)
                        _atomic_write(staging / "OBJECTIVE.md", compiled.prompt)
                        _atomic_write(
                            staging / "MANIFEST.json",
                            json.dumps(manifest, ensure_ascii=False, indent=2),
                        )
                        staging.replace(final_directory)
                    except OSError as exc:
                        # A concurrent writer may have won the content-addressed
                        # rename. Authenticate its complete packet on retry;
                        # never overwrite a final directory.
                        if final_directory.exists():
                            _, recovered_manifest = _existing_locked_packet(
                                campaign_dir,
                                campaign,
                                created_summary,
                                manifest,
                                compiled.prompt,
                            )
                            created_at = recovered_manifest["created_at"]
                            created_summary["created_at"] = created_at
                            manifest = recovered_manifest
                        else:
                            raise HTTPException(
                                500, "Failed to freeze immutable locked contract"
                            ) from exc

                next_config = dict(current_config)
                next_config["locked_contract"] = created_summary
                next_config["locked_contract_sha256"] = contract_sha256
                next_config["locked_contract_version"] = version
                next_config["locked_contract_history"] = [*history, created_summary]
                transaction.execute(
                    "UPDATE campaigns SET objective=?,config_json=?,science_state='hypothesis_locked',"
                    "schedule_state='admitted',updated_at=? WHERE id=?",
                    (
                        compiled.prompt,
                        json.dumps(next_config, ensure_ascii=False),
                        created_at,
                        target_campaign_id,
                    ),
                )
                transaction.execute(
                    "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        "campaigns",
                        "campaign.contract_locked",
                        "info",
                        "campaign",
                        target_campaign_id,
                        json.dumps(
                            {
                                **created_summary,
                                "human_approved": True,
                                "approval_reason": frozen_fields["approval_reason"],
                                "launch_triggered": False,
                                "submission": False,
                                "submission_triggered": False,
                            },
                            ensure_ascii=False,
                        ),
                        created_at,
                    ),
                )
                if promoted_from_campaign_id:
                    transaction.execute(
                        "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                        "VALUES (?,?,?,?,?,?,?)",
                        (
                            "campaigns",
                            "campaign.contract_promoted",
                            "info",
                            "campaign",
                            promoted_from_campaign_id,
                            json.dumps(
                                {
                                    "source_campaign_id": promoted_from_campaign_id,
                                    "target_campaign_id": target_campaign_id,
                                    "request_sha256": request_sha256,
                                    "contract_sha256": contract_sha256,
                                    "human_approved": True,
                                    "approval_reason": frozen_fields["approval_reason"],
                                    "launch_triggered": False,
                                    "submission": False,
                                    "submission_triggered": False,
                                },
                                ensure_ascii=False,
                            ),
                            created_at,
                        ),
                    )

        result = _campaign_row(db, target_campaign_id)
        result["promoted_from_campaign_id"] = lineage_source_campaign_id
        result["target_campaign_id"] = target_campaign_id
        if idempotent_summary is not None:
            response.status_code = 200
            result["locked_contract"] = {
                **idempotent_summary,
                "idempotent": True,
                "objective": idempotent_objective,
                "manifest": idempotent_manifest,
            }
            return result
        if created_summary is None:
            raise HTTPException(500, "Locked contract transaction produced no receipt")
        objective, manifest = _load_locked_contract_packet(
            campaign_dir, result, created_summary
        )
        result["locked_contract"] = {
            **created_summary,
            "idempotent": False,
            "objective": objective,
            "manifest": manifest,
        }
        return result

    @router.patch("/campaigns/{campaign_id}")
    def update_campaign(
        campaign_id: str, body: CampaignUpdate, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        campaign = _campaign_row(db, campaign_id)
        changes = body.model_dump(exclude_unset=True)
        if not changes:
            return campaign
        campaign_config = (
            campaign.get("config") if isinstance(campaign.get("config"), dict) else {}
        )
        conditioned_binding = db.fetch_one(
            "SELECT campaign_id FROM conditioned_campaign_bindings WHERE campaign_id=?",
            (campaign_id,),
        )
        if conditioned_binding or campaign_config.get("campaign_kind") in {
            "conditioned_ideation",
            "conditioned_candidate_research",
            "rebuttal_follow_up",
        }:
            frozen_fields = {
                "objective": campaign.get("objective"),
                "connection_id": campaign.get("connection_id"),
                "resource_id": campaign.get("resource_id"),
                "config": campaign_config,
            }
            mutated = sorted(
                key
                for key, frozen in frozen_fields.items()
                if key in changes and changes[key] != frozen
            )
            if mutated:
                raise HTTPException(
                    409,
                    "Campaign provenance is immutable; create a new formally bound "
                    "campaign instead of changing: "
                    + ", ".join(mutated),
                )
        if campaign.get("launch_command_id"):
            launch_critical = {
                "title": campaign.get("title"),
                "objective": campaign.get("objective"),
                "connection_id": campaign.get("connection_id"),
                "resource_id": campaign.get("resource_id"),
                "config": campaign_config,
            }
            mutated = sorted(
                key
                for key, frozen in launch_critical.items()
                if key in changes and changes[key] != frozen
            )
            if mutated:
                raise HTTPException(
                    409,
                    "Launch receipt freezes launch-critical fields: " + ", ".join(mutated),
                )
        locked_summary = campaign_config.get("locked_contract")
        if isinstance(locked_summary, dict):
            campaign_dir = request.app.state.settings.data_dir / "campaigns" / campaign_id
            frozen_objective, _ = _load_locked_contract_packet(
                campaign_dir, campaign, locked_summary
            )
            if "objective" in changes and changes["objective"] != frozen_objective:
                raise HTTPException(
                    409,
                    "Objective is frozen by the locked contract; create a new contract version",
                )
            if (
                "resource_id" in changes
                and changes["resource_id"] != campaign.get("resource_id")
            ):
                raise HTTPException(
                    409,
                    "Resource binding is frozen by the locked contract; create a new contract version",
                )
            if (
                "science_state" in changes
                and changes["science_state"] != "hypothesis_locked"
            ):
                raise HTTPException(409, "A locked hypothesis cannot be manually unlocked")
            if "config" in changes:
                replacement = changes["config"]
                immutable_keys = (
                    "locked_contract",
                    "locked_contract_sha256",
                    "locked_contract_version",
                    "locked_contract_history",
                )
                if not isinstance(replacement, dict) or any(
                    replacement.get(key) != campaign_config.get(key)
                    for key in immutable_keys
                ):
                    raise HTTPException(
                        409,
                        "Locked contract registry is immutable; preserve it or create a new version",
                    )
        if "connection_id" in changes and changes["connection_id"]:
            _require(db, "connections", changes["connection_id"])
        if "resource_id" in changes and changes["resource_id"]:
            _require(db, "resources", changes["resource_id"])
        if "config" in changes:
            changes["config_json"] = json.dumps(changes.pop("config"), ensure_ascii=False)
        if "scheduled_for" in changes:
            changes["scheduled_for"] = _utc_schedule(changes["scheduled_for"], "scheduled_for")
        allowed = {
            "title", "objective", "connection_id", "resource_id", "scheduled_for",
            "schedule_state", "science_state", "integrity_state", "deadline_state",
            "progress", "last_summary", "config_json",
        }
        changes = {key: value for key, value in changes.items() if key in allowed}
        changes["updated_at"] = utc_now()
        assignments = ",".join(f"{key}=?" for key in changes)
        db.execute(
            f"UPDATE campaigns SET {assignments} WHERE id=?",
            [*changes.values(), campaign_id],
        )
        db.append_event(
            "campaigns", "campaign.updated", entity_type="campaign", entity_id=campaign_id,
            payload={"fields": sorted(changes)},
        )
        return _campaign_row(db, campaign_id)

    @router.post("/campaigns/{campaign_id}/start")
    async def start_campaign(
        campaign_id: str,
        body: CampaignStartRequest,
        request: Request,
    ) -> dict[str, Any]:
        db = _db(request)
        campaign = _campaign_row(db, campaign_id)
        launch_provenance = _verify_campaign_launch_provenance(
            db,
            campaign,
            request.app.state.settings.data_dir,
            request.app.state.settings,
        )
        if campaign["execution_state"] in {"starting", "running"}:
            raise HTTPException(409, "Campaign is already active")
        retrying = bool(campaign.get("launch_command_id"))
        if retrying and (
            campaign.get("argus_project_id")
            or campaign["execution_state"] not in {"failed", "needs_attention"}
        ):
            raise HTTPException(
                409,
                "Existing launch receipt can only be reconciled from failed/needs_attention "
                "when no Argus project is attached",
            )
        if not campaign.get("connection_id"):
            raise HTTPException(409, "Select an Argus connection before starting")
        connection = _require(db, "connections", campaign["connection_id"])
        if not connection["enabled"]:
            raise HTTPException(409, "Selected Argus connection is disabled")
        decoded_connection = decode_row(connection) or {}
        connection_metadata = decoded_connection.get("metadata")
        launch_compatible = (
            isinstance(connection_metadata, dict)
            and connection_metadata.get("launch_compatible") is True
        )
        if connection["status"] != "online" or not launch_compatible:
            raise HTTPException(
                409,
                "Selected Argus connection has not passed the launch-compatibility gate "
                f"(status={connection['status']}, launch_compatible={launch_compatible}); "
                "use Test connection and resolve any reported incompatibility before starting",
            )
        if not campaign.get("resource_id"):
            raise HTTPException(409, "Select and configure a resource profile before starting")
        resource = decode_row(_require(db, "resources", campaign["resource_id"])) or {}
        if not resource.get("enabled"):
            raise HTTPException(409, "Selected resource profile is disabled")
        if str(resource.get("availability_state") or "").strip().lower() != "available":
            raise HTTPException(
                409,
                "Selected resource profile is not available "
                f"(availability_state={resource.get('availability_state')})",
            )
        deadline = (
            decode_row(_require(db, "deadlines", campaign["deadline_id"]))
            if campaign.get("deadline_id") else None
        )
        venue = decode_row(_require(db, "venues", campaign["venue_id"])) or {}
        if deadline is None and _rolling_submission_model(venue) is None:
            raise HTTPException(409, "A fixed-deadline venue requires an associated deadline")
        campaign_dir = request.app.state.settings.data_dir / "campaigns" / campaign_id
        workspace = campaign_dir / "workspace"
        for directory in (workspace, campaign_dir / "life", campaign_dir / "reviews"):
            directory.mkdir(parents=True, exist_ok=True)
        if retrying:
            command_id = str(campaign["launch_command_id"])
            objective, manifest = _load_frozen_launch_packet(
                campaign_dir, workspace, campaign, connection
            )
            launch = manifest["launch"]
            launch_name = launch["name"]
            launch_workdir = launch["workdir"]
            launch_cwd = launch["launch_cwd"]
            frozen_approval = manifest.get("human_launch_approval")
            requested_approval = body.model_dump(mode="json")
            if not isinstance(frozen_approval, dict) or any(
                frozen_approval.get(key) != value
                for key, value in requested_approval.items()
            ):
                raise HTTPException(
                    409,
                    "Launch retry must preserve the immutable human approval, reason, and actor",
                )
            frozen_resources = manifest.get("resource_contract")
            if not isinstance(frozen_resources, dict):
                raise HTTPException(409, "Frozen launch packet has no resource contract")
            _require_future_launch_cutoff(frozen_resources)
            now = utc_now()
            with db.transaction() as transaction:
                reserved = transaction.execute(
                    "UPDATE campaigns SET execution_state='starting',updated_at=? WHERE id=? "
                    "AND launch_command_id=? AND argus_project_id IS NULL "
                    "AND execution_state IN ('failed','needs_attention')",
                    (now, campaign_id, command_id),
                )
                if reserved.rowcount != 1:
                    raise HTTPException(409, "Launch receipt changed concurrently; refresh and retry")
                transaction.execute(
                    "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        "campaigns",
                        "campaign.start_reconciliation_attempted",
                        "attention",
                        "campaign",
                        campaign_id,
                        json.dumps(
                            {
                                "launch_command_id": command_id,
                                "prompt_sha256": manifest["prompt_sha256"],
                                "frozen_packet_reused": True,
                                "human_launch_approval": frozen_approval,
                            },
                            ensure_ascii=False,
                        ),
                        now,
                    ),
                )
        else:
            resource_contract, missing_resources = _resource_contract(
                resource, deadline, campaign.get("config") or {}
            )
            if missing_resources:
                raise HTTPException(
                    409,
                    "Resource contract is incomplete: " + "; ".join(missing_resources),
                )
            _require_future_launch_cutoff(resource_contract)
            submission_snapshot = _submission_snapshot(venue, deadline, resource_contract)
            campaign_config = campaign.get("config") or {}
            requested_backend = str(campaign_config.get("backend") or "connection-default")
            if requested_backend != "connection-default":
                raise HTTPException(
                    409,
                    "Argus WebAPI CreateDaemonIn cannot apply a per-launch backend override; "
                    "configure and verify the backend on the target Argus instance, then use "
                    "backend='connection-default'",
                )
            preflight_attestations = _validated_preflight(
                campaign_config, str(campaign.get("category_id") or "")
            )
            locked_summary = campaign_config.get("locked_contract")
            locked_packet_manifest: dict[str, Any] | None = None
            if isinstance(locked_summary, dict):
                objective, locked_packet_manifest = _load_locked_contract_packet(
                    campaign_dir, campaign, locked_summary
                )
                if locked_packet_manifest.get("resource_contract") != resource_contract:
                    raise HTTPException(
                        409,
                        "Resource contract changed after hypothesis lock; create a new locked version",
                    )
                if locked_packet_manifest.get("preflight_attestations") != preflight_attestations:
                    raise HTTPException(
                        409,
                        "Preflight attestations changed after hypothesis lock; create a new locked version",
                    )
                prompt_manifest = dict(locked_packet_manifest.get("prompt_manifest") or {})
                if prompt_manifest.get("phase") != "locked":
                    raise HTTPException(409, "Frozen contract is not a locked-phase prompt")
            else:
                raw_objective = str(campaign.get("objective") or "")
                objective = (
                    raw_objective
                    if launch_provenance is not None
                    else raw_objective.strip()
                )
                provenance_kind = (
                    str(launch_provenance.get("campaign_kind") or "")
                    if launch_provenance is not None
                    else ""
                )
                compiler = {
                    "conditioned_ideation": "conditioned-ideation",
                    "conditioned_candidate_research": "conditioned-candidate",
                    "rebuttal_follow_up": "rebuttal-follow-up",
                }.get(provenance_kind, "custom-objective")
                prompt_manifest = {
                    "compiler": compiler,
                    "oral_is_aspiration_only": True,
                    "launch_provenance": launch_provenance,
                }
            if not objective:
                if not campaign.get("idea_id"):
                    raise HTTPException(409, "Campaign has neither an objective nor an idea")
                idea = decode_row(_require(db, "ideas", campaign["idea_id"])) or {}
                compiled, compiled_resources, missing_compiled = _compile_idea_packet(
                    idea,
                    venue,
                    deadline,
                    resource,
                    request,
                    campaign_config,
                )
                if missing_compiled:
                    raise HTTPException(
                        409,
                        "Resource contract is incomplete: " + "; ".join(missing_compiled),
                    )
                if compiled_resources != resource_contract:
                    raise HTTPException(
                        409,
                        "Resource contract changed while compiling the launch prompt; retry",
                    )
                objective = compiled.prompt
                prompt_manifest = dict(compiled.manifest)
                db.execute(
                    "UPDATE campaigns SET objective=?,updated_at=? WHERE id=?",
                    (objective, utc_now(), campaign_id),
                )
            command_id = str(uuid.uuid4())
            launch_name = campaign["title"]
            remote_target = connection["kind"] == "remote"
            launch_workdir = "" if remote_target else str(workspace)
            launch_cwd = "" if remote_target else str(workspace)
            workspace_mode = (
                "target_argus_default" if remote_target else "foundry_local_isolated"
            )
            connection_metadata = (decode_row(connection) or {}).get("metadata") or {}
            release_truth = _release_pin_truth(
                campaign_config,
                connection,
                connection_metadata,
            )
            launch_approved_at = utc_now()
            human_launch_approval = {
                **body.model_dump(mode="json"),
                "approved_at": launch_approved_at,
                "source": "explicit_start_request",
            }
            manifest = {
                "schema_version": 1,
                "campaign_id": campaign_id,
                "prompt_sha256": hashlib.sha256(objective.encode("utf-8")).hexdigest(),
                "prompt_manifest": prompt_manifest,
                "launch_command_id": command_id,
                "created_at": utc_now(),
                "venue_key": campaign["venue_key"],
                "idea_id": campaign.get("idea_id"),
                "deadline": deadline,
                "submission_snapshot": submission_snapshot,
                "resource_id": resource["id"],
                "resource_contract": resource_contract,
                "preflight_attestations": preflight_attestations,
                "locked_contract": (
                    {
                        "version": locked_packet_manifest["version"],
                        "contract_sha256": locked_packet_manifest["contract_sha256"],
                        "request_sha256": locked_packet_manifest["request_sha256"],
                        "directory": locked_summary["directory"],
                    }
                    if locked_packet_manifest is not None
                    else None
                ),
                "connection": {
                    "id": connection["id"],
                    "name": connection["name"],
                    "kind": connection["kind"],
                    "base_url": connection["base_url"],
                },
                "launch": {
                    "name": launch_name,
                    "workdir": launch_workdir,
                    "launch_cwd": launch_cwd,
                    "workspace_mode": workspace_mode,
                    "local_evidence_dir": str(campaign_dir),
                },
                **release_truth,
                "human_launch_approval": human_launch_approval,
                "backend": "connection-default",
                "backend_source": "target_argus_instance_configuration",
                "backend_override_applied": False,
                "submission_is_never_automatic": True,
            }
            reserved_at = utc_now()
            with db.transaction() as transaction:
                global_limit_row = transaction.execute(
                    "SELECT value_json FROM app_settings WHERE key='max_concurrent_campaigns'"
                ).fetchone()
                try:
                    configured_global_limit = (
                        json.loads(global_limit_row["value_json"])
                        if global_limit_row is not None
                        else None
                    )
                except json.JSONDecodeError as exc:
                    raise HTTPException(
                        409, "max_concurrent_campaigns must be valid JSON"
                    ) from exc
                global_limit = _strict_positive_limit(
                    configured_global_limit, "max_concurrent_campaigns"
                )
                resource_limit = _strict_positive_limit(
                    resource_contract.get("max_parallel_jobs"),
                    "resource capacity.max_parallel_jobs",
                )
                active_states = ("starting", "running", "draining")
                active_global = transaction.execute(
                    "SELECT COUNT(*) AS n FROM campaigns WHERE execution_state IN (?,?,?)",
                    active_states,
                ).fetchone()["n"]
                if active_global >= global_limit:
                    raise HTTPException(
                        409,
                        f"Global campaign concurrency limit reached ({global_limit})",
                    )
                active_resource = transaction.execute(
                    "SELECT COUNT(*) AS n FROM campaigns WHERE resource_id=? "
                    "AND execution_state IN (?,?,?)",
                    (resource["id"], *active_states),
                ).fetchone()["n"]
                if active_resource >= resource_limit:
                    raise HTTPException(
                        409,
                        f"Resource parallel-job limit reached ({resource_limit})",
                    )
                reserved = transaction.execute(
                    "UPDATE campaigns SET execution_state='starting',schedule_state='started',"
                    "launch_command_id=?,updated_at=? WHERE id=? AND launch_command_id IS NULL "
                    "AND argus_project_id IS NULL AND execution_state NOT IN ('starting','running')",
                    (command_id, reserved_at, campaign_id),
                )
                if reserved.rowcount != 1:
                    raise HTTPException(409, "Campaign launch changed concurrently; refresh and retry")
                transaction.execute(
                    "INSERT INTO events(topic,event_type,severity,entity_type,entity_id,payload_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        "campaigns",
                        "campaign.start_authorized",
                        "info",
                        "campaign",
                        campaign_id,
                        json.dumps(
                            {
                                "launch_command_id": command_id,
                                "prompt_sha256": manifest["prompt_sha256"],
                                "human_launch_approval": human_launch_approval,
                            },
                            ensure_ascii=False,
                        ),
                        reserved_at,
                    ),
                )
            try:
                _atomic_write(campaign_dir / "OBJECTIVE.md", objective)
                _atomic_write(
                    campaign_dir / "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
                _atomic_write(
                    campaign_dir / "SOURCE_SNAPSHOT.json",
                    json.dumps(
                        {
                            "captured_at": utc_now(),
                            "deadline": deadline,
                            "idea_id": campaign.get("idea_id"),
                            "note": "Runtime literature/GitHub snapshots are appended; this launch file is immutable.",
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            except OSError as exc:
                db.execute(
                    "UPDATE campaigns SET execution_state='failed',last_summary=?,updated_at=? WHERE id=?",
                    (f"Failed to freeze launch packet: {exc}", utc_now(), campaign_id),
                )
                db.append_event(
                    "campaigns",
                    "campaign.launch_packet_failed",
                    severity="error",
                    entity_type="campaign",
                    entity_id=campaign_id,
                    payload={"error": str(exc), "launch_command_id": command_id},
                )
                raise HTTPException(500, "Failed to freeze the campaign launch packet") from exc
        try:
            response = await asyncio.to_thread(
                _client(request, connection).create_daemon,
                objective=objective,
                name=launch_name,
                workdir=launch_workdir,
                launch_cwd=launch_cwd,
                command_id=command_id,
            )
            receipt = require_argus_daemon_command_applied(
                response,
                operation="create daemon",
                require_activation=True,
            )
        except ArgusDaemonCommandError as exc:
            receipt = exc.receipt
            project_id = receipt.project_id
            needs_attention = bool(project_id) or exc.outcome in {
                "admission_required",
                "inconclusive",
            }
            failed_state = "needs_attention" if needs_attention else "failed"
            now = utc_now()
            if project_id:
                db.execute(
                    "UPDATE campaigns SET execution_state=?,argus_project_id=?,"
                    "last_summary=?,updated_at=? WHERE id=?",
                    (failed_state, project_id, str(exc), now, campaign_id),
                )
            else:
                db.execute(
                    "UPDATE campaigns SET execution_state=?,last_summary=?,updated_at=? WHERE id=?",
                    (failed_state, str(exc), now, campaign_id),
                )
            if exc.outcome == "admission_required":
                event_type = "campaign.start_admission_required"
            elif exc.outcome == "inconclusive":
                event_type = "campaign.start_receipt_inconclusive"
            else:
                event_type = "campaign.start_rejected"
            db.append_event(
                "campaigns",
                event_type,
                severity="attention" if needs_attention else "error",
                entity_type="campaign",
                entity_id=campaign_id,
                payload={
                    **receipt.audit_payload(),
                    "launch_command_id": command_id,
                    "reconciliation_attempt": retrying,
                    "human_launch_approval": (
                        manifest.get("human_launch_approval")
                        if isinstance(manifest, dict)
                        else None
                    ),
                },
            )
            status_code = 409 if exc.outcome == "admission_required" else 502
            raise HTTPException(status_code, str(exc)) from exc
        except (ArgusWebApiError, ValueError) as exc:
            db.execute(
                "UPDATE campaigns SET execution_state='failed',last_summary=?,updated_at=? WHERE id=?",
                (str(exc), utc_now(), campaign_id),
            )
            db.append_event(
                "campaigns", "campaign.start_failed", severity="error", entity_type="campaign",
                entity_id=campaign_id,
                payload={
                    "error": str(exc),
                    "launch_command_id": command_id,
                    "reconciliation_attempt": retrying,
                    "human_launch_approval": (
                        manifest.get("human_launch_approval")
                        if isinstance(manifest, dict)
                        else None
                    ),
                },
            )
            raise HTTPException(502, str(exc)) from exc
        project_id = receipt.project_id or _extract_project_id(response)
        if not project_id:
            db.execute(
                "UPDATE campaigns SET execution_state='needs_attention',last_summary=?,updated_at=? WHERE id=?",
                ("Argus accepted the request but returned no project id", utc_now(), campaign_id),
            )
            db.append_event(
                "campaigns",
                "campaign.start_receipt_inconclusive",
                severity="attention",
                entity_type="campaign",
                entity_id=campaign_id,
                payload={
                    "launch_command_id": command_id,
                    "reconciliation_attempt": retrying,
                    "command_status": response.get("command_status")
                    if isinstance(response, dict)
                    else None,
                },
            )
            raise HTTPException(502, "Argus response did not contain a project/session id")
        db.execute(
            "UPDATE campaigns SET execution_state='running',science_state='researching',"
            "argus_project_id=?,started_at=?,updated_at=? WHERE id=?",
            (project_id, utc_now(), utc_now(), campaign_id),
        )
        db.append_event(
            "campaigns", "campaign.started", entity_type="campaign", entity_id=campaign_id,
            payload={
                "argus_project_id": project_id,
                "connection_id": connection["id"],
                "launch_command_id": command_id,
                "receipt_reconciled": retrying,
                "human_launch_approval": manifest.get("human_launch_approval"),
                "target_workdir": _extract_remote_workdir(response),
                "workspace_mode": manifest.get("launch", {}).get("workspace_mode"),
            },
        )
        return _campaign_row(db, campaign_id)

    async def _stop_campaign(
        campaign_id: str, body: CampaignAction, request: Request, *, drain: bool
    ) -> dict[str, Any]:
        db = _db(request)
        campaign = _campaign_row(db, campaign_id)
        if not campaign.get("argus_project_id") or not campaign.get("connection_id"):
            raise HTTPException(409, "Campaign has no attached Argus project")
        connection = _require(db, "connections", campaign["connection_id"])
        try:
            response = await asyncio.to_thread(
                _client(request, connection).stop,
                campaign["argus_project_id"],
                drain=drain,
                force=body.force if not drain else False,
            )
            receipt = require_argus_daemon_command_applied(
                response,
                operation="drain daemon" if drain else "stop daemon",
            )
        except (ArgusWebApiError, ValueError) as exc:
            action = "drain" if drain else "pause"
            receipt_payload = (
                exc.receipt.audit_payload()
                if isinstance(exc, ArgusDaemonCommandError)
                else {}
            )
            db.append_event(
                "campaigns",
                f"campaign.{action}_failed",
                severity="error",
                entity_type="campaign",
                entity_id=campaign_id,
                payload={
                    **receipt_payload,
                    "error": str(exc),
                    "reason": body.reason,
                    "force": body.force,
                    "previous_execution_state": campaign["execution_state"],
                },
            )
            raise HTTPException(502, str(exc)) from exc
        state = "draining" if drain and not body.force else "paused"
        db.execute(
            "UPDATE campaigns SET execution_state=?,last_summary=?,updated_at=? WHERE id=?",
            (state, body.reason, utc_now(), campaign_id),
        )
        db.append_event(
            "campaigns", f"campaign.{state}", severity="attention", entity_type="campaign",
            entity_id=campaign_id,
            payload={
                "reason": body.reason,
                "force": body.force,
                **receipt.audit_payload(),
            },
        )
        return _campaign_row(db, campaign_id)

    @router.post("/campaigns/{campaign_id}/pause")
    async def pause_campaign(
        campaign_id: str, body: CampaignAction, request: Request
    ) -> dict[str, Any]:
        return await _stop_campaign(campaign_id, body, request, drain=False)

    @router.post("/campaigns/{campaign_id}/drain")
    async def drain_campaign(
        campaign_id: str, body: CampaignAction, request: Request
    ) -> dict[str, Any]:
        return await _stop_campaign(campaign_id, body, request, drain=True)

    @router.post("/campaigns/{campaign_id}/review", status_code=202)
    async def request_review(
        campaign_id: str, body: ReviewRequest, request: Request
    ) -> dict[str, Any]:
        campaign = _campaign_row(_db(request), campaign_id)
        evidence = await _freeze_viewer_evidence(campaign, request)
        approval = {
            "human_approved": True,
            "actor": body.actor,
            "approval_reason": body.approval_reason,
            "approved_at": utc_now(),
            "scope": "one_independent_viewer_request",
        }
        return _enqueue_independent_review(
            campaign=campaign,
            reviewer_kind=body.reviewer_kind,
            rubric=body.rubric,
            approval=approval,
            evidence=evidence,
            request=request,
        )

    @router.post("/campaigns/{campaign_id}/review-panel", status_code=202)
    async def request_review_panel(
        campaign_id: str, body: ReviewPanelRequest, request: Request
    ) -> dict[str, Any]:
        """Queue 2-5 fresh-context reviewers against one frozen evidence snapshot."""

        campaign = _campaign_row(_db(request), campaign_id)
        evidence = await _freeze_viewer_evidence(campaign, request)
        approved_at = utc_now()
        receipts: list[dict[str, Any]] = []
        for reviewer_kind in body.reviewer_kinds:
            approval = {
                "human_approved": True,
                "actor": body.actor,
                "approval_reason": body.approval_reason,
                "approved_at": approved_at,
                "scope": "independent_review_panel",
                "panel_size": len(body.reviewer_kinds),
            }
            receipts.append(
                _enqueue_independent_review(
                    campaign=campaign,
                    reviewer_kind=reviewer_kind,
                    rubric=body.rubrics.get(reviewer_kind, {}),
                    approval=approval,
                    evidence=evidence,
                    request=request,
                )
            )
        return {
            "campaign_id": campaign_id,
            "state": "queued",
            "panel_size": len(receipts),
            "reviewers": receipts,
            "evidence_snapshot_sha256": evidence.sha256,
            "evidence_snapshot_state": evidence.state,
            "aggregation_policy": "preserve_dimension_scores_vetoes_and_disagreement",
            "acceptance_probability": None,
        }

    @router.get("/connections")
    def connections(request: Request) -> dict[str, Any]:
        items = connections_public(_db(request).fetch_all("SELECT * FROM connections ORDER BY name"))
        return {"items": items, "total": len(items)}

    @router.post("/connections", status_code=201)
    def create_connection(body: ConnectionCreate, request: Request) -> dict[str, Any]:
        db = _db(request)
        _reject_reserved_connection_metadata(body.metadata)
        if body.token_env and body.bearer_token is not None:
            raise HTTPException(422, "bearer_token and token_env are mutually exclusive")
        base_url = str(body.base_url).rstrip("/")
        _validate_connection_token_env(request, body.token_env, base_url)
        connection_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO connections(id,name,kind,base_url,token_ref,enabled,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                connection_id, body.name, body.kind, base_url,
                (f"env:{body.token_env}" if body.token_env else (
                    request.app.state.secrets.put(connection_id, body.bearer_token) if body.bearer_token else None)),
                int(body.enabled), json.dumps(body.metadata, ensure_ascii=False), now, now,
            ),
        )
        db.append_event(
            "connections", "connection.created", entity_type="connection", entity_id=connection_id,
            payload={"name": body.name, "kind": body.kind},
        )
        return connection_public(_require(db, "connections", connection_id)) or {}

    @router.patch("/connections/{connection_id}")
    def update_connection(
        connection_id: str, body: ConnectionUpdate, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        existing_raw = _require(db, "connections", connection_id)
        existing = decode_row(existing_raw) or {}
        existing_metadata = dict(existing.get("metadata") or {})
        changes = body.model_dump(exclude_unset=True)
        clear_token = bool(changes.pop("clear_bearer_token", False))
        if "base_url" in changes and changes["base_url"] is not None:
            changes["base_url"] = str(changes["base_url"]).rstrip("/")
        token_env_was_supplied = "token_env" in changes and bool(changes.get("token_env"))
        token_env = changes.pop("token_env", None)
        bearer_was_supplied = "bearer_token" in changes
        if token_env_was_supplied and bearer_was_supplied:
            raise HTTPException(422, "bearer_token and token_env are mutually exclusive")
        target_base_url = str(changes.get("base_url") or existing.get("base_url") or "")
        target_token_ref = existing.get("token_ref")
        if token_env_was_supplied:
            target_token_ref = f"env:{token_env}"
        elif bearer_was_supplied:
            target_token_ref = None
        elif clear_token:
            target_token_ref = None
        _validate_connection_token_ref(
            request,
            {"token_ref": target_token_ref, "base_url": target_base_url},
        )
        endpoint_or_kind_changed = (
            ("base_url" in changes and changes["base_url"] != existing.get("base_url"))
            or ("kind" in changes and changes["kind"] != existing.get("kind"))
        )
        credential_changed = bearer_was_supplied or clear_token or (
            token_env_was_supplied
            and existing.get("token_ref") != f"env:{token_env}"
        )
        identity_changed = endpoint_or_kind_changed or credential_changed
        if identity_changed:
            active = db.fetch_one(
                "SELECT COUNT(*) AS n FROM campaigns WHERE connection_id=? "
                "AND execution_state IN ('starting','running','draining')",
                (connection_id,),
            )
            if active and active["n"]:
                raise HTTPException(
                    409,
                    "An active campaign freezes its Argus endpoint, kind, and credentials",
                )
        if "metadata" in changes:
            supplied_metadata = changes.pop("metadata")
            if not isinstance(supplied_metadata, dict):
                raise HTTPException(422, "Connection metadata must be an object")
            _reject_reserved_connection_metadata(supplied_metadata)
            preserved_probe_truth = {
                key: value
                for key, value in existing_metadata.items()
                if key in _CONNECTION_PROBE_METADATA_KEYS
            }
            existing_metadata = {**supplied_metadata, **preserved_probe_truth}
        if token_env:
            request.app.state.secrets.remove(connection_id)
            changes["token_ref"] = f"env:{token_env}"
        elif "bearer_token" in changes:
            secret = changes.pop("bearer_token")
            changes["token_ref"] = request.app.state.secrets.put(connection_id, secret) if secret else None
        elif clear_token:
            request.app.state.secrets.remove(connection_id)
            changes["token_ref"] = None
        if identity_changed:
            existing_metadata = _without_connection_probe_truth(existing_metadata)
            changes["status"] = "unknown"
            changes["last_checked_at"] = None
            changes["last_error"] = "Connection identity changed; compatibility must be probed again"
        if "metadata" in body.model_fields_set or identity_changed:
            changes["metadata_json"] = json.dumps(existing_metadata, ensure_ascii=False)
        if "enabled" in changes:
            changes["enabled"] = int(changes["enabled"])
        if changes:
            changes["updated_at"] = utc_now()
            assignments = ",".join(f"{key}=?" for key in changes)
            db.execute(
                f"UPDATE connections SET {assignments} WHERE id=?",
                [*changes.values(), connection_id],
            )
        return connection_public(_require(db, "connections", connection_id)) or {}

    async def _probe(connection_id: str, request: Request) -> dict[str, Any]:
        db = _db(request)
        connection = _require(db, "connections", connection_id)
        _validate_connection_token_ref(request, connection)
        try:
            tested = await asyncio.to_thread(_client(request, connection).test_connection)
            assessment = assess_argus_connection(tested)
            protocol = dict(tested.protocol)
            capabilities = list(tested.capabilities)
            meta = {
                "authentication": {
                    "required": tested.authentication_required,
                    "authenticated": tested.authenticated,
                },
                "runtime": dict(tested.runtime),
                "protocol": protocol,
                "snapshot_schema_version": tested.snapshot_schema_version,
                "capabilities": capabilities,
                "protocol_compatible": assessment.protocol_compatible,
                "launch_compatible": assessment.launch_compatible,
                "backend_ready": assessment.backend_ready,
                "system_doctor": {
                    **dict(tested.doctor_summary),
                    "generated_at": tested.doctor_generated_at,
                },
                "missing_capabilities": list(assessment.missing_capabilities),
            }
            status, error = assessment.status, assessment.error
            decoded_connection = decode_row(connection) or {}
            persisted_metadata = dict(decoded_connection.get("metadata") or {})
            persisted_metadata.update(argus_connection_metadata(tested, assessment))
        except (ArgusWebApiError, ValueError) as exc:
            meta, persisted_metadata, status, error = {}, None, "offline", str(exc)
        db.execute(
            "UPDATE connections SET status=?,last_checked_at=?,last_error=?,metadata_json="
            "COALESCE(?,metadata_json),updated_at=? WHERE id=?",
            (
                status,
                utc_now(),
                error,
                json.dumps(persisted_metadata, ensure_ascii=False)
                if persisted_metadata is not None
                else None,
                utc_now(),
                connection_id,
            ),
        )
        db.append_event(
            "connections", f"connection.{status}",
            severity="warning" if error else "info", entity_type="connection",
            entity_id=connection_id, payload={"error": error},
        )
        result = connection_public(_require(db, "connections", connection_id)) or {}
        result["argus_meta"] = meta
        return result

    @router.post("/connections/{connection_id}/test")
    async def test_connection(connection_id: str, request: Request) -> dict[str, Any]:
        return await _probe(connection_id, request)

    @router.post("/connections/{connection_id}/probe")
    async def probe_connection_alias(connection_id: str, request: Request) -> dict[str, Any]:
        return await _probe(connection_id, request)

    @router.delete("/connections/{connection_id}", status_code=204)
    def delete_connection(connection_id: str, request: Request) -> None:
        db = _db(request)
        _require(db, "connections", connection_id)
        in_use = db.fetch_one(
            "SELECT COUNT(*) AS n FROM campaigns WHERE connection_id=?", (connection_id,)
        )["n"]
        if in_use:
            raise HTTPException(409, "Connection is used by one or more campaigns; disable it instead")
        db.execute("DELETE FROM connections WHERE id=?", (connection_id,))

    @router.get("/resources")
    def resources(request: Request) -> dict[str, Any]:
        items = decode_rows(_db(request).fetch_all("SELECT * FROM resources ORDER BY name"))
        return {"items": items, "total": len(items)}

    @router.post("/resources/probe")
    def detect_resources(request: Request) -> dict[str, Any]:
        db = _db(request)
        detected = probe_resources()
        now = utc_now()
        if detected.get("available") and detected.get("devices"):
            devices = detected["devices"]
            capacity = {"configured": True, "gpu_count": len(devices),
                        "gpu_model": devices[0]["name"] if len({d["name"] for d in devices}) == 1 else "mixed",
                        "devices": devices}
            db.execute(
                """INSERT INTO resources(id,name,resource_type,capacity_json,availability_state,
                   enabled,metadata_json,created_at,updated_at) VALUES('local-detected',?,'gpu_pool',?,
                   'available',1,?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                   capacity_json=excluded.capacity_json,availability_state=excluded.availability_state,
                   metadata_json=excluded.metadata_json,updated_at=excluded.updated_at""",
                (f"Local detected · {capacity['gpu_count']}×{capacity['gpu_model']}",
                 json.dumps(capacity, ensure_ascii=False), json.dumps({"probe": "nvidia-smi"}), now, now))
        db.append_event("resources", "resources.probed", entity_type="resource",
                        entity_id="local-detected", payload=detected)
        return {"probe": detected,
                "resource": decode_row(db.fetch_one("SELECT * FROM resources WHERE id='local-detected'"))}

    @router.post("/resources", status_code=201)
    def create_resource(body: ResourceCreate, request: Request) -> dict[str, Any]:
        db = _db(request)
        resource_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO resources(id,name,resource_type,capacity_json,availability_state,enabled,"
            "metadata_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                resource_id, body.name, body.resource_type,
                json.dumps(body.capacity, ensure_ascii=False), body.availability_state,
                int(body.enabled), json.dumps(body.metadata, ensure_ascii=False), now, now,
            ),
        )
        return decode_row(_require(db, "resources", resource_id)) or {}

    @router.patch("/resources/{resource_id}")
    def update_resource(
        resource_id: str, body: ResourceUpdate, request: Request
    ) -> dict[str, Any]:
        db = _db(request)
        _require(db, "resources", resource_id)
        changes = body.model_dump(exclude_unset=True)
        for key in ("capacity", "metadata"):
            if key in changes:
                changes[f"{key}_json"] = json.dumps(changes.pop(key), ensure_ascii=False)
        if "enabled" in changes:
            changes["enabled"] = int(changes["enabled"])
        if changes:
            changes["updated_at"] = utc_now()
            db.execute(
                f"UPDATE resources SET {','.join(f'{key}=?' for key in changes)} WHERE id=?",
                [*changes.values(), resource_id],
            )
        return decode_row(_require(db, "resources", resource_id)) or {}

    @router.get("/settings")
    def settings(request: Request) -> dict[str, Any]:
        rows = _db(request).fetch_all("SELECT * FROM app_settings ORDER BY key")
        values = {}
        for row in rows:
            try:
                values[row["key"]] = json.loads(row["value_json"])
            except json.JSONDecodeError:
                values[row["key"]] = None
        return {"values": values, "updated_at": max((r["updated_at"] for r in rows), default=None)}

    @router.patch("/settings")
    def patch_settings(body: SettingsPatch, request: Request) -> dict[str, Any]:
        db = _db(request)
        with db.transaction() as connection:
            for key, value in body.values.items():
                if len(key) > 100:
                    raise HTTPException(422, "Setting keys may not exceed 100 characters")
                connection.execute(
                    "INSERT INTO app_settings(key,value_json,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                    (key, json.dumps(value, ensure_ascii=False), utc_now()),
                )
        db.append_event("settings", "settings.updated", payload={"keys": sorted(body.values)})
        return settings(request)

    @router.get("/reminders")
    def reminders(
        request: Request,
        state: str | None = None,
        from_time: str | None = None,
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, Any]:
        where = ["1=1"]
        params: list[Any] = []
        if state:
            where.append("r.state=?")
            params.append(state)
        if from_time:
            where.append("r.trigger_at>=?")
            params.append(from_time)
        rows = _db(request).fetch_all(
            "SELECT r.*,v.venue_key,v.display_name AS venue_name,d.deadline_date "
            "FROM reminders r LEFT JOIN venues v ON v.id=r.venue_id "
            "LEFT JOIN deadlines d ON d.id=r.deadline_id "
            f"WHERE {' AND '.join(where)} ORDER BY r.trigger_at LIMIT ?",
            [*params, limit],
        )
        return {"items": decode_rows(rows), "total": len(rows)}

    @router.get("/pipeline")
    def pipeline(
        request: Request,
        venue_key: str | None = None,
        deadline_id: int | None = None,
    ) -> dict[str, Any]:
        db = _db(request)
        if deadline_id is not None:
            deadline = decode_row(_require(db, "deadlines", deadline_id)) or {}
            venue = decode_row(_require(db, "venues", deadline["venue_id"])) or {}
        elif venue_key:
            venue = decode_row(db.fetch_one("SELECT * FROM venues WHERE venue_key=?", (venue_key,)))
            if not venue:
                raise HTTPException(404, f"Venue not found: {venue_key}")
            deadline = decode_row(db.fetch_one(
                "SELECT * FROM deadlines WHERE venue_id=? ORDER BY deadline_date LIMIT 1",
                (venue["id"],),
            ))
            if not deadline:
                return {
                    "venue_key": venue_key,
                    "rolling_or_tba": True,
                    "detail": "This venue has no fixed deadline; choose an internal milestone before scheduling.",
                    "auto_submission": False,
                    "stages": [],
                }
        else:
            raise HTTPException(422, "venue_key or deadline_id is required")
        return {"venue_key": venue["venue_key"], **build_pipeline(deadline)}

    @router.get("/calendar.ics")
    def calendar_feed(request: Request) -> Response:
        db = _db(request)
        deadlines = db.fetch_all(
            """SELECT d.*,v.venue_key,v.display_name FROM deadlines d
               JOIN venues v ON v.id=d.venue_id ORDER BY d.deadline_date""")
        reminders = db.fetch_all(
            "SELECT r.*,v.display_name FROM reminders r "
            "LEFT JOIN venues v ON v.id=r.venue_id ORDER BY r.trigger_at"
        )
        payload = build_ical_calendar(deadlines, reminders)
        return Response(
            content=payload,
            media_type="text/calendar; charset=utf-8",
            headers={"Content-Disposition": "inline; filename=argus-research.ics"},
        )

    @router.post("/reminders", status_code=201)
    def create_reminder(body: ReminderCreate, request: Request) -> dict[str, Any]:
        db = _db(request)
        try:
            trigger = datetime.fromisoformat(body.trigger_at)
        except ValueError as exc:
            raise HTTPException(422, "trigger_at must be an ISO-8601 datetime") from exc
        if trigger.tzinfo is None:
            raise HTTPException(422, "trigger_at must include an explicit UTC offset")
        trigger_at = trigger.astimezone(UTC).isoformat()
        venue_id = None
        if body.venue_key:
            venue = db.fetch_one("SELECT id FROM venues WHERE venue_key=?", (body.venue_key,))
            if not venue:
                raise HTTPException(404, f"Venue not found: {body.venue_key}")
            venue_id = venue["id"]
        if body.deadline_id:
            _require(db, "deadlines", body.deadline_id)
        if body.campaign_id:
            _require(db, "campaigns", body.campaign_id)
        reminder_id = str(uuid.uuid4())
        now = utc_now()
        db.execute(
            "INSERT INTO reminders(id,venue_id,deadline_id,campaign_id,trigger_at,title,payload_json,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                reminder_id, venue_id, body.deadline_id, body.campaign_id,
                trigger_at, body.title, json.dumps(body.payload, ensure_ascii=False), now, now,
            ),
        )
        return decode_row(db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))) or {}

    @router.post("/reminders/{reminder_id}/ack")
    def acknowledge_reminder(reminder_id: str, request: Request) -> dict[str, Any]:
        db = _db(request)
        row = db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))
        if not row:
            raise HTTPException(404, f"Reminder not found: {reminder_id}")
        db.execute(
            "UPDATE reminders SET state='acknowledged',updated_at=? WHERE id=?",
            (utc_now(), reminder_id),
        )
        return decode_row(db.fetch_one("SELECT * FROM reminders WHERE id=?", (reminder_id,))) or {}

    @router.get("/events")
    def events(
        request: Request,
        after_id: int = Query(0, ge=0),
        topic: str | None = None,
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, Any]:
        db = _db(request)
        if topic:
            rows = db.fetch_all(
                "SELECT * FROM events WHERE id>? AND topic=? ORDER BY id LIMIT ?",
                (after_id, topic, limit),
            )
        else:
            rows = db.fetch_all(
                "SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after_id, limit)
            )
        items = decode_rows(rows)
        return {"items": items, "cursor": items[-1]["id"] if items else after_id}

    @router.post("/sources/sync")
    def sync_sources(request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
        db = _db(request)
        try:
            counts = seed_database(db, request.app.state.settings.seed_data_dir)
        except (FileNotFoundError, json.JSONDecodeError) as exc:
            raise HTTPException(503, f"Local source sync failed: {exc}") from exc
        db.append_event(
            "sources", "sources.local_sync_completed", entity_type="source",
            entity_id="bundled_seed_snapshot", payload=counts,
        )
        external = None
        idea_delta = None
        if body and body.get("requests"):
            external = sync_external_sources(
                body["requests"],
                cache_dir=request.app.state.settings.data_dir / "source-cache",
                github_token=os.getenv(request.app.state.settings.github_token_env),
            )
            if body.get("idea_id") is not None:
                idea = decode_row(_require(db, "ideas", int(body["idea_id"]))) or {}
                updates = [
                    update for update in external.get("updates", [])
                    if isinstance(update, dict)
                ]
                source_items = [
                    item
                    for update in updates
                    for item in (update.get("items") or [])
                    if isinstance(item, dict)
                ]
                observed_changes: dict[str, set[str]] = {
                    "added": set(), "removed": set(), "changed": set(),
                }
                source_deltas: list[dict[str, Any]] = []
                for update in updates:
                    delta: dict[str, list[str]] = {}
                    for result_key, update_key in (
                        ("added", "added_ids"),
                        ("removed", "removed_ids"),
                        ("changed", "changed_ids"),
                    ):
                        raw_ids = update.get(update_key)
                        ids = sorted({
                            str(item_id)
                            for item_id in raw_ids
                            if str(item_id)
                        }) if isinstance(raw_ids, (list, tuple, set)) else []
                        delta[result_key] = ids
                        observed_changes[result_key].update(ids)
                    source_deltas.append({
                        "source": str(update.get("source") or ""),
                        "query": str(update.get("query") or ""),
                        "status": str(update.get("status") or ""),
                        "added": delta["added"],
                        "removed": delta["removed"],
                        "changed": delta["changed"],
                        "difference_summary": str(update.get("difference_summary") or ""),
                    })
                idea_delta = differentiate_idea(
                    idea,
                    source_items,
                    observed_changes={
                        key: tuple(sorted(values))
                        for key, values in observed_changes.items()
                    },
                ).to_dict()
                idea_delta["source_deltas"] = source_deltas
                freshness = (
                    "collision_review_required"
                    if idea_delta["novelty_risk"] == "high_collision_risk"
                    else "refreshed"
                )
                db.execute(
                    "UPDATE ideas SET differentiation=?,freshness_state=?,updated_at=? WHERE id=?",
                    (json.dumps(idea_delta, ensure_ascii=False), freshness, utc_now(), idea["id"]),
                )
                db.append_event(
                    "sources",
                    "idea.differentiation_refreshed",
                    severity="attention" if freshness == "collision_review_required" else "info",
                    entity_type="idea",
                    entity_id=str(idea["id"]),
                    payload={
                        "novelty_risk": idea_delta["novelty_risk"],
                        "source_item_count": len(source_items),
                        "changed_since_snapshot": idea_delta["changed_since_snapshot"],
                        "change_basis": idea_delta["change_basis"],
                        "source_deltas": source_deltas,
                        "heuristic_only": True,
                    },
                )
        return {
            "ok": True,
            "local_seed": counts,
            "external": external,
            "idea_delta": idea_delta,
            "connectors": ["arxiv", "openreview", "github"],
        }

    @router.get("/releases")
    def releases(request: Request) -> dict[str, Any]:
        registry_path = request.app.state.settings.data_dir / "releases" / "registry.json"
        staging_path = request.app.state.settings.data_dir / "releases" / "staging"
        registry: dict[str, Any] = {}
        if registry_path.is_file():
            try:
                value = json.loads(registry_path.read_text(encoding="utf-8"))
                if isinstance(value, dict):
                    registry = value
            except (OSError, json.JSONDecodeError):
                registry = {"status": "invalid_registry"}
        staged: list[dict[str, Any]] = []
        if staging_path.is_dir() and not staging_path.is_symlink():
            for child in sorted(staging_path.iterdir(), key=lambda item: item.name):
                if not child.is_dir() or child.is_symlink() or len(child.name) not in {40, 64}:
                    continue
                manifest_path = child / "manifest.json"
                try:
                    value = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict) and value.get("sha") == child.name:
                    staged.append(value)
        return {
            "registry": registry,
            "staged": staged,
            "policy": {
                "remote_check": "read_only_git_ls_remote",
                "staging": "explicit_confirmation_and_matching_full_sha_required",
                "stage_only_default": True,
                "tests": "not_run_until_separately_requested",
                "canary": "required_before_adoption",
                "adoption": "human_approval_for_new_campaigns_only",
                "running_campaigns": "never_mutated",
            },
        }

    @router.post("/releases/inspect")
    def inspect_argus_release(request: Request, body: dict[str, Any]) -> dict[str, Any]:
        repository = str(body.get("repository") or "https://github.com/microsoft/ArgusAgent.git")
        ref = str(body.get("ref") or "refs/heads/main")
        registry_path = request.app.state.settings.data_dir / "releases" / "registry.json"
        try:
            result = inspect_release(
                repository,
                ref=ref,
                reported_release=body.get("reported_release") or {},
                release_registry=registry_path,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        try:
            registry = record_release_inspection(result, release_registry=registry_path)
        except ReleaseRegistryError as exc:
            _db(request).append_event(
                "releases",
                "release.registry_write_failed",
                severity="error" if exc.http_status >= 500 else "attention",
                entity_type="release",
                entity_id=str(result.get("remote_sha") or ref),
                payload={
                    "repository": repository,
                    "ref": ref,
                    "inspection_status": result.get("status"),
                    "error_code": exc.code,
                    "existing_checkout_mutated": False,
                },
            )
            raise HTTPException(
                exc.http_status,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        _db(request).append_event(
            "releases",
            "release.remote_inspected",
            entity_type="release",
            entity_id=str(result.get("remote_sha") or ref),
            payload={
                "repository": repository,
                "status": result.get("status"),
                "candidate_available": result.get("candidate_available"),
                "read_only": True,
                "registry_persisted": True,
            },
        )
        return {**result, "registry_persisted": True, "registry": registry}

    @router.post("/releases/stage")
    def stage_argus_release(request: Request, body: ReleaseStageRequest) -> dict[str, Any]:
        """Stage a verified SHA in Flywheel data; never adopt or execute it."""
        try:
            result = stage_release(
                body.repository,
                ref=body.ref,
                expected_sha=body.expected_sha,
                confirm_isolated_stage=body.confirm_isolated_stage,
                data_dir=request.app.state.settings.data_dir,
                timeout=request.app.state.settings.release_git_timeout_seconds,
            )
        except ReleaseStageError as exc:
            _db(request).append_event(
                "releases",
                "release.stage_failed" if exc.http_status >= 500 else "release.stage_rejected",
                severity="error" if exc.http_status >= 500 else "attention",
                entity_type="release",
                entity_id=body.expected_sha.lower(),
                payload={
                    "repository": body.repository,
                    "ref": body.ref,
                    "error_code": exc.code,
                    "attempt_id": exc.attempt_id,
                    "adopted": False,
                    "daemon_started": False,
                },
            )
            raise HTTPException(
                exc.http_status,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "attempt_id": exc.attempt_id,
                },
            ) from exc
        _db(request).append_event(
            "releases",
            "release.stage_reused" if result.get("reused") else "release.staged",
            entity_type="release",
            entity_id=str(result["sha"]),
            payload={
                "repository": result["repository"],
                "ref": result["ref"],
                "sha": result["sha"],
                "attempt_id": result["attempt_id"],
                "reused": result["reused"],
                "adopted": False,
                "daemon_started": False,
            },
        )
        return result

    @router.websocket("/ws")
    async def event_websocket(websocket: WebSocket) -> None:
        # Browser WebSockets are not covered by CORSMiddleware. A hostile page can
        # otherwise connect to localhost and read the event ledger (CSWSH). Non-browser
        # clients may omit Origin; any supplied browser Origin must match the explicit
        # HTTP CORS allow-list after canonicalization. Wildcards are intentionally not
        # honored for this event-bearing endpoint.
        supplied_origin = websocket.headers.get("origin")
        if supplied_origin is not None:
            allowed_origins = {
                origin
                for value in websocket.app.state.settings.cors_origins
                if (origin := _canonical_websocket_origin(value)) is not None
            }
            canonical_origin = _canonical_websocket_origin(supplied_origin)
            if canonical_origin is None or canonical_origin not in allowed_origins:
                await websocket.close(code=1008, reason="WebSocket origin is not allowed")
                return
        await websocket.accept()
        db: Database = websocket.app.state.db
        try:
            cursor = max(0, int(websocket.query_params.get("after_id", "0")))
        except ValueError:
            cursor = 0
        try:
            await websocket.send_json({"type": "ready", "cursor": cursor, "server_time": utc_now()})
            heartbeat = 0
            while True:
                rows = decode_rows(
                    db.fetch_all("SELECT * FROM events WHERE id>? ORDER BY id LIMIT 200", (cursor,))
                )
                for row in rows:
                    cursor = row["id"]
                    await websocket.send_json({"type": "event", "event": row, "cursor": cursor})
                heartbeat += 1
                if heartbeat % 15 == 0:
                    await websocket.send_json({"type": "heartbeat", "cursor": cursor, "server_time": utc_now()})
                await asyncio.sleep(1)
        except WebSocketDisconnect:
            return

    return router
