#!/usr/bin/env python3
"""Content-contract, OCR, and validation for the eight AI-redrawn report figures.

This module defines the exact per-figure semantic contracts approved in
``docs/superpowers/specs/2026-07-15-ai-redraw-all-report-figures-design.md``
and validates already-generated figures against them. It intentionally does
**not** draw, render, or generate any image: it only reads committed PNG
bytes and their sidecar evidence files (prompt, generation sidecar, inspect,
provenance, review, content-review, and OCR sidecars) and reports pass/fail.

Public surface:

- ``FIGURE_CONTRACTS``: the exact eight figure contracts, keyed by stem.
- ``normalize_ocr(text)``: whitespace + multiplication/dash Unicode
  normalization only. Digits and decimal points are never altered.
- ``run_tesseract(image)``: runs Tesseract with ``--psm 6``, ``11``, and
  ``12`` and returns every raw and normalized transcript.
- ``validate_figure(root, figure_id)``: validates one figure's dimensions,
  sidecars, review acceptance, and OCR/data-token coverage.
- ``write_validation_manifest(root)``: validates all eight figures and writes
  ``technical_report/figures/AI_FIGURE_VALIDATION.json``.

CLI:

    python -m technical_report.figures.validate_ai_figures ocr --stem NAME
    python -m technical_report.figures.validate_ai_figures validate --stem NAME
    python -m technical_report.figures.validate_ai_figures validate-all --write-manifest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[2]

FIGURES_DIR_NAME = "technical_report/figures"
EVIDENCE_DIR_NAME = "technical_report/evidence"

TESSERACT_BIN = "tesseract"
PSM_MODES: tuple[int, ...] = (6, 11, 12)

REQUIRED_WIDTH = 1536
REQUIRED_HEIGHT = 1024

# Sidecar suffixes shared by every figure, plus the data-figure-only
# independent exact-content review. Order matters only for readability; the
# CLI/tests treat this as a set.
_COMMON_SIDECAR_SUFFIXES: tuple[str, ...] = (
    "prompt.txt",
    "png.json",
    "inspect.json",
    "provenance.json",
    "review.json",
    "ocr.txt",
    "ocr.json",
)
_DATA_FIGURE_SIDECAR_SUFFIXES: tuple[str, ...] = _COMMON_SIDECAR_SUFFIXES + (
    "content-review.json",
)

# Unicode code points that Tesseract (or a font) may render in place of a
# plain multiplication sign. Normalized to ascii "x" so contract tokens and
# OCR output compare equal regardless of glyph choice.
_MULTIPLICATION_VARIANTS: str = (
    "\u00d7"  # × MULTIPLICATION SIGN
    "\u2715"  # ✕ MULTIPLICATION X
    "\u2716"  # ✖ HEAVY MULTIPLICATION X
    "\u2a2f"  # ⨯ VECTOR OR CROSS PRODUCT
    "\u2062"  # ⁢ INVISIBLE TIMES
)

# Unicode code points that stand in for a plain hyphen-minus. Normalized to
# ascii "-". Digits and "." are never members of either variant set.
_DASH_VARIANTS: str = (
    "\u2010"  # ‐ HYPHEN
    "\u2011"  # ‑ NON-BREAKING HYPHEN
    "\u2012"  # ‒ FIGURE DASH
    "\u2013"  # – EN DASH
    "\u2014"  # — EM DASH
    "\u2015"  # ― HORIZONTAL BAR
    "\u2212"  # − MINUS SIGN
)

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_ocr(text: str | None) -> str:
    """Normalize whitespace and multiplication/dash glyph variants only.

    Digits and decimal points are never modified: this function never maps,
    strips, or otherwise alters any ``0``-``9`` character or ``.``. It exists
    so that OCR transcripts and contract tokens can be compared for exact
    (not fuzzy) equality regardless of incidental glyph or spacing choices.
    """
    if not text:
        return ""
    normalized = text
    for variant in _MULTIPLICATION_VARIANTS:
        normalized = normalized.replace(variant, "x")
    for variant in _DASH_VARIANTS:
        normalized = normalized.replace(variant, "-")
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


@dataclass(frozen=True)
class FigureContract:
    """The exact content contract for one of the eight visible figures."""

    stem: str
    figure_id: str
    title: str
    data_figure: bool
    required_labels: tuple[str, ...]
    status_counts: dict[str, int] | None = None
    source_evidence: tuple[str, ...] = field(default_factory=tuple)

    @property
    def sidecar_suffixes(self) -> tuple[str, ...]:
        if self.data_figure:
            return _DATA_FIGURE_SIDECAR_SUFFIXES
        return _COMMON_SIDECAR_SUFFIXES


def _contract(**kwargs: Any) -> FigureContract:
    return FigureContract(**kwargs)


# The exact eight visible figures from the approved design spec, in the order
# they are enumerated there. Keys are the snake_case file stem (matching
# "<stem>.png"); ``figure_id`` is the kebab-case id used in provenance
# manifests such as IMAGE2_FIGURES.json.
FIGURE_CONTRACTS: dict[str, FigureContract] = {
    "master_spine": _contract(
        stem="master_spine",
        figure_id="master-spine",
        title="Master Spine",
        data_figure=False,
        required_labels=(
            "Every run expands the frontier.",
            "Unknown objective",
            "Dense Intelligence Runtime",
            "Evidence Gate",
            "Runtime Evolution",
            "Expanded OOD Frontier",
            "Manager",
            "Planner",
            "Engineer",
            "Reviewer",
            "Memory",
            "Skills",
            "Tools",
            "Verifiers",
            "Routing",
            "Evaluations",
            "model parameters remain fixed",
            "capability is not guaranteed to grow every run",
        ),
    ),
    "dense_intelligence": _contract(
        stem="dense_intelligence",
        figure_id="dense-intelligence",
        title="Dense Intelligence",
        data_figure=False,
        required_labels=(
            "Dense Intelligence",
            "Episodic research",
            "Argus Life",
            "decision",
            "execution",
            "verification",
            "state retention",
            "conceptual model \u00b7 not a reported benchmark",
        ),
    ),
    "system_planes": _contract(
        stem="system_planes",
        figure_id="system-planes",
        title="Three Planes",
        data_figure=False,
        required_labels=(
            "Control Plane",
            "Execution Plane",
            "Evidence Plane",
            "Manager",
            "Planner",
            "LifeSupervisor",
            "SkillLoop",
            "Engineer",
            "Reviewer",
            "Run Gateway",
            "Event Tape",
            "Usage Ledger",
            "Credential Redaction",
            "Provenance",
            "112 typed events",
        ),
    ),
    "argus_architecture": _contract(
        stem="argus_architecture",
        figure_id="argus-architecture",
        title="Argus Architecture",
        data_figure=False,
        required_labels=(
            "Argus",
            "Operator objective",
            "Persistent research runtime",
            "Manager",
            "Planner",
            "Engineer",
            "Reviewer",
            "Manager: front door and stage authority",
            "Reviewer: completion authority",
            "Inspectable artifacts and evidence",
        ),
    ),
    "mission_lifecycle": _contract(
        stem="mission_lifecycle",
        figure_id="mission-lifecycle",
        title="Mission Lifecycle",
        data_figure=False,
        required_labels=(
            "Claim backlog item",
            "pending \u2192 running",
            "Run mission",
            "Engineer \u2194 Reviewer",
            "bounded session reuse",
            "Reviewer verdict",
            "done",
            "continue",
            "Plan next work",
            "Backlog / continuous",
            "paused",
            "blocked",
            "replan_requested",
            "drain to mission boundary",
        ),
    ),
    "long_horizon_reliability": _contract(
        stem="long_horizon_reliability",
        figure_id="long-horizon-reliability",
        title="Long-Horizon Reliability",
        data_figure=False,
        required_labels=(
            "Argus long-horizon cycle",
            "Planner",
            "Engineer",
            "Reviewer",
            "Checkpoint",
            "Decision progress",
            "Supervised background jobs",
            "run independently",
            "Safe round boundary",
            "No new decision",
            "1,800 s decision budget",
            "Return to Planner",
            "Budget",
            "Event log",
            "Artifacts",
            "Process liveness",
        ),
    ),
    "public_results": _contract(
        stem="public_results",
        figure_id="public-results",
        title="Public Results",
        data_figure=True,
        required_labels=(
            "NVIDIA SOL-ExecBench",
            "Global #6",
            "2\u00d7 #1",
            "7 top-3",
            "nanochat \u00b7 B200",
            "0.9636 BPB",
            "Human SOTA 0.9646",
            "nanochat \u00b7 H100",
            "0.9855 BPB",
            "Human SOTA 0.9879",
            "nanoGPT speedrun",
            "79.77 s",
            "Human #83 80.18 s",
            "AARRI-Bench",
            "63/82",
            "76.8%",
            "Paper best 68.3%",
            "Arbor \u00b7 RUC NLPIR",
            "28.0 gap",
            "Arbor 20.83",
            "Claude Code 8.33",
            "Codex 6.25",
        ),
        status_counts={"artifact digest": 2, "website snapshot": 4},
        source_evidence=(f"{EVIDENCE_DIR_NAME}/website_results.json",),
    ),
    "paper_portfolio": _contract(
        stem="paper_portfolio",
        figure_id="paper-portfolio",
        title="Paper Portfolio",
        data_figure=True,
        required_labels=(
            "Research Portfolio",
            "41 papers",
            "35 manuscripts",
            "6 drafts",
            "Multimodal & Vision-Language Models 16",
            "Cognitive Bias in LLMs 9",
            "Efficiency, Compression & Decoding 7",
            "LLM Agent Methods 5",
            "World Models 2",
            "State Trace & Auditability 2",
            "output inventory \u00b7 not accepted papers",
        ),
        source_evidence=(f"{EVIDENCE_DIR_NAME}/paper_inventory.json",),
    ),
}

# Every stem is reachable by either its snake_case stem or its kebab-case
# figure_id, so CLI callers and provenance-manifest consumers can use either
# spelling interchangeably.
_STEM_BY_ANY_ID: dict[str, str] = {}
for _stem, _contract_obj in FIGURE_CONTRACTS.items():
    _STEM_BY_ANY_ID[_stem] = _stem
    _STEM_BY_ANY_ID[_contract_obj.figure_id] = _stem
del _stem, _contract_obj


def _resolve_stem(identifier: str) -> str:
    try:
        return _STEM_BY_ANY_ID[identifier]
    except KeyError as exc:
        raise KeyError(f"unknown figure id/stem: {identifier!r}") from exc


def _lookup_contract(identifier: str) -> FigureContract:
    return FIGURE_CONTRACTS[_resolve_stem(identifier)]


def figures_dir(root: Path) -> Path:
    return Path(root) / FIGURES_DIR_NAME


def sidecar_paths(root: Path, figure_id: str) -> dict[str, Path]:
    """Return the ``{suffix: path}`` map of every required sidecar file."""
    contract = _lookup_contract(figure_id)
    base = figures_dir(root)
    return {
        suffix: base / f"{contract.stem}.{suffix}"
        for suffix in contract.sidecar_suffixes
    }


def _png_dimensions(path: Path) -> tuple[int, int]:
    """Read width/height from a PNG's IHDR chunk without decoding pixels."""
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG file: {path}")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _psm_command(image: Path, psm: int) -> list[str]:
    return [TESSERACT_BIN, str(image), "stdout", "--psm", str(psm)]


