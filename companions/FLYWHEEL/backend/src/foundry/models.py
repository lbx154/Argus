from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, StrictBool, StrictInt, field_validator


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal["local", "remote"] = "local"
    base_url: HttpUrl
    bearer_token: str | None = Field(default=None, max_length=4096)
    token_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def strip_url(cls, value: HttpUrl) -> HttpUrl:
        return value


class ConnectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: Literal["local", "remote"] | None = None
    base_url: HttpUrl | None = None
    bearer_token: str | None = Field(default=None, max_length=4096)
    token_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    clear_bearer_token: bool = False
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class ResourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    resource_type: str = Field(min_length=1, max_length=50)
    capacity: dict[str, Any] = Field(default_factory=dict)
    availability_state: str = "available"
    enabled: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceUpdate(BaseModel):
    name: str | None = None
    resource_type: str | None = None
    capacity: dict[str, Any] | None = None
    availability_state: str | None = None
    enabled: bool | None = None
    metadata: dict[str, Any] | None = None


class CampaignCreate(BaseModel):
    venue_key: str
    idea_id: int | None = None
    deadline_id: int | None = None
    connection_id: str | None = None
    resource_id: str | None = None
    title: str | None = Field(default=None, max_length=300)
    objective: str = Field(default="", max_length=200_000)
    scheduled_for: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class CampaignUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    objective: str | None = Field(default=None, max_length=200_000)
    connection_id: str | None = None
    resource_id: str | None = None
    scheduled_for: str | None = None
    schedule_state: str | None = None
    science_state: str | None = None
    integrity_state: str | None = None
    deadline_state: str | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    last_summary: str | None = Field(default=None, max_length=20_000)
    config: dict[str, Any] | None = None


class CampaignAction(BaseModel):
    reason: str = Field(default="", max_length=1000)
    force: bool = False


class CampaignStartRequest(BaseModel):
    """Explicit, attributable human authorization for one immutable launch receipt."""

    human_approved: StrictBool
    approval_reason: str = Field(min_length=1, max_length=10_000)
    actor: str = Field(min_length=1, max_length=500)

    @field_validator("approval_reason", "actor")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("human_approved")
    @classmethod
    def require_human_approval(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("human_approved must be true")
        return value


class LockedContractRequest(BaseModel):
    """Human-approved confirmatory contract; every field is frozen into an immutable packet."""

    primary_claim: str = Field(min_length=1, max_length=20_000)
    primary_metric: str = Field(min_length=1, max_length=2_000)
    minimum_effect: str = Field(min_length=1, max_length=2_000)
    data_split: str = Field(min_length=1, max_length=10_000)
    confirmatory_seeds: list[StrictInt] = Field(min_length=1, max_length=100)
    strongest_baselines: list[str] = Field(min_length=1, max_length=50)
    human_approved: StrictBool
    approval_reason: str = Field(min_length=1, max_length=10_000)

    @field_validator(
        "primary_claim",
        "primary_metric",
        "minimum_effect",
        "data_split",
        "approval_reason",
    )
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("confirmatory_seeds")
    @classmethod
    def unique_seeds(cls, value: list[int]) -> list[int]:
        if any(seed < 0 for seed in value):
            raise ValueError("confirmatory_seeds must be non-negative integers")
        if len(set(value)) != len(value):
            raise ValueError("confirmatory_seeds must not contain duplicates")
        return value

    @field_validator("strongest_baselines")
    @classmethod
    def clean_baselines(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value]
        if any(not item for item in cleaned):
            raise ValueError("strongest_baselines must not contain blank entries")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("strongest_baselines must not contain duplicates")
        return cleaned

    @field_validator("human_approved")
    @classmethod
    def require_human_approval(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("human_approved must be true")
        return value


class ApprovalDecision(BaseModel):
    decision: Literal["approve", "reject"]
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class ReviewRequest(BaseModel):
    reviewer_kind: str = Field(default="venue_reviewer", max_length=80)
    rubric: dict[str, Any] = Field(default_factory=dict)
    human_approved: StrictBool
    actor: str = Field(min_length=1, max_length=500)
    approval_reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("reviewer_kind", "actor", "approval_reason")
    @classmethod
    def clean_review_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean

    @field_validator("human_approved")
    @classmethod
    def require_review_approval(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("human_approved must be true")
        return value


class ReviewPanelRequest(BaseModel):
    reviewer_kinds: list[Literal[
        "novelty_reviewer",
        "methods_reviewer",
        "resource_reviewer",
        "venue_reviewer",
        "integrity_reviewer",
    ]] = Field(min_length=2, max_length=5)
    rubrics: dict[str, dict[str, Any]] = Field(default_factory=dict)
    human_approved: StrictBool
    actor: str = Field(min_length=1, max_length=500)
    approval_reason: str = Field(min_length=1, max_length=4_000)

    @field_validator("reviewer_kinds")
    @classmethod
    def unique_reviewers(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("reviewer_kinds must be unique")
        return value

    @field_validator("actor", "approval_reason")
    @classmethod
    def clean_panel_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean

    @field_validator("human_approved")
    @classmethod
    def require_panel_approval(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("human_approved must be true")
        return value


class ReminderCreate(BaseModel):
    venue_key: str | None = None
    deadline_id: int | None = None
    campaign_id: str | None = None
    trigger_at: str
    title: str = Field(min_length=1, max_length=300)
    payload: dict[str, Any] = Field(default_factory=dict)


class SettingsPatch(BaseModel):
    values: dict[str, Any]


class ReleaseStageRequest(BaseModel):
    repository: str = Field(min_length=1, max_length=2048)
    ref: str = Field(default="refs/heads/main", min_length=1, max_length=255)
    expected_sha: str = Field(pattern=r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
    confirm_isolated_stage: StrictBool

    @field_validator("confirm_isolated_stage")
    @classmethod
    def require_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("confirm_isolated_stage must be true")
        return value
