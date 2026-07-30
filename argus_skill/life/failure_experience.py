"""Compact, open-ended memory for learning from unsuccessful missions.

Failure experiences live in Argus project state, separate from project
artifacts. Records contain prose plus advisory facets and references; ordinary
retrieval never opens those references or walks the project worktree.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Iterator

fcntl: Any
try:  # pragma: no cover - platform-specific import
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

_TOKEN_RE = re.compile(r"[\w-]{2,}", re.UNICODE)
_DEFAULT_SCAN_BYTES = 1_000_000
_DEFAULT_SCAN_RECORDS = 256


def _clean_text(value: Any, *, limit: int = 4_000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _clean_list(values: Any, *, item_limit: int = 1_000) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    cleaned: list[str] = []
    for value in values:
        text = _clean_text(value, limit=item_limit)
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _tokens(*values: Any) -> set[str]:
    return {
        token.casefold()
        for value in values
        for token in _TOKEN_RE.findall(str(value or ""))
    }


@dataclass(frozen=True)
class FailureAnnotation:
    """A later, append-only interpretation of one experience."""

    id: str
    created_at: float
    text: str
    relation: str = ""
    evidence_refs: list[str] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        text: str,
        *,
        relation: str = "",
        evidence_refs: list[str] | None = None,
    ) -> "FailureAnnotation":
        return cls(
            id=uuid.uuid4().hex[:16],
            created_at=time.time(),
            text=_clean_text(text),
            relation=_clean_text(relation, limit=200),
            evidence_refs=_clean_list(evidence_refs or [], item_limit=500),
        )


@dataclass(frozen=True)
class FailureExperience:
    """One bounded capsule; prose is primary and facets are retrieval hints."""

    id: str
    created_at: float
    mission_id: str
    title: str
    objective: str
    status: str
    factual_outcome: str
    research_narrative: str = ""
    passed_assumptions: list[str] = field(default_factory=list)
    lessons: list[str] = field(default_factory=list)
    transfer_insights: list[str] = field(default_factory=list)
    claim_boundaries: list[str] = field(default_factory=list)
    retry_conditions: list[str] = field(default_factory=list)
    artifact_refs: list[str] = field(default_factory=list)
    concepts: list[str] = field(default_factory=list)
    causes: list[str] = field(default_factory=list)
    related_experience_ids: list[str] = field(default_factory=list)
    annotations: list[FailureAnnotation] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        mission_id: str,
        title: str,
        objective: str,
        status: str,
        factual_outcome: str,
        research_narrative: str = "",
        passed_assumptions: list[str] | None = None,
        lessons: list[str] | None = None,
        transfer_insights: list[str] | None = None,
        claim_boundaries: list[str] | None = None,
        retry_conditions: list[str] | None = None,
        artifact_refs: list[str] | None = None,
        concepts: list[str] | None = None,
        causes: list[str] | None = None,
        related_experience_ids: list[str] | None = None,
    ) -> "FailureExperience":
        return cls(
            id=uuid.uuid4().hex[:16],
            created_at=time.time(),
            mission_id=_clean_text(mission_id, limit=300),
            title=_clean_text(title, limit=500),
            objective=_clean_text(objective),
            status=_clean_text(status, limit=200),
            factual_outcome=_clean_text(factual_outcome),
            research_narrative=_clean_text(research_narrative),
            passed_assumptions=_clean_list(passed_assumptions or []),
            lessons=_clean_list(lessons or []),
            transfer_insights=_clean_list(transfer_insights or []),
            claim_boundaries=_clean_list(claim_boundaries or []),
            retry_conditions=_clean_list(retry_conditions or []),
            artifact_refs=_clean_list(artifact_refs or [], item_limit=800),
            concepts=_clean_list(concepts or [], item_limit=200),
            causes=_clean_list(causes or [], item_limit=300),
            related_experience_ids=_clean_list(
                related_experience_ids or [], item_limit=100
            ),
        )

    @classmethod
    def from_jsonable(cls, row: dict[str, Any]) -> "FailureExperience":
        annotations = [
            FailureAnnotation(
                id=str(annotation.get("id") or uuid.uuid4().hex[:16]),
                created_at=float(annotation.get("created_at") or time.time()),
                text=_clean_text(annotation.get("text")),
                relation=_clean_text(annotation.get("relation"), limit=200),
                evidence_refs=_clean_list(annotation.get("evidence_refs") or []),
            )
            for annotation in row.get("annotations", [])
            if isinstance(annotation, dict)
        ]
        return cls(
            id=str(row.get("id") or uuid.uuid4().hex[:16]),
            created_at=float(row.get("created_at") or time.time()),
            mission_id=_clean_text(row.get("mission_id"), limit=300),
            title=_clean_text(row.get("title"), limit=500),
            objective=_clean_text(row.get("objective")),
            status=_clean_text(row.get("status"), limit=200),
            factual_outcome=_clean_text(row.get("factual_outcome")),
            research_narrative=_clean_text(row.get("research_narrative")),
            passed_assumptions=_clean_list(row.get("passed_assumptions") or []),
            lessons=_clean_list(row.get("lessons") or []),
            transfer_insights=_clean_list(row.get("transfer_insights") or []),
            claim_boundaries=_clean_list(row.get("claim_boundaries") or []),
            retry_conditions=_clean_list(row.get("retry_conditions") or []),
            artifact_refs=_clean_list(row.get("artifact_refs") or []),
            concepts=_clean_list(row.get("concepts") or []),
            causes=_clean_list(row.get("causes") or []),
            related_experience_ids=_clean_list(
                row.get("related_experience_ids") or []
            ),
            annotations=annotations,
        )

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FailureExperienceHit:
    experience: FailureExperience
    channel: str


class FailureExperienceStore:
    """Append-only compact store with bounded, multi-channel retrieval."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as handle:
            if fcntl is None:  # pragma: no cover - Windows fallback
                yield
                return
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def append(self, experience: FailureExperience) -> None:
        row = {"record_type": "experience", **experience.to_jsonable()}
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        with self._locked():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

    def annotate(self, experience_id: str, annotation: FailureAnnotation) -> None:
        row = {
            "record_type": "annotation",
            "experience_id": str(experience_id),
            "annotation": asdict(annotation),
        }
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        with self._locked():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())

    def _bounded_rows(
        self,
        *,
        max_bytes: int = _DEFAULT_SCAN_BYTES,
        max_records: int = _DEFAULT_SCAN_RECORDS,
    ) -> list[dict[str, Any]]:
        if max_bytes <= 0 or max_records <= 0 or not self.path.exists():
            return []
        try:
            with self.path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                start = max(0, size - max_bytes)
                handle.seek(start)
                data = handle.read(max_bytes)
        except OSError:
            return []
        if start:
            newline = data.find(b"\n")
            data = data[newline + 1 :] if newline >= 0 else b""
        rows: list[dict[str, Any]] = []
        for raw in data.splitlines()[-max_records:]:
            try:
                row = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(row, dict):
                rows.append(row)
        return rows

    def recent(
        self,
        *,
        max_entries: int = 64,
        max_bytes: int = _DEFAULT_SCAN_BYTES,
    ) -> list[FailureExperience]:
        rows = self._bounded_rows(max_bytes=max_bytes)
        annotations: dict[str, list[FailureAnnotation]] = {}
        experiences: list[FailureExperience] = []
        for row in reversed(rows):
            if row.get("record_type") == "annotation":
                payload = row.get("annotation")
                if isinstance(payload, dict):
                    try:
                        annotation = FailureAnnotation(
                            id=str(payload.get("id") or uuid.uuid4().hex[:16]),
                            created_at=float(payload.get("created_at") or time.time()),
                            text=_clean_text(payload.get("text")),
                            relation=_clean_text(payload.get("relation"), limit=200),
                            evidence_refs=_clean_list(
                                payload.get("evidence_refs") or []
                            ),
                        )
                    except (TypeError, ValueError):
                        continue
                    annotations.setdefault(
                        str(row.get("experience_id") or ""), []
                    ).append(annotation)
                continue
            if row.get("record_type") not in {None, "experience"}:
                continue
            try:
                experience = FailureExperience.from_jsonable(row)
            except (TypeError, ValueError):
                continue
            attached = list(reversed(annotations.get(experience.id, [])))
            if attached:
                experience = replace(experience, annotations=attached)
            experiences.append(experience)
            if len(experiences) >= max_entries:
                break
        return experiences

    def retrieve(
        self,
        objective: str,
        *,
        max_entries: int = 4,
        max_bytes: int = _DEFAULT_SCAN_BYTES,
    ) -> list[FailureExperienceHit]:
        """Mix recent, direct, transfer, and distant-analogy channels.

        Facets influence candidate ordering only. They never reject a new
        attempt or assert that a prior failure applies.
        """
        if max_entries <= 0:
            return []
        candidates = self.recent(max_entries=64, max_bytes=max_bytes)
        if not candidates:
            return []
        query = _tokens(objective)

        def direct_score(item: FailureExperience) -> int:
            return len(
                query
                & _tokens(
                    item.title,
                    item.objective,
                    item.status,
                    *item.concepts,
                    *item.causes,
                )
            )

        def transfer_score(item: FailureExperience) -> int:
            annotation_text = [annotation.text for annotation in item.annotations]
            return len(
                query
                & _tokens(
                    item.research_narrative,
                    *item.lessons,
                    *item.transfer_insights,
                    *item.retry_conditions,
                    *annotation_text,
                )
            )

        hits: list[FailureExperienceHit] = []
        selected: set[str] = set()

        def add(item: FailureExperience, channel: str) -> None:
            if item.id not in selected and len(hits) < max_entries:
                selected.add(item.id)
                hits.append(FailureExperienceHit(item, channel))

        add(candidates[0], "recent")
        direct = max(candidates, key=lambda item: (direct_score(item), item.created_at))
        if direct_score(direct):
            add(direct, "direct factual/conceptual")
        transfer = max(
            candidates,
            key=lambda item: (transfer_score(item), item.created_at),
        )
        if transfer_score(transfer):
            add(transfer, "transfer insight")

        remaining = [item for item in candidates if item.id not in selected]
        if remaining and len(hits) < max_entries:
            # Intentionally reserve one slot for a low-overlap analogy. A stable
            # hash rotates ties without pretending the harness can judge which
            # distant scientific idea will be useful.
            salt = hashlib.sha256(objective.encode("utf-8")).hexdigest()
            exploratory = min(
                remaining,
                key=lambda item: (
                    direct_score(item) + transfer_score(item),
                    hashlib.sha256(f"{salt}:{item.id}".encode()).hexdigest(),
                ),
            )
            add(exploratory, "exploratory analogy")

        for item in candidates:
            add(item, "recent")
        return hits

    def render_context(
        self,
        objective: str,
        *,
        max_entries: int = 4,
        max_chars: int = 6_000,
        max_bytes: int = _DEFAULT_SCAN_BYTES,
    ) -> str:
        hits = self.retrieve(
            objective,
            max_entries=max_entries,
            max_bytes=max_bytes,
        )
        if not hits or max_chars <= 0:
            return ""
        lines = [
            "### Prior failure experiences (advisory, compact)",
            (
                "These capsules are prompts for scientific judgment, not rules. "
                "Facets and channel labels only explain retrieval; a timeout, "
                "negative result, or prior failed mechanism does not prove "
                "impossibility or block a changed approach. Raw artifacts are "
                "references and have not been opened."
            ),
        ]
        for hit in hits:
            item = hit.experience
            lines.extend([
                "",
                f"#### {item.title or item.mission_id} [{hit.channel}]",
                f"- Outcome: {item.factual_outcome or item.status}",
            ])
            if item.research_narrative:
                lines.append(f"- Narrative: {item.research_narrative}")
            if item.lessons:
                lines.append("- Lessons: " + " | ".join(item.lessons))
            if item.transfer_insights:
                lines.append(
                    "- Transfer ideas: " + " | ".join(item.transfer_insights)
                )
            if item.claim_boundaries:
                lines.append(
                    "- Boundaries: " + " | ".join(item.claim_boundaries)
                )
            if item.retry_conditions:
                lines.append(
                    "- Retry conditions: " + " | ".join(item.retry_conditions)
                )
            if item.artifact_refs:
                lines.append(
                    "- Lazy evidence refs: " + " | ".join(item.artifact_refs)
                )
            if item.annotations:
                lines.append(
                    "- Later interpretations: "
                    + " | ".join(annotation.text for annotation in item.annotations)
                )
            rendered = "\n".join(lines)
            if len(rendered) > max_chars:
                return rendered[: max_chars - 1].rstrip() + "…\n"
        return "\n".join(lines).strip() + "\n"


