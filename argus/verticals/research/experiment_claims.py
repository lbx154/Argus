"""The experiment claims ledger: what the experiments actually established.

The Experiment stage ends when its evidence supports a scoped thesis. Prose
alone cannot prove that: a single-seed run, a comparison against a weak
baseline, or a difference smaller than run-to-run noise all read as a
"result" in a summary. The ledger at ``experiments/claims.json`` makes the
claim-bearing comparisons explicit so the stage machine, the Reviewer and the
Paper stage can all check the same facts:

* every claim names its metric, direction, the proposed method's arm and the
  strongest same-information baseline's arm, with mean, spread and repeat
  count;
* stochastic comparisons carry at least :data:`MIN_REPEATS` independent
  repeats per arm, and a deterministic comparison says why one run is enough;
* a claim may be called ``supported`` only when the proposed method is on the
  claimed side of the baseline by more than the run-to-run uncertainty;
* the strongest baseline must be the best baseline actually measured;
* at least one ``headline`` claim is supported on a non-synthetic benchmark;
* the ``mechanism`` map names each load-bearing component of the selected idea
  and the code path that implements it, marking simplifications, so a paper
  cannot describe an algorithm the code never ran;
* ``reference_implementations`` records the official or strongest public
  codebases that were cloned and run as references, or says why none exists.

The validator is deterministic and runs from ``stage_completion_issues`` for
the Experiment stage, so Manager cannot advance to Paper on prose alone. The
Engineer runs the same validator from the command line before reporting.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from ...core.evidence_status import is_placeholder_text

CLAIMS_PATH = "experiments/claims.json"
SCHEMA_VERSION = 1

ROLES = frozenset({"headline", "mechanism", "control", "scope", "completeness"})
DIRECTIONS = frozenset({"lower", "higher", "parity"})
STATUSES = frozenset({"supported", "refuted", "inconclusive"})
VARIATIONS = frozenset({"seeds", "splits", "folds", "bootstrap", "repeats", "none"})
MECHANISM_STATUSES = frozenset({"faithful", "simplified", "missing"})
_SYMBOL_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$")
_MAX_SYMBOL_FILE_BYTES = 4_000_000
# Independent repeats each arm needs before run-to-run spread means anything.
MIN_REPEATS = 3
# Two-sided normal quantile for the "difference exceeds uncertainty" test.
SEPARATION_Z = 1.96
_MAX_CLAIMS = 200

__all__ = [
    "CLAIMS_PATH",
    "DIRECTIONS",
    "MECHANISM_STATUSES",
    "MIN_REPEATS",
    "ROLES",
    "SCHEMA_VERSION",
    "STATUSES",
    "VARIATIONS",
    "claims_path",
    "experiment_claims_issues",
    "load_claims",
    "main",
    "template",
    "validate_claims",
]


def claims_path(project_root: Path | str) -> Path:
    return Path(project_root) / CLAIMS_PATH


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _arm_issues(arm: Any, *, label: str, needs_spread: bool) -> list[str]:
    if not isinstance(arm, dict):
        return [f"{label} must be an object with name, mean, std and n"]
    issues: list[str] = []
    if is_placeholder_text(arm.get("name")):
        issues.append(f"{label}.name is empty or templated")
    if _number(arm.get("mean")) is None:
        issues.append(f"{label}.mean must be a finite number")
    repeats = arm.get("n")
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        issues.append(f"{label}.n must be a positive integer repeat count")
    std = arm.get("std")
    if std is not None:
        parsed = _number(std)
        if parsed is None or parsed < 0:
            issues.append(f"{label}.std must be a non-negative finite number or null")
    elif needs_spread:
        issues.append(
            f"{label}.std is required when the comparison is repeated; record the "
            "spread across independent repeats"
        )
    return issues


def _better(direction: str, candidate: float, reference: float) -> bool:
    """Whether ``candidate`` beats ``reference`` in the claim's direction."""
    if direction == "lower":
        return candidate < reference
    if direction == "higher":
        return candidate > reference
    return False


def _standard_error(ours: dict[str, Any], baseline: dict[str, Any]) -> float | None:
    parts: list[float] = []
    for arm in (ours, baseline):
        std = _number(arm.get("std"))
        repeats = arm.get("n")
        if std is None or not isinstance(repeats, int) or repeats < 1:
            return None
        parts.append(std * std / repeats)
    return math.sqrt(sum(parts))


