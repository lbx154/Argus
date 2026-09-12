"""Compact, open-ended memory for learning from unsuccessful missions.

Failure experiences live in Argus project state, separate from project
artifacts. Records contain prose plus advisory facets and references; ordinary
retrieval never opens those references or walks the project worktree.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

from .failure_experience_index import (
    EmbeddingAdapter,
    FailureExperienceIndex,
    RecallDocument,
    lexical_scores,
)
from .failure_experience_storage import ExperienceRepository, ExperienceSnapshot

log = logging.getLogger(__name__)
_DEFAULT_SCAN_BYTES = 1_000_000


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
        if len(cleaned) >= 24:
            break
    return cleaned


class StaleFailureExperienceWrite(ValueError):
    """A revision changed, or a physically retired identity was replayed."""


@dataclass(frozen=True)
class FailureAnnotation:
    """A later advisory interpretation folded into the current revision."""

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
    revision: int = 1
    state: str = "active"
    updated_at: float = 0.0
    source_refs: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    superseded_by: list[str] = field(default_factory=list)
    retirement_reason: str = ""
    expires_at: float = 0.0

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
        experience_id: str = "",
        created_at: float | None = None,
        source_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
    ) -> "FailureExperience":
        birth = time.time() if created_at is None else float(created_at)
        return cls(
            id=experience_id or f"exp:{birth.hex()}:{uuid.uuid4().hex[:16]}",
            created_at=birth,
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
            related_experience_ids=_clean_list(related_experience_ids or [], item_limit=100),
            source_refs=_clean_list(source_refs or [], item_limit=800),
            evidence_refs=_clean_list(evidence_refs or [], item_limit=800),
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
            related_experience_ids=_clean_list(row.get("related_experience_ids") or []),
            annotations=annotations,
            revision=int(row.get("revision", 1)),
            state=str(row.get("state", "active")),
            updated_at=float(row.get("updated_at") or row.get("created_at") or 0),
            source_refs=_clean_list(row.get("source_refs") or [], item_limit=800),
            evidence_refs=_clean_list(row.get("evidence_refs") or [], item_limit=800),
            superseded_by=_clean_list(row.get("superseded_by") or [], item_limit=100),
            retirement_reason=_clean_text(row.get("retirement_reason")),
            expires_at=float(row.get("expires_at") or 0),
        )

    def to_jsonable(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FailureExperienceHit:
    experience: FailureExperience
    channel: str


class FailureExperienceStore:
    """Revisioned, capacity-bounded experiences with a disposable recall index."""

    def __init__(
        self,
        path: Path,
        *,
        max_active: int = 256,
        max_history: int = 64,
        max_active_bytes: int = _DEFAULT_SCAN_BYTES,
        max_history_bytes: int = 256_000,
        embedder: EmbeddingAdapter | None = None,
    ) -> None:
        self.path = Path(path)
        self._repository = ExperienceRepository(
            self.path,
            max_active=max_active,
            max_history=max_history,
            max_active_bytes=max_active_bytes,
            max_history_bytes=max_history_bytes,
        )
        self.index = FailureExperienceIndex(self.path.with_suffix(".sqlite3"), embedder=embedder)

    @staticmethod
    def _same(left: FailureExperience, right: FailureExperience) -> bool:
        def content(item: FailureExperience) -> dict[str, Any]:
            payload = item.to_jsonable()
            for name in ("created_at", "updated_at"):
                payload.pop(name)
            return payload

        return content(left) == content(right)

    @staticmethod
    def _require_current(
        snapshot: ExperienceSnapshot, identity: str, revision: int
    ) -> FailureExperience:
        item = snapshot.current.get(identity)
        if item is None or item.revision != revision or item.state != "active":
            raise StaleFailureExperienceWrite(
                "experience is missing, retired, or has a newer revision"
            )
        return item

    @staticmethod
    def _evidence(evidence_refs: list[str]) -> list[str]:
        refs = _clean_list(evidence_refs, item_limit=800)
        if not refs:
            raise ValueError("a lifecycle correction requires explicit evidence references")
        return refs

    def _documents(self, snapshot: ExperienceSnapshot) -> tuple[list[RecallDocument], str]:
        from .failure_experience_storage import active

        documents = []
        for item in snapshot.current.values():
            if not active(item, now=time.time()):
                continue
            digest = hashlib.sha256(
                json.dumps(item.to_jsonable(), sort_keys=True).encode()
            ).hexdigest()
            documents.append(
                RecallDocument(
                    item.id,
                    item.revision,
                    digest,
                    "\n".join(
                        [item.title, item.objective, item.status, *item.concepts, *item.causes]
                    ),
                    "\n".join(
                        [
                            item.research_narrative,
                            *item.lessons,
                            *item.transfer_insights,
                            *item.retry_conditions,
                            *(annotation.text for annotation in item.annotations),
                        ]
                    ),
                )
            )
        digest = hashlib.sha256(
            (
                snapshot.digest
                + "".join(
                    f"{item.id}:{item.revision}:{item.digest}"
                    for item in sorted(documents, key=lambda row: row.id)
                )
            ).encode()
        ).hexdigest()
        return documents, digest

    def _sync(self, snapshot: ExperienceSnapshot) -> bool:
        documents, digest = self._documents(snapshot)
        try:
            self.index.sync(documents, digest)
            return True
        except Exception:
            # Source is already committed. Never acknowledge an old cache as
            # current, or roll the source back because a derived write failed.
            log.warning(
                "experience index sync failed; rebuilding from canonical state", exc_info=True
            )
        try:
            self.index.rebuild(documents, digest)
            return True
        except Exception:
            log.warning("experience index unavailable; using current lexical recall", exc_info=True)
            return False

    def _commit(self, snapshot: ExperienceSnapshot, *, protected: set[str] | None = None) -> None:
        self._repository.save(snapshot, now=time.time(), protected=protected)
        self._sync(snapshot)

    def append(self, experience: FailureExperience) -> FailureExperience:
        """Idempotent insert/upsert. Existing identities require the next revision."""
        self._repository.validate(experience)
        with self._repository.locked():
            snapshot = self._repository.load(full_legacy=True)
            previous = snapshot.current.get(experience.id)
            if previous is not None:
                if self._same(previous, experience):
                    self._sync(snapshot)
                    return previous
                self._require_current(snapshot, experience.id, experience.revision - 1)
                self._evidence(experience.evidence_refs)
                experience = replace(experience, created_at=previous.created_at)
                snapshot.history.append(previous)
            elif (
                experience.revision != 1
                or experience.created_at <= snapshot.admission_floor
                or (snapshot.admission_floor > 0 and not experience.id.startswith("exp:"))
            ):
                raise StaleFailureExperienceWrite(
                    "old or unknown revised experience cannot be recreated"
                )
            if experience.state != "active":
                raise ValueError(
                    "new or revised experiences must be active; use lifecycle operations"
                )
            written = replace(experience, updated_at=time.time())
            snapshot.current[written.id] = written
            self._commit(snapshot, protected={written.id})
            return snapshot.current[written.id]

    def revise(
        self,
        experience_id: str,
        *,
        expected_revision: int,
        evidence_refs: list[str],
        **changes: Any,
    ) -> FailureExperience:
        refs = self._evidence(evidence_refs)
        forbidden = {
            "id",
            "revision",
            "state",
            "created_at",
            "updated_at",
            "superseded_by",
            "retirement_reason",
            "source_refs",
        }
        if forbidden.intersection(changes):
            raise ValueError("identity and lifecycle fields cannot be rewritten as content")
        with self._repository.locked():
            snapshot = self._repository.load(full_legacy=True)
            previous = self._require_current(snapshot, experience_id, expected_revision)
            written = replace(
                previous,
                **changes,
                revision=previous.revision + 1,
                updated_at=time.time(),
                evidence_refs=_clean_list(refs + previous.evidence_refs, item_limit=800),
            )
            self._repository.validate(written)
            snapshot.history.append(previous)
            snapshot.current[experience_id] = written
            self._commit(snapshot, protected={experience_id})
            return written

    def annotate(self, experience_id: str, annotation: FailureAnnotation) -> FailureExperience:
        """Attach advisory interpretation to a real current identity, once per id."""
        with self._repository.locked():
            snapshot = self._repository.load(full_legacy=True)
            previous = snapshot.current.get(experience_id)
            if previous is None or previous.state != "active":
                raise StaleFailureExperienceWrite("annotation target is missing or retired")
            for existing in previous.annotations:
                if existing.id == annotation.id:
                    if existing != annotation:
                        raise StaleFailureExperienceWrite(
                            "annotation identity already names different content"
                        )
                    return previous
            written = replace(
                previous,
                revision=previous.revision + 1,
                updated_at=time.time(),
                annotations=(previous.annotations + [annotation])[-24:],
                evidence_refs=_clean_list(
                    annotation.evidence_refs + previous.evidence_refs, item_limit=800
                ),
            )
            self._repository.validate(written)
            snapshot.history.append(previous)
            snapshot.current[experience_id] = written
            self._commit(snapshot, protected={experience_id})
            return written

    def _retire(
        self,
        experience_id: str,
        *,
        expected_revision: int,
        state: str,
        evidence_refs: list[str],
        reason: str,
        replacement_id: str = "",
    ) -> FailureExperience:
        refs = self._evidence(evidence_refs)
        if not str(reason).strip():
            raise ValueError("retirement requires its reason")
        with self._repository.locked():
            snapshot = self._repository.load(full_legacy=True)
            previous = self._require_current(snapshot, experience_id, expected_revision)
            if replacement_id:
                target = snapshot.current.get(replacement_id)
                if target is None or target.state != "active" or replacement_id == experience_id:
                    raise ValueError("supersession requires a different active replacement")
            written = replace(
                previous,
                revision=previous.revision + 1,
                state=state,
                updated_at=time.time(),
                retirement_reason=_clean_text(reason),
                superseded_by=[replacement_id] if replacement_id else [],
                evidence_refs=_clean_list(refs + previous.evidence_refs, item_limit=800),
            )
            snapshot.history.append(previous)
            snapshot.current[experience_id] = written
            self._commit(snapshot)
            return written

    def retract(
        self, experience_id: str, *, expected_revision: int, evidence_refs: list[str], reason: str
    ) -> FailureExperience:
        return self._retire(
            experience_id,
            expected_revision=expected_revision,
            state="retracted",
            evidence_refs=evidence_refs,
            reason=reason,
        )

    def supersede(
        self,
        experience_id: str,
        replacement_id: str,
        *,
        expected_revision: int,
        evidence_refs: list[str],
        reason: str,
    ) -> FailureExperience:
        return self._retire(
            experience_id,
            expected_revision=expected_revision,
            state="superseded",
            evidence_refs=evidence_refs,
            reason=reason,
            replacement_id=replacement_id,
        )

    def merge(
        self,
        target_id: str,
        source_ids: list[str],
        *,
        expected_revisions: Mapping[str, int],
        evidence_refs: list[str],
        reason: str,
        **changes: Any,
    ) -> FailureExperience:
        """Commit an explicitly authored consolidation; similarity never authorizes it."""
        refs = self._evidence(evidence_refs)
        identities = list(dict.fromkeys([target_id, *source_ids]))
        if len(identities) < 2 or set(expected_revisions) != set(identities) or not reason.strip():
            raise ValueError("merge requires distinct sources, their exact revisions and a reason")
        if {
            "id",
            "revision",
            "state",
            "created_at",
            "updated_at",
            "superseded_by",
            "retirement_reason",
            "source_refs",
            "related_experience_ids",
        }.intersection(changes):
            raise ValueError("merge cannot override identities or provenance")
        with self._repository.locked():
            snapshot = self._repository.load(full_legacy=True)
            items = [
                self._require_current(snapshot, identity, expected_revisions[identity])
                for identity in identities
            ]
            target = items[0]
            sources = list(dict.fromkeys(ref for item in items for ref in item.source_refs))
            if len(sources) > 24 or len(identities) > 24:
                raise ValueError("merge exceeds the bounded provenance budget")
            written = replace(
                target,
                **changes,
                revision=target.revision + 1,
                updated_at=time.time(),
                evidence_refs=_clean_list(
                    refs + [ref for item in items for ref in item.evidence_refs], item_limit=800
                ),
                source_refs=sources,
                related_experience_ids=_clean_list(
                    identities[1:] + target.related_experience_ids, item_limit=100
                ),
            )
            self._repository.validate(written)
            snapshot.history.extend(items)
            snapshot.current[target_id] = written
            for source in items[1:]:
                snapshot.current[source.id] = replace(
                    source,
                    revision=source.revision + 1,
                    state="superseded",
                    updated_at=time.time(),
                    superseded_by=[target_id],
                    retirement_reason=_clean_text(reason),
                    evidence_refs=_clean_list(refs + source.evidence_refs, item_limit=800),
                )
            self._commit(snapshot, protected={target_id})
            return written

    def compact(self) -> dict[str, int]:
        """Migrate old JSONL, prune old revisions/expired entries, reclaim the cache."""
        with self._repository.locked():
            snapshot = self._repository.load(full_legacy=True)
            self._commit(snapshot)
            try:
                self.index.compact()
            except Exception:
                log.warning("experience cache compaction failed", exc_info=True)
            return {
                "active": sum(item.state == "active" for item in snapshot.current.values()),
                "terminal": sum(item.state != "active" for item in snapshot.current.values()),
                "history": len(snapshot.history),
            }

    def get(self, experience_id: str) -> FailureExperience | None:
        with self._repository.locked():
            return self._repository.load().current.get(experience_id)

    def recent(
        self, *, max_entries: int = 64, max_bytes: int = _DEFAULT_SCAN_BYTES
    ) -> list[FailureExperience]:
        from .failure_experience_storage import active

        if max_entries <= 0 or max_bytes <= 0:
            return []
        with self._repository.locked():
            snapshot = self._repository.load(max_bytes=max_bytes)
            return sorted(
                (item for item in snapshot.current.values() if active(item, now=time.time())),
                key=lambda item: (item.updated_at or item.created_at, item.id),
                reverse=True,
            )[:max_entries]

    def retrieve(
        self, objective: str, *, max_entries: int = 4, max_bytes: int = _DEFAULT_SCAN_BYTES
    ) -> list[FailureExperienceHit]:
        if max_entries <= 0 or max_bytes <= 0:
            return []
        with self._repository.locked():
            snapshot = self._repository.load(max_bytes=max_bytes)
            # First ordinary recall upgrades the old append log. Explicit small
            # legacy read windows remain read-only compatibility probes.
            if snapshot.legacy and max_bytes == _DEFAULT_SCAN_BYTES:
                snapshot = self._repository.load(full_legacy=True)
                self._commit(snapshot)
            documents, digest = self._documents(snapshot)
            if not documents:
                self._sync(snapshot)
                return []
            scores = lexical_scores(documents, objective)
            if self._sync(snapshot):
                try:
                    scores = self.index.scores(objective, source_digest=digest)
                except Exception:
                    log.warning(
                        "experience index query failed; using current lexical recall", exc_info=True
                    )
            candidates = sorted(
                (snapshot.current[doc.id] for doc in documents),
                key=lambda item: (item.updated_at or item.created_at, item.id),
                reverse=True,
            )
            hits: list[FailureExperienceHit] = []
            selected: set[str] = set()

            def add(item: FailureExperience, channel: str) -> None:
                if item.id not in selected and len(hits) < max_entries:
                    selected.add(item.id)
                    hits.append(FailureExperienceHit(item, channel))

            add(candidates[0], "recent")
            direct = max(
                candidates,
                key=lambda item: (scores[item.id].direct, scores[item.id].vector, item.updated_at),
            )
            if scores[direct.id].direct:
                add(direct, "direct factual/conceptual")
            transfer = max(
                candidates,
                key=lambda item: (
                    scores[item.id].transfer,
                    scores[item.id].vector,
                    item.updated_at,
                ),
            )
            if scores[transfer.id].transfer:
                add(transfer, "transfer insight")
            if not self.index.embedder.identifier.startswith("lexical-hash-"):
                vector_hit = max(candidates, key=lambda item: scores[item.id].vector)
                if scores[vector_hit.id].vector > 0:
                    add(vector_hit, "embedding similarity (advisory)")
            remaining = [item for item in candidates if item.id not in selected]
            if remaining:
                salt = hashlib.sha256(objective.encode()).hexdigest()
                exploratory = min(
                    remaining,
                    key=lambda item: (
                        scores[item.id].direct + scores[item.id].transfer,
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
        if max_entries <= 0 or max_chars <= 0 or max_bytes <= 0:
            return ""
        try:
            hits = self.retrieve(
                objective,
                max_entries=max_entries,
                max_bytes=max_bytes,
            )
        except (OSError, TypeError, ValueError):
            # Curated recall is optional. A corrupt source must not revive an
            # old cache, and must not prevent the next real mission either.
            log.warning("failure experience source unavailable; omitting recall", exc_info=True)
            return ""
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
            lines.extend(
                [
                    "",
                    f"#### {item.title or item.mission_id} [{hit.channel}]",
                    f"- Outcome: {item.factual_outcome or item.status}",
                ]
            )
            if item.research_narrative:
                lines.append(f"- Narrative: {item.research_narrative}")
            lessons = [lesson for lesson in item.lessons if lesson != item.factual_outcome]
            if lessons:
                lines.append("- Lessons: " + " | ".join(lessons))
            if item.transfer_insights:
                lines.append("- Transfer ideas: " + " | ".join(item.transfer_insights))
            if item.claim_boundaries:
                lines.append("- Boundaries: " + " | ".join(item.claim_boundaries))
            if item.retry_conditions:
                lines.append("- Retry conditions: " + " | ".join(item.retry_conditions))
            if item.artifact_refs:
                lines.append("- Lazy evidence refs: " + " | ".join(item.artifact_refs))
            if item.evidence_refs:
                lines.append("- Revision evidence: " + " | ".join(item.evidence_refs))
            if item.source_refs:
                lines.append("- Sources: " + " | ".join(item.source_refs))
            if item.annotations:
                lines.append(
                    "- Later interpretations: "
                    + " | ".join(annotation.text for annotation in item.annotations)
                )
            rendered = "\n".join(lines)
            if len(rendered) > max_chars:
                return rendered[: max_chars - 1].rstrip() + "…"
        return ("\n".join(lines).strip() + "\n")[:max_chars]


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
    attempt_id: str = "",
    created_at: float | None = None,
) -> FailureExperience:
    """Build a conservative capsule from fields already in settled memory."""
    if created_at is None:
        raise ValueError("settled experience requires its original stable creation timestamp")
    birth = float(created_at)
    report = planner_report if isinstance(planner_report, dict) else {}
    transfer = _clean_list(
        [
            report.get("next_action"),
            report.get("recommendation"),
            report.get("reason"),
        ]
    )
    retry = _clean_list(
        [
            report.get("retry_condition"),
            report.get("next_action") if recoverable else "",
        ]
    )
    boundaries = [
        "This records one bounded mission outcome, not a general impossibility result.",
        *(_clean_list(non_goals or [])),
    ]
    causes = _clean_list([stop_kind, report.get("diagnosis"), report.get("cause")])
    source_identity = f"mission:{mission_id}/attempt:{attempt_id or 'default'}"
    return FailureExperience.new(
        experience_id=f"exp:{birth.hex()}:{hashlib.sha256(source_identity.encode()).hexdigest()[:32]}",
        created_at=birth,
        source_refs=[source_identity],
        evidence_refs=[source_identity, *(artifact_refs or [])],
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
    "StaleFailureExperienceWrite",
    "experience_from_settled_mission",
]
