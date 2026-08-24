"""Evaluation-contamination gate for claim-bearing research evidence.

This gate prevents a training set from silently reappearing in the evaluation
set that carries a paper claim. In campaign run-03, BCPO-v10 trained on 130
unique prompts and then reported 0.792 pass@1 on a 500-prompt MATH-500 rollout;
all 130 training prompts were in that rollout (100% of training and 26% of
evaluation), yet the overlap reached the manuscript and final certification.

An initial blanket declaration requirement was the wrong shape. It broke
mid-campaign compatibility for run-01, run-04, run-05, and run-06-control even
though they already satisfied the publication-scale gate, and run-04's frozen-
model token-frontier measurements have no training set to declare. Omission is
therefore allowed when the row itself gives no sign that training is involved.
If ``source_type``, ``claim``, ``artifacts``, or ``arm_configs`` indicates a
trained artifact, the paired declaration is required and the triggering field
is reported.

Campaigns declare artifact paths, never a self-attested contamination boolean.
The harness reads a complete train/evaluation pair, extracts identifiers, and
computes the intersection. Partial or unreadable declarations and unusable
identifiers fail closed rather than certifying an overlap that could not be
measured.
"""
from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .publication_scale import ASSESSMENT_PATH

IDENTIFIER_KEYS = (
    "prompt_id",
    "problem_id",
    "unique_id",
    "id",
    "idx",
    "question_id",
)
_TRAINING_SIGNAL = re.compile(
    r"(?:(?<![A-Za-z0-9])(?:train(?:ed|ing)?|fine[-_\s]?tun(?:e|ed|ing)|"
    r"finetun(?:e|ed|ing)|sft|dpo|lora|adapters?|checkpoints?)(?![A-Za-z0-9])|"
    r"\.(?:safetensors|pt|ckpt)(?![A-Za-z0-9]))",
    re.IGNORECASE,
)


def _declared_file(project_root: Path, raw: Any) -> tuple[Path | None, str]:
    value = str(raw or "").strip()
    if not value:
        return None, "declared artifact path is empty"
    candidate = Path(value).expanduser()
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (project_root / candidate).resolve()
    )
    try:
        relative = resolved.relative_to(project_root.resolve())
    except ValueError:
        return None, f"declared artifact escapes project root: {value}"
    if not resolved.is_file():
        return None, f"declared artifact does not exist: {relative.as_posix()}"
    return resolved, ""


def _records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".jsonl":
        rows: list[Any] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at line {line_number}: {exc}") from exc
    elif path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError("JSON artifact must be a list of objects")
    else:
        raise ValueError("artifact must use .jsonl or .json")
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("artifact records must all be objects")
    return rows


def _identifiers(path: Path) -> set[str]:
    try:
        rows = _records(path)
    except (OSError, ValueError) as exc:
        raise ValueError(str(exc)) from exc

    identifiers: set[str] = set()
    for index, row in enumerate(rows):
        key = next((candidate for candidate in IDENTIFIER_KEYS if candidate in row), None)
        if key is None:
            raise ValueError(
                f"record {index} has none of the supported identifier keys: "
                + ", ".join(IDENTIFIER_KEYS)
            )
        value = row[key]
        if value is None or isinstance(value, (dict, list)):
            raise ValueError(f"record {index} has an unusable {key} identifier")
        identifiers.add(str(value))
    return identifiers


def _display_path(project_root: Path, path: Path) -> str:
    return path.relative_to(project_root.resolve()).as_posix()


def _field_values(field: str, value: Any) -> Iterator[tuple[str, str]]:
    """Yield leaf field paths and values from a row field."""
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _field_values(f"{field}.{key}", child)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _field_values(f"{field}[{index}]", child)
    elif isinstance(value, (str, Path)):
        yield field, str(value)


def _training_signal(row: dict[str, Any]) -> tuple[str, str] | None:
    """Return the first row-local field that indicates a trained artifact."""
    for field in ("source_type", "claim", "artifacts", "arm_configs"):
        if field not in row:
            continue
        for leaf, value in _field_values(field, row[field]):
            # An arm-config key such as ``training_config`` is itself evidence,
            # even when its value is a generically named JSON path.
            searchable = f"{leaf} {value}" if field == "arm_configs" else value
            if _TRAINING_SIGNAL.search(searchable):
                return leaf, value
    return None