def _evidence_issues(
    claim: dict[str, Any],
    *,
    label: str,
    project_root: Path | None,
) -> list[str]:
    evidence = claim.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        return [
            f"{label}.evidence must list at least one project-relative raw result file"
        ]
    issues: list[str] = []
    for item in evidence:
        if not isinstance(item, str) or not item.strip():
            issues.append(f"{label}.evidence entries must be non-empty relative paths")
            continue
        raw = Path(item)
        if raw.is_absolute() or ".." in raw.parts:
            issues.append(
                f"{label}.evidence `{item}` must stay inside the project as a relative path"
            )
            continue
        if project_root is not None and not (Path(project_root) / raw).is_file():
            issues.append(
                f"{label}.evidence `{item}` does not exist; point at the raw result file"
            )
    return issues


def _claim_issues(
    claim: Any,
    *,
    index: int,
    project_root: Path | None,
) -> list[str]:
    label = f"claims[{index}]"
    if not isinstance(claim, dict):
        return [f"{label} must be an object"]
    claim_id = claim.get("claim_id")
    if not is_placeholder_text(claim_id):
        label = f"claim `{claim_id}`"
    issues: list[str] = []
    if is_placeholder_text(claim_id):
        issues.append(f"{label}.claim_id is empty or templated")
    if is_placeholder_text(claim.get("statement")):
        issues.append(f"{label}.statement is empty or templated")
    if is_placeholder_text(claim.get("metric")):
        issues.append(f"{label}.metric is empty or templated")
    role = claim.get("role")
    if role not in ROLES:
        issues.append(f"{label}.role must be one of {sorted(ROLES)}")
    direction = claim.get("direction")
    if direction not in DIRECTIONS:
        issues.append(f"{label}.direction must be one of {sorted(DIRECTIONS)}")
    status = claim.get("status")
    if status not in STATUSES:
        issues.append(f"{label}.status must be one of {sorted(STATUSES)}")
    if not isinstance(claim.get("synthetic"), bool):
        issues.append(
            f"{label}.synthetic must be true or false: whether the benchmark or data "
            "is synthetic rather than an established real dataset or task"
        )
    variation = claim.get("variation")
    if variation not in VARIATIONS:
        issues.append(f"{label}.variation must be one of {sorted(VARIATIONS)}")
    repeated = variation in VARIATIONS and variation != "none"
    if variation == "none" and is_placeholder_text(claim.get("deterministic_reason")):
        issues.append(
            f"{label}.deterministic_reason must explain why a single run settles this "
            "comparison when variation is `none`"
        )

    ours = claim.get("ours")
    baseline = claim.get("strongest_baseline")
    issues.extend(_arm_issues(ours, label=f"{label}.ours", needs_spread=repeated))
    issues.extend(
        _arm_issues(
            baseline, label=f"{label}.strongest_baseline", needs_spread=repeated
        )
    )
    others = claim.get("other_baselines", [])
    if not isinstance(others, list):
        issues.append(f"{label}.other_baselines must be a list of arms")
        others = []
    for position, other in enumerate(others):
        issues.extend(
            _arm_issues(
                other,
                label=f"{label}.other_baselines[{position}]",
                needs_spread=False,
            )
        )
    issues.extend(_evidence_issues(claim, label=label, project_root=project_root))
    if issues:
        return issues

    assert isinstance(ours, dict) and isinstance(baseline, dict)
    if str(ours.get("name")).strip() == str(baseline.get("name")).strip():
        issues.append(
            f"{label}: strongest_baseline must be a different method from ours"
        )
    if repeated:
        for arm_label, arm in (("ours", ours), ("strongest_baseline", baseline)):
            if int(arm["n"]) < MIN_REPEATS:
                issues.append(
                    f"{label}.{arm_label}.n={arm['n']} is below {MIN_REPEATS} independent "
                    f"{variation}; repeat the comparison or record why it is deterministic"
                )
    ours_mean = float(ours["mean"])
    base_mean = float(baseline["mean"])
    delta = ours_mean - base_mean
    if direction in {"lower", "higher"}:
        for other in others:
            if isinstance(other, dict) and _better(
                str(direction), float(other["mean"]), base_mean
            ):
                issues.append(
                    f"{label}: strongest_baseline `{baseline['name']}` is not the strongest "
                    f"measured baseline; `{other['name']}` scores better on {claim['metric']}"
                )
    if status == "supported":
        if direction == "parity":
            tolerance = _number(claim.get("tolerance"))
            if tolerance is None or tolerance <= 0:
                issues.append(
                    f"{label}: a supported parity claim needs a positive `tolerance` on "
                    f"{claim['metric']}"
                )
            elif abs(delta) > tolerance:
                issues.append(
                    f"{label}: |ours - baseline| = {abs(delta):.6g} exceeds the parity "
                    f"tolerance {tolerance:.6g}"
                )
        elif not _better(str(direction), ours_mean, base_mean):
            issues.append(
                f"{label}: status is `supported` but ours ({ours_mean:.6g}) is not "
                f"{direction} than strongest_baseline ({base_mean:.6g}) on "
                f"{claim['metric']}; record it as refuted or inconclusive, or improve "
                "the method"
            )
        elif repeated:
            error = _standard_error(ours, baseline)
            if error is not None and abs(delta) <= SEPARATION_Z * error:
                issues.append(
                    f"{label}: the difference {abs(delta):.6g} on {claim['metric']} is "
                    f"within run-to-run uncertainty ({SEPARATION_Z:g} x {error:.6g}); "
                    "record it as inconclusive or add repeats until it separates"
                )
    elif status == "refuted" and direction in {"lower", "higher"}:
        if _better(str(direction), ours_mean, base_mean):
            error = _standard_error(ours, baseline) if repeated else None
            if error is None or abs(delta) > SEPARATION_Z * error:
                issues.append(
                    f"{label}: status is `refuted` but ours beats strongest_baseline on "
                    f"{claim['metric']}; use supported or inconclusive"
                )
    return issues


