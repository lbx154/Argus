from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, StrictBool, field_validator, model_validator

ArgusArtifactRole = Literal[
    "condition_snapshot",
    "prompt_contract",
    "trajectory",
    "experiment_spec",
    "experiment_result",
    "paper",
    "outcome",
    "review_certificate",
    "integrity_report",
    "reproducibility_manifest",
]


class ArgusArtifactStageRequest(BaseModel):
    artifact_path: str = Field(min_length=1, max_length=4096)
    role: ArgusArtifactRole
    expected_entry_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("artifact_path")
    @classmethod
    def validate_remote_artifact_path(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("artifact_path must not contain surrounding whitespace")
        parts = value.split("/")
        if (
            value.startswith("/")
            or value.endswith("/")
            or "\\" in value
            or ":" in parts[0]
            or any(part in {"", ".", ".."} for part in parts)
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            raise ValueError("artifact_path must be a normalized Argus-relative path")
        return value

    @field_validator("idempotency_key")
    @classmethod
    def clean_idempotency_key(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("idempotency_key must not be blank")
        return cleaned


class ArgusArtifactConfirmRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=500)
    expected_source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    redaction_confirmed: StrictBool
    manual_redaction_confirmed: StrictBool = False
    training_consent: StrictBool = False
    license_basis: str = Field(min_length=1, max_length=2_000)
    disposition: Literal["as_is", "replace_text"] = "as_is"
    replacement_text: str | None = Field(default=None, max_length=2_000_000)

    @field_validator("actor", "license_basis")
    @classmethod
    def clean_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned

    @field_validator("redaction_confirmed")
    @classmethod
    def require_redaction_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("redaction_confirmed must be true")
        return value

    @model_validator(mode="after")
    def validate_disposition(self) -> "ArgusArtifactConfirmRequest":
        if self.disposition == "replace_text":
            if self.replacement_text is None or not self.replacement_text.strip():
                raise ValueError("replace_text requires non-blank replacement_text")
        elif self.replacement_text is not None:
            raise ValueError("replacement_text is only allowed with replace_text")
        return self


class ArgusArtifactDiscardRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=10_000)

    @field_validator("actor", "reason")
    @classmethod
    def clean_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned
