"""Deterministic target-disease evidence dossier artifacts."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .connectors import (
    RetrievalBatch,
    fetch_clinical_trials,
    fetch_pubmed,
    medical_query_id,
)
from .evidence import (
    EvidenceRecord,
    MedicalScope,
    normalize_clinical_trials,
    normalize_pubmed,
    validate_evidence_record,
)

_REQUIRED_FILES = (
    "scope.json",
    "queries.jsonl",
    "evidence.jsonl",
    "evidence_matrix.csv",
    "target_disease_memo.md",
    "review.json",
)
_MATRIX_FIELDS = (
    "source_type",
    "source_id",
    "title",
    "date",
    "status",
    "evidence_class",
    "study_design",
    "population_or_model",
    "intervention_or_exposure",
    "comparator",
    "endpoint",
    "sample_size",
    "follow_up",
    "scope_population",
    "registry_last_update",
    "data_cutoff",
    "result_direction",
    "canonical_url",
    "raw_artifact",
    "limitations",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_text(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name} line {line_number} is not an object")
        rows.append(value)
    return rows


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    text = "".join(
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n" for row in rows
    )
    _atomic_write(path, text)


def _append_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    existing = _read_jsonl(path)
    _write_jsonl(path, (*existing, *(dict(row) for row in rows)))


def _scope_matches(existing: Mapping[str, Any], scope: MedicalScope) -> bool:
    expected = scope.to_dict()
    for field, expected_value in expected.items():
        existing_value = existing.get(field)
        if field in {"target", "disease"}:
            if str(existing_value or "").strip().casefold() != str(
                expected_value
            ).casefold():
                return False
            continue
        if field in {"target_aliases", "disease_aliases"}:
            existing_aliases = tuple(
                str(value).strip().casefold()
                for value in (existing_value or ())
                if str(value).strip()
            )
            expected_aliases = tuple(
                str(value).strip().casefold()
                for value in expected_value
                if str(value).strip()
            )
            if existing_aliases != expected_aliases:
                return False
            continue
        if str(existing_value or "").strip() != str(expected_value or "").strip():
            return False
    return True


def _fixture_batch(
    source_type: str,
    payload: Mapping[str, Any],
    *,
    scope: MedicalScope,
    retrieved_at: str,
) -> RetrievalBatch:
    query_id = medical_query_id(source_type.replace("_", "-"), scope)
    if source_type == "pubmed":
        evidence = normalize_pubmed(
            payload,
            scope=scope,
            query_id=query_id,
            retrieved_at=retrieved_at,
        )
    else:
        evidence = normalize_clinical_trials(
            payload,
            scope=scope,
            query_id=query_id,
            retrieved_at=retrieved_at,
        )
    return RetrievalBatch(
        source_type=source_type,
        query_id=query_id,
        query_urls=(f"fixture://{source_type}",),
        raw_payloads=(payload,),
        evidence=evidence,
        failures=(),
    )


def _raw_path(directory: Path, query_id: str, retrieved_at: str, index: int) -> Path:
    stamp = re.sub(r"[^0-9A-Za-z]+", "-", retrieved_at).strip("-") or "retrieval"
    stem = f"{stamp}-{query_id}-{index + 1}"
    candidate = directory / f"{stem}.json"
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{stem}-{suffix}.json"
        suffix += 1
    return candidate


def _query_row(
    batch: RetrievalBatch,
    retrieved_at: str,
    raw_artifacts: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "source_type": batch.source_type,
        "query_id": batch.query_id,
        "query_urls": list(batch.query_urls),
        "retrieved_at": retrieved_at,
        "raw_payload_count": len(batch.raw_payloads),
        "raw_artifacts": list(raw_artifacts),
        "evidence_count": len(batch.evidence),
        "infrastructure_failure_count": len(batch.failures),
    }


def _write_matrix(path: Path, records: Sequence[EvidenceRecord]) -> None:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_MATRIX_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for record in records:
        row = record.to_dict()
        row["limitations"] = " | ".join(record.limitations)
        writer.writerow(row)
    _atomic_write(path, buffer.getvalue())


def _render_memo(
    scope: MedicalScope,
    records: Sequence[EvidenceRecord],
    failure_count: int,
) -> str:
    counts = Counter(record.source_type for record in records)
    lines = [
        "# Target-Disease Research Memo",
        "",
        "**Status:** Pending independent Argus review",
        "",
        (
            "This dossier supports research and portfolio decisions; it is not "
            "diagnosis or treatment advice."
        ),
        "",
        "## Scope",
        "",
        f"- Target: {scope.target}",
        f"- Disease: {scope.disease}",
        f"- Population: {scope.population or 'Not specified'}",
        f"- Decision question: {scope.decision_question or 'Not specified'}",
        "",
        "## Evidence Inventory",
        "",
        f"- PubMed records: {counts.get('pubmed', 0)}",
        f"- ClinicalTrials.gov records: {counts.get('clinical_trials', 0)}",
        f"- Infrastructure failures: {failure_count}",
        "",
        "| Source | ID | Title | Status | URL |",
        "|---|---|---|---|---|",
    ]
    for record in records:
        title = record.title.replace("|", "\\|")
        lines.append(
            f"| {record.source_type} | {record.source_id} | {title} | "
            f"{record.status or ''} | {record.canonical_url} |"
        )
    if not records:
        lines.append("| - | - | No usable evidence records retrieved | - | - |")
    lines.extend(
        (
            "",
            "## Interpretation Boundary",
            "",
            "This deterministic memo is an evidence inventory, not a biomedical "
            "conclusion. PubMed summaries are metadata rather than verified full text, "
            "and trial registration does not establish efficacy. Mechanism, safety, "
            "comparability, conflicts, and claim wording require independent review.",
            "",
        )
    )
    return "\n".join(lines)


def build_target_disease_dossier(
    project_root: Path | str,
    *,
    target: str,
    disease: str,
    target_aliases: Sequence[str] = (),
    disease_aliases: Sequence[str] = (),
    population: str = "",
    intervention_class: str = "",
    date_from: str = "",
    date_to: str = "",
    decision_question: str = "",
    output_language: str = "",
    pubmed_payload: Mapping[str, Any] | None = None,
    clinical_trials_payload: Mapping[str, Any] | None = None,
    retrieved_at: str | None = None,
    live: bool = False,
    opener: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser()
    if not root.is_dir():
        raise ValueError(f"project_root is not an existing directory: {root}")
    scope = MedicalScope(
        target=target,
        disease=disease,
        target_aliases=tuple(target_aliases),
        disease_aliases=tuple(disease_aliases),
        population=population,
        intervention_class=intervention_class,
        date_from=date_from,
        date_to=date_to,
        decision_question=decision_question,
        output_language=output_language,
    )
    timestamp = retrieved_at or _utc_now()
    medical = root / "medical"
    medical.mkdir(parents=True, exist_ok=True)
    scope_path = medical / "scope.json"
    if scope_path.is_file():
        existing_scope = json.loads(scope_path.read_text(encoding="utf-8"))
        if not isinstance(existing_scope, Mapping) or not _scope_matches(
            existing_scope, scope
        ):
            raise ValueError(
                "existing medical scope differs from the requested scope; "
                "use a separate Argus project"
            )
    scope_payload = {"schema_version": 1, **scope.to_dict()}
    _atomic_write(scope_path, _json_text(scope_payload))

    batches: list[RetrievalBatch] = []
    if pubmed_payload is not None:
        batches.append(
            _fixture_batch(
                "pubmed", pubmed_payload, scope=scope, retrieved_at=timestamp
            )
        )
    elif live:
        batches.append(
            fetch_pubmed(scope, opener=opener, retrieved_at=timestamp)
        )
    if clinical_trials_payload is not None:
        batches.append(
            _fixture_batch(
                "clinical_trials",
                clinical_trials_payload,
                scope=scope,
                retrieved_at=timestamp,
            )
        )
    elif live:
        batches.append(
            fetch_clinical_trials(scope, opener=opener, retrieved_at=timestamp)
        )

    query_rows: list[dict[str, Any]] = []
    new_records: list[EvidenceRecord] = []
    failure_rows: list[dict[str, Any]] = []
    for batch in batches:
        failure_rows.extend(asdict(failure) for failure in batch.failures)
        raw_dir = medical / "raw" / batch.source_type
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_artifacts: list[str] = []
        for index, payload in enumerate(batch.raw_payloads):
            raw_path = _raw_path(raw_dir, batch.query_id, timestamp, index)
            _atomic_write(
                raw_path,
                _json_text(dict(payload)),
            )
            raw_artifacts.append(raw_path.relative_to(root).as_posix())
        query_rows.append(_query_row(batch, timestamp, raw_artifacts))
        evidence_raw = raw_artifacts[-1] if raw_artifacts else ""
        new_records.extend(
            replace(record, raw_artifact=evidence_raw) for record in batch.evidence
        )

    _append_jsonl(medical / "queries.jsonl", query_rows)
    failure_path = medical / "infrastructure_failures.jsonl"
    if failure_rows:
        _append_jsonl(failure_path, failure_rows)
    all_failure_rows = _read_jsonl(failure_path)
    _append_jsonl(
        medical / "evidence_history.jsonl",
        [record.to_dict() for record in new_records],
    )

    current_path = medical / "evidence.jsonl"
    current_records: dict[str, EvidenceRecord] = {}
    for payload in _read_jsonl(current_path):
        record = EvidenceRecord.from_dict(payload)
        current_records[record.record_id] = record
    for record in new_records:
        current_records[record.record_id] = record
    records = tuple(current_records.values())
    _write_jsonl(current_path, [record.to_dict() for record in records])
    _write_matrix(medical / "evidence_matrix.csv", records)

    review_path = medical / "review.json"
    if review_path.is_file():
        prior_review = json.loads(review_path.read_text(encoding="utf-8"))
        if isinstance(prior_review, Mapping):
            _append_jsonl(
                medical / "review_history.jsonl",
                [{**dict(prior_review), "superseded_at": timestamp}],
            )
    review = {"schema_version": 1, "status": "pending_review", "certified": False}
    _atomic_write(review_path, _json_text(review))
    _atomic_write(
        medical / "target_disease_memo.md",
        _render_memo(scope, records, len(all_failure_rows)),
    )

    usable_source_count = sum(
        1 for batch in batches if batch.raw_payloads and not batch.failures
    )
    return {
        "project_root": str(root.resolve()),
        "medical_root": str(medical.resolve()),
        "evidence_count": len(records),
        "new_evidence_count": len(new_records),
        "usable_source_count": usable_source_count,
        "new_infrastructure_failure_count": len(failure_rows),
        "infrastructure_failure_count": len(all_failure_rows),
        "review_status": "pending_review",
    }


def _project_artifact_status(root: Path, raw_path: str) -> str:
    text = str(raw_path or "").strip()
    relative = Path(text)
    if not text or relative.is_absolute() or ".." in relative.parts:
        return "outside"
    try:
        resolved = (root / relative).resolve()
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return "outside"
    return "ok" if resolved.is_file() else "missing"


def validate_dossier(project_root: Path | str) -> tuple[str, ...]:
    root = Path(project_root).expanduser().resolve()
    medical = root / "medical"
    issues: list[str] = []
    for name in _REQUIRED_FILES:
        if not (medical / name).is_file():
            issues.append(f"missing {name}")

    scope_path = medical / "scope.json"
    if scope_path.is_file():
        try:
            scope = json.loads(scope_path.read_text(encoding="utf-8"))
            if not isinstance(scope, dict):
                issues.append("scope.json is not a JSON object")
            else:
                for field in ("target", "disease"):
                    if not str(scope.get(field) or "").strip():
                        issues.append(f"scope.json missing {field}")
        except json.JSONDecodeError:
            issues.append("scope.json is not valid JSON")

    records: list[EvidenceRecord] = []
    evidence_path = medical / "evidence.jsonl"
    if evidence_path.is_file():
        try:
            payloads = _read_jsonl(evidence_path)
            if not payloads:
                issues.append("evidence.jsonl contains no records")
            for index, payload in enumerate(payloads, start=1):
                try:
                    record = EvidenceRecord.from_dict(payload)
                except (TypeError, ValueError) as exc:
                    issues.append(f"evidence.jsonl line {index} is invalid: {exc}")
                    continue
                records.append(record)
                issues.extend(
                    f"evidence.jsonl line {index}: {issue}"
                    for issue in validate_evidence_record(record)
                )
                raw_artifact = str(record.raw_artifact or "").strip()
                if not raw_artifact:
                    issues.append(
                        f"evidence.jsonl line {index}: raw_artifact is required"
                    )
                elif _project_artifact_status(root, raw_artifact) == "outside":
                    issues.append(
                        f"evidence.jsonl line {index}: raw_artifact is outside project"
                    )
                elif _project_artifact_status(root, raw_artifact) == "missing":
                    issues.append(
                        f"evidence.jsonl line {index}: raw_artifact does not exist"
                    )
        except ValueError as exc:
            issues.append(f"evidence.jsonl is invalid: {exc}")

    matrix_path = medical / "evidence_matrix.csv"
    if matrix_path.is_file():
        try:
            with matrix_path.open(encoding="utf-8", newline="") as handle:
                matrix_rows = list(csv.DictReader(handle))
            if records and len(matrix_rows) != len(records):
                issues.append("evidence_matrix.csv row count does not match evidence.jsonl")
        except (OSError, csv.Error) as exc:
            issues.append(f"evidence_matrix.csv is invalid: {exc}")

    memo_path = medical / "target_disease_memo.md"
    if memo_path.is_file():
        memo = " ".join(memo_path.read_text(encoding="utf-8").casefold().split())
        if "not diagnosis or treatment advice" not in memo:
            issues.append("target_disease_memo.md missing non-diagnostic boundary")

    review_path = medical / "review.json"
    if review_path.is_file():
        try:
            review = json.loads(review_path.read_text(encoding="utf-8"))
            if not isinstance(review, dict):
                issues.append("review.json is not a JSON object")
            elif not isinstance(review.get("certified"), bool):
                issues.append("review.json certified must be boolean")
        except json.JSONDecodeError:
            issues.append("review.json is not valid JSON")

    queries_path = medical / "queries.jsonl"
    if queries_path.is_file():
        try:
            query_rows = _read_jsonl(queries_path)
            if not query_rows:
                issues.append("queries.jsonl contains no records")
            for index, row in enumerate(query_rows, start=1):
                raw_artifacts = row.get("raw_artifacts")
                if not isinstance(raw_artifacts, list):
                    issues.append(
                        f"queries.jsonl line {index}: raw_artifacts must be a list"
                    )
                    continue
                for raw in raw_artifacts:
                    raw_status = _project_artifact_status(root, str(raw or ""))
                    if raw_status == "outside":
                        issues.append(
                            f"queries.jsonl line {index}: raw artifact is outside project"
                        )
                    elif raw_status == "missing":
                        issues.append(
                            f"queries.jsonl line {index}: raw artifact does not exist"
                        )
        except ValueError as exc:
            issues.append(f"queries.jsonl is invalid: {exc}")
    return tuple(issues)


def load_optional_json(path: str) -> Mapping[str, Any] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"fixture is not a JSON object: {path}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build an auditable target-disease dossier")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--disease", required=True)
    parser.add_argument("--target-alias", action="append", default=[])
    parser.add_argument("--disease-alias", action="append", default=[])
    parser.add_argument("--population", default="")
    parser.add_argument("--intervention-class", default="")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--decision-question", default="")
    parser.add_argument("--output-language", default="")
    parser.add_argument("--pubmed-fixture", default="")
    parser.add_argument("--clinical-trials-fixture", default="")
    parser.add_argument("--retrieved-at", default="")
    parser.add_argument("--live", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = build_target_disease_dossier(
            args.project_root,
            target=args.target,
            disease=args.disease,
            target_aliases=args.target_alias,
            disease_aliases=args.disease_alias,
            population=args.population,
            intervention_class=args.intervention_class,
            date_from=args.date_from,
            date_to=args.date_to,
            decision_question=args.decision_question,
            output_language=args.output_language,
            pubmed_payload=load_optional_json(args.pubmed_fixture),
            clinical_trials_payload=load_optional_json(
                args.clinical_trials_fixture
            ),
            retrieved_at=args.retrieved_at or None,
            live=args.live,
        )
    except (OSError, ValueError) as exc:
        print(f"medical dossier failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["usable_source_count"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