def _relative_project_path(
    raw: Any,
    *,
    label: str,
    project_root: Path | None,
    must_exist: bool,
) -> tuple[Path | None, list[str]]:
    if not isinstance(raw, str) or not raw.strip():
        return None, [f"{label} must be a project-relative path"]
    path = Path(raw.strip())
    if path.is_absolute() or ".." in path.parts:
        return None, [f"{label} `{raw}` must stay inside the project as a relative path"]
    if project_root is not None:
        resolved = Path(project_root) / path
        if must_exist and not resolved.exists():
            return None, [f"{label} `{raw}` does not exist in the project"]
        return resolved, []
    return None, []


def _symbol_present(path: Path, symbol: str) -> bool:
    name = symbol.rsplit(".", 1)[-1]
    try:
        if not path.is_file() or path.stat().st_size > _MAX_SYMBOL_FILE_BYTES:
            return True
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def _mechanism_issues(
    record: dict[str, Any],
    *,
    project_root: Path | None,
) -> tuple[list[str], bool]:
    """Validate the idea-to-code map; also report whether any component is missing."""
    entries = record.get("mechanism")
    if not isinstance(entries, list) or not entries:
        return (
            [
                "mechanism must list the selected idea's load-bearing components and "
                "where each runs: component, idea_says, implemented_in `path:Symbol`, "
                "status (faithful, simplified, missing) and a note for any simplification"
            ],
            True,
        )
    issues: list[str] = []
    any_missing = False
    any_simplified = False
    for index, entry in enumerate(entries):
        label = f"mechanism[{index}]"
        if not isinstance(entry, dict):
            issues.append(f"{label} must be an object")
            any_missing = True
            continue
        component = entry.get("component")
        if not is_placeholder_text(component):
            label = f"mechanism `{component}`"
        else:
            issues.append(f"{label}.component is empty or templated")
        if is_placeholder_text(entry.get("idea_says")):
            issues.append(f"{label}.idea_says must state what the selected idea prescribes here")
        status = entry.get("status")
        if status not in MECHANISM_STATUSES:
            issues.append(f"{label}.status must be one of {sorted(MECHANISM_STATUSES)}")
            any_missing = True
            continue
        if status == "missing":
            any_missing = True
            continue
        if status == "simplified":
            any_simplified = True
            if is_placeholder_text(entry.get("note")):
                issues.append(
                    f"{label}.note must say exactly how the implementation departs from the idea"
                )
        located = str(entry.get("implemented_in") or "").strip()
        path_part, _, symbol = located.partition(":")
        resolved, path_issues = _relative_project_path(
            path_part,
            label=f"{label}.implemented_in",
            project_root=project_root,
            must_exist=True,
        )
        issues.extend(path_issues)
        if symbol:
            if not _SYMBOL_RE.match(symbol):
                issues.append(f"{label}.implemented_in symbol `{symbol}` is not a dotted identifier")
            elif resolved is not None and not _symbol_present(resolved, symbol):
                issues.append(
                    f"{label}.implemented_in names `{symbol}` but `{path_part}` does not "
                    "define or mention it"
                )
    if any_simplified and is_placeholder_text(record.get("variant")):
        issues.append(
            "variant must name the implemented variant when any mechanism component is "
            "simplified; the claims, notes and paper describe that variant, not the "
            "idealized idea"
        )
    return issues, any_missing


