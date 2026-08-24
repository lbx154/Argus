from __future__ import annotations

import json

from .db import Database, utc_now

MANAGED_ARGUS_CONNECTION_ID = "argus-managed-local"


def ensure_managed_argus_connection(
    database: Database,
    *,
    base_url: str,
    token_env: str,
) -> str | None:
    """Register the Argus control plane used by this companion.

    This is deliberately a reference-only pairing: Flywheel stores the name of
    Argus' WebAPI token environment variable, never the bearer value and never
    an LLM provider credential.  Provider/model selection remains entirely in
    Argus and is inherited by every session Flywheel launches there.
    """

    normalized_url = str(base_url or "").strip().rstrip("/")
    if not normalized_url:
        return None
    normalized_token_env = str(token_env or "ARGUS_SKILL_WEB_TOKEN").strip()
    token_ref = f"env:{normalized_token_env}" if normalized_token_env else None
    now = utc_now()
    metadata = {
        "managed_by": "argus-flywheel",
        "configuration_contract": "argus_control_plane_delegation/v1",
        "provider_configuration": "delegated_to_argus",
        "model_configuration": "delegated_to_argus",
        "llm_credentials_copied": False,
    }
    existing = database.fetch_one(
        "SELECT * FROM connections WHERE id=?", (MANAGED_ARGUS_CONNECTION_ID,)
    )
    if existing is None:
        database.execute(
            "INSERT INTO connections("
            "id,name,kind,base_url,token_ref,enabled,status,last_error,metadata_json,created_at,updated_at"
            ") VALUES(?,?,?,?,?,1,'unknown',?,?,?,?)",
            (
                MANAGED_ARGUS_CONNECTION_ID,
                "Argus · managed companion",
                "local",
                normalized_url,
                token_ref,
                "Awaiting compatibility and provider-readiness probe",
                json.dumps(metadata, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        database.append_event(
            "connections",
            "connection.managed_registered",
            entity_type="connection",
            entity_id=MANAGED_ARGUS_CONNECTION_ID,
            payload={
                "base_url": normalized_url,
                "token_source": "environment" if token_ref else None,
                "provider_configuration": "delegated_to_argus",
            },
        )
        return MANAGED_ARGUS_CONNECTION_ID

    identity_changed = (
        existing.get("base_url") != normalized_url
        or existing.get("token_ref") != token_ref
    )
    if identity_changed:
        active = database.fetch_one(
            "SELECT COUNT(*) AS n FROM campaigns WHERE connection_id=? "
            "AND execution_state IN ('starting','running','draining')",
            (MANAGED_ARGUS_CONNECTION_ID,),
        )
        if active and int(active.get("n") or 0) > 0:
            database.append_event(
                "connections",
                "connection.managed_update_blocked",
                severity="warning",
                entity_type="connection",
                entity_id=MANAGED_ARGUS_CONNECTION_ID,
                payload={"reason": "active_campaign_freezes_argus_identity"},
            )
            return MANAGED_ARGUS_CONNECTION_ID

    try:
        existing_metadata = json.loads(existing.get("metadata_json") or "{}")
    except (TypeError, json.JSONDecodeError):
        existing_metadata = {}
    if not isinstance(existing_metadata, dict):
        existing_metadata = {}
    persisted_metadata = {**existing_metadata, **metadata}
    database.execute(
        "UPDATE connections SET name=?,kind='local',base_url=?,token_ref=?,enabled=1,"
        "status=CASE WHEN base_url<>? OR COALESCE(token_ref,'')<>COALESCE(?,'') "
        "THEN 'unknown' ELSE status END,"
        "last_checked_at=CASE WHEN base_url<>? OR COALESCE(token_ref,'')<>COALESCE(?,'') "
        "THEN NULL ELSE last_checked_at END,"
        "last_error=CASE WHEN base_url<>? OR COALESCE(token_ref,'')<>COALESCE(?,'') "
        "THEN 'Managed Argus identity changed; probe required' ELSE last_error END,"
        "metadata_json=?,updated_at=? WHERE id=?",
        (
            "Argus · managed companion",
            normalized_url,
            token_ref,
            normalized_url,
            token_ref,
            normalized_url,
            token_ref,
            normalized_url,
            token_ref,
            json.dumps(persisted_metadata, ensure_ascii=False, sort_keys=True),
            now,
            MANAGED_ARGUS_CONNECTION_ID,
        ),
    )
    return MANAGED_ARGUS_CONNECTION_ID


__all__ = ["MANAGED_ARGUS_CONNECTION_ID", "ensure_managed_argus_connection"]
