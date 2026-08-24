"""Normalized, source-addressable medical evidence records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence


def _text(value: Any) -> str:
    return str(value or "").strip()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _strings(value: Any) -> tuple[str, ...]:
    values: list[str] = []
    seen: set[str] = set()
    for item in _sequence(value):
        text = _text(item)
        folded = text.casefold()
        if text and folded not in seen:
            seen.add(folded)
            values.append(text)
    return tuple(values)


def _join(values: Sequence[Any]) -> str | None:
    texts = _strings(values)
    return "; ".join(texts) if texts else None


@dataclass(frozen=True)
class MedicalScope:
    target: str
    disease: str
    target_aliases: tuple[str, ...] = ()
    disease_aliases: tuple[str, ...] = ()
    population: str = ""
    intervention_class: str = ""
    date_from: str = ""
    date_to: str = ""
    decision_question: str = ""
    output_language: str = ""

    def __post_init__(self) -> None:
        target = self.target.strip()
        disease = self.disease.strip()
        if not target:
            raise ValueError("target is required")
        if not disease:
            raise ValueError("disease is required")
        object.__setattr__(self, "target", target)
        object.__setattr__(self, "disease", disease)
        object.__setattr__(self, "target_aliases", _strings(self.target_aliases))
        object.__setattr__(self, "disease_aliases", _strings(self.disease_aliases))
        for name in (
            "population",
            "intervention_class",
            "date_from",
            "date_to",
            "decision_question",
            "output_language",
        ):
            object.__setattr__(self, name, _text(getattr(self, name)))
        parsed_dates: dict[str, date] = {}
        for name in ("date_from", "date_to"):
            value = getattr(self, name)
            if not value:
                continue
            try:
                parsed_dates[name] = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError(f"{name} must use YYYY-MM-DD") from exc
        if (
            "date_from" in parsed_dates
            and "date_to" in parsed_dates
            and parsed_dates["date_from"] > parsed_dates["date_to"]
        ):
            raise ValueError("date_from cannot be after date_to")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["target_aliases"] = list(self.target_aliases)
        payload["disease_aliases"] = list(self.disease_aliases)
        return payload


@dataclass(frozen=True)
class EvidenceRecord:
    schema_version: int
    record_id: str
    source_type: str
    source_id: str
    canonical_url: str
    retrieved_at: str
    query_id: str
    title: str
    target_submitted: str
    disease_submitted: str
    target_canonical: str = ""
    disease_canonical: str = ""
    target_aliases: tuple[str, ...] = ()
    disease_aliases: tuple[str, ...] = ()
    scope_population: str = ""
    date: str | None = None
    status: str | None = None
    evidence_class: str = "literature"
    study_design: str | None = None
    population_or_model: str | None = None
    intervention_or_exposure: str | None = None
    comparator: str | None = None
    endpoint: str | None = None
    sample_size: int | None = None
    follow_up: str | None = None
    registry_last_update: str | None = None
    data_cutoff: str | None = None
    result_direction: str | None = None
    source_locator: str = ""
    raw_artifact: str = ""
    identifiers: Mapping[str, str] = field(default_factory=dict)
    has_results: bool | None = None
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["target_aliases"] = list(self.target_aliases)
        payload["disease_aliases"] = list(self.disease_aliases)
        payload["identifiers"] = dict(self.identifiers)
        payload["limitations"] = list(self.limitations)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceRecord":
        values = dict(payload)
        values["target_aliases"] = tuple(_strings(values.get("target_aliases")))
        values["disease_aliases"] = tuple(_strings(values.get("disease_aliases")))
        values["limitations"] = tuple(_strings(values.get("limitations")))
        values["identifiers"] = dict(_mapping(values.get("identifiers")))
        return cls(**values)


def normalize_pubmed(
    payload: Mapping[str, Any],
    *,
    scope: MedicalScope,
    query_id: str,
    retrieved_at: str,
) -> tuple[EvidenceRecord, ...]:
    result = _mapping(payload.get("result"))
    raw_uids = _sequence(result.get("uids"))
    if not raw_uids:
        raw_uids = tuple(key for key in result if key != "uids")
    rows: list[EvidenceRecord] = []
    seen: set[str] = set()
    for raw_uid in raw_uids:
        uid = _text(raw_uid)
        if not uid or uid in seen:
            continue
        entry = _mapping(result.get(uid))
        source_id = _text(entry.get("uid")) or uid
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        identifiers: dict[str, str] = {}
        for raw_identifier in _sequence(entry.get("articleids")):
            identifier = _mapping(raw_identifier)
            id_type = _text(identifier.get("idtype"))
            value = _text(identifier.get("value"))
            if id_type and value and id_type not in identifiers:
                identifiers[id_type] = value
        rows.append(
            EvidenceRecord(
                schema_version=1,
                record_id=f"pubmed:{source_id}",
                source_type="pubmed",
                source_id=source_id,
                canonical_url=f"https://pubmed.ncbi.nlm.nih.gov/{source_id}/",
                retrieved_at=retrieved_at,
                query_id=query_id,
                title=_text(entry.get("title")),
                target_submitted=scope.target,
                disease_submitted=scope.disease,
                target_aliases=scope.target_aliases,
                disease_aliases=scope.disease_aliases,
                scope_population=scope.population,
                date=_text(entry.get("pubdate")) or None,
                evidence_class="literature",
                source_locator=f"result.{source_id}",
                identifiers=identifiers,
                limitations=("PubMed summary metadata; full text not reviewed.",),
            )
        )
    return tuple(rows)


def normalize_clinical_trials(
    payload: Mapping[str, Any],
    *,
    scope: MedicalScope,
    query_id: str,
    retrieved_at: str,
) -> tuple[EvidenceRecord, ...]:
    rows: list[EvidenceRecord] = []
    seen: set[str] = set()
    for index, raw_study in enumerate(_sequence(payload.get("studies"))):
        study = _mapping(raw_study)
        protocol = _mapping(study.get("protocolSection"))
        identification = _mapping(protocol.get("identificationModule"))
        source_id = _text(identification.get("nctId"))
        if not source_id or source_id in seen:
            continue
        seen.add(source_id)
        status_module = _mapping(protocol.get("statusModule"))
        design = _mapping(protocol.get("designModule"))
        phases = _strings(design.get("phases"))
        design_parts = tuple(
            value
            for value in (_text(design.get("studyType")), *phases)
            if value
        )
        enrollment = _mapping(design.get("enrollmentInfo"))
        raw_count = enrollment.get("count")
        sample_size = (
            int(raw_count)
            if isinstance(raw_count, (int, float)) and not isinstance(raw_count, bool)
            else None
        )
        arms = _mapping(protocol.get("armsInterventionsModule"))
        intervention_names = [
            _text(_mapping(item).get("name"))
            for item in _sequence(arms.get("interventions"))
        ]
        comparator_names = [
            _text(group.get("label"))
            for raw_group in _sequence(arms.get("armGroups"))
            if (
                (group := _mapping(raw_group))
                and _text(group.get("type"))
                in {"ACTIVE_COMPARATOR", "PLACEBO_COMPARATOR", "SHAM_COMPARATOR"}
            )
        ]
        outcomes = _mapping(protocol.get("outcomesModule"))
        primary_outcomes = [
            _mapping(item) for item in _sequence(outcomes.get("primaryOutcomes"))
        ]
        endpoint = _join([item.get("measure") for item in primary_outcomes])
        follow_up = _join([item.get("timeFrame") for item in primary_outcomes])
        eligibility = _mapping(protocol.get("eligibilityModule"))
        eligibility_parts = [
            _text(eligibility.get("sex")),
            _text(eligibility.get("minimumAge")),
            _text(eligibility.get("maximumAge")),
        ]
        population = _join(eligibility_parts)
        has_results = bool(study.get("hasResults") or study.get("resultsSection"))
        limitations = ["ClinicalTrials.gov registry metadata; publication not reviewed."]
        if not has_results:
            limitations.append("Registration record has no posted results.")
        rows.append(
            EvidenceRecord(
                schema_version=1,
                record_id=f"clinical_trials:{source_id}",
                source_type="clinical_trials",
                source_id=source_id,
                canonical_url=f"https://clinicaltrials.gov/study/{source_id}",
                retrieved_at=retrieved_at,
                query_id=query_id,
                title=_text(identification.get("briefTitle")),
                target_submitted=scope.target,
                disease_submitted=scope.disease,
                target_aliases=scope.target_aliases,
                disease_aliases=scope.disease_aliases,
                scope_population=scope.population,
                date=_text(_mapping(status_module.get("startDateStruct")).get("date"))
                or None,
                status=_text(status_module.get("overallStatus")) or None,
                evidence_class="clinical",
                study_design="; ".join(design_parts) if design_parts else None,
                population_or_model=population,
                intervention_or_exposure=_join(intervention_names),
                comparator=_join(comparator_names),
                endpoint=endpoint,
                sample_size=sample_size,
                follow_up=follow_up,
                registry_last_update=(
                    _text(status_module.get("lastUpdateSubmitDate")) or None
                ),
                data_cutoff=None,
                result_direction=None,
                source_locator=f"studies[{index}].protocolSection",
                identifiers={"nct": source_id},
                has_results=has_results,
                limitations=tuple(limitations),
            )
        )
    return tuple(rows)


def validate_evidence_record(record: EvidenceRecord) -> tuple[str, ...]:
    issues: list[str] = []
    if record.schema_version != 1:
        issues.append("schema_version must be 1")
    for name in (
        "record_id",
        "source_type",
        "source_id",
        "canonical_url",
        "retrieved_at",
        "query_id",
        "title",
        "target_submitted",
        "disease_submitted",
    ):
        if not _text(getattr(record, name)):
            issues.append(f"{name} is required")
    expected_id = f"{record.source_type}:{record.source_id}"
    if record.record_id and record.record_id != expected_id:
        issues.append(f"record_id must be {expected_id}")
    if record.canonical_url and not record.canonical_url.startswith("https://"):
        issues.append("canonical_url must use https")
    if record.sample_size is not None and record.sample_size < 0:
        issues.append("sample_size cannot be negative")
    if record.result_direction and record.has_results is False:
        issues.append("result_direction requires posted results")
    return tuple(issues)