def _reference_issues(
    record: dict[str, Any],
    *,
    project_root: Path | None,
) -> list[str]:
    refs = record.get("reference_implementations")
    if refs is None or refs == []:
        if is_placeholder_text(record.get("no_reference_implementation_reason")):
            return [
                "reference_implementations is empty: record the official or strongest "
                "public codebases cloned and run as references (name, url, revision, "
                "local_path, used_for), or set no_reference_implementation_reason "
                "explaining why none exists for this method"
            ]
        return []
    if not isinstance(refs, list):
        return ["reference_implementations must be a list"]
    issues: list[str] = []
    for index, ref in enumerate(refs):
        label = f"reference_implementations[{index}]"
        if not isinstance(ref, dict):
            issues.append(f"{label} must be an object")
            continue
        if not is_placeholder_text(ref.get("name")):
            label = f"reference `{ref['name']}`"
        else:
            issues.append(f"{label}.name is empty or templated")
        if is_placeholder_text(ref.get("used_for")):
            issues.append(f"{label}.used_for must say what was reused or compared")
        url = str(ref.get("url") or "").strip()
        local_path = ref.get("local_path")
        if not url and not local_path:
            issues.append(f"{label} needs a url or a local_path")
        if local_path:
            _, path_issues = _relative_project_path(
                local_path,
                label=f"{label}.local_path",
                project_root=project_root,
                must_exist=True,
            )
            issues.extend(path_issues)
        if url and is_placeholder_text(ref.get("revision")):
            issues.append(f"{label}.revision must pin the commit or tag that was run")
    return issues


def validate_claims(
    record: Any,
    *,
    project_root: Path | str | None = None,
) -> list[str]:
    """Return every defect in a claims ledger; an empty list means it validates."""
    if not isinstance(record, dict):
        return [f"{CLAIMS_PATH} must be one JSON object"]
    issues: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"unsupported schema_version: {record.get('schema_version')!r}")
    claims = record.get("claims")
    if not isinstance(claims, list) or not claims:
        issues.append(
            "claims must be a non-empty list: record every claim-bearing comparison "
            "the paper will rely on"
        )
        return issues
    if len(claims) > _MAX_CLAIMS:
        issues.append(f"claims lists {len(claims)} entries; keep at most {_MAX_CLAIMS}")
        return issues
    root = Path(project_root) if project_root is not None else None
    seen: set[str] = set()
    supported_headline = False
    real_headline = False
    for index, claim in enumerate(claims):
        claim_issues = _claim_issues(claim, index=index, project_root=root)
        issues.extend(claim_issues)
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("claim_id")
        if isinstance(claim_id, str) and claim_id.strip():
            if claim_id in seen:
                issues.append(f"claim `{claim_id}` appears more than once")
            seen.add(claim_id)
        if (
            not claim_issues
            and claim.get("role") == "headline"
            and claim.get("status") == "supported"
        ):
            supported_headline = True
            if claim.get("synthetic") is False:
                real_headline = True
    mechanism_issues, any_missing = _mechanism_issues(record, project_root=root)
    issues.extend(mechanism_issues)
    if supported_headline and any_missing and not mechanism_issues:
        issues.append(
            "a headline claim is supported while a mechanism component is still "
            "`missing`: implement it, or mark it simplified and scope the claims to "
            "the implemented variant"
        )
    issues.extend(_reference_issues(record, project_root=root))
    if not supported_headline:
        issues.append(
            "no headline claim is supported: the evidence does not yet establish the "
            "thesis; improve the method or experiment, or re-derive the thesis from what "
            "the evidence does establish, before Paper"
        )
    elif not real_headline and is_placeholder_text(
        record.get("headline_synthetic_justification")
    ):
        issues.append(
            "every supported headline claim rests on synthetic data; add a supported "
            "headline claim on an established real benchmark or dataset, or record "
            "`headline_synthetic_justification` explaining why the mechanism claim is "
            "legitimately settled on synthetic evidence"
        )
    return list(dict.fromkeys(issues))


