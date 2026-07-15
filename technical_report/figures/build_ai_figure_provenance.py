#!/usr/bin/env python3
"""Rebuild public-safe provenance for the six AI structural report figures.

This script does **not** call any image model. It reads the six already
generated, operator-accepted ``gpt-image-2`` rasters and their committed prompt
files and (re)writes their public-safe provenance evidence:

  * ``<stem>.png.json``            -- sanitized generation sidecar (no API vault
                                      fields, no absolute paths, no session ids)
  * ``<stem>.png.inspect.json``    -- local ``inspect`` of the committed PNG
  * ``<stem>.ocr.txt`` / ``.ocr.json`` -- Tesseract PSM 6/11/12 OCR evidence
  * ``<stem>.png.provenance.json`` -- public-safe per-figure provenance
  * ``IMAGE2_FIGURES.json``        -- six-entry image-2 manifest

Every path recorded is repo-relative; every hash is computed from the committed
bytes so the manifest, sidecars, and provenance are hash-consistent with the
rasters. The two deterministic data figures (``public_results``,
``paper_portfolio``) are handled by ``build_report_figures.py`` and are not
touched here.

Usage::

    python technical_report/figures/build_ai_figure_provenance.py
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from validate_ai_figures import (  # noqa: E402
    FIGURE_CONTRACTS,
    PSM_MODES,
    normalize_ocr,
    run_tesseract,
)

FIGURES_REL = "technical_report/figures"
MODEL = "gpt-image-2"
REQUESTED_SIZE = "1536x1024"
PROMPT_TEMPLATE_ID = "argus-image2-paper-prompt-v1"
FIGURE_STUDIO_SOURCE = "paper-framework-figure-studio-pro-v3.1.4a"
FIGURE_STUDIO_STAGE = "S5-CANDIDATE-IMAGE"
TOOL = "argus_skill.tools.image_tool"
MANIFEST_PATH = _HERE / "IMAGE2_FIGURES.json"


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rel(stem: str, suffix: str) -> str:
    return f"{FIGURES_REL}/{stem}{suffix}"


def _png_dimensions(path: Path) -> tuple[int, int]:
    header = path.read_bytes()[:24]
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG file: {path}")
    import struct

    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def build_one(stem: str, contract) -> dict:
    png_path = _HERE / f"{stem}.png"
    prompt_path = _HERE / f"{stem}.prompt.txt"
    if not png_path.is_file():
        raise FileNotFoundError(f"missing raster: {png_path}")
    if not prompt_path.is_file():
        raise FileNotFoundError(f"missing prompt: {prompt_path}")

    output_sha = _sha256_file(png_path)
    prompt_sha = _sha256_file(prompt_path)
    prompt_text = prompt_path.read_text(encoding="utf-8")
    width, height = _png_dimensions(png_path)
    n_bytes = png_path.stat().st_size

    output_rel = _rel(stem, ".png")
    prompt_rel = _rel(stem, ".prompt.txt")
    sidecar_rel = _rel(stem, ".png.json")
    inspect_rel = _rel(stem, ".png.inspect.json")
    provenance_rel = _rel(stem, ".png.provenance.json")
    ocr_txt_rel = _rel(stem, ".ocr.txt")
    ocr_json_rel = _rel(stem, ".ocr.json")

    image_block = {
        "bytes": n_bytes,
        "exists": True,
        "height": height,
        "image": output_rel,
        "mime": "image/png",
        "sha256": output_sha,
        "width": width,
    }

    # 1) sanitized generation sidecar (public-safe; no api vault / abs paths).
    sidecar = {
        "artifact": output_rel,
        "image": image_block,
        "model": MODEL,
        "output_path": output_rel,
        "output_sha256": output_sha,
        "prompt": prompt_text,
        "prompt_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "requested_size": REQUESTED_SIZE,
        "sidecar": sidecar_rel,
    }
    _write_json(_HERE / f"{stem}.png.json", sidecar)

    # 2) local inspect.
    _write_json(_HERE / f"{stem}.png.inspect.json", dict(image_block))

    # 3) Tesseract OCR evidence.
    ocr_result = run_tesseract(png_path)
    raw_sections = "\n\n".join(
        f"--- psm {psm} ---\n{ocr_result['raw'][f'psm_{psm}']}" for psm in PSM_MODES
    )
    (_HERE / f"{stem}.ocr.txt").write_text(raw_sections, encoding="utf-8")

    expected_tokens = list(contract.required_labels)
    combined = ocr_result["combined_normalized"]
    per_psm = list((ocr_result.get("normalized") or {}).values())
    unresolved = [
        label
        for label in expected_tokens
        if not any(normalize_ocr(label) in text for text in per_psm)
    ]
    coverage = (
        (len(expected_tokens) - len(unresolved)) / len(expected_tokens)
        if expected_tokens
        else 1.0
    )
    ocr_payload = {
        "image": output_rel,
        "psm_modes": list(PSM_MODES),
        "expected_tokens": expected_tokens,
        "normalized_observed": combined,
        "coverage": coverage,
        "unresolved": unresolved,
    }
    _write_json(_HERE / f"{stem}.ocr.json", ocr_payload)

    # 4) public-safe provenance (no review fields, only repo-relative paths).
    provenance = {
        "figure_id": contract.figure_id,
        "figure_type": contract.figure_type,
        "generator": "codex-image2",
        "generator_model": MODEL,
        "model": MODEL,
        "tool": TOOL,
        "prompt_template_id": PROMPT_TEMPLATE_ID,
        "figure_studio_source": FIGURE_STUDIO_SOURCE,
        "figure_studio_stage": FIGURE_STUDIO_STAGE,
        "prompt_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "output_path": output_rel,
        "output_sha256": output_sha,
        "sidecar_path": sidecar_rel,
        "inspect_path": inspect_rel,
        "ocr_path": ocr_json_rel,
        "requested_size": REQUESTED_SIZE,
        "width": width,
        "height": height,
        "validation_route": (
            "operator-accepted raster; provenance/hash/dimension/no-local-path "
            "checks via technical_report/figures/validate_ai_figures.py; "
            "Tesseract OCR coverage recorded as evidence"
        ),
    }
    _write_json(_HERE / f"{stem}.png.provenance.json", provenance)

    return {
        "figure_id": contract.figure_id,
        "figure_type": contract.figure_type,
        "source": "raster",
        "generator": "codex-image2",
        "generator_model": MODEL,
        "model": MODEL,
        "prompt_template_id": PROMPT_TEMPLATE_ID,
        "figure_studio_source": FIGURE_STUDIO_SOURCE,
        "figure_studio_stage": FIGURE_STUDIO_STAGE,
        "requested_size": REQUESTED_SIZE,
        "width": width,
        "height": height,
        "output_path": output_rel,
        "output_sha256": output_sha,
        "prompt_path": prompt_rel,
        "prompt_sha256": prompt_sha,
        "sidecar_path": sidecar_rel,
        "inspect_path": inspect_rel,
        "ocr_path": ocr_json_rel,
        "generation_provenance_path": provenance_rel,
    }


def main() -> int:
    entries = [
        build_one(stem, contract) for stem, contract in FIGURE_CONTRACTS.items()
    ]
    entries.sort(key=lambda e: e["figure_id"])
    manifest = {
        "schema": "argus-image2-figures/v2",
        "description": (
            "Six structural report figures drawn by the gpt-image-2 image "
            "model and accepted by the operator for final integration. Each "
            "entry is hash-consistent with the committed raster, prompt, and "
            "public-safe provenance sidecar. No local/absolute paths or "
            "secrets are recorded; the two deterministic data figures live in "
            "REPORT_FIGURES.json."
        ),
        "generator": f"{FIGURES_REL}/build_ai_figure_provenance.py",
        "figure_count": len(entries),
        "figures": entries,
    }
    _write_json(MANIFEST_PATH, manifest)
    for e in entries:
        print(f"{e['figure_id']:26s} png={e['output_sha256'][:12]}  "
              f"prompt={e['prompt_sha256'][:12]}")
    print(f"manifest -> {MANIFEST_PATH.name} ({len(entries)} figures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
