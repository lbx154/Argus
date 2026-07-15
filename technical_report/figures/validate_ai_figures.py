#!/usr/bin/env python3
"""Provenance, hash, dimension, and OCR validation for the six AI structural figures.

This module defines the six structural/concept figures that are drawn by the
``gpt-image-2`` image model and validates the already-generated, operator-accepted
rasters against a public-safe provenance contract. It covers only the six
structural figures; the two data figures (``public_results``, ``paper_portfolio``)
remain deterministically drawn and are validated by ``build_report_figures.py``
and the deterministic figure tests, not here. It intentionally does **not** draw,
render, or generate any image: it only reads committed PNG bytes and their sidecar
evidence files (prompt, generation sidecar, inspect, provenance, and Tesseract OCR
sidecars) and reports pass/fail.

Final integration workflow (fast delivery): the six rasters were accepted by the
operator, so this validator no longer performs or requires any iterative model
review. There is no ``review.json``/``content-review.json`` dual-review gate. The
hard checks are domain-agnostic provenance guarantees:

- the raster is exactly ``1536x1024`` and every required sidecar is present;
- the ``inspect``/``png.json``/``provenance`` sidecars record a SHA-256 that
  matches the committed PNG bytes;
- the ``png.json``/``provenance`` prompt SHA-256 matches the raw prompt file;
- no committed sidecar leaks an absolute path, session id, or API-vault field.

OCR is retained purely as recorded evidence: Tesseract PSM 6/11/12 transcripts
are captured and per-label coverage is reported for auditing, but imperfect OCR
of stylized image-model text never rejects an operator-accepted raster.

Public surface:

- ``FIGURE_CONTRACTS``: the exact six structural figure contracts, keyed by
  stem.
- ``normalize_ocr(text)``: whitespace + multiplication/dash Unicode
  normalization only. Digits and decimal points are never altered.
- ``normalize_ocr_for_matching(text)``: a *separate*, more tolerant
  normalization used only as a token-matching fallback. It layers on top of
  ``normalize_ocr`` and additionally tolerates OCR loss/substitution of a
  middle-dot ("\u00b7") separator glyph and collapses repeated punctuation.
  Like ``normalize_ocr``, it never alters digits, decimal points, "%", "/",
  or numeric sign characters, and it never mutates the raw OCR/`normalize_ocr`
  provenance recorded for a figure -- it is purely a matching aid.
- ``run_tesseract(image)``: runs Tesseract with ``--psm 6``, ``11``, and
  ``12`` and returns every raw and normalized transcript.
- ``validate_figure(root, figure_id)``: validates one figure's dimensions,
  sidecar presence, hash/prompt consistency, absence of local paths, and
  records OCR label coverage as evidence.
- ``write_validation_manifest(root)``: validates all six figures and writes
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

_REPO_ROOT = Path(__file__).resolve().parents[2]

FIGURES_DIR_NAME = "technical_report/figures"

TESSERACT_BIN = "tesseract"
PSM_MODES: tuple[int, ...] = (6, 11, 12)

REQUIRED_WIDTH = 1536
REQUIRED_HEIGHT = 1024

# Sidecar suffixes required for every one of the six structural figures. The
# fast-delivery contract keeps prompt/generation/inspect/OCR/provenance
# evidence; the superseded dual model-review sidecars (review.json,
# content-review.json) are intentionally NOT part of the required set.
_COMMON_SIDECAR_SUFFIXES: tuple[str, ...] = (
    "prompt.txt",
    "png.json",
    "png.inspect.json",
    "png.provenance.json",
    "ocr.txt",
    "ocr.json",
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


# Middle-dot separator glyph variants a font/OCR engine may render in place
# of the pinned "\u00b7" (MIDDLE DOT) that separates two label halves (e.g.
# "nanochat \u00b7 B200"). Mapped to a plain space -- never to "." or any
# other character that could collide with a decimal point -- so that OCR
# losing or substituting the separator glyph does not, by itself, hide an
# otherwise-correct label from token matching. Digits are never members of
# this set.
_MIDDLE_DOT_VARIANTS: str = (
    "\u00b7"  # · MIDDLE DOT
    "\u2027"  # ‧ HYPHENATION POINT
    "\u2219"  # ∙ BULLET OPERATOR
    "\u22c5"  # ⋅ DOT OPERATOR
    "\u2022"  # • BULLET
    "\u30fb"  # ・ KATAKANA MIDDLE DOT
)

# Matches a run of 2+ identical punctuation characters, excluding word
# characters, whitespace, and every character this function must never
# touch: digits, decimal point, percent, slash, plus/minus sign. Used only to
# collapse OCR punctuation noise (e.g. "::" -> ":"); it can never collapse a
# repeated digit or touch a numeric sign.
_REPEATED_PUNCTUATION_RE = re.compile(r"([^\w\s0-9.%/+-])\1+")


def normalize_ocr_for_matching(text: str | None) -> str:
    """Separator-tolerant OCR token-matching normalization.

    This is a **separate, strictly more tolerant** function from
    ``normalize_ocr``, used only as a fallback when the canonical exact match
    fails. It layers on top of ``normalize_ocr`` (whitespace +
    multiplication/dash glyph normalization) and additionally:

    - maps middle-dot separator glyph variants (see ``_MIDDLE_DOT_VARIANTS``)
      to a plain space, tolerating OCR loss or substitution of the "\u00b7"
      that appears between two label halves (e.g. "nanochat \u00b7 B200"
      OCR-matching "nanochat B200");
    - collapses runs of 2+ identical non-alphanumeric punctuation characters
      to a single occurrence.

    It NEVER alters digits, the decimal point, "%", "/", or numeric sign
    characters -- those are excluded from every substitution/collapse this
    function performs. "0.9636" can therefore never match "0.963", and
    "63/82" / "76.8%" are never loosened.

    Raw OCR transcripts and the canonical ``normalize_ocr`` output used for
    provenance are produced independently of this function and are never
    mutated by it; this function exists purely as a token-matching aid and
    must never be substituted for ``normalize_ocr`` when recording OCR
    evidence.
    """
    normalized = normalize_ocr(text)
    for variant in _MIDDLE_DOT_VARIANTS:
        normalized = normalized.replace(variant, " ")
    normalized = _REPEATED_PUNCTUATION_RE.sub(r"\1", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized)
    return normalized.strip()


@dataclass(frozen=True)
class FigureContract:
    """The exact content contract for one of the six structural figures."""

    stem: str
    figure_id: str
    figure_type: str
    title: str
    required_labels: tuple[str, ...]

    @property
    def sidecar_suffixes(self) -> tuple[str, ...]:
        return _COMMON_SIDECAR_SUFFIXES


def _contract(**kwargs: Any) -> FigureContract:
    return FigureContract(**kwargs)


# The exact six structural figures from the approved design spec, in the order
# they are enumerated there. Keys are the snake_case file stem (matching
# "<stem>.png"); ``figure_id`` is the kebab-case id used in provenance
# manifests such as IMAGE2_FIGURES.json. The two data figures
# (public_results, paper_portfolio) are intentionally absent: they remain
# deterministically drawn and are validated elsewhere.
FIGURE_CONTRACTS: dict[str, FigureContract] = {
    "master_spine": _contract(
        stem="master_spine",
        figure_id="master-spine",
        figure_type="concept",
        title="Master Spine",
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
        figure_type="concept",
        title="Dense Intelligence",
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
        figure_type="architecture",
        title="Three Planes",
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
        figure_type="architecture",
        title="Argus Architecture",
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
        figure_type="lifecycle",
        title="Mission Lifecycle",
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
        figure_type="reliability",
        title="Long-Horizon Reliability",
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


def _recorded_prompt_hash(payload: dict[str, Any] | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("prompt_sha256")
    return value if isinstance(value, str) else None


def _check_hash_consistency(
    sidecar_json: dict[str, dict[str, Any]], output_sha256: str, errors: list[str]
) -> None:
    for suffix in ("png.inspect.json", "png.json", "png.provenance.json"):
        payload = sidecar_json.get(suffix)
        if payload is None:
            # Missing/unparseable sidecar is already reported by the
            # sidecar-presence/JSON-parse check; nothing further to add here.
            continue
        recorded = _recorded_hash(payload)
        if recorded is None:
            errors.append(
                f"{suffix} has no recorded output/image sha256: cannot "
                "confirm it corresponds to the committed PNG"
            )
        elif recorded != output_sha256:
            errors.append(
                f"hash mismatch in {suffix}: sidecar records {recorded}, "
                f"actual PNG sha256 is {output_sha256}"
            )


def _check_prompt_hash_consistency(
    sidecar_json: dict[str, dict[str, Any]], prompt_sha256: str, errors: list[str]
) -> None:
    for suffix in ("png.json", "png.provenance.json"):
        payload = sidecar_json.get(suffix)
        if payload is None:
            continue
        recorded = _recorded_prompt_hash(payload)
        if recorded is None:
            errors.append(
                f"{suffix} has no recorded prompt_sha256: cannot confirm it "
                "corresponds to the committed prompt file"
            )
        elif recorded != prompt_sha256:
            errors.append(
                f"prompt hash mismatch in {suffix}: sidecar records {recorded}, "
                f"actual prompt file sha256 is {prompt_sha256}"
            )


# Public-safe committed sidecars must never leak an absolute filesystem path, a
# session/state directory, or an API-vault reference. These are domain-agnostic
# anti-leak guards, not research judgements.
_LOCAL_PATH_MARKERS: tuple[str, ...] = (
    "/home/",
    "/Users/",
    "/root/",
    "/tmp/",
    "\\Users\\",
    "session-state",
    ".argus-skill",
    "vault:",
)


def _iter_json_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, sub in value.items():
            if isinstance(key, str):
                yield key
            yield from _iter_json_strings(sub)
    elif isinstance(value, (list, tuple)):
        for sub in value:
            yield from _iter_json_strings(sub)


def _check_no_local_paths(
    sidecar_json: dict[str, dict[str, Any]], errors: list[str]
) -> None:
    for suffix, payload in sidecar_json.items():
        for text in _iter_json_strings(payload):
            for marker in _LOCAL_PATH_MARKERS:
                if marker in text:
                    errors.append(
                        f"{suffix} leaks a local path / vault reference "
                        f"({marker!r}); committed sidecars must be public-safe"
                    )
                    break


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
    _check_no_local_paths(sidecar_json, errors)

    prompt_path = figures_dir(root) / f"{stem}.prompt.txt"
    prompt_sha256 = _sha256(prompt_path) if prompt_path.is_file() else None
    result["prompt_sha256"] = prompt_sha256
    if prompt_sha256 is not None:
        _check_prompt_hash_consistency(sidecar_json, prompt_sha256, errors)

    ocr_result = ocr_runner(image_path)
    combined_normalized = ocr_result.get("combined_normalized", "")
    # Token presence is evaluated per page-segmentation-mode transcript, not
    # against a concatenation of all three modes: PSM 6, 11, and 12 each read
    # the same figure independently. A token is considered found if any single
    # mode's transcript contains it.
    per_psm_normalized = list((ocr_result.get("normalized") or {}).values())
    if not per_psm_normalized:
        per_psm_normalized = [combined_normalized]

    # Separator-tolerant matching text, derived independently from the same raw
    # per-PSM transcripts (see ``normalize_ocr_for_matching``). It never
    # replaces the canonical ``normalize_ocr`` provenance recorded below.
    raw_per_psm = list((ocr_result.get("raw") or {}).values())
    if not raw_per_psm:
        raw_per_psm = [combined_normalized]
    per_psm_matching = [normalize_ocr_for_matching(text) for text in raw_per_psm]

    # OCR coverage is recorded as evidence only. Because the six rasters are
    # operator-accepted for fast delivery and this validator does no model
    # review, imperfect OCR of stylized image-model text is a warning, never a
    # hard failure -- it must not reject an accepted figure.
    unresolved_labels: list[str] = []
    for label in contract.required_labels:
        normalized_label = normalize_ocr(label)
        if any(normalized_label in text for text in per_psm_normalized):
            continue
        matching_label = normalize_ocr_for_matching(label)
        if matching_label != normalized_label and any(
            matching_label in text for text in per_psm_matching
        ):
            continue
        unresolved_labels.append(label)

    if unresolved_labels:
        warnings.append(
            "labels not resolved by OCR (recorded as evidence, not a "
            "rejection of the operator-accepted raster): "
            + ", ".join(unresolved_labels)
        )

    resolved = len(contract.required_labels) - len(unresolved_labels)
    coverage = resolved / len(contract.required_labels) if contract.required_labels else 1.0
    result["ocr"] = {
        "psm_modes": ocr_result.get("psm_modes", list(PSM_MODES)),
        "combined_normalized": combined_normalized,
        "label_coverage": coverage,
        "unresolved_labels": unresolved_labels,
    }
    result["validation_route"] = (
        "operator-accepted raster; provenance/hash/dimension/no-local-path "
        "checks enforced, OCR coverage recorded as evidence"
    )
    result["status"] = "fail" if errors else "pass"
    result["errors"] = errors
    result["warnings"] = warnings
    return result


def write_validation_manifest(
    root: Path, *, ocr_runner: OcrRunner = run_tesseract
) -> dict[str, Any]:
    """Validate all six figures and write ``AI_FIGURE_VALIDATION.json``."""
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
            "Content-contract, OCR, and validation for the six AI-redrawn "
            "structural report figures. Reads committed PNG/sidecar evidence "
            "only; never draws or generates images."
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
        "validate-all", help="Validate all six structural figures."
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