def load_claims(project_root: Path | str) -> tuple[dict[str, Any] | None, str]:
    """Read the ledger; return ``(record, problem)`` with exactly one populated."""
    path = claims_path(project_root)
    if not path.is_file():
        return None, (
            f"{CLAIMS_PATH} is missing: the Experiment stage records every "
            "claim-bearing comparison (metric, direction, ours vs the strongest "
            "same-information baseline with mean/std/n, repeats, evidence files) "
            "there before Paper"
        )
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, f"{CLAIMS_PATH} is not readable JSON: {exc}"
    if not isinstance(record, dict):
        return None, f"{CLAIMS_PATH} must be one JSON object"
    return record, ""


def experiment_claims_issues(
    project_root: Path | str,
    *,
    require: bool = True,
) -> tuple[str, ...]:
    """Deterministic ledger check for the stage machine.

    With ``require=False`` a missing ledger is not an issue; a present but
    invalid one still is, so a later stage cannot keep a ledger that the
    method changes have falsified.
    """
    if not require and not claims_path(project_root).is_file():
        return ()
    record, problem = load_claims(project_root)
    if record is None:
        return (problem,)
    return tuple(
        f"{CLAIMS_PATH}: {issue}"
        for issue in validate_claims(record, project_root=project_root)
    )


def template() -> dict[str, Any]:
    arm = {"name": "REPLACE method name", "mean": 0.0, "std": 0.0, "n": MIN_REPEATS}
    return {
        "schema_version": SCHEMA_VERSION,
        "mechanism": [
            {
                "component": "REPLACE with a load-bearing component of the selected idea",
                "idea_says": "REPLACE with what the idea prescribes for this component",
                "implemented_in": "REPLACE with src/module.py:Class.method",
                "status": "faithful",
                "note": "",
            }
        ],
        "variant": "",
        "reference_implementations": [
            {
                "name": "REPLACE with the official or strongest public codebase",
                "url": "https://REPLACE",
                "revision": "REPLACE with the commit or tag that was run",
                "local_path": "third_party/REPLACE",
                "used_for": "REPLACE with what was reused or compared against",
            }
        ],
        "claims": [
            {
                "claim_id": "headline-1",
                "statement": "REPLACE with the exact scoped claim the paper will make",
                "role": "headline",
                "metric": "REPLACE with the metric name and unit",
                "direction": "lower",
                "dataset": "REPLACE with the benchmark or dataset and split",
                "synthetic": False,
                "variation": "seeds",
                "seeds": [0, 1, 2],
                "ours": dict(arm),
                "strongest_baseline": dict(arm),
                "other_baselines": [],
                "evidence": ["REPLACE with experiments/<attempt>/results.json"],
                "command": "REPLACE with the exact command that produced the evidence",
                "status": "inconclusive",
            }
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m argus.verticals.research.experiment_claims",
        description=(
            "Validate or template the experiment claims ledger "
            f"({CLAIMS_PATH}); the Experiment stage cannot advance until it validates."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate", help="report every defect in the ledger")
    validate.add_argument("--project-root", type=Path, default=Path("."))
    sub.add_parser("template", help="print a ledger skeleton to fill in")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "template":
        print(json.dumps(template(), indent=2, ensure_ascii=False))
        return 0
    issues = experiment_claims_issues(args.project_root)
    if issues:
        for issue in issues:
            print(issue, file=sys.stderr)
        return 1
    print(f"{CLAIMS_PATH} validates")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
