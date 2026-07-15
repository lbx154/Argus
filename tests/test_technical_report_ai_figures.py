"""Tests for technical_report/figures/validate_ai_figures.py (final hybrid contract).

The final integration scope is a HYBRID figure set:

  * SIX structural figures (master_spine, dense_intelligence, system_planes,
    argus_architecture, mission_lifecycle, long_horizon_reliability) are drawn by
    the gpt-image-2 image model and validated here against a public-safe
    provenance contract (dimensions, sidecar presence, hash/prompt consistency,
    no leaked local paths, and recorded OCR evidence). There is NO iterative
    model-review gate: the six rasters were accepted by the operator, so imperfect
    OCR of stylized text is evidence, never a rejection.
  * TWO deterministic data figures (public_results, paper_portfolio) are NOT
    covered here -- they are validated by build_report_figures.py and
    tests/test_technical_report_figures.py.

Fixture-based tests exercise the validator through temp directories; a final
block validates the real committed six-figure set and its IMAGE2_FIGURES.json.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIGURES_DIR = _REPO_ROOT / "technical_report" / "figures"
_MODULE_PATH = _FIGURES_DIR / "validate_ai_figures.py"
_MANIFEST_PATH = _FIGURES_DIR / "IMAGE2_FIGURES.json"

_EXPECTED_STEMS = (
    "master_spine",
    "dense_intelligence",
    "system_planes",
    "argus_architecture",
    "mission_lifecycle",
    "long_horizon_reliability",
)
_EXPECTED_FIGURE_IDS = (
    "master-spine",
    "dense-intelligence",
    "system-planes",
    "argus-architecture",
    "mission-lifecycle",
    "long-horizon-reliability",
)
_DATA_FIGURE_IDS = ("public_results", "public-results", "paper_portfolio", "paper-portfolio")


def _load_module():
    spec = importlib.util.spec_from_file_location("validate_ai_figures", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


vaf = _load_module()


# ---------------------------------------------------------------------------
# PNG + sidecar fixtures (no PIL: hand-roll a minimal valid PNG so the only
# thing under test is validate_ai_figures.py's own IHDR reader/hasher).
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
    raw_row = b"\x00" + (b"\xff\xff\xff" * width)
    raw = raw_row * height
    idat = zlib.compress(raw)
    path.write_bytes(
        signature + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
    )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


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
    png_overrides: dict[str, Any] | None = None,
    inspect_overrides: dict[str, Any] | None = None,
    provenance_overrides: dict[str, Any] | None = None,
) -> str:
    """Write a PNG plus every required sidecar for ``stem`` in the final hybrid
    naming (png.json / png.inspect.json / png.provenance.json / ocr.*). Returns
    the PNG sha256 so callers can assert hash-consistency behavior. No
    review.json/content-review.json is written: they are not part of the
    contract anymore.
    """
    png_path = figures / f"{stem}.png"
    _write_png(png_path, width, height)
    sha256 = vaf._sha256(png_path)

    prompt_path = figures / f"{stem}.prompt.txt"
    prompt_path.write_text("prompt body\n", encoding="utf-8")
    prompt_sha = vaf._sha256(prompt_path)

    png_json = {
        "output_sha256": sha256,
        "prompt_sha256": prompt_sha,
        "model": "gpt-image-2",
        "output_path": f"technical_report/figures/{stem}.png",
    }
    if png_overrides:
        png_json.update(png_overrides)
    _write_json(figures / f"{stem}.png.json", png_json)

    inspect_json = {"sha256": sha256, "width": width, "height": height}
    if inspect_overrides:
        inspect_json.update(inspect_overrides)
    _write_json(figures / f"{stem}.png.inspect.json", inspect_json)

    provenance_json = {
        "output_sha256": sha256,
        "prompt_sha256": prompt_sha,
        "generator": "codex-image2",
        "output_path": f"technical_report/figures/{stem}.png",
    }
    if provenance_overrides:
        provenance_json.update(provenance_overrides)
    _write_json(figures / f"{stem}.png.provenance.json", provenance_json)

    (figures / f"{stem}.ocr.txt").write_text("raw ocr text", encoding="utf-8")
    _write_json(
        figures / f"{stem}.ocr.json",
        {"expected_tokens": [], "unresolved": [], "coverage": 1.0},
    )
    return sha256


def _ocr_runner_returning(text: str):
    def _runner(image_path: Path) -> dict[str, Any]:
        normalized = vaf.normalize_ocr(text)
        return {
            "image": str(image_path),
            "psm_modes": list(vaf.PSM_MODES),
            "raw": {"psm_6": text, "psm_11": text, "psm_12": text},
            "normalized": {"psm_6": normalized, "psm_11": normalized, "psm_12": normalized},
            "combined_normalized": normalized,
        }

    return _runner


def _full_ocr_text_for(stem: str) -> str:
    contract = vaf.FIGURE_CONTRACTS[stem]
    return " ".join(contract.required_labels)


# ---------------------------------------------------------------------------
# FIGURE_CONTRACTS shape
# ---------------------------------------------------------------------------
def test_figure_contracts_has_exactly_six_entries() -> None:
    assert len(vaf.FIGURE_CONTRACTS) == 6


def test_figure_contracts_has_exact_expected_stems() -> None:
    assert tuple(vaf.FIGURE_CONTRACTS) == _EXPECTED_STEMS


def test_figure_contracts_has_exact_expected_kebab_ids() -> None:
    assert tuple(c.figure_id for c in vaf.FIGURE_CONTRACTS.values()) == _EXPECTED_FIGURE_IDS


def test_every_contract_carries_a_figure_type() -> None:
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert isinstance(contract.figure_type, str) and contract.figure_type


def test_no_data_figures_are_present_in_contracts() -> None:
    for ident in _DATA_FIGURE_IDS:
        assert ident not in vaf.FIGURE_CONTRACTS
        assert ident not in vaf._STEM_BY_ANY_ID


def test_required_sidecars_exclude_superseded_review_sidecars() -> None:
    suffixes = set(vaf._COMMON_SIDECAR_SUFFIXES)
    assert "review.json" not in suffixes
    assert "content-review.json" not in suffixes
    assert {"prompt.txt", "png.json", "png.inspect.json",
            "png.provenance.json", "ocr.txt", "ocr.json"} <= suffixes


def test_no_review_verdict_extractor_remains() -> None:
    # The dual-review workflow is gone; its extractor must not linger.
    assert not hasattr(vaf, "_extract_review_verdict")


def test_master_spine_required_labels_include_spine_chain() -> None:
    labels = set(vaf.FIGURE_CONTRACTS["master_spine"].required_labels)
    assert {"Every run expands the frontier.", "Unknown objective",
            "Manager", "Planner", "Engineer", "Reviewer"} <= labels


def test_long_horizon_reliability_required_labels_include_budget() -> None:
    labels = set(vaf.FIGURE_CONTRACTS["long_horizon_reliability"].required_labels)
    assert "1,800 s decision budget" in labels
    assert "Checkpoint" in labels


# ---------------------------------------------------------------------------
# normalize_ocr / normalize_ocr_for_matching
# ---------------------------------------------------------------------------
def test_normalize_ocr_collapses_whitespace_runs() -> None:
    assert vaf.normalize_ocr("a   b\t\nc") == "a b c"


def test_normalize_ocr_strips_leading_and_trailing_whitespace() -> None:
    assert vaf.normalize_ocr("  hello  ") == "hello"


def test_normalize_ocr_handles_none_and_empty_string() -> None:
    assert vaf.normalize_ocr(None) == ""
    assert vaf.normalize_ocr("") == ""


@pytest.mark.parametrize("variant", ["\u00d7", "\u2715", "\u2716", "\u2a2f"])
def test_normalize_ocr_maps_multiplication_variants_to_ascii_x(variant: str) -> None:
    assert vaf.normalize_ocr(f"1{variant}B200") == "1xB200"


@pytest.mark.parametrize("variant", ["\u2010", "\u2013", "\u2014", "\u2212"])
def test_normalize_ocr_maps_dash_variants_to_ascii_hyphen(variant: str) -> None:
    assert vaf.normalize_ocr(f"lower{variant}better") == "lower-better"


def test_normalize_ocr_never_alters_digits_or_decimals() -> None:
    assert vaf.normalize_ocr("0.9636") == "0.9636"
    assert vaf.normalize_ocr("79.77s") == "79.77s"


def test_normalize_ocr_is_idempotent() -> None:
    once = vaf.normalize_ocr("a  \u00d7  0.5")
    assert vaf.normalize_ocr(once) == once


def test_normalize_ocr_for_matching_is_distinct_function() -> None:
    assert vaf.normalize_ocr_for_matching is not vaf.normalize_ocr


def test_normalize_ocr_for_matching_maps_middle_dot_to_space() -> None:
    assert vaf.normalize_ocr_for_matching("nanochat \u00b7 B200") == "nanochat B200"


def test_normalize_ocr_for_matching_never_loosens_numbers() -> None:
    assert vaf.normalize_ocr_for_matching("0.9636") == "0.9636"
    assert vaf.normalize_ocr_for_matching("63/82") == "63/82"
    assert vaf.normalize_ocr_for_matching("76.8%") == "76.8%"


def test_normalize_ocr_for_matching_does_not_make_1800_equal_180() -> None:
    assert vaf.normalize_ocr_for_matching("1800") != vaf.normalize_ocr_for_matching("180")


# ---------------------------------------------------------------------------
# run_tesseract
# ---------------------------------------------------------------------------
def test_run_tesseract_raises_for_missing_image(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        vaf.run_tesseract(tmp_path / "nope.png")


def test_run_tesseract_invokes_psm_6_11_and_12(tmp_path, monkeypatch) -> None:
    image = tmp_path / "img.png"
    _write_png(image, 8, 8)
    seen_psm: list[str] = []

    class _Completed:
        returncode = 0
        stdout = "text"
        stderr = ""

    def fake_run(cmd, capture_output, text, check):  # noqa: ANN001
        seen_psm.append(cmd[cmd.index("--psm") + 1])
        return _Completed()

    monkeypatch.setattr(vaf.subprocess, "run", fake_run)
    vaf.run_tesseract(image)
    assert seen_psm == ["6", "11", "12"]


def test_run_tesseract_raises_on_nonzero_exit(tmp_path, monkeypatch) -> None:
    image = tmp_path / "img.png"
    _write_png(image, 8, 8)

    class _Completed:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(vaf.subprocess, "run", lambda *a, **k: _Completed())
    with pytest.raises(RuntimeError):
        vaf.run_tesseract(image)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_run_tesseract_reads_real_committed_master_spine() -> None:
    result = vaf.run_tesseract(_FIGURES_DIR / "master_spine.png")
    assert set(result["raw"]) == {"psm_6", "psm_11", "psm_12"}
    assert isinstance(result["combined_normalized"], str)


# ---------------------------------------------------------------------------
# validate_figure (fixture-based)
# ---------------------------------------------------------------------------
def test_validate_figure_passes_when_everything_is_consistent(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert outcome["status"] == "pass", outcome["errors"]
    assert outcome["errors"] == []


def test_validate_figure_accepts_kebab_figure_id(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master-spine", ocr_runner=ocr)
    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_fails_when_image_missing(tmp_path: Path) -> None:
    _figures_dir(tmp_path)
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=_ocr_runner_returning(""))
    assert outcome["status"] == "fail"
    assert any("missing figure image" in e for e in outcome["errors"])


def test_validate_figure_fails_on_wrong_dimensions(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine", width=1024, height=1024)
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert outcome["status"] == "fail"
    assert any("dimension mismatch" in e for e in outcome["errors"])


def test_validate_figure_fails_when_a_sidecar_is_missing(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    (figures / "master_spine.png.provenance.json").unlink()
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert outcome["status"] == "fail"
    assert any("missing sidecar" in e for e in outcome["errors"])


def test_validate_figure_fails_when_inspect_hash_mismatches(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine", inspect_overrides={"sha256": "0" * 64})
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert outcome["status"] == "fail"
    assert any("hash mismatch in png.inspect.json" in e for e in outcome["errors"])


def test_validate_figure_fails_when_prompt_hash_mismatches(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine", png_overrides={"prompt_sha256": "f" * 64})
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert outcome["status"] == "fail"
    assert any("prompt hash mismatch in png.json" in e for e in outcome["errors"])


def test_validate_figure_fails_when_sidecar_leaks_local_path(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(
        figures,
        "master_spine",
        provenance_overrides={"leaked": "/home/argustest/.argus-skill/vault.json"},
    )
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert outcome["status"] == "fail"
    assert any("leaks a local path" in e for e in outcome["errors"])


def test_validate_figure_fails_when_vault_reference_leaks(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(
        figures,
        "master_spine",
        png_overrides={"api": {"key_source": "vault:/x/model_api.json:routes.image"}},
    )
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert outcome["status"] == "fail"
    assert any("leaks a local path" in e for e in outcome["errors"])


def test_validate_figure_unresolved_ocr_labels_are_warning_not_error(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    # OCR returns nothing: every required label is unresolved, yet the
    # operator-accepted raster must still PASS (evidence, not rejection).
    outcome = vaf.validate_figure(
        tmp_path, "master_spine", ocr_runner=_ocr_runner_returning("")
    )
    assert outcome["status"] == "pass", outcome["errors"]
    assert outcome["errors"] == []
    assert outcome["warnings"]
    assert outcome["ocr"]["unresolved_labels"]
    assert outcome["ocr"]["label_coverage"] == 0.0


def test_validate_figure_records_validation_route(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    _write_full_sidecars(figures, "master_spine")
    ocr = _ocr_runner_returning(_full_ocr_text_for("master_spine"))
    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr)
    assert "operator-accepted" in outcome["validation_route"]


def test_validate_figure_raises_for_unknown_figure_id(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        vaf.validate_figure(tmp_path, "not-a-figure")


def test_validate_figure_rejects_removed_data_figure_ids(tmp_path: Path) -> None:
    for ident in _DATA_FIGURE_IDS:
        with pytest.raises(KeyError):
            vaf.validate_figure(tmp_path, ident)


# ---------------------------------------------------------------------------
# Real committed IMAGE2_FIGURES.json manifest + rasters
# ---------------------------------------------------------------------------
def _load_manifest() -> dict[str, Any]:
    return json.loads(_MANIFEST_PATH.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _no_local_paths(payload: Any) -> bool:
    markers = ("/home/", "/Users/", "/root/", "session-state", ".argus-skill", "vault:")
    text = json.dumps(payload)
    return not any(m in text for m in markers)


def test_committed_manifest_has_exactly_six_entries() -> None:
    manifest = _load_manifest()
    assert manifest["figure_count"] == 6
    assert len(manifest["figures"]) == 6


def test_committed_manifest_has_expected_figure_ids() -> None:
    manifest = _load_manifest()
    ids = {e["figure_id"] for e in manifest["figures"]}
    assert ids == set(_EXPECTED_FIGURE_IDS)


def test_committed_manifest_entries_hash_match_rasters_and_prompts() -> None:
    manifest = _load_manifest()
    for entry in manifest["figures"]:
        png = _REPO_ROOT / entry["output_path"]
        prompt = _REPO_ROOT / entry["prompt_path"]
        assert png.is_file(), entry["output_path"]
        assert _sha256_file(png) == entry["output_sha256"]
        assert _sha256_file(prompt) == entry["prompt_sha256"]


def test_committed_manifest_entries_are_1536x1024() -> None:
    manifest = _load_manifest()
    for entry in manifest["figures"]:
        assert (entry["width"], entry["height"]) == (1536, 1024)


def test_committed_manifest_has_no_local_paths_or_secrets() -> None:
    assert _no_local_paths(_load_manifest())


def test_committed_provenance_present_and_public_safe() -> None:
    manifest = _load_manifest()
    for entry in manifest["figures"]:
        prov_path = _REPO_ROOT / entry["generation_provenance_path"]
        assert prov_path.is_file(), entry["generation_provenance_path"]
        prov = json.loads(prov_path.read_text(encoding="utf-8"))
        assert prov["output_sha256"] == entry["output_sha256"]
        assert prov["prompt_sha256"] == entry["prompt_sha256"]
        assert _no_local_paths(prov)


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
@pytest.mark.parametrize("stem", _EXPECTED_STEMS)
def test_real_committed_figure_validates(stem: str) -> None:
    outcome = vaf.validate_figure(_REPO_ROOT, stem)
    assert outcome["status"] == "pass", outcome["errors"]
    assert outcome["errors"] == []
    assert outcome["dimensions"] == {"width": 1536, "height": 1024}
