"""Immutable provenance receipts for conditioned candidate campaigns."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .flywheel_data import new_key

BINDING_SCHEMA = "flywheel.conditioned-campaign-binding/1"
_KEY_FIELDS = (
    "condition_key",
    "parent_objective_key",
    "candidate_artifact_key",
    "candidate_record_key",
    "candidate_input_key",
    "candidate_prompt_key",
)
_CORE_FIELDS = (
    "schema_version",
    "campaign_id",
    "ideation_run_id",
    "candidate_id",
    *_KEY_FIELDS,
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
    condition_key: str,
    parent_objective_key: str,
    candidate_artifact_key: str,
    candidate_record_key: str,
    candidate_input_key: str,
    candidate_prompt_key: str,
    objective_path: str,
) -> dict[str, str]:
    core = {
        "schema_version": BINDING_SCHEMA,
        "campaign_id": _required_text(campaign_id, "campaign_id"),
        "ideation_run_id": _required_text(ideation_run_id, "ideation_run_id"),
        "candidate_id": _required_text(candidate_id, "candidate_id"),
        "condition_key": condition_key.lower(),
        "parent_objective_key": parent_objective_key.lower(),
        "candidate_artifact_key": candidate_artifact_key.lower(),
        "candidate_record_key": candidate_record_key.lower(),
        "candidate_input_key": candidate_input_key.lower(),
        "candidate_prompt_key": candidate_prompt_key.lower(),
        "objective_path": _required_text(objective_path, "objective_path"),
    }
    for field in _KEY_FIELDS:
        _required_key(core[field], field)
    return {**core, "receipt_key": new_key()}


def validate_campaign_binding(value: Mapping[str, Any]) -> dict[str, str]:
    try:
        rebuilt = build_campaign_binding(
            **{field: str(value.get(field) or "") for field in _CORE_FIELDS if field != "schema_version"}
        )
    except (TypeError, CampaignBindingError) as exc:
        raise CampaignBindingError(f"invalid conditioned campaign binding: {exc}") from exc
    if value.get("schema_version") != BINDING_SCHEMA:
        raise CampaignBindingError("conditioned campaign binding schema mismatch")
    try:
        receipt_key = _required_key(value.get("receipt_key"), "receipt_key")
    except CampaignBindingError as exc:
        raise CampaignBindingError(f"invalid conditioned campaign binding: {exc}") from exc
    return {**rebuilt, "receipt_key": receipt_key}


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CampaignBindingError(f"{field} must be a nonblank string")
    return value.strip()


def _required_key(value: Any, field: str) -> str:
    text = _required_text(value, field).lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise CampaignBindingError(f"{field} must be a 64-hex key")
    return text


__all__ = [
    "BINDING_SCHEMA",
    "CampaignBindingError",
    "build_campaign_binding",
    "canonical_bytes",
    "validate_campaign_binding",
]