def experience_from_settled_mission(
    *,
    mission_id: str,
    title: str,
    objective: str,
    status: str,
    factual_outcome: str,
    final_message: str = "",
    review_reason: str = "",
    planner_report: dict[str, Any] | None = None,
    stop_kind: str = "",
    recoverable: bool = False,
    concepts: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    non_goals: list[str] | None = None,
) -> FailureExperience:
    """Build a conservative capsule from fields already in settled memory."""
    report = planner_report if isinstance(planner_report, dict) else {}
    transfer = _clean_list([
        report.get("next_action"),
        report.get("recommendation"),
        report.get("reason"),
    ])
    retry = _clean_list([
        report.get("retry_condition"),
        report.get("next_action") if recoverable else "",
    ])
    boundaries = [
        "This records one bounded mission outcome, not a general impossibility result.",
        *(_clean_list(non_goals or [])),
    ]
    causes = _clean_list([stop_kind, report.get("diagnosis"), report.get("cause")])
    return FailureExperience.new(
        mission_id=mission_id,
        title=title,
        objective=objective,
        status=status,
        factual_outcome=factual_outcome,
        research_narrative=final_message,
        lessons=_clean_list([review_reason, report.get("reason")]),
        transfer_insights=transfer,
        claim_boundaries=boundaries,
        retry_conditions=retry,
        artifact_refs=artifact_refs,
        concepts=concepts,
        causes=causes,
    )


__all__ = [
    "FailureAnnotation",
    "FailureExperience",
    "FailureExperienceHit",
    "FailureExperienceStore",
    "experience_from_settled_mission",
]
