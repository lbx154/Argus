"""Tests for technical_report/figures/validate_ai_figures.py.

These tests exercise the AI figure content-contract, OCR, and validation
module entirely through fixtures/temp directories built in-test. They never
depend on the current production figure set under
``technical_report/figures/``.

Scope: the validator covers only the SIX structural/concept figures that are
regenerated with an image model. The two data figures (``public_results``,
``paper_portfolio``) remain deterministically drawn and are validated by
``build_report_figures.py`` and the deterministic-figure tests, not here.
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


def _review_wrapper(verdict: dict[str, Any], *, fence: bool = False) -> dict[str, Any]:
    """Build the *real* wrapper shape written by
    ``argus_skill.tools.image_tool.review_image(..., out=...)``: the
    verdict object is JSON-encoded as a *string* inside the top-level
    "review" field, alongside the other fields the real tool always writes
    (``image``, ``model``, ``endpoint``, ``prompt``, ``rubric``). Every test
    fixture in this file writes ``.review.json``/``.content-review.json`` in
    this exact shape -- never the flattened (verdict-at-top-level) shape --
    so the tests exercise the validator against reality, not a fixture
    convenience shortcut that would hide a real parsing bug.
    """
    review_text = json.dumps(verdict)
    if fence:
        review_text = f"```json\n{review_text}\n```"
    return {
        "image": {
            "path": "figure.png",
            "width": 1536,
            "height": 1024,
            "sha256": "0" * 64,
        },
        "model": "gpt-5-vision",
        "endpoint": "/responses",
        "prompt": "prompt body",
        "rubric": "rubric body",
        "review": review_text,
    }


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
    review_overrides: dict[str, Any] | None = None,
    content_review_overrides: dict[str, Any] | None = None,
) -> str:
    """Write a PNG plus every required sidecar for ``stem`` and return its
    sha256 so callers can assert hash-consistency behavior.

    Every structural figure gets a ``content-review.json`` second independent
    exact-content vision review, per the approved Generation Workflow.
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
    _write_json(figures / f"{stem}.review.json", _review_wrapper(review_payload))

    (figures / f"{stem}.ocr.txt").write_text("raw ocr text", encoding="utf-8")
    _write_json(
        figures / f"{stem}.ocr.json",
        {"expected_tokens": [], "unresolved": [], "coverage": 1.0},
    )

    content_review_payload = {
        "score_1_to_5": 5,
        "extra_tokens_present": [],
        "keep_or_regenerate": "keep",
    }
    if content_review_overrides:
        content_review_payload.update(content_review_overrides)
    _write_json(
        figures / f"{stem}.content-review.json",
        _review_wrapper(content_review_payload),
    )

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


def test_figure_contracts_has_exactly_six_entries() -> None:
    assert len(vaf.FIGURE_CONTRACTS) == 6


def test_figure_contracts_has_exact_expected_stems() -> None:
    expected_stems = {
        "master_spine",
        "dense_intelligence",
        "system_planes",
        "argus_architecture",
        "mission_lifecycle",
        "long_horizon_reliability",
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
    }
    actual_ids = {c.figure_id for c in vaf.FIGURE_CONTRACTS.values()}
    assert actual_ids == expected_ids


def test_no_data_figures_are_present() -> None:
    # The two data figures are deterministic and out of scope for this
    # validator; they must never appear in the AI contract set.
    assert "public_results" not in vaf.FIGURE_CONTRACTS
    assert "paper_portfolio" not in vaf.FIGURE_CONTRACTS


def test_contract_has_no_data_figure_attributes() -> None:
    # The AI validator no longer carries data-figure status counts,
    # source-evidence, or the data_figure flag.
    contract = vaf.FIGURE_CONTRACTS["master_spine"]
    assert not hasattr(contract, "data_figure")
    assert not hasattr(contract, "status_counts")
    assert not hasattr(contract, "source_evidence")


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


