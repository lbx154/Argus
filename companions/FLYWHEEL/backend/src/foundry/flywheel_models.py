from __future__ import annotations

import base64
import binascii
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

MAX_PDF_BYTES = 10 * 1024 * 1024
_MAX_PDF_BASE64_CHARS = ((MAX_PDF_BYTES + 2) // 3) * 4
_OPENREVIEW_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,199}$")


class EntityLinkInput(BaseModel):
    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=500)
    relation: str = Field(default="related", min_length=1, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EpisodeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    objective: str = Field(default="", max_length=200_000)
    team_profile_id: str | None = None
    venue_id: int | None = None
    deadline_id: int | None = None
    ideation_run_id: str | None = None
    candidate_id: str | None = None
    campaign_id: str | None = None
    training_consent: bool = False
    license_basis: str = Field(default="", max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    links: list[EntityLinkInput] = Field(default_factory=list, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class EpisodeSealRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=10_000)
    terminal_state: Literal[
        "active", "paused", "submitted", "rebuttal", "decided", "closed",
        "NO_WINNER", "NOVELTY_COLLISION", "RESOURCE_INFEASIBLE", "NEGATIVE_RESULT",
        "INCONCLUSIVE", "KILLED", "DEFERRED", "POLICY_BLOCKED",
        "SUBMISSION_READY_FOR_HUMAN_REVIEW", "ACCEPTED", "REJECTED", "WITHDRAWN",
    ] | None = None

    @field_validator("actor", "reason")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class TeamIntakeExtractRequest(BaseModel):
    raw_text: str = Field(min_length=1, max_length=200_000)

    @field_validator("raw_text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("raw_text must not be blank")
        return value


class TeamIntakeConfirmRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=500)
    profile: dict[str, Any]
    name: str | None = Field(default=None, max_length=200)
    training_consent: bool = False
    license_basis: str = Field(default="", max_length=2_000)

    @field_validator("actor")
    @classmethod
    def clean_actor(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("actor must not be blank")
        return value


class PdfReviewPayload(BaseModel):
    """Transport representation for a PDF while it is awaiting human confirmation."""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    mime_type: str
    content_base64: str = Field(min_length=1, max_length=_MAX_PDF_BASE64_CHARS)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        cleaned = value.strip()
        if (
            cleaned != value
            or cleaned in {".", ".."}
            or "/" in cleaned
            or "\\" in cleaned
            or "\x00" in cleaned
            or any(ord(character) < 32 for character in cleaned)
            or not cleaned.lower().endswith(".pdf")
        ):
            raise ValueError("filename must be a plain .pdf basename")
        return cleaned

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, value: str) -> str:
        if value.strip().lower() != "application/pdf":
            raise ValueError("mime_type must be application/pdf")
        return "application/pdf"

    @field_validator("content_base64")
    @classmethod
    def validate_pdf_bytes(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 must be strict RFC 4648 base64") from exc
        if len(decoded) > MAX_PDF_BYTES:
            raise ValueError("PDF exceeds the 10 MiB limit")
        if not decoded.startswith(b"%PDF-"):
            raise ValueError("decoded content does not have a PDF magic header")
        return value

    def decoded_bytes(self) -> bytes:
        # Validation has already established strict base64 and the byte-size/magic gates.
        return base64.b64decode(self.content_base64, validate=True)


class OpenReviewFetchRequest(BaseModel):
    """Credential-free request for one public OpenReview forum/note thread."""

    model_config = ConfigDict(extra="forbid")

    forum_id: str = Field(min_length=1, max_length=200)

    @field_validator("forum_id")
    @classmethod
    def validate_forum_id(cls, value: str) -> str:
        cleaned = value.strip()
        if cleaned != value or not _OPENREVIEW_ID.fullmatch(cleaned):
            raise ValueError("forum_id must be a plain OpenReview forum/note identifier")
        return cleaned


class ReviewImportCreate(BaseModel):
    source_kind: Literal["paste", "json", "pdf", "openreview"]
    raw_text: str | None = Field(default=None, max_length=2_000_000)
    payload: Any | None = None
    source_ref: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def exactly_one_payload(self) -> "ReviewImportCreate":
        if (self.raw_text is None) == (self.payload is None):
            raise ValueError("provide exactly one of raw_text or payload")
        if self.raw_text is not None and not self.raw_text.strip():
            raise ValueError("raw_text must not be blank")
        if self.source_kind == "paste" and self.raw_text is None:
            raise ValueError("paste imports require raw_text")
        if self.source_kind == "json" and self.payload is None:
            raise ValueError("json imports require payload")
        if self.source_kind == "pdf":
            if self.payload is None:
                raise ValueError("pdf imports require payload")
            PdfReviewPayload.model_validate(self.payload)
        if self.source_kind == "openreview":
            raise ValueError("use the dedicated OpenReview public fetch endpoint")
        return self


class ReviewImportConfirm(BaseModel):
    actor: str = Field(min_length=1, max_length=500)
    parsed: dict[str, Any] | list[Any] | None = None
    redaction_confirmed: StrictBool
    training_consent: bool = False
    license_basis: str = Field(min_length=1, max_length=2_000)

    @field_validator("actor", "license_basis")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("redaction_confirmed")
    @classmethod
    def require_redaction_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("redaction_confirmed must be true")
        return value


class ReviewImportDiscard(BaseModel):
    actor: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=10_000)

    @field_validator("actor", "reason")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value


class DatasetSelection(BaseModel):
    episode_ids: list[str] = Field(default_factory=list, max_length=10_000)
    require_training_consent: bool = True


class DatasetSnapshotCreate(DatasetSelection):
    name: str = Field(min_length=1, max_length=500)
    actor: str = Field(min_length=1, max_length=500)
    license_basis: str = Field(min_length=1, max_length=2_000)
    expected_selection_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("name", "actor", "license_basis")
    @classmethod
    def clean_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value
