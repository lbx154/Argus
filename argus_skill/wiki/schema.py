"""Card dataclasses and frontmatter (de)serialization.

A card on disk is a markdown file with a YAML frontmatter block followed
by a free-form body:

    ---
    id: ...
    ...
    ---

    body...

This module is the single point that turns those bytes into typed objects
and back. Nothing else in the package should touch YAML directly.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date
from typing import Any, Literal, TypeVar

import yaml

CardType = Literal["technique", "conflict", "pattern"]
CardStatus = Literal["scratch", "candidate", "stable"]

_VALID_TYPES = {"technique", "conflict", "pattern"}
_VALID_STATUSES = {"scratch", "candidate", "stable"}
_VALID_OUTCOMES = {"success", "partial", "failure"}


@dataclass
class PageCard:
    id: str
    type: str
    status: str
    title: str
    tags: list[str]
    sources: list[str]
    related_runs: list[str]
    related_projects: list[str]
    revisit_after: date | None
    created_at: date
    last_reviewed_at: date
    reviewer_note: str
    body: str

    def __post_init__(self) -> None:
        if self.type not in _VALID_TYPES:
            raise ValueError(f"type must be one of {_VALID_TYPES}, got {self.type!r}")
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {_VALID_STATUSES}, got {self.status!r}")


@dataclass
class SourcePaper:
    id: str
    url: str
    title: str
    ingested_at: date
    ingested_by: str
    checksum: str
    body: str


@dataclass
class SourceRepo:
    id: str
    url: str
    title: str
    ingested_at: date
    ingested_by: str
    checksum: str
    body: str


@dataclass
class SourceRun:
    id: str
    mission_id: str
    git_commit: str
    project: str
    config_path: str
    dataset: str
    metrics: dict[str, float]
    artifacts: dict[str, str]
    outcome: str
    failure_signature: str
    suspected_cause: str
    next_action: str
    body: str
    closed_at: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in _VALID_OUTCOMES:
            raise ValueError(f"outcome must be one of {_VALID_OUTCOMES}, got {self.outcome!r}")


@dataclass
class SourceNote:
    id: str
    title: str
    mission_id: str
    created_at: date
    tags: list[str]
    body: str


T = TypeVar("T", PageCard, SourcePaper, SourceRepo, SourceRun, SourceNote)


def serialize_frontmatter(card: PageCard | SourcePaper | SourceRepo | SourceRun | SourceNote) -> str:
    data = asdict(card)
    body = data.pop("body", "") or ""
    # PyYAML serializes dates as ISO automatically; sort_keys=False keeps dataclass order.
    front = yaml.safe_dump(data, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body}\n"


def parse_frontmatter(text: str, cls: type[T]) -> T:
    if not text.startswith("---\n"):
        raise ValueError("expected frontmatter to start with '---\\n'")
    _, _, rest = text.partition("---\n")
    front_text, sep, body = rest.partition("\n---\n")
    if not sep:
        raise ValueError("expected closing frontmatter delimiter")
    data: dict[str, Any] = yaml.safe_load(front_text) or {}
    data["body"] = body.lstrip("\n").rstrip("\n")
    # Coerce ISO date strings back to date objects when a dumper/producer quoted them.
    for f in fields(cls):
        if "date" in str(f.type):
            val = data.get(f.name)
            if isinstance(val, str):
                data[f.name] = date.fromisoformat(val)
    # Back-compat: legacy cards may carry retired frontmatter keys (e.g. the
    # removed ``confidence`` field). Silently drop any key the dataclass no
    # longer declares so old entries still load instead of raising TypeError.
    known = {f.name for f in fields(cls)}
    data = {k: v for k, v in data.items() if k in known}
    return cls(**data)