def test_long_horizon_reliability_required_labels_match_spec() -> None:
    contract = vaf.FIGURE_CONTRACTS["long_horizon_reliability"]
    required = {
        "Argus long-horizon cycle",
        "Checkpoint",
        "Decision progress",
        "Supervised background jobs",
        "Safe round boundary",
        "1,800 s decision budget",
        "Return to Planner",
    }
    assert required <= set(contract.required_labels)


def test_every_concept_figure_sidecar_suffixes_include_content_review() -> None:
    # Every one of the six structural figures gets a second independent
    # exact-content vision review.
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert "content-review.json" in contract.sidecar_suffixes, contract.stem


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
    assert vaf.normalize_ocr("a   b\t c\n d") == "a b c d"


def test_normalize_ocr_strips_leading_and_trailing_whitespace() -> None:
    assert vaf.normalize_ocr("   hello world   ") == "hello world"


def test_normalize_ocr_handles_none_and_empty_string() -> None:
    assert vaf.normalize_ocr(None) == ""
    assert vaf.normalize_ocr("") == ""


@pytest.mark.parametrize("variant", ["\u00d7", "\u2715", "\u2716", "\u2a2f", "\u2062"])
def test_normalize_ocr_maps_multiplication_variants_to_ascii_x(variant: str) -> None:
    assert vaf.normalize_ocr(f"2{variant} #1") == "2x #1"


@pytest.mark.parametrize(
    "variant",
    ["\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\u2212"],
)
def test_normalize_ocr_maps_dash_variants_to_ascii_hyphen(variant: str) -> None:
    assert vaf.normalize_ocr(f"Long{variant}Horizon") == "Long-Horizon"


def test_normalize_ocr_never_alters_digits() -> None:
    assert vaf.normalize_ocr("112 typed events") == "112 typed events"
    assert "1,800" in vaf.normalize_ocr("1,800 s decision budget")


def test_normalize_ocr_never_alters_decimal_points() -> None:
    assert vaf.normalize_ocr("28.0 gap") == "28.0 gap"
    assert "0.9636" in vaf.normalize_ocr("0.9636 BPB")


def test_normalize_ocr_does_not_touch_fullwidth_unicode_digits() -> None:
    # A full-width digit is a different character; normalize must not fold it
    # into an ascii digit (that would be a silent numeric rewrite).
    fullwidth = "\uff11\uff12"  # "１２"
    assert vaf.normalize_ocr(fullwidth) == fullwidth


def test_normalize_ocr_is_idempotent() -> None:
    once = vaf.normalize_ocr("2\u00d7   #1\n\ntop-3")
    assert vaf.normalize_ocr(once) == once


# ---------------------------------------------------------------------------
# run_tesseract
# ---------------------------------------------------------------------------


