from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request, Response

from .argus_artifact_models import (
    ArgusArtifactConfirmRequest,
    ArgusArtifactDiscardRequest,
    ArgusArtifactStageRequest,
)
from .integrations.argus_webapi import ArgusWebApiClient, ArgusWebApiError
from .services.argus_artifact_ingest import (
    ArgusArtifactImportService,
    artifact_limits,
)
from .services.flywheel_data import FlywheelDataError, assert_no_secrets


def _service(request: Request) -> ArgusArtifactImportService:
    data_dir = request.app.state.settings.data_dir
    return ArgusArtifactImportService(
        request.app.state.db,
        staging_root=data_dir / "staging" / "argus-artifacts",
        object_root=data_dir / "data-vault" / "objects",
    )


def _client(request: Request, connection: dict) -> ArgusWebApiClient:
    token = request.app.state.secrets.resolve(
        connection["id"],
        connection.get("token_ref"),
        endpoint=connection.get("base_url"),
    )
    return ArgusWebApiClient(connection["base_url"], token=token)


def _expected_error(exc: FlywheelDataError) -> HTTPException:
    detail = str(exc)
    lowered = detail.lower()
    if "not found" in lowered:
        return HTTPException(404, detail)
    if "exceed" in lowered or "byte budget" in lowered or "staging limit" in lowered:
        return HTTPException(413, detail)
    return HTTPException(409, detail)


def _reject_secrets(value: object) -> None:
    try:
        assert_no_secrets(value)
    except FlywheelDataError as exc:
        raise HTTPException(422, str(exc)) from exc


def create_argus_artifact_router() -> APIRouter:
    router = APIRouter(prefix="/api", tags=["Argus artifact intake"])

    @router.get("/episodes/{episode_id}/argus-artifacts")
    async def list_remote_argus_artifacts(
        episode_id: str, request: Request
    ) -> dict:
        service = _service(request)
        try:
            binding = service.resolve_binding(episode_id)
            items = await asyncio.to_thread(
                service.remote_index, binding, _client(request, binding.connection)
            )
        except FlywheelDataError as exc:
            raise _expected_error(exc) from exc
        except (ArgusWebApiError, ValueError) as exc:
            raise HTTPException(502, f"Argus artifact index is unavailable: {exc}") from exc
        return {
            "episode_id": episode_id,
            "campaign_id": binding.campaign["id"],
            "argus_project_id": binding.campaign["argus_project_id"],
            "items": items,
            "limits": artifact_limits(),
        }

    @router.get("/episodes/{episode_id}/argus-artifact-imports")
    def list_argus_artifact_imports(episode_id: str, request: Request) -> dict:
        try:
            return {"items": _service(request).list_imports(episode_id)}
        except FlywheelDataError as exc:
            raise _expected_error(exc) from exc

    @router.post("/episodes/{episode_id}/argus-artifact-imports", status_code=201)
    async def stage_argus_artifact(
        episode_id: str,
        body: ArgusArtifactStageRequest,
        request: Request,
        response: Response,
    ) -> dict:
        _reject_secrets(body.model_dump())
        service = _service(request)
        try:
            binding = service.resolve_binding(episode_id)
            result, idempotent = await asyncio.to_thread(
                service.stage,
                episode_id,
                artifact_path=body.artifact_path,
                role=body.role,
                expected_entry_sha256=body.expected_entry_sha256,
                idempotency_key=body.idempotency_key,
                client=_client(request, binding.connection),
            )
        except FlywheelDataError as exc:
            raise _expected_error(exc) from exc
        except (ArgusWebApiError, ValueError) as exc:
            raise HTTPException(502, f"Argus artifact download failed: {exc}") from exc
        if idempotent:
            response.status_code = 200
        result["idempotent"] = idempotent
        result["needs_human_confirmation"] = result.get("state") == "draft"
        return result

    @router.get("/argus-artifact-imports/{import_id}")
    def argus_artifact_import_detail(import_id: str, request: Request) -> dict:
        try:
            return _service(request).import_detail(import_id)
        except FlywheelDataError as exc:
            raise _expected_error(exc) from exc

    @router.post("/argus-artifact-imports/{import_id}/confirm")
    def confirm_argus_artifact_import(
        import_id: str, body: ArgusArtifactConfirmRequest, request: Request
    ) -> dict:
        _reject_secrets(body.model_dump())
        try:
            return _service(request).confirm(
                import_id,
                actor=body.actor,
                expected_source_sha256=body.expected_source_sha256,
                redaction_confirmed=body.redaction_confirmed,
                manual_redaction_confirmed=body.manual_redaction_confirmed,
                training_consent=body.training_consent,
                license_basis=body.license_basis,
                disposition=body.disposition,
                replacement_text=body.replacement_text,
            )
        except FlywheelDataError as exc:
            raise _expected_error(exc) from exc

    @router.post("/argus-artifact-imports/{import_id}/discard")
    def discard_argus_artifact_import(
        import_id: str, body: ArgusArtifactDiscardRequest, request: Request
    ) -> dict:
        _reject_secrets(body.model_dump())
        try:
            return _service(request).discard(
                import_id, actor=body.actor, reason=body.reason
            )
        except FlywheelDataError as exc:
            raise _expected_error(exc) from exc

    return router