def run_tesseract(image: Path) -> dict[str, Any]:
    """Run Tesseract with ``--psm 6``, ``11``, and ``12`` over ``image``.

    Returns every raw transcript (one per page-segmentation mode) alongside
    per-mode and combined whitespace/glyph-normalized text. Nothing is
    written to disk; this is a pure OCR read of already-committed image
    bytes.
    """
    image = Path(image)
    if not image.is_file():
        raise FileNotFoundError(f"OCR image not found: {image}")

    raw: dict[str, str] = {}
    for psm in PSM_MODES:
        completed = subprocess.run(
            _psm_command(image, psm),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"tesseract --psm {psm} failed for {image}: "
                f"{completed.stderr.strip()}"
            )
        raw[f"psm_{psm}"] = completed.stdout

    normalized = {key: normalize_ocr(value) for key, value in raw.items()}
    combined_normalized = normalize_ocr(" ".join(raw.values()))
    return {
        "image": str(image),
        "psm_modes": list(PSM_MODES),
        "raw": raw,
        "normalized": normalized,
        "combined_normalized": combined_normalized,
    }


OcrRunner = Callable[[Path], dict[str, Any]]


def _load_json_sidecars(
    root: Path, contract: FigureContract, errors: list[str]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load every existing JSON sidecar, recording existence/parse errors."""
    base = figures_dir(root)
    sidecar_status: dict[str, Any] = {}
    sidecar_json: dict[str, dict[str, Any]] = {}
    for suffix in contract.sidecar_suffixes:
        path = base / f"{contract.stem}.{suffix}"
        exists = path.is_file()
        sidecar_status[suffix] = {"path": str(path), "exists": exists}
        if not exists:
            errors.append(f"missing sidecar: {path.name}")
            continue
        if not suffix.endswith(".json"):
            continue
        try:
            sidecar_json[suffix] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON sidecar {path.name}: {exc}")
    return sidecar_status, sidecar_json


def _recorded_hash(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("output_sha256", "sha256"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    image_payload = payload.get("image")
    if isinstance(image_payload, dict):
        value = image_payload.get("sha256")
        if isinstance(value, str):
            return value
    return None


def _check_hash_consistency(
    sidecar_json: dict[str, dict[str, Any]], output_sha256: str, errors: list[str]
) -> None:
    for suffix in ("inspect.json", "png.json", "provenance.json"):
        recorded = _recorded_hash(sidecar_json.get(suffix))
        if recorded is not None and recorded != output_sha256:
            errors.append(
                f"hash mismatch in {suffix}: sidecar records {recorded}, "
                f"actual PNG sha256 is {output_sha256}"
            )


def validate_figure(
    root: Path, figure_id: str, *, ocr_runner: OcrRunner = run_tesseract
) -> dict[str, Any]:
    """Validate one figure's dimensions, sidecars, review, and OCR coverage.

    ``figure_id`` may be either the snake_case stem (e.g. ``"master_spine"``)
    or the kebab-case figure id (e.g. ``"master-spine"``). Nothing is drawn,
    generated, or mutated; this function only reads already-committed files.
    """
    root = Path(root)
    contract = _lookup_contract(figure_id)
    stem = contract.stem
    image_path = figures_dir(root) / f"{stem}.png"

    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "stem": stem,
        "figure_id": contract.figure_id,
        "data_figure": contract.data_figure,
        "image_path": str(image_path),
    }

    if not image_path.is_file():
        errors.append(f"missing figure image: {image_path}")
        result.update(status="fail", errors=errors, warnings=warnings)
        return result

    output_sha256 = _sha256(image_path)
    result["output_sha256"] = output_sha256

    try:
        width, height = _png_dimensions(image_path)
    except ValueError as exc:
        errors.append(str(exc))
        width = height = None
    result["dimensions"] = {"width": width, "height": height}
    if (width, height) != (REQUIRED_WIDTH, REQUIRED_HEIGHT):
        errors.append(
            "dimension mismatch: expected "
            f"{REQUIRED_WIDTH}x{REQUIRED_HEIGHT}, got {width}x{height}"
        )

    sidecar_status, sidecar_json = _load_json_sidecars(root, contract, errors)
    result["sidecars"] = sidecar_status

    _check_hash_consistency(sidecar_json, output_sha256, errors)

    review = sidecar_json.get("review.json")
    if review is not None and review.get("keep_or_regenerate") != "keep":
        errors.append(
            "review.json does not accept the figure (keep_or_regenerate="
            f"{review.get('keep_or_regenerate')!r})"
        )

    content_review = sidecar_json.get("content-review.json")
    if contract.data_figure and content_review is not None:
        if content_review.get("keep_or_regenerate") != "keep":
            errors.append(
                "content-review.json does not accept the figure "
                f"(keep_or_regenerate={content_review.get('keep_or_regenerate')!r})"
            )
        unresolved = content_review.get("unresolved_numeric_mismatches") or []
        if unresolved:
            errors.append(
                "content-review.json reports unresolved numeric mismatches: "
                f"{unresolved}"
            )

    ocr_result = ocr_runner(image_path)
    combined_normalized = ocr_result.get("combined_normalized", "")
    # Token presence/counts are evaluated per page-segmentation-mode
    # transcript, not against a concatenation of all three modes: PSM 6, 11,
    # and 12 each read the same figure independently, so naively joining
    # their raw text before counting would multiply every occurrence count
    # (e.g. a label appearing once per mode would count as 3x). A token is
    # considered found if any single mode's transcript contains it; a status
    # count is considered matched if any single mode's transcript shows the
    # exact expected count.
    per_psm_normalized = list((ocr_result.get("normalized") or {}).values())
    if not per_psm_normalized:
        per_psm_normalized = [combined_normalized]

    confirmed_labels: set[str] = set()
    for payload in (review, content_review):
        if isinstance(payload, dict):
            confirmed_labels.update(payload.get("confirmed_labels") or [])
    normalized_confirmed = {normalize_ocr(label) for label in confirmed_labels}

    missing_labels: list[str] = []
    for label in contract.required_labels:
        normalized_label = normalize_ocr(label)
        found_in_ocr = any(normalized_label in text for text in per_psm_normalized)
        if found_in_ocr:
            continue
        if not contract.data_figure and normalized_label in normalized_confirmed:
            continue
        missing_labels.append(label)

    if missing_labels:
        if contract.data_figure:
            errors.append(
                "required data tokens missing from OCR evidence: "
                + ", ".join(missing_labels)
            )
        else:
            errors.append(
                "required labels missing from OCR evidence and vision "
                "review confirmation: " + ", ".join(missing_labels)
            )

    status_count_errors: list[str] = []
    if contract.status_counts:
        for label, expected_count in contract.status_counts.items():
            normalized_label = normalize_ocr(label)
            counts = [text.count(normalized_label) for text in per_psm_normalized]
            if expected_count not in counts:
                status_count_errors.append(
                    f"{label!r} expected {expected_count}x, found {counts} "
                    "across psm modes"
                )
    if status_count_errors:
        errors.append("status label count mismatch: " + "; ".join(status_count_errors))

    result["ocr"] = {
        "psm_modes": ocr_result.get("psm_modes", list(PSM_MODES)),
        "combined_normalized": combined_normalized,
        "missing_labels": missing_labels,
    }
    result["status"] = "fail" if errors else "pass"
    result["errors"] = errors
    result["warnings"] = warnings
    return result


def write_validation_manifest(
    root: Path, *, ocr_runner: OcrRunner = run_tesseract
) -> dict[str, Any]:
    """Validate all eight figures and write ``AI_FIGURE_VALIDATION.json``."""
    root = Path(root)
    figures = [
        validate_figure(root, stem, ocr_runner=ocr_runner)
        for stem in FIGURE_CONTRACTS
    ]
    overall_status = "pass" if all(f["status"] == "pass" for f in figures) else "fail"
    manifest_path = figures_dir(root) / "AI_FIGURE_VALIDATION.json"
    manifest: dict[str, Any] = {
        "schema": "ai-figure-validation/v1",
        "generated_by": "technical_report/figures/validate_ai_figures.py",
        "figure_count": len(figures),
        "overall_status": overall_status,
        "figures": figures,
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _cmd_ocr(args: argparse.Namespace) -> int:
    root = Path(args.root)
    contract = _lookup_contract(args.stem)
    stem = contract.stem
    base = figures_dir(root)
    image_path = base / f"{stem}.png"

    ocr_result = run_tesseract(image_path)
    combined_normalized = ocr_result["combined_normalized"]
    expected_tokens = list(contract.required_labels)
    unresolved = [
        label
        for label in expected_tokens
        if normalize_ocr(label) not in combined_normalized
    ]
    coverage = (
        (len(expected_tokens) - len(unresolved)) / len(expected_tokens)
        if expected_tokens
        else 1.0
    )

    raw_sections = "\n\n".join(
        f"--- psm {psm} ---\n{ocr_result['raw'][f'psm_{psm}']}" for psm in PSM_MODES
    )
    (base / f"{stem}.ocr.txt").write_text(raw_sections, encoding="utf-8")

    ocr_payload: dict[str, Any] = {
        "image": str(image_path),
        "psm_modes": list(PSM_MODES),
        "expected_tokens": expected_tokens,
        "normalized_observed": combined_normalized,
        "coverage": coverage,
        "unresolved": unresolved,
    }
    (base / f"{stem}.ocr.json").write_text(
        json.dumps(ocr_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(ocr_payload, indent=2, sort_keys=True))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.root)
    outcome = validate_figure(root, args.stem)
    print(json.dumps(outcome, indent=2, sort_keys=True))
    return 0 if outcome["status"] == "pass" else 1


def _cmd_validate_all(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if args.write_manifest:
        manifest = write_validation_manifest(root)
    else:
        figures = [validate_figure(root, stem) for stem in FIGURE_CONTRACTS]
        manifest = {
            "figure_count": len(figures),
            "overall_status": (
                "pass" if all(f["status"] == "pass" for f in figures) else "fail"
            ),
            "figures": figures,
        }
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0 if manifest["overall_status"] == "pass" else 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validate_ai_figures",
        description=(
            "Content-contract, OCR, and validation for the eight AI-redrawn "
            "report figures. Reads committed PNG/sidecar evidence only; "
            "never draws or generates images."
        ),
    )
    parser.add_argument(
        "--root",
        default=str(_REPO_ROOT),
        help="Repository root containing technical_report/figures.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    ocr_parser = subparsers.add_parser(
        "ocr",
        help="Run Tesseract PSM 6/11/12 on one figure and write its OCR sidecars.",
    )
    ocr_parser.add_argument("--stem", required=True)
    ocr_parser.set_defaults(handler=_cmd_ocr)

    validate_parser = subparsers.add_parser(
        "validate", help="Validate one figure against its content contract."
    )
    validate_parser.add_argument("--stem", required=True)
    validate_parser.set_defaults(handler=_cmd_validate)

    validate_all_parser = subparsers.add_parser(
        "validate-all", help="Validate all eight figures."
    )
    validate_all_parser.add_argument(
        "--write-manifest",
        action="store_true",
        help="Write technical_report/figures/AI_FIGURE_VALIDATION.json.",
    )
    validate_all_parser.set_defaults(handler=_cmd_validate_all)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    # Subparsers each set --root at the top-level parser, but argparse
    # requires --root before the subcommand unless repeated per-subparser;
    # keep both spellings working by falling back to the top-level value.
    if not hasattr(args, "root"):
        args.root = str(_REPO_ROOT)
    return args.handler(args)


if __name__ == "__main__":
    sys.exit(main())