def test_run_tesseract_raises_for_missing_image(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        vaf.run_tesseract(tmp_path / "nope.png")


def test_run_tesseract_invokes_psm_6_11_and_12(tmp_path, monkeypatch) -> None:
    image = tmp_path / "x.png"
    _write_png(image, 1536, 1024)
    seen_psm: list[str] = []

    class _Completed:
        def __init__(self) -> None:
            self.stdout = "text"
            self.stderr = ""
            self.returncode = 0

    def fake_run(cmd, **kwargs):
        # cmd == [bin, image, "stdout", "--psm", N]
        seen_psm.append(cmd[cmd.index("--psm") + 1])
        return _Completed()

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vaf.run_tesseract(image)

    assert seen_psm == ["6", "11", "12"]
    assert set(result["raw"]) == {"psm_6", "psm_11", "psm_12"}


def test_run_tesseract_retains_all_raw_psm_outputs_even_when_they_differ(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "x.png"
    _write_png(image, 1536, 1024)
    outputs = {"6": "alpha", "11": "beta", "12": "gamma"}

    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    def fake_run(cmd, **kwargs):
        return _Completed(outputs[cmd[cmd.index("--psm") + 1]])

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = vaf.run_tesseract(image)

    assert result["raw"]["psm_6"] == "alpha"
    assert result["raw"]["psm_11"] == "beta"
    assert result["raw"]["psm_12"] == "gamma"


def test_run_tesseract_raises_runtime_error_on_nonzero_exit(
    tmp_path, monkeypatch
) -> None:
    image = tmp_path / "x.png"
    _write_png(image, 1536, 1024)

    class _Completed:
        def __init__(self) -> None:
            self.stdout = ""
            self.stderr = "boom"
            self.returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: _Completed())
    with pytest.raises(RuntimeError):
        vaf.run_tesseract(image)


def test_run_tesseract_reads_real_text_via_real_binary(tmp_path) -> None:
    # Skip when tesseract is not installed; otherwise smoke-test the real path.
    import shutil

    if shutil.which(vaf.TESSERACT_BIN) is None:
        pytest.skip("tesseract binary not available")
    image = tmp_path / "blank.png"
    _write_png(image, 1536, 1024)
    result = vaf.run_tesseract(image)
    assert "raw" in result and set(result["raw"]) == {"psm_6", "psm_11", "psm_12"}


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
    # Structural figures may satisfy a required label either via OCR or via
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


def test_validate_figure_raises_for_unknown_figure_id(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        vaf.validate_figure(tmp_path, "not_a_real_figure")


def test_validate_figure_rejects_removed_data_figure_ids(tmp_path: Path) -> None:
    # public_results / paper_portfolio are no longer part of the AI validator.
    for removed in ("public_results", "paper_portfolio"):
        with pytest.raises(KeyError):
            vaf.validate_figure(tmp_path, removed)


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


# ===========================================================================
# Real vision-review wrapper parsing.
#
# `argus_skill.tools.image_tool.review_image(..., out=...)` writes
# {"image": {...}, "model": ..., "endpoint": ..., "prompt": ..., "rubric": ...,
#  "review": "<model text, optionally fenced as ```json ... ```>"}
# -- the verdict (keep_or_regenerate/confirmed_labels/...) lives inside the
# top-level *string* field "review", not at the sidecar's top level.
# ===========================================================================


def _write_wrapped_sidecars(
    figures: Path,
    stem: str,
    *,
    review_verdict: dict[str, Any] | None = None,
    content_review_verdict: dict[str, Any] | None = None,
    review_fence: bool = False,
    content_review_fence: bool = False,
    width: int = 1536,
    height: int = 1024,
) -> str:
    """Like ``_write_full_sidecars`` but lets a caller substitute a raw
    ``.review.json`` / ``.content-review.json`` payload directly (still
    wrapped in the real tool's shape unless the caller passes a raw dict
    that intentionally is NOT a valid wrapper, to probe fail-closed
    behavior).
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

    default_review = {
        "score_1_to_5": 5,
        "major_issues": [],
        "concrete_revision_prompt": "",
        "keep_or_regenerate": "keep",
    }
    review_verdict = default_review if review_verdict is None else review_verdict
    _write_json(
        figures / f"{stem}.review.json",
        _review_wrapper(review_verdict, fence=review_fence),
    )

    (figures / f"{stem}.ocr.txt").write_text("raw ocr text", encoding="utf-8")
    _write_json(
        figures / f"{stem}.ocr.json",
        {"expected_tokens": [], "unresolved": [], "coverage": 1.0},
    )

    default_content_review = {
        "score_1_to_5": 5,
        "extra_tokens_present": [],
        "keep_or_regenerate": "keep",
    }
    content_review_verdict = (
        default_content_review if content_review_verdict is None else content_review_verdict
    )
    _write_json(
        figures / f"{stem}.content-review.json",
        _review_wrapper(content_review_verdict, fence=content_review_fence),
    )

    return sha256


def test_validate_figure_accepts_real_wrapper_for_review_json_plain_json(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine")
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_accepts_real_wrapper_for_content_review_json_plain_json(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "system_planes")
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("system_planes"))

    outcome = vaf.validate_figure(tmp_path, "system_planes", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_accepts_fenced_json_review_field(tmp_path: Path) -> None:
    # The real vision model frequently wraps its JSON verdict in a markdown
    # ```json fence; the wrapper's "review" string then looks like
    # "```json\n{...}\n```" rather than bare JSON.
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine", review_fence=True)
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_accepts_fenced_json_content_review_field(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine", content_review_fence=True)
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_accepts_bare_fence_without_json_language_tag(
    tmp_path: Path,
) -> None:
    # Some model responses fence with plain ``` rather than ```json.
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine")
    verdict = {
        "score_1_to_5": 5,
        "major_issues": [],
        "concrete_revision_prompt": "",
        "keep_or_regenerate": "keep",
    }
    wrapper = _review_wrapper(verdict)
    wrapper["review"] = "```\n" + json.dumps(verdict) + "\n```"
    _write_json(figures / "master_spine.review.json", wrapper)
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_validate_figure_fails_closed_on_malformed_review_json_field(
    tmp_path: Path,
) -> None:
    # The "review" field exists but is not valid JSON (e.g. the model
    # returned prose instead of JSON). This must fail closed -- never be
    # silently treated as an accepting review.
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine")
    wrapper = _review_wrapper({"keep_or_regenerate": "keep"})
    wrapper["review"] = "This figure looks great, I would keep it."
    _write_json(figures / "master_spine.review.json", wrapper)
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        "review.json" in e and ("not valid JSON" in e or "review" in e)
        for e in outcome["errors"]
    ), outcome["errors"]


def test_validate_figure_fails_closed_on_malformed_content_review_json_field(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine")
    wrapper = _review_wrapper({"keep_or_regenerate": "keep"})
    wrapper["review"] = "{not: valid json,,,"
    _write_json(figures / "master_spine.content-review.json", wrapper)
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("content-review.json" in e for e in outcome["errors"]), outcome["errors"]


def test_validate_figure_fails_closed_when_review_field_missing_entirely(
    tmp_path: Path,
) -> None:
    # A sidecar that never even has a top-level "review" string field (e.g.
    # someone accidentally wrote the flattened verdict shape directly, or an
    # empty/placeholder file) must fail closed, not be silently accepted.
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine")
    _write_json(
        figures / "master_spine.review.json",
        {"keep_or_regenerate": "keep", "confirmed_labels": []},
    )
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        "review.json" in e and "review" in e for e in outcome["errors"]
    ), outcome["errors"]


def test_validate_figure_fails_closed_when_review_field_is_not_a_string(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(figures, "master_spine")
    wrapper = _review_wrapper({"keep_or_regenerate": "keep"})
    wrapper["review"] = 12345  # not a string, not the real tool's shape
    _write_json(figures / "master_spine.review.json", wrapper)
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"


def test_validate_figure_wrapper_regeneration_verdict_still_fails(
    tmp_path: Path,
) -> None:
    # A correctly-parsed wrapper whose verdict is "regenerate" must still be
    # rejected -- the wrapper-parsing fix must not accidentally coerce every
    # parsed verdict into an acceptance.
    figures = _figures_dir(tmp_path)
    _write_wrapped_sidecars(
        figures,
        "master_spine",
        review_verdict={
            "score_1_to_5": 2,
            "major_issues": ["wrong palette"],
            "concrete_revision_prompt": "fix palette",
            "keep_or_regenerate": "regenerate",
        },
    )
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any("review.json does not accept" in e for e in outcome["errors"])


# ===========================================================================
# Separator-tolerant OCR token-matching normalization: tolerates OCR
# losing/substituting a "\u00b7" middle-dot separator, but never loosens
# digits/decimal points/percent/slash/numeric sign, and a label whose
# words/numbers are wholly absent from OCR still cannot pass on vision
# confirmation alone.
# ===========================================================================


def test_normalize_ocr_for_matching_is_a_distinct_function_from_normalize_ocr() -> None:
    assert vaf.normalize_ocr_for_matching is not vaf.normalize_ocr
    dotted = "Backlog \u00b7 continuous"
    assert vaf.normalize_ocr(dotted) == dotted  # canonical: dot untouched
    assert vaf.normalize_ocr_for_matching(dotted) == "Backlog continuous"


def test_normalize_ocr_for_matching_maps_middle_dot_to_space() -> None:
    assert vaf.normalize_ocr_for_matching("Backlog \u00b7 continuous") == "Backlog continuous"


def test_normalize_ocr_for_matching_tolerates_missing_middle_dot() -> None:
    # OCR that already lost the dot entirely (double space collapses).
    assert vaf.normalize_ocr_for_matching("Backlog  continuous") == "Backlog continuous"


def test_normalize_ocr_for_matching_collapses_repeated_punctuation() -> None:
    assert vaf.normalize_ocr_for_matching("Backlog :: continuous") == "Backlog : continuous"


def test_normalize_ocr_for_matching_never_alters_digits() -> None:
    assert vaf.normalize_ocr_for_matching("112 typed events") == "112 typed events"
    assert "1,800" in vaf.normalize_ocr_for_matching("1,800 s decision budget")


def test_normalize_ocr_for_matching_never_alters_decimal_point() -> None:
    assert vaf.normalize_ocr_for_matching("76.8%") == "76.8%"


def test_normalize_ocr_for_matching_never_alters_percent() -> None:
    assert "%" in vaf.normalize_ocr_for_matching("76.8%")


def test_normalize_ocr_for_matching_never_alters_slash() -> None:
    assert vaf.normalize_ocr_for_matching("Backlog / continuous") == "Backlog / continuous"


def test_normalize_ocr_for_matching_never_alters_numeric_sign() -> None:
    assert "-" in vaf.normalize_ocr_for_matching("-5.2%")
    assert vaf.normalize_ocr_for_matching("-5.2%") == "-5.2%"


def test_normalize_ocr_for_matching_does_not_make_1800_equal_180() -> None:
    tolerant = vaf.normalize_ocr_for_matching("1,800 s")
    assert "1,80 s" != tolerant
    assert tolerant not in "1,80 s"


def test_concept_figure_middot_label_gated_by_both_reviews(
    tmp_path: Path,
) -> None:
    # dense_intelligence's disclaimer label carries a middle dot; the
    # separator-tolerant path applies, still gated on BOTH reviews confirming
    # the exact original label.
    figures = _figures_dir(tmp_path)
    label = "conceptual model \u00b7 not a reported benchmark"
    _write_full_sidecars(
        figures,
        "dense_intelligence",
        review_overrides={"confirmed_labels": [label]},
        content_review_overrides={"confirmed_labels": [label]},
    )
    text = _full_ocr_text_for("dense_intelligence").replace(
        label, "conceptual model not a reported benchmark"
    )
    ocr_runner = _ocr_runner_returning(text)

    outcome = vaf.validate_figure(tmp_path, "dense_intelligence", ocr_runner=ocr_runner)

    assert outcome["status"] == "pass", outcome["errors"]


def test_concept_figure_middot_label_fails_when_only_one_review_confirms(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    label = "conceptual model \u00b7 not a reported benchmark"
    _write_full_sidecars(
        figures,
        "dense_intelligence",
        review_overrides={"confirmed_labels": [label]},
    )
    text = _full_ocr_text_for("dense_intelligence").replace(
        label, "conceptual model not a reported benchmark"
    )
    ocr_runner = _ocr_runner_returning(text)

    outcome = vaf.validate_figure(tmp_path, "dense_intelligence", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(label in e for e in outcome["errors"])


# ---------------------------------------------------------------------------
# content-review.json is required for every structural figure.
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
    ``content-review.json``.
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
    _write_json(figures / f"{stem}.review.json", _review_wrapper(review_payload))
    (figures / f"{stem}.ocr.txt").write_text("raw ocr text", encoding="utf-8")
    _write_json(
        figures / f"{stem}.ocr.json",
        {"expected_tokens": [], "unresolved": [], "coverage": 1.0},
    )
    return sha256


def test_every_contract_requires_content_review_sidecar() -> None:
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert "content-review.json" in contract.sidecar_suffixes, contract.stem


def test_validate_figure_fails_when_content_review_sidecar_absent(
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


def test_validate_figure_fails_when_content_review_not_keep(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    _write_core_sidecars_without_content_review(figures, "master_spine")
    _write_json(
        figures / "master_spine.content-review.json",
        _review_wrapper(
            {
                "score_1_to_5": 2,
                "extra_tokens_present": [],
                "keep_or_regenerate": "regenerate",
            }
        ),
    )
    ocr_runner = _ocr_runner_returning(_full_ocr_text_for("master_spine"))

    outcome = vaf.validate_figure(tmp_path, "master_spine", ocr_runner=ocr_runner)

    assert outcome["status"] == "fail"
    assert any(
        "content-review.json does not accept" in e for e in outcome["errors"]
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
        _review_wrapper(
            {
                "score_1_to_5": 5,
                "extra_tokens_present": [],
                "keep_or_regenerate": "keep",
                "confirmed_labels": ["Reviewer"],
            }
        ),
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
        _review_wrapper(
            {
                "score_1_to_5": 5,
                "extra_tokens_present": [],
                "keep_or_regenerate": "keep",
            }
        ),
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
        _review_wrapper(
            {
                "score_1_to_5": 5,
                "extra_tokens_present": [],
                "keep_or_regenerate": "keep",
                "confirmed_labels": ["Reviewer"],
            }
        ),
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
    )


def test_write_validation_manifest_writes_expected_file(tmp_path: Path) -> None:
    figures = _figures_dir(tmp_path)
    for stem in vaf.FIGURE_CONTRACTS:
        _write_full_sidecars(figures, stem)

    all_text = _full_ocr_text_for_all(vaf.FIGURE_CONTRACTS)
    ocr_runner = _ocr_runner_returning(all_text)

    manifest = vaf.write_validation_manifest(tmp_path, ocr_runner=ocr_runner)

    manifest_path = figures / "AI_FIGURE_VALIDATION.json"
    assert manifest_path.is_file()
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["figure_count"] == 6
    assert on_disk["overall_status"] == "pass"
    assert manifest["overall_status"] == "pass"


def test_write_validation_manifest_hash_matches_actual_png_bytes(
    tmp_path: Path,
) -> None:
    figures = _figures_dir(tmp_path)
    for stem in vaf.FIGURE_CONTRACTS:
        _write_full_sidecars(figures, stem)

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
    for stem in vaf.FIGURE_CONTRACTS:
        _write_full_sidecars(figures, stem)
    # Break exactly one figure's dimensions.
    _write_png(figures / "system_planes.png", 800, 600)

    all_text = _full_ocr_text_for_all(vaf.FIGURE_CONTRACTS)
    ocr_runner = _ocr_runner_returning(all_text)

    manifest = vaf.write_validation_manifest(tmp_path, ocr_runner=ocr_runner)

    assert manifest["overall_status"] == "fail"
    statuses = {f["stem"]: f["status"] for f in manifest["figures"]}
    assert statuses["system_planes"] == "fail"
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
    for stem in vaf.FIGURE_CONTRACTS:
        _write_full_sidecars(figures, stem)

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
    for stem in vaf.FIGURE_CONTRACTS:
        _write_full_sidecars(figures, stem)

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
# Six complete Blue-Gold Precision Atlas prompts + two review rubrics. These
# tests are the prompt-authoring contract: they assert every prompt is
# independently complete (shared style block + exact pinned
# labels/relationships + OCR-friendly horizontal typography + negative
# prompt), and they pin each prompt/rubric file's bytes to a recorded SHA-256
# so the eventual IMAGE2_FIGURES.json prompt-hash provenance has a
# test-enforced anchor.
# ===========================================================================

_FIGURES_DIR = _REPO_ROOT / "technical_report" / "figures"

_ALL_STEMS: tuple[str, ...] = tuple(vaf.FIGURE_CONTRACTS)

# Verbatim delimiters that must bracket, byte-for-byte, the one shared style
# block copied into every prompt, and the exact-token region of each prompt.
_STYLE_BEGIN = "=== BEGIN SHARED STYLE BLOCK: Blue-Gold Precision Atlas ==="
_STYLE_END = "=== END SHARED STYLE BLOCK: Blue-Gold Precision Atlas ==="
_PINNED_BEGIN = "=== BEGIN PINNED LABELS ==="
_PINNED_END = "=== END PINNED LABELS ==="

_PALETTE_HEXES = ("#FBFAF6", "#315BCE", "#214884", "#C38A20", "#24272B")

_REVIEW_RUBRIC = "ai_figure_review_rubric.txt"
_CONTENT_RUBRIC = "ai_figure_content_rubric.txt"

# Byte-for-byte SHA-256 pins. Any later edit to a prompt must deliberately
# update its pin (and regenerate the figure).
_PROMPT_SHA256: dict[str, str] = {
    "master_spine": "99e0f487c6dac696e337e0e868ec82cf4c9706d01d236a1a32f44e80a4752979",
    "dense_intelligence": "eba165cffa0009522506970a11d26ad03e89271f06428d1474a0153b3a837088",
    "system_planes": "cfe7d8a7ee9ce072c32b84e8fc3ef91492f42e8139054387941f8f24a834ae63",
    "argus_architecture": "2e73c7a16387aa40435e567e159bd2ad371e33d0d076f813a6f07e5976c99a67",
    "mission_lifecycle": "63bcbf6eeffd73b0fe4a876b493cb712053a2be44ebb2d34c3e4d876c3b88b16",
    "long_horizon_reliability": "e097144e01a6b1283bf6225837100413e1f70451a54986e46673752be2c71954",
}
_RUBRIC_SHA256: dict[str, str] = {
    _REVIEW_RUBRIC: "6a5b0c04d54726d5733ddb8ef6b25948b2e1e5aa9f229ee0fa3bf8bbfb14271a",
    _CONTENT_RUBRIC: "6703f503c70a1b25a0e9687a54a9f6decd3a87be5a35669173c2a0a35182a18b",
}


def _read_prompt(stem: str) -> str:
    return (_FIGURES_DIR / f"{stem}.prompt.txt").read_text(encoding="utf-8")


def _extract_block(text: str, begin: str, end: str) -> str:
    assert begin in text, f"missing block-begin marker {begin!r}"
    assert end in text, f"missing block-end marker {end!r}"
    return text.split(begin, 1)[1].split(end, 1)[0]


# --- every prompt exists and is independently complete --------------------


@pytest.mark.parametrize("stem", _ALL_STEMS)
def test_prompt_file_exists(stem: str) -> None:
    assert (_FIGURES_DIR / f"{stem}.prompt.txt").is_file()


def test_only_six_structural_prompt_files_exist() -> None:
    # The two data-figure prompts were deleted; no prompt file for them may
    # remain.
    assert not (_FIGURES_DIR / "public_results.prompt.txt").exists()
    assert not (_FIGURES_DIR / "paper_portfolio.prompt.txt").exists()


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


# --- one shared style block, copied byte-for-byte into all six ------------


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


# --- concept prompts: relationships present -------------------------------


@pytest.mark.parametrize("stem", _ALL_STEMS)
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
    for contract in vaf.FIGURE_CONTRACTS.values():
        assert contract.title in text, f"content rubric omits {contract.title}"


def test_content_rubric_has_no_data_figure_source_wording() -> None:
    # Data-specific source/numeric clauses were removed with the two data
    # figures.
    text = (_FIGURES_DIR / _CONTENT_RUBRIC).read_text(encoding="utf-8")
    assert "website_results.json" not in text
    assert "paper_inventory.json" not in text
    assert "Public Results" not in text
    assert "Paper Portfolio" not in text