def contamination_issues(
    project_root: Path,
    *,
    assessment_path: Path = ASSESSMENT_PATH,
) -> tuple[str, ...]:
    """Return blocking issues computed from declared train/evaluation artifacts."""
    root = project_root.resolve()
    path = assessment_path if assessment_path.is_absolute() else root / assessment_path
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # publication_scale_issues owns whether this contract is required for a
        # target. There is no contamination declaration to evaluate here.
        return ()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return (f"unreadable {assessment_path.as_posix()}: {exc}",)
    if not isinstance(payload, dict):
        return (f"{assessment_path.as_posix()} must be a JSON object",)

    rows = payload.get("claim_bearing_evidence")
    if not isinstance(rows, list):
        return ()

    issues: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        prefix = f"claim_bearing_evidence[{index}]"
        has_training = "training_artifacts" in row or "training_artifact" in row
        has_evaluation = "evaluation_artifact" in row

        if has_training != has_evaluation:
            missing = (
                "evaluation_artifact" if has_training else "training_artifacts"
            )
            issues.append(
                f"{prefix}.{missing} must be declared to complete the partial "
                "training/evaluation artifact pair"
            )
            continue
        if not has_training:
            signal = _training_signal(row)
            if signal is not None:
                field, value = signal
                issues.append(
                    f"{prefix}.training_artifacts and {prefix}.evaluation_artifact "
                    f"must be declared because {prefix}.{field} indicates training: "
                    f"{json.dumps(value)}"
                )
            continue

        raw_training = row.get("training_artifacts", row.get("training_artifact"))
        if isinstance(raw_training, (str, Path)):
            raw_training = [raw_training]
        if not isinstance(raw_training, list) or not raw_training:
            issues.append(
                f"{prefix}.training_artifacts must declare at least one artifact path"
            )
            continue
        if not str(row.get("evaluation_artifact") or "").strip():
            issues.append(f"{prefix}.evaluation_artifact must declare one artifact path")
            continue

        training_paths: list[Path] = []
        declaration_failed = False
        for raw_path in raw_training:
            resolved, error = _declared_file(root, raw_path)
            if error:
                issues.append(f"{prefix}: {error}")
                declaration_failed = True
            elif resolved is not None:
                training_paths.append(resolved)
        evaluation_path, error = _declared_file(root, row.get("evaluation_artifact"))
        if error:
            issues.append(f"{prefix}: {error}")
            declaration_failed = True
        if declaration_failed or evaluation_path is None:
            continue

        training_ids: set[str] = set()
        identifiers_failed = False
        for training_path in training_paths:
            try:
                training_ids.update(_identifiers(training_path))
            except ValueError as exc:
                issues.append(
                    f"{prefix}: unreadable declared training artifact "
                    f"{_display_path(root, training_path)}: {exc}"
                )
                identifiers_failed = True
        try:
            evaluation_ids = _identifiers(evaluation_path)
        except ValueError as exc:
            issues.append(
                f"{prefix}: unreadable declared evaluation artifact "
                f"{_display_path(root, evaluation_path)}: {exc}"
            )
            identifiers_failed = True
            evaluation_ids = set()
        if identifiers_failed:
            continue

        overlap = training_ids & evaluation_ids
        if overlap:
            training_fraction = len(overlap) / len(training_ids) if training_ids else 0.0
            evaluation_fraction = (
                len(overlap) / len(evaluation_ids) if evaluation_ids else 0.0
            )
            training_display = ", ".join(
                _display_path(root, item) for item in training_paths
            )
            issues.append(
                f"{prefix}: evaluation contamination: {len(overlap)} identifier(s) "
                f"overlap ({training_fraction:.1%} of training; "
                f"{evaluation_fraction:.1%} of evaluation) between training "
                f"artifact(s) [{training_display}] and evaluation artifact "
                f"{_display_path(root, evaluation_path)}"
            )

    return tuple(dict.fromkeys(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    issues = contamination_issues(args.project_root)
    if args.json:
        print(json.dumps({"ok": not issues, "issues": list(issues)}, indent=2))
    elif issues:
        for issue in issues:
            print(f"ERROR: {issue}")
    else:
        print("evaluation contamination: PASS")
    return 0 if not issues else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
