"""Immutable provenance receipts for conditioned candidate campaigns."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

BINDING_SCHEMA = "flywheel.conditioned-campaign-binding/1"
_DIGEST_FIELDS = (
    "condition_sha256",
    "parent_objective_sha256",
    "candidate_artifact_sha256",
    "candidate_record_sha256",
    "candidate_input_sha256",
    "candidate_prompt_sha256",
)
_CORE_FIELDS = (
    "schema_version",
    "campaign_id",
    "ideation_run_id",
    "candidate_id",
    *_DIGEST_FIELDS,
    "objective_path",
)


class CampaignBindingError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def build_campaign_binding(
    *,
    campaign_id: str,
    ideation_run_id: str,
    candidate_id: str,
    condition_sha256: str,
    parent_objective_sha256: str,
    candidate_artifact_sha256: str,
    candidate_record_sha256: str,
    candidate_input_sha256: str,
    candidate_prompt_sha256: str,
    objective_path: str,
) -> dict[str, str]:
    core = {
        "schema_version": BINDING_SCHEMA,
        "campaign_id": _required_text(campaign_id, "campaign_id"),
        "ideation_run_id": _required_text(ideation_run_id, "ideation_run_id"),
        "candidate_id": _required_text(candidate_id, "candidate_id"),
        "condition_sha256": condition_sha256.lower(),
        "parent_objective_sha256": parent_objective_sha256.lower(),
        "candidate_artifact_sha256": candidate_artifact_sha256.lower(),
        "candidate_record_sha256": candidate_record_sha256.lower(),
        "candidate_input_sha256": candidate_input_sha256.lower(),
        "candidate_prompt_sha256": candidate_prompt_sha256.lower(),
        "objective_path": _required_text(objective_path, "objective_path"),
    }
    for field in _DIGEST_FIELDS:
        _required_sha256(core[field], field)
    receipt_sha256 = hashlib.sha256(canonical_bytes(core)).hexdigest()
    return {**core, "receipt_sha256": receipt_sha256}


def validate_campaign_binding(value: Mapping[str, Any]) -> dict[str, str]:
    try:
        rebuilt = build_campaign_binding(
            **{field: str(value.get(field) or "") for field in _CORE_FIELDS if field != "schema_version"}
        )
    except (TypeError, CampaignBindingError) as exc:
        raise CampaignBindingError(f"invalid conditioned campaign binding: {exc}") from exc
    if value.get("schema_version") != BINDING_SCHEMA:
        raise CampaignBindingError("conditioned campaign binding schema mismatch")
    if str(value.get("receipt_sha256") or "").lower() != rebuilt["receipt_sha256"]:
        raise CampaignBindingError("conditioned campaign binding receipt digest mismatch")
    return rebuilt


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignBindingError(f"{field} must be a nonblank string")
    return value.strip()


def _required_sha256(value: Any, field: str) -> str:
    text = _required_text(value, field).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise CampaignBindingError(f"{field} must be a SHA-256 digest")
    return text


__all__ = [
    "BINDING_SCHEMA",
    "CampaignBindingError",
    "build_campaign_binding",
    "canonical_bytes",
    "validate_campaign_binding",
]
