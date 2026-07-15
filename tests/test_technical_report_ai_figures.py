"""Tests for technical_report/figures/validate_ai_figures.py.

These tests exercise the AI figure content-contract, OCR, and validation
module entirely through fixtures/temp directories built in-test. They never
depend on the current (incomplete, partially-deterministic) production
figure set under ``technical_report/figures/`` -- that set predates the new
eight-figure AI contract and does not yet carry the sidecars this validator
requires.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import struct
import subprocess
import sys
import zlib
from pathlib import Path
from typing import Any

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = (
    _REPO_ROOT / "technical_report" / "figures" / "validate_ai_figures.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "validate_ai_figures", _MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vaf = _load_module()


# ---------------------------------------------------------------------------
# PNG fixture helpers (no PIL dependency: hand-roll a minimal valid PNG so the
# only thing under test is validate_ai_figures.py's own IHDR reader/hasher).
# ---------------------------------------------------------------------------


def _write_png(path: Path, width: int, height: int) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data))
        )

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    # One row of raw RGB pixels (all white), prefixed by the filter-type byte.
    raw_row = b"\x00" + (b"\xff\xff\xff" * width)
    raw = raw_row * height
    idat = zlib.compress(raw)
    png_bytes = (
        signature
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png_bytes)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixture project scaffolding
# ---------------------------------------------------------------------------


def _figures_dir(project_root: Path) -> Path:
    figures = project_root / "technical_report" / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    return figures


def _write_full_sidecars(
    figures: Path,
    stem: str,
    *,
    width: int = 1536,
    height: int = 1024,
    data_figure: bool = False,
    review_overrides: dict[str, Any] | None = None,
    content_review_overrides: dict[str, Any] | None = None,
) -> str:
    """Write a PNG plus every required sidecar for ``stem`` and return its
    sha256 so callers can assert hash-consistency behavior.

    Every figure -- concept or data -- gets a ``content-review.json`` second
    independent exact-content vision review, per the approved Generation
    Workflow; ``data_figure`` only affects whether extra strict
    numeric/source fields (``unresolved_numeric_mismatches``) are meaningful.
    """
    png_path = figures / f"{stem}.png"
    _write_png(png_path, width, height)
    sha256 = vaf._sha256(png_path)

    (figures / f"{stem}.prompt.txt").write_text("prompt body", encoding="utf-8")

    _write_json(
        figures / f"{stem}.png.json",
        {"output_sha256": sha256, "model": "gpt-image-2"},
    )
    _write_json(
        figures / f"{stem}.inspect.json",
        {"sha256": sha256, "width": width, "height": height},
    )
    _write_json(
        figures / f"{stem}.provenance.json",
        {"output_sha256": sha256, "generator": "codex-image2"},
    )

    review_payload = {
        "score_1_to_5": 5,
        "major_issues": [],
        "concrete_revision_prompt": "",
        "keep_or_regenerate": "keep",
    }
    if review_overrides:
        review_payload.update(review_overrides)
    _write_json(figures / f"{stem}.review.json", review_payload)

    (figures / f"{stem}.ocr.txt").write_text("raw ocr text", encoding="utf-8")
    _write_json(
        figures / f"{stem}.ocr.json",
        {"expected_tokens": [], "unresolved": [], "coverage": 1.0},
    )

    content_review_payload = {
        "score_1_to_5": 5,
        "unresolved_numeric_mismatches": [],
        "keep_or_regenerate": "keep",
    }
    if content_review_overrides:
        content_review_payload.update(content_review_overrides)
    _write_json(figures / f"{stem}.content-review.json", content_review_payload)

    return sha256


def _ocr_runner_returning(text: str):
    def _runner(image_path: Path) -> dict[str, Any]:
        normalized = vaf.normalize_ocr(text)
        return {
            "image": str(image_path),
            "psm_modes": list(vaf.PSM_MODES),
            "raw": {"psm_6": text, "psm_11": text, "psm_12": text},
            "normalized": {
                "psm_6": normalized,
                "psm_11": normalized,
                "psm_12": normalized,
            },
            "combined_normalized": normalized,
        }

    return _runner


def _full_ocr_text_for(stem: str) -> str:
    contract = vaf.FIGURE_CONTRACTS[stem]
    return " ".join(contract.required_labels)


# ---------------------------------------------------------------------------
# FIGURE_CONTRACTS
# ---------------------------------------------------------------------------


def test_figure_contracts_has_exactly_eight_entries() -> None:
    assert len(vaf.FIGURE_CONTRACTS) == 8


def test_figure_contracts_has_exact_expected_stems() -> None:
    expected_stems = {
        "master_spine",
        "dense_intelligence",
        "system_planes",
        "argus_architecture",
        "mission_lifecycle",
        "long_horizon_reliability",
        "public_results",
        "paper_portfolio",
    }
    assert set(vaf.FIGURE_CONTRACTS) == expected_stems


def test_figure_contracts_has_exact_expected_kebab_figure_ids() -> None:
    expected_ids = {
        "master-spine",
        "dense-intelligence",
        "system-planes",
        "argus-architecture",
        "mission-lifecycle",
        "long-horizon-reliability",
        "public-results",
        "paper-portfolio",
    }
    actual_ids = {c.figure_id for c in vaf.FIGURE_CONTRACTS.values()}
    assert actual_ids == expected_ids


def test_only_public_results_and_paper_portfolio_are_data_figures() -> None:
    data_figures = {
        stem for stem, c in vaf.FIGURE_CONTRACTS.items() if c.data_figure
    }
    assert data_figures == {"public_results", "paper_portfolio"}


def test_public_results_status_counts_are_two_digest_four_snapshot() -> None:
    contract = vaf.FIGURE_CONTRACTS["public_results"]
    assert contract.status_counts == {"artifact digest": 2, "website snapshot": 4}


def test_non_data_figures_have_no_status_counts() -> None:
    for stem, contract in vaf.FIGURE_CONTRACTS.items():
        if stem in {"public_results", "paper_portfolio"}:
            continue
        assert contract.status_counts is None


def test_master_spine_required_labels_match_spec() -> None:
    contract = vaf.FIGURE_CONTRACTS["master_spine"]
    required = {
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
    }
    assert required <= set(contract.required_labels)


def test_paper_portfolio_required_labels_include_totals() -> None:
    contract = vaf.FIGURE_CONTRACTS["paper_portfolio"]
    required = {
        "Research Portfolio",
        "41 papers",
        "35 manuscripts",
        "6 drafts",
        "output inventory \u00b7 not accepted papers",
    }
    assert required <= set(contract.required_labels)


def test_public_results_required_labels_include_all_six_arenas() -> None:
    contract = vaf.FIGURE_CONTRACTS["public_results"]
    required = {
        "NVIDIA SOL-ExecBench",
        "nanochat \u00b7 B200",
        "nanochat \u00b7 H100",
        "nanoGPT speedrun",
        "AARRI-Bench",
        "Arbor \u00b7 RUC NLPIR",
        "0.9636 BPB",
        "0.9855 BPB",
        "79.77 s",
        "63/82",
        "76.8%",
        "28.0 gap",
    }
    assert required <= set(contract.required_labels)


def test_data_figure_sidecar_suffixes_include_content_review() -> None:
    contract = vaf.FIGURE_CONTRACTS["public_results"]
    assert "content-review.json" in contract.sidecar_suffixes


def test_concept_figure_sidecar_suffixes_also_include_content_review() -> None:
    # Amended spec: every one of the eight figures -- not just data figures
    # -- gets a second independent exact-content vision review.
    contract = vaf.FIGURE_CONTRACTS["master_spine"]
    assert "content-review.json" in contract.sidecar_suffixes


def test_every_contract_requires_the_core_sidecar_set() -> None:
    core = {
        "prompt.txt",
        "png.json",
        "inspect.json",
        "provenance.json",
        "review.json",
        "ocr.txt",
        "ocr.json",
    }
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert core <= set(contract.sidecar_suffixes)


# ---------------------------------------------------------------------------
# normalize_ocr
# ---------------------------------------------------------------------------


def test_normalize_ocr_collapses_whitespace_runs() -> None:
    assert vaf.normalize_ocr("Manager \n\t Planner   Engineer") == (
        "Manager Planner Engineer"
    )


def test_normalize_ocr_strips_leading_and_trailing_whitespace() -> None:
    assert vaf.normalize_ocr("  Reviewer  ") == "Reviewer"


def test_normalize_ocr_handles_none_and_empty_string() -> None:
    assert vaf.normalize_ocr(None) == ""
    assert vaf.normalize_ocr("") == ""


@pytest.mark.parametrize(
    "variant",
    ["\u00d7", "\u2715", "\u2716", "\u2a2f"],
)
def test_normalize_ocr_maps_multiplication_variants_to_ascii_x(variant: str) -> None:
    assert vaf.normalize_ocr(f"2{variant}#1") == "2x#1"


@pytest.mark.parametrize(
    "variant",
    ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"],
)
def test_normalize_ocr_maps_dash_variants_to_ascii_hyphen(variant: str) -> None:
    assert vaf.normalize_ocr(f"7 top{variant}3") == "7 top-3"


def test_normalize_ocr_never_alters_digits() -> None:
    text = "0.9636 BPB \u00d7 79.77 s \u2013 63/82"
    normalized = vaf.normalize_ocr(text)
    assert "0.9636" in normalized
    assert "79.77" in normalized
    assert "63/82" in normalized


def test_normalize_ocr_never_alters_decimal_points() -> None:
    # A dash-variant character placed directly next to a decimal number must
    # not disturb the number's own digits or its decimal point.
    text = "28.0\u2013gap"
    normalized = vaf.normalize_ocr(text)
    assert "28.0" in normalized
    assert normalized == "28.0-gap"


def test_normalize_ocr_does_not_touch_fullwidth_unicode_digits() -> None:
    # Fullwidth digit U+FF10 ("０") is not ascii "0" and must be left exactly
    # as-is: normalize_ocr must never perform any digit-shape normalization.
    fullwidth_zero = "\uff10"
    assert fullwidth_zero in vaf.normalize_ocr(f"score {fullwidth_zero}")


def test_normalize_ocr_is_idempotent() -> None:
    text = "2\u00d7 #1 \u2013 7 top-3"
    once = vaf.normalize_ocr(text)
    twice = vaf.normalize_ocr(once)
    assert once == twice


# ---------------------------------------------------------------------------
# run_tesseract
# ---------------------------------------------------------------------------


def test_run_tesseract_raises_for_missing_image(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        vaf.run_tesseract(tmp_path / "does_not_exist.png")


def test_run_tesseract_invokes_psm_6_11_and_12(tmp_path, monkeypatch) -> None:
    image_path = tmp_path / "figure.png"
    _write_png(image_path, 1536, 1024)

    invoked_psms: list[str] = []

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    def fake_run(cmd, capture_output, text, check):
        assert cmd[0] == vaf.TESSERACT_BIN
        assert cmd[1] == str(image_path)
        assert "--psm" in cmd
        psm = cmd[cmd.index("--psm") + 1]
        invoked_psms.append(psm)
        return _Completed(f"text for psm {psm}")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vaf.run_tesseract(image_path)

    assert invoked_psms == ["6", "11", "12"]
    assert set(result["raw"]) == {"psm_6", "psm_11", "psm_12"}
    assert result["raw"]["psm_6"] == "text for psm 6"
    assert result["normalized"]["psm_11"] == vaf.normalize_ocr("text for psm 11")
    assert "text for psm 12" in result["combined_normalized"]


def test_run_tesseract_retains_all_raw_psm_outputs_even_when_they_differ(
    tmp_path, monkeypatch
) -> None:
    image_path = tmp_path / "figure.png"
    _write_png(image_path, 1536, 1024)

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    outputs = {"6": "alpha", "11": "beta", "12": "gamma"}

    def fake_run(cmd, capture_output, text, check):
        psm = cmd[cmd.index("--psm") + 1]
        return _Completed(outputs[psm])

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vaf.run_tesseract(image_path)

    assert result["raw"]["psm_6"] == "alpha"
    assert result["raw"]["psm_11"] == "beta"
    assert result["raw"]["psm_12"] == "gamma"


def test_run_tesseract_raises_runtime_error_on_nonzero_exit(
    tmp_path, monkeypatch
) -> None:
    image_path = tmp_path / "figure.png"
    _write_png(image_path, 1536, 1024)

    class _Completed:
        stdout = ""
        stderr = "tesseract exploded"
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Completed())

    with pytest.raises(RuntimeError, match="tesseract exploded"):
        vaf.run_tesseract(image_path)


@pytest.mark.skipif(
    __import__("shutil").which("tesseract") is None,
    reason="tesseract binary not available in this environment",
)
def test_run_tesseract_reads_real_text_via_real_binary(tmp_path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image_path = tmp_path / "real_ocr.png"
    image = Image.new("RGB", (1536, 1024), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 96
        )
    except OSError:
        font = ImageFont.load_default()
    draw.text((100, 400), "REVIEWER", fill="black", font=font)
    image.save(image_path)

    result = vaf.run_tesseract(image_path)

    assert "REVIEWER" in result["combined_normalized"].upper()


# ---------------------------------------------------------------------------
# validate_figure
# ---------------------------------------------------------------------------


def test_validate_figure_passes_when_everything_is_consistent(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]
    assert outcome["errors"] == []


def test_validate_figure_accepts_kebab_figure_id(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master-spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_fails_when_image_missing(tmp_path: Path) -> None:
    _figures_dir(tmp_path)  # directory exists but no PNG written

    outcome = vaf.validate_figure(tmp_path, "master_spine")

    assert outcome["status"] == "fail"
    assert any("missing figure image" in e for e in outcome["errors"])


def test_validate_figure_fails_on_wrong_dimensions(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine", width=800, height=600)
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert outcome["dimensions"] == {"width": 800, "height": 600}
    assert any("dimension mismatch" in e for e in outcome["errors"])


def test_validate_figure_fails_when_a_sidecar_is_missing(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    (figures / "master_spine.review.json").unlink()
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("missing sidecar" in e and "review.json" in e for e in outcome["errors"])


def test_validate_figure_fails_when_inspect_hash_does_not_match_png(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    _write_json(
        figures / "master_spine.inspect.json",
        {"sha256": "0" * 64, "width": 1536, "height": 1024},
    )
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("hash mismatch" in e for e in outcome["errors"])


def test_validate_figure_fails_when_review_requests_regeneration(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(
        figures,
        "master_spine",
        review_overrides={"keep_or_regenerate": "regenerate"},
    )
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("review.json does not accept" in e for e in outcome["errors"])


def test_validate_figure_fails_when_required_label_missing_from_ocr(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    text_without_reviewer = _full_ocr_text_for("master_spine").replace("Reviewer", "")
    ocr_runner = _ocr_runner_returning(text_without_reviewer)

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("Reviewer" in e for e in outcome["errors"])


def test_validate_figure_concept_label_passes_via_vision_review_confirmation(
    tmp_path: Path,
) -> None:
    # Concept figures may satisfy a required label either via OCR or via
    # explicit confirmation from BOTH independent vision reviews; OCR alone
    # missing "Reviewer" must not fail the figure if both reviews confirm it.
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(
        figures,
        "master_spine",
        review_overrides={"confirmed_labels": ["Reviewer"]},
        content_review_overrides={"confirmed_labels": ["Reviewer"]},
    )
    text_without_reviewer = _full_ocr_text_for("master_spine").replace("Reviewer", "")
    ocr_runner = _ocr_runner_returning(text_without_reviewer)

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_data_figure_label_cannot_be_satisfied_by_review_alone(
    tmp_path: Path,
) -> None:
    # Data figures require every numeric/data token to be confirmed by OCR;
    # a vision-review confirmation alone is not sufficient.
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(
        figures,
        "public_results",
        data_figure=True,
        content_review_overrides={"confirmed_labels": ["0.9636 BPB"]},
    )
    text_missing_token = _full_ocr_text_for("public_results").replace(
        "0.9636 BPB", ""
    )
    ocr_runner = _ocr_runner_returning(text_missing_token)

    outcome = vaf.validate_figure(tmp_path, "public_results", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("0.9636 BPB" in e for e in outcome["errors"])


def test_validate_figure_data_figure_requires_content_review_sidecar(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "public_results", data_figure=True)
    (figures / "public_results.content-review.json").unlink()
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("public_results"))

    outcome = vaf.validate_figure(tmp_path, "public_results", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        "missing sidecar" in e and "content-review.json" in e
        for e in outcome["errors"]
    )


def test_validate_figure_data_figure_fails_on_unresolved_numeric_mismatch(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(
        figures,
        "public_results",
        data_figure=True,
        content_review_overrides={
            "unresolved_numeric_mismatches": ["0.9636 BPB read as 0.9736 BPB"]
        },
    )
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("public_results"))

    outcome = vaf.validate_figure(tmp_path, "public_results", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("unresolved numeric mismatches" in e for e in outcome["errors"])


def test_validate_figure_data_figure_passes_two_digest_four_snapshot_counts(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "public_results", data_figure=True)
    text = _full_ocr_text_for("public_results") + (
        " artifact digest artifact digest "
        "website snapshot website snapshot website snapshot website snapshot"
    )
    ocr_runner = _ocr_runner_returning(text)

    outcome = vaf.validate_figure(tmp_path, "public_results", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_data_figure_fails_wrong_digest_snapshot_counts(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "public_results", data_figure=True)
    text = _full_ocr_text_for("public_results") + (
        " artifact digest website snapshot website snapshot"
    )
    ocr_runner = _ocr_runner_returning(text)

    outcome = vaf.validate_figure(tmp_path, "public_results", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("status label count mismatch" in e for e in outcome["errors"])


def test_validate_figure_exact_digit_ocr_error_is_not_silently_accepted(
    tmp_path: Path,
) -> None:
    # An OCR misread that drops or alters a digit must never be treated as a
    # match: normalize_ocr must not perform fuzzy digit correction.
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "public_results", data_figure=True)
    text = _full_ocr_text_for("public_results").replace("0.9636 BPB", "0.963 BPB")
    ocr_runner = _ocr_runner_returning(text)

    outcome = vaf.validate_figure(tmp_path, "public_results", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("0.9636 BPB" in e for e in outcome["errors"])


def test_validate_figure_raises_for_unknown_figure_id(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        vaf.validate_figure(tmp_path, "not_a_real_figure")


def test_validate_figure_output_includes_sha256_and_sidecar_map(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    sha256 = _write_full_sidecars(figures, "master_spine")
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["output_sha256"] == sha256
    assert "review.json" in outcome["sidecars"]
    assert outcome["sidecars"]["review.json"]["exists"] is True


# ---------------------------------------------------------------------------
# Stricter approved-spec contracts: every figure gets a second independent
# exact-content vision review (content-review.json); data figures
# additionally require strict numeric/source review. These tests were
# written before the corresponding validator fix landed and must fail (RED)
# against the pre-fix implementation, which only wired content-review.json
# into the data-figure sidecar set/acceptance path.
# ---------------------------------------------------------------------------


def _write_core_sidecars_without_content_review(
    figures: Path,
    stem: str,
    *,
    width: int = 1536,
    height: int = 1024,
    review_overrides: dict[str, Any] | None = None,
) -> str:
    """Write a PNG plus only the seven "core" sidecars for ``stem`` -- no
    ``content-review.json`` -- regardless of whether ``stem`` is a data
    figure. Used to probe the "every figure requires content-review.json"
    requirement without depending on the (pre-fix) data-figure-only
    fixture gating in ``_write_full_sidecars``.
    """
    png_path = figures / f"{stem}.png"
    _write_png(png_path, width, height)
    sha256 = vaf._sha256(png_path)

    (figures / f"{stem}.prompt.txt").write_text("prompt body", encoding="utf-8")
    _write_json(
        figures / f"{stem}.png.json",
        {"output_sha256": sha256, "model": "gpt-image-2"},
    )
    _write_json(
        figures / f"{stem}.inspect.json",
        {"sha256": sha256, "width": width, "height": height},
    )
    _write_json(
        figures / f"{stem}.provenance.json",
        {"output_sha256": sha256, "generator": "codex-image2"},
    )
    review_payload = {
        "score_1_to_5": 5,
        "major_issues": [],
        "concrete_revision_prompt": "",
        "keep_or_regenerate": "keep",
    }
    if review_overrides:
        review_payload.update(review_overrides)
    _write_json(figures / f"{stem}.review.json", review_payload)
    (figures / f"{stem}.ocr.txt").write_text("raw ocr text", encoding="utf-8")
    _write_json(
        figures / f"{stem}.ocr.json",
        {"expected_tokens": [], "unresolved": [], "coverage": 1.0},
    )
    return sha256


def test_every_contract_requires_content_review_sidecar() -> None:
    # Per the amended Generation Workflow, every one of the eight figures
    # gets a second independent exact-content vision review -- not just the
    # two data figures.
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert "content-review.json" in contract.sidecar_suffixes, contract.stem


def test_validate_figure_fails_when_content_review_sidecar_absent_for_concept_figure(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_core_sidecars_without_content_review(figures, "master_spine")
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        "missing sidecar" in e and "content-review.json" in e
        for e in outcome["errors"]
    ), outcome["errors"]


def test_validate_figure_fails_when_content_review_not_keep_for_concept_figure(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_core_sidecars_without_content_review(figures, "master_spine")
    _write_json(
        figures / "master_spine.content-review.json",
        {
            "score_1_to_5": 2,
            "unresolved_numeric_mismatches": [],
            "keep_or_regenerate": "regenerate",
        },
    )
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        "content-review.json does not accept" in e for e in outcome["errors"]
    ), outcome["errors"]


def test_validate_figure_fails_when_content_review_absent_for_data_figure(
    tmp_path: Path,
) -> None:
    # Absence must fail even though it is trivially implied by the existing
    # "missing sidecar" check for data figures -- assert it explicitly so a
    # future refactor of the sidecar-presence check cannot silently drop the
    # content-review acceptance requirement for data figures.
    figures = _figures_dir(tmp_path)
    _write_core_sidecars_without_content_review(figures, "public_results")
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("public_results"))

    outcome = vaf.validate_figure(tmp_path, "public_results", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        "missing sidecar" in e and "content-review.json" in e
        for e in outcome["errors"]
    ), outcome["errors"]


def test_concept_label_passes_only_when_both_reviews_independently_confirm(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_core_sidecars_without_content_review(
        figures,
        "master_spine",
        review_overrides={"confirmed_labels": ["Reviewer"]},
    )
    _write_json(
        figures / "master_spine.content-review.json",
        {
            "score_1_to_5": 5,
            "unresolved_numeric_mismatches": [],
            "keep_or_regenerate": "keep",
            "confirmed_labels": ["Reviewer"],
        },
    )
    text_without_reviewer = _full_ocr_text_for("master_spine").replace("Reviewer", "")
    ocr_runner = _ocr_runner_returning(text_without_reviewer)

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_review_json_confirmation_alone_cannot_bypass_ocr(tmp_path: Path) -> None:
    # review.json confirms "Reviewer" but content-review.json does not: a
    # single review must never be sufficient to bypass OCR.
    figures = _figures_dir(tmp_path)
    _write_core_sidecars_without_content_review(
        figures,
        "master_spine",
        review_overrides={"confirmed_labels": ["Reviewer"]},
    )
    _write_json(
        figures / "master_spine.content-review.json",
        {
            "score_1_to_5": 5,
            "unresolved_numeric_mismatches": [],
            "keep_or_regenerate": "keep",
        },
    )
    text_without_reviewer = _full_ocr_text_for("master_spine").replace("Reviewer", "")
    ocr_runner = _ocr_runner_returning(text_without_reviewer)

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("Reviewer" in e for e in outcome["errors"])


def test_content_review_json_confirmation_alone_cannot_bypass_ocr(
    tmp_path: Path,
) -> None:
    # Symmetric case: content-review.json confirms "Reviewer" but
    # review.json does not.
    figures = _figures_dir(tmp_path)
    _write_core_sidecars_without_content_review(figures, "master_spine")
    _write_json(
        figures / "master_spine.content-review.json",
        {
            "score_1_to_5": 5,
            "unresolved_numeric_mismatches": [],
            "keep_or_regenerate": "keep",
            "confirmed_labels": ["Reviewer"],
        },
    )
    text_without_reviewer = _full_ocr_text_for("master_spine").replace("Reviewer", "")
    ocr_runner = _ocr_runner_returning(text_without_reviewer)

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("Reviewer" in e for e in outcome["errors"])


@pytest.mark.parametrize("sidecar_suffix", ["inspect.json", "png.json", "provenance.json"])
def test_sidecar_fails_when_no_recorded_hash_present_not_only_on_mismatch(
    tmp_path: Path, sidecar_suffix: str
) -> None:
    # A sidecar that records no sha256/output_sha256 at all must fail
    # (missing provenance is not "trust it"), not just a sidecar that
    # records the wrong hash.
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    _write_json(figures / f"master_spine.{sidecar_suffix}", {"width": 1536, "height": 1024})
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        sidecar_suffix in e and ("missing" in e or "no recorded" in e)
        for e in outcome["errors"]
    ), outcome["errors"]


# ---------------------------------------------------------------------------
# write_validation_manifest
# ---------------------------------------------------------------------------


def _full_ocr_text_for_all(contracts) -> str:
    return " ".join(
        label for contract in contracts.values() for label in contract.required_labels
    ) + " artifact digest artifact digest website snapshot website snapshot website snapshot website snapshot"


def test_write_validation_manifest_writes_expected_file(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    for stem, contract in vaf.FIGURE_CONTRACTS.items():
        _write_full_sidecars(figures, stem, data_figure=contract.data_figure)

    all_text = _full_ocr_text_for_all(vaf.FIGURE_CONTRACTS)
    ocr_runner = _ocr_runner_returning(all_text)

    manifest = vaf.write_validation_manifest(tmp_path, ocr_runner=ocr_runner)

    manifest_path = figures / "AI_FIGURE_VALIDATION.json"
    assert manifest_path.is_file()
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["figure_count"] == 8
    assert on_disk["overall_status"] == "pass"
    assert manifest["overall_status"] == "pass"


def test_write_validation_manifest_hash_matches_actual_png_bytes(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    for stem, contract in vaf.FIGURE_CONTRACTS.items():
        _write_full_sidecars(figures, stem, data_figure=contract.data_figure)

    all_text = _full_ocr_text_for_all(vaf.FIGURE_CONTRACTS)
    ocr_runner = _ocr_runner_returning(all_text)

    manifest = vaf.write_validation_manifest(tmp_path, ocr_runner=ocr_runner)

    for entry in manifest["figures"]:
        stem = entry["stem"]
        actual_sha256 = vaf._sha256(figures / f"{stem}.png")
        assert entry["output_sha256"] == actual_sha256


def test_write_validation_manifest_overall_status_fails_if_any_figure_fails(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    for stem, contract in vaf.FIGURE_CONTRACTS.items():
        _write_full_sidecars(figures, stem, data_figure=contract.data_figure)
    # Break exactly one figure's dimensions.
    _write_png(figures / "paper_portfolio.png", 800, 600)

    all_text = _full_ocr_text_for_all(vaf.FIGURE_CONTRACTS)
    ocr_runner = _ocr_runner_returning(all_text)

    manifest = vaf.write_validation_manifest(tmp_path, ocr_runner=ocr_runner)

    assert manifest["overall_status"] == "fail"
    statuses = {f["stem"]: f["status"] for f in manifest["figures"]}
    assert statuses["paper_portfolio"] == "fail"
    assert statuses["master_spine"] == "pass"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_validate_stem_reports_pass_for_consistent_fixture(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    def fake_run(cmd, **kwargs):
        return _Completed(_full_ocr_text_for("master_spine"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = vaf.main(
        ["--root", str(tmp_path), "validate", "--stem", "master_spine"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["status"] == "pass"


def test_cli_validate_stem_reports_failure_exit_code(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _figures_dir(tmp_path)  # no PNG written -> guaranteed failure

    exit_code = vaf.main(
        ["--root", str(tmp_path), "validate", "--stem", "master_spine"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert payload["status"] == "fail"


def test_cli_ocr_stem_writes_sidecars_and_prints_json(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    figures = _figures_dir(tmp_path)
    image_path = figures / "master_spine.png"
    _write_png(image_path, 1536, 1024)

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    def fake_run(cmd, **kwargs):
        return _Completed(_full_ocr_text_for("master_spine"))

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = vaf.main(["--root", str(tmp_path), "ocr", "--stem", "master_spine"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["unresolved"] == []
    assert (figures / "master_spine.ocr.txt").is_file()
    assert (figures / "master_spine.ocr.json").is_file()
    on_disk = json.loads((figures / "master_spine.ocr.json").read_text())
    assert on_disk["unresolved"] == []


def test_cli_validate_all_write_manifest_creates_manifest_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    figures = _figures_dir(tmp_path)
    for stem, contract in vaf.FIGURE_CONTRACTS.items():
        _write_full_sidecars(figures, stem, data_figure=contract.data_figure)

    all_text = _full_ocr_text_for_all(vaf.FIGURE_CONTRACTS)

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    def fake_run(cmd, **kwargs):
        return _Completed(all_text)

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = vaf.main(
        ["--root", str(tmp_path), "validate-all", "--write-manifest"]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["overall_status"] == "pass"
    assert (figures / "AI_FIGURE_VALIDATION.json").is_file()


def test_cli_validate_all_without_write_manifest_does_not_create_file(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    figures = _figures_dir(tmp_path)
    for stem, contract in vaf.FIGURE_CONTRACTS.items():
        _write_full_sidecars(figures, stem, data_figure=contract.data_figure)

    all_text = _full_ocr_text_for_all(vaf.FIGURE_CONTRACTS)

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    def fake_run(cmd, **kwargs):
        return _Completed(all_text)

    monkeypatch.setattr(subprocess, "run", fake_run)

    exit_code = vaf.main(["--root", str(tmp_path), "validate-all"])
    captured = capsys.readouterr()
    json.loads(captured.out)  # must still be valid JSON

    assert exit_code == 0
    assert not (figures / "AI_FIGURE_VALIDATION.json").is_file()


# ---------------------------------------------------------------------------
# The validator must not draw or generate images.
# ---------------------------------------------------------------------------


def test_module_does_not_import_drawing_or_generation_libraries() -> None:
    source = _MODULE_PATH.read_text(encoding="utf-8")
    forbidden_substrings = [
        "matplotlib",
        "ImageDraw",
        "Image.new(",
        "image_tool",
        "requests.post",
        "openai",
        "urlopen",
    ]
    for forbidden in forbidden_substrings:
        assert forbidden not in source, f"unexpected drawing/generation dependency: {forbidden!r}"


def test_module_has_no_image_generation_function() -> None:
    assert not hasattr(vaf, "generate")
    assert not hasattr(vaf, "draw")


# ===========================================================================
# Task 2: eight complete Blue-Gold Precision Atlas prompts + two review
# rubrics. These tests are the prompt-authoring contract: they assert every
# prompt is independently complete (shared style block + exact pinned
# labels/relationships + OCR-friendly horizontal typography + negative
# prompt), that the two data prompts derive every numeric token from the
# committed evidence JSON and prohibit extras, and they pin each prompt/rubric
# file's bytes to a recorded SHA-256 so the eventual IMAGE2_FIGURES.json
# prompt-hash provenance has a test-enforced anchor.
# ===========================================================================

_FIGURES_DIR = _REPO_ROOT / "technical_report" / "figures"
_EVIDENCE_DIR = _REPO_ROOT / "technical_report" / "evidence"

_ALL_STEMS: tuple[str, ...] = tuple(vaf.FIGURE_CONTRACTS)
_DATA_STEMS: tuple[str, ...] = tuple(
    stem for stem, c in vaf.FIGURE_CONTRACTS.items() if c.data_figure
)
_CONCEPT_STEMS: tuple[str, ...] = tuple(
    stem for stem, c in vaf.FIGURE_CONTRACTS.items() if not c.data_figure
)

# Verbatim delimiters that must bracket, byte-for-byte, the one shared style
# block copied into every prompt, and the exact-token region of each prompt.
_STYLE_BEGIN = "=== BEGIN SHARED STYLE BLOCK: Blue-Gold Precision Atlas ==="
_STYLE_END = "=== END SHARED STYLE BLOCK: Blue-Gold Precision Atlas ==="
_PINNED_BEGIN = "=== BEGIN PINNED LABELS ==="
_PINNED_END = "=== END PINNED LABELS ==="

_PALETTE_HEXES = ("#FBFAF6", "#315BCE", "#214884", "#C38A20", "#24272B")

_REVIEW_RUBRIC = "ai_figure_review_rubric.txt"
_CONTENT_RUBRIC = "ai_figure_content_rubric.txt"

# Byte-for-byte SHA-256 pins. Placeholder during RED; filled with the real
# digests once the prompt/rubric bytes are authored (GREEN). Any later edit to
# a prompt must deliberately update its pin (and regenerate the figure).
_PROMPT_SHA256: dict[str, str] = {
    "master_spine": "b740535eb054fbf76a6cbb9da31655d70bddd521cf19bc4a7df620344ea1d8a9",
    "dense_intelligence": "57346912336050548a3eb1cb81155c07fcf42a3791c4f6a0d13282484a8d582a",
    "system_planes": "8e6e52237aed476b10aa2d10ff1c2ef6784d55d0f6c6c94ace43c2ef053e813d",
    "argus_architecture": "732e1bb9d00ecc0cb131c8347ea9c26590758d62cd0a6b16128f90d90066e1f7",
    "mission_lifecycle": "c087d16de9546dff53999e14892f735cf4c94d91eefc80ae04652a60c15d7974",
    "long_horizon_reliability": "b573d9501910a9768a4177d0d728c1cd1beb4266efb4ffacfea8e47138a1e4fc",
    "public_results": "03dcbb9679f79c8e217207c7c311f6be18e62f229fcde7ff3eea009b76a3185e",
    "paper_portfolio": "a584596ee35115223e17c2b7472d3204b61b0010b4088201f4d01f235d60f6e9",
}
_RUBRIC_SHA256: dict[str, str] = {
    _REVIEW_RUBRIC: "b0d3ba5b528df9a13ad6cd8e578e9567b73b1066d39d81fcff6478985c266fc5",
    _CONTENT_RUBRIC: "944d90d8d492eeadf0c5b54a9b0b7a977c6aa4c6ed6c9c9e385524799e04174c",
}


def _read_prompt(stem: str) -> str:
    return (_FIGURES_DIR / f"{stem}.prompt.txt").read_text(encoding="utf-8")


def _extract_block(text: str, begin: str, end: str) -> str:
    assert begin in text, f"missing block-begin marker {begin!r}"
    assert end in text, f"missing block-end marker {end!r}"
    return text.split(begin, 1)[1].split(end, 1)[0]


def _numbers_in(text: str) -> set[str]:
    """Every standalone integer/decimal numeric run in ``text``."""
    return set(re.findall(r"\d+(?:\.\d+)?", text))


# --- every prompt exists and is independently complete --------------------


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_prompt_file_exists(stem: str) -> None:
    assert (_FIGURES_DIR / f"{stem}.prompt.txt").is_file()


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_prompt_contains_every_required_label_verbatim(stem: str) -> None:
    text = _read_prompt(stem)
    missing = [
        label
        for label in vaf.FIGURE_CONTRACTS[stem].required_labels
        if label not in text
    ]
    assert not missing, f"{stem} prompt missing pinned labels: {missing}"


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_prompt_pins_every_label_in_dedicated_block(stem: str) -> None:
    block = _extract_block(_read_prompt(stem), _PINNED_BEGIN, _PINNED_END)
    missing = [
        label
        for label in vaf.FIGURE_CONTRACTS[stem].required_labels
        if label not in block
    ]
    assert not missing, f"{stem} pinned-labels block missing: {missing}"


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_prompt_declares_spell_exactly_and_no_invented_labels(stem: str) -> None:
    text = _read_prompt(stem).lower()
    assert "spell" in text and "exactly" in text
    assert "invent" in text  # "do not invent ... labels"


# --- one shared style block, copied byte-for-byte into all eight ----------


def test_shared_style_block_is_byte_identical_across_all_prompts() -> None:
    blocks = {
        stem: _extract_block(_read_prompt(stem), _STYLE_BEGIN, _STYLE_END)
        for stem in _ALL_STEMS
    }
    reference = blocks[_ALL_STEMS[0]]
    for stem, block in blocks.items():
        assert block == reference, f"shared style block differs in {stem}"


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_shared_style_block_declares_full_palette(stem: str) -> None:
    block = _extract_block(_read_prompt(stem), _STYLE_BEGIN, _STYLE_END)
    assert "Blue-Gold Precision Atlas" in block
    for hexcode in _PALETTE_HEXES:
        assert hexcode in block, f"{stem} style block missing palette {hexcode}"


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_shared_style_block_declares_canvas_1536x1024(stem: str) -> None:
    block = _extract_block(_read_prompt(stem), _STYLE_BEGIN, _STYLE_END)
    assert "1536" in block and "1024" in block


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_shared_style_block_requires_ocr_friendly_horizontal_typography(
    stem: str,
) -> None:
    block = _extract_block(_read_prompt(stem), _STYLE_BEGIN, _STYLE_END).lower()
    assert "ocr" in block
    assert "horizontal" in block
    assert "no vertical" in block
    assert "no curved" in block or "no rotated" in block


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_prompt_has_negative_prompt_with_spec_prohibitions(stem: str) -> None:
    text = _read_prompt(stem).lower()
    assert "negative prompt" in text
    for banned in (
        "cyberpunk",
        "neon",
        "robot",
        "brain",
        "face",
        "dashboard",
        "logo",
        "watermark",
        "badge",
    ):
        assert banned in text, f"{stem} negative prompt missing {banned!r}"


# --- data prompts: evidence-derived numbers, extras prohibited ------------


@pytest.mark.parametrize("stem", _DATA_STEMS)
def test_data_prompt_cites_its_committed_evidence_source(stem: str) -> None:
    text = _read_prompt(stem)
    for src in vaf.FIGURE_CONTRACTS[stem].source_evidence:
        assert src in text, f"{stem} prompt does not cite evidence source {src}"


@pytest.mark.parametrize("stem", _DATA_STEMS)
def test_data_prompt_prohibits_extra_numbers_and_labels(stem: str) -> None:
    text = _read_prompt(stem)
    lower = text.lower()
    assert "PROHIBIT EXTRAS" in text
    assert "not pinned" in lower
    assert "no other number" in lower or "any number" in lower


def test_public_results_prohibits_shared_or_normalized_scale() -> None:
    lower = _read_prompt("public_results").lower()
    assert "normalized scale" in lower or "shared scale" in lower
    assert "panel-local" in lower


@pytest.mark.parametrize("stem", _DATA_STEMS)
def test_data_prompt_pinned_numbers_are_exactly_contract_numbers(
    stem: str,
) -> None:
    block = _extract_block(_read_prompt(stem), _PINNED_BEGIN, _PINNED_END)
    contract = vaf.FIGURE_CONTRACTS[stem]
    expected: set[str] = set()
    for label in contract.required_labels:
        expected |= _numbers_in(label)
    assert _numbers_in(block) == expected, (
        f"{stem} pinned block has numeric tokens beyond its frozen contract"
    )


@pytest.mark.parametrize("stem", _DATA_STEMS)
def test_data_prompt_numbers_appear_in_evidence_json(stem: str) -> None:
    contract = vaf.FIGURE_CONTRACTS[stem]
    blob = json.dumps(
        json.loads(
            (_REPO_ROOT / contract.source_evidence[0]).read_text(encoding="utf-8")
        )
    )
    for label in contract.required_labels:
        for num in _numbers_in(label):
            assert num in blob, f"{stem} token {label!r} number {num} not in evidence"


def test_public_results_numbers_derive_from_website_evidence() -> None:
    site = json.loads(
        (_EVIDENCE_DIR / "website_results.json").read_text(encoding="utf-8")
    )
    results = {r["arena"]: r for r in site["results"]}
    # Each pinned value token is the literal published website value.
    assert results["nanochat \u00b7 B200"]["result"] == "0.9636 BPB"
    assert "0.9646" in results["nanochat \u00b7 B200"]["human_comparison"]
    assert results["nanochat \u00b7 H100"]["result"] == "0.9855 BPB"
    assert "0.9879" in results["nanochat \u00b7 H100"]["human_comparison"]
    assert "79.77" in results["nanoGPT speedrun"]["result"]
    assert "80.18" in results["nanoGPT speedrun"]["human_comparison"]
    assert results["AARRI-Bench"]["result"] == "63/82 \u00b7 76.8%"
    assert "68.3%" in results["AARRI-Bench"]["human_comparison"]
    assert results["Arbor \u00b7 RUC NLPIR"]["result"] == "28.0 gap"
    for token in ("Arbor 20.83", "Claude Code 8.33", "Codex 6.25"):
        assert token in results["Arbor \u00b7 RUC NLPIR"]["human_comparison"]
    assert results["NVIDIA SOL-ExecBench"]["result"] == "Global #6 \u00b7 2\u00d7 #1 \u00b7 7 top-3"


def test_public_results_status_counts_derive_from_corroboration_evidence() -> None:
    site = json.loads(
        (_EVIDENCE_DIR / "website_results.json").read_text(encoding="utf-8")
    )
    results = {r["arena"]: r for r in site["results"]}
    digest = sorted(
        a for a, r in results.items() if r["corroboration"] == "local_artifact"
    )
    snapshot = sorted(
        a for a, r in results.items() if r["corroboration"] == "website_snapshot"
    )
    assert digest == sorted(["nanochat \u00b7 B200", "nanoGPT speedrun"])
    assert len(snapshot) == 4
    counts = vaf.FIGURE_CONTRACTS["public_results"].status_counts
    assert counts == {"artifact digest": 2, "website snapshot": 4}
    assert len(digest) == counts["artifact digest"]
    assert len(snapshot) == counts["website snapshot"]


def test_public_results_prompt_assigns_status_to_correct_panels() -> None:
    text = _read_prompt("public_results")
    assert '"artifact digest" appears on exactly two panels' in text
    assert '"website snapshot" appears on exactly four panels' in text
    # the two digest panels are the two local-artifact arenas
    digest_section = text.split('"artifact digest" appears on exactly two panels', 1)[1]
    digest_section = digest_section.split('"website snapshot"', 1)[0]
    assert "nanochat \u00b7 B200" in digest_section
    assert "nanoGPT speedrun" in digest_section


def test_paper_portfolio_numbers_derive_from_inventory_evidence() -> None:
    inv = json.loads(
        (_EVIDENCE_DIR / "paper_inventory.json").read_text(encoding="utf-8")
    )
    assert inv["totals"]["papers"] == 41
    assert inv["totals"]["manuscript"] == 35
    assert inv["totals"]["draft"] == 6
    assert inv["totals"]["manuscript"] + inv["totals"]["draft"] == 41
    program_counts = inv["program_counts"]
    assert sum(program_counts.values()) == 41
    expected = {
        "Multimodal & Vision-Language Models": 16,
        "Cognitive Bias in LLMs": 9,
        "Efficiency, Compression & Decoding": 7,
        "LLM Agent Methods": 5,
        "World Models": 2,
        "State Trace & Auditability": 2,
    }
    assert program_counts == expected


def test_paper_portfolio_prompt_forbids_acceptance_status() -> None:
    text = _read_prompt("paper_portfolio")
    assert "output inventory \u00b7 not accepted papers" in text
    lower = text.lower()
    assert "accept" in lower  # must speak to (and forbid) acceptance framing


# --- concept prompts: relationships present -------------------------------


@pytest.mark.parametrize("stem", _CONCEPT_STEMS)
def test_concept_prompt_states_required_relationships(stem: str) -> None:
    text = _read_prompt(stem)
    assert "Required relationships" in text or "RELATIONSHIPS" in text


# --- byte-for-byte SHA-256 pins (provenance anchor) -----------------------


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_prompt_bytes_match_pinned_sha256(stem: str) -> None:
    actual = hashlib.sha256(
        (_FIGURES_DIR / f"{stem}.prompt.txt").read_bytes()
    ).hexdigest()
    assert actual == _PROMPT_SHA256[stem], (
        f"{stem}.prompt.txt bytes changed; update its pinned hash and regenerate"
    )


@pytest.mark.parametrize("name", [_REVIEW_RUBRIC, _CONTENT_RUBRIC])
def test_rubric_bytes_match_pinned_sha256(name: str) -> None:
    actual = hashlib.sha256((_FIGURES_DIR / name).read_bytes()).hexdigest()
    assert actual == _RUBRIC_SHA256[name], (
        f"{name} bytes changed; update its pinned hash"
    )


# --- the two review rubrics ------------------------------------------------


def test_review_rubric_is_semantic_and_covers_every_figure() -> None:
    text = (_FIGURES_DIR / _REVIEW_RUBRIC).read_text(encoding="utf-8")
    assert "keep_or_regenerate" in text
    assert "confirmed_labels" in text
    lower = text.lower()
    assert "semantic" in lower
    for banned in ("robot", "brain", "watermark"):
        assert banned in lower
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert contract.title in text, f"review rubric omits {contract.title}"


def test_content_rubric_is_exact_content_and_covers_every_figure() -> None:
    text = (_FIGURES_DIR / _CONTENT_RUBRIC).read_text(encoding="utf-8")
    assert "keep_or_regenerate" in text
    assert "confirmed_labels" in text
    assert "unresolved_numeric_mismatches" in text
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert contract.title in text, f"content rubric omits {contract.title}"


def test_content_rubric_adds_strict_numeric_source_for_data_figures() -> None:
    text = (_FIGURES_DIR / _CONTENT_RUBRIC).read_text(encoding="utf-8")
    assert "technical_report/evidence/website_results.json" in text
    assert "technical_report/evidence/paper_inventory.json" in text
    lower = text.lower()
    assert "numeric" in lower and ("source" in lower or "evidence" in lower)
    # the two data-figure titles must be named as the stricter cases
    assert "Public Results" in text
    assert "Paper Portfolio" in text
