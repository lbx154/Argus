"""Generate an image-2 conceptual figure and IMAGE2_FIGURES.json manifest.

Run from a project root with:

    python code/generate_image_2.py \
      --init-prompt --figure-title "Method Overview"

Edit the prompt scaffold before final generation. This helper preserves the
generated raster and writes provenance sidecars expected by the paper gates.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from argus_skill.tools.image_tool import (
    PAPER_FIGURE_PROMPT_TEMPLATE,
    PAPER_FIGURE_PROMPT_TEMPLATE_ID,
    PAPER_FIGURE_STUDIO_SOURCE_ID,
    generate_image,
    inspect_image,
    review_image,
    write_paper_figure_prompt,
)
from argus_skill.skills.venue_profiles import DEFAULT_VENUE_KEY, resolve_venue_profile

DEFAULT_PROMPT_PATH = Path("paper/figures/method_overview.prompt.txt")
DEFAULT_OUTPUT_PATH = Path("paper/figures/method_overview.png")
DEFAULT_MANIFEST_PATH = Path("paper/figures/IMAGE2_FIGURES.json")
DEFAULT_SIZE = "1536x1024"

PROMPT_SCAFFOLD = PAPER_FIGURE_PROMPT_TEMPLATE


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def project_path(project_root: Path, path: Path) -> Path:
    return path if path.is_absolute() else project_root / path


def relpath(project_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def write_prompt_scaffold(
    prompt_path: Path,
    *,
    figure_title: str = "Method Overview",
    input_label: str = "Literature-grounded inputs",
    mechanism_label: str = "Reusable agent skill loop",
    verification_label: str = "Evidence gate",
    output_label: str = "Submission-ready paper",
    benefit_label: str = "Better grounded claims",
    evidence_label: str = "Full-scale evidence",
    failure_label: str = "Overclaiming avoided",
    overwrite: bool = False,
) -> Path:
    if prompt_path.exists() and not overwrite:
        return prompt_path
    write_paper_figure_prompt(
        prompt_path,
        figure_title=figure_title,
        input_label=input_label,
        mechanism_label=mechanism_label,
        verification_label=verification_label,
        output_label=output_label,
        benefit_label=benefit_label,
        evidence_label=evidence_label,
        failure_label=failure_label,
        force=overwrite,
    )
    return prompt_path


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"figures": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"figures": []}
    if not isinstance(payload, dict):
        return {"figures": []}
    figures = payload.get("figures")
    if not isinstance(figures, list):
        payload["figures"] = []
    return payload


def upsert_manifest_entry(manifest_path: Path, entry: dict[str, Any]) -> None:
    payload = load_manifest(manifest_path)
    figures = payload.setdefault("figures", [])
    figure_id = entry["figure_id"]
    replaced = False
    for index, existing in enumerate(figures):
        if isinstance(existing, dict) and existing.get("figure_id") == figure_id:
            figures[index] = entry
            replaced = True
            break
    if not replaced:
        figures.append(entry)
    write_json(manifest_path, payload)


def generate_image2_figure(
    *,
    project_root: Path,
    prompt_file: Path = DEFAULT_PROMPT_PATH,
    output: Path = DEFAULT_OUTPUT_PATH,
    manifest: Path = DEFAULT_MANIFEST_PATH,
    figure_id: str = "method-overview",
    figure_type: str = "method",
    size: str = DEFAULT_SIZE,
    force: bool = False,
    review: bool = True,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    venue_profile = resolve_venue_profile(project_root)
    prompt_path = project_path(project_root, prompt_file)
    output_path = project_path(project_root, output)
    manifest_path = project_path(project_root, manifest)
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt file is empty: {prompt_path}")
    if PAPER_FIGURE_PROMPT_TEMPLATE_ID not in prompt or PAPER_FIGURE_STUDIO_SOURCE_ID not in prompt:
        raise RuntimeError(
            "prompt file must be created from the canonical Argus figure-studio "
            "paper prompt; run with --init-prompt or use image_tool paper-prompt"
        )

    generation = generate_image(
        prompt=prompt,
        prompt_file=prompt_path,
        out=output_path,
        size=size,
        force=force,
    )
    image_info = inspect_image(output_path)
    inspect_path = output_path.with_suffix(output_path.suffix + ".inspect.json")
    write_json(inspect_path, image_info)

    review_path = output_path.with_suffix(output_path.suffix + ".review.json")
    if review:
        # Only thread the resolved venue when it diverges from the EMNLP
        # default so an unconfigured (EMNLP) project keeps calling
        # review_image with the exact legacy keyword set — byte-identical
        # behavior and backward-compatible with callers/mocks that predate
        # the venue_profile parameter.
        review_kwargs: dict[str, Any] = {}
        if venue_profile.key != DEFAULT_VENUE_KEY:
            review_kwargs["venue_profile"] = venue_profile
        review_image(image=output_path, out=review_path, prompt=prompt, **review_kwargs)

    sidecar_path = Path(str(generation.get("sidecar") or output_path.with_suffix(output_path.suffix + ".json")))
    provenance_path = output_path.with_suffix(output_path.suffix + ".provenance.json")
    model = str(generation.get("model") or "gpt-image-2")
    requested_size = str(generation.get("requested_size") or size)
    original_requested_size = generation.get("original_requested_size")
    size_was_normalized = generation.get("size_normalized_to_multiple_of_16") is True
    width = image_info.get("width")
    height = image_info.get("height")
    output_sha256 = str(image_info.get("sha256") or sha256_file(output_path))
    provenance = {
        "generator": "codex-image2",
        "model": model,
        "tool": "argus_skill.tools.image_tool",
        "prompt_template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "figure_studio_source": PAPER_FIGURE_STUDIO_SOURCE_ID,
        "figure_studio_stage": "S5-CANDIDATE-IMAGE",
        "prompt_path": relpath(project_root, prompt_path),
        "prompt_sha256": sha256_text(prompt),
        "output_path": relpath(project_root, output_path),
        "output_sha256": output_sha256,
        "sidecar_path": relpath(project_root, sidecar_path),
        "requested_size": requested_size,
        "width": width,
        "height": height,
    }
    if isinstance(original_requested_size, str) and original_requested_size:
        provenance["original_requested_size"] = original_requested_size
    if size_was_normalized:
        provenance["size_normalized_to_multiple_of_16"] = True
    write_json(provenance_path, provenance)

    entry = {
        "figure_id": figure_id,
        "figure_type": figure_type,
        "source": "raster",
        "generator": "codex-image2",
        "model": model,
        "generator_model": model,
        "prompt_template_id": PAPER_FIGURE_PROMPT_TEMPLATE_ID,
        "figure_studio_source": PAPER_FIGURE_STUDIO_SOURCE_ID,
        "figure_studio_stage": "S5-CANDIDATE-IMAGE",
        "prompt_path": relpath(project_root, prompt_path),
        "output_path": relpath(project_root, output_path),
        "output_sha256": output_sha256,
        "sidecar_path": relpath(project_root, sidecar_path),
        "inspect_path": relpath(project_root, inspect_path),
        "review_path": relpath(project_root, review_path),
        "generation_provenance_path": relpath(project_root, provenance_path),
        "requested_size": requested_size,
        "width": width,
        "height": height,
    }
    if isinstance(original_requested_size, str) and original_requested_size:
        entry["original_requested_size"] = original_requested_size
    if size_was_normalized:
        entry["size_normalized_to_multiple_of_16"] = True
    upsert_manifest_entry(manifest_path, entry)
    return entry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument("--figure-id", default="method-overview")
    parser.add_argument("--figure-type", default="method")
    parser.add_argument("--size", default=DEFAULT_SIZE)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-review", action="store_true")
    parser.add_argument("--init-prompt", action="store_true")
    parser.add_argument("--write-prompt-only", action="store_true")
    parser.add_argument("--overwrite-prompt", action="store_true")
    parser.add_argument("--figure-title", default="Method Overview")
    args = parser.parse_args(argv)

    project_root = args.project_root.resolve()
    prompt_path = project_path(project_root, args.prompt_file)
    if args.init_prompt or args.write_prompt_only:
        write_prompt_scaffold(
            prompt_path,
            figure_title=args.figure_title,
            overwrite=args.overwrite_prompt,
        )
    if args.write_prompt_only:
        print(prompt_path)
        return 0

    entry = generate_image2_figure(
        project_root=project_root,
        prompt_file=args.prompt_file,
        output=args.out,
        manifest=args.manifest,
        figure_id=args.figure_id,
        figure_type=args.figure_type,
        size=args.size,
        force=args.force,
        review=not args.skip_review,
    )
    print(json.dumps(entry, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
