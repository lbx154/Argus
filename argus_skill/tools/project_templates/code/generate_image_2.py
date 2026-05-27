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

from argus_skill.tools.image_tool import generate_image, inspect_image, review_image

DEFAULT_PROMPT_PATH = Path("paper/figures/method_overview.prompt.txt")
DEFAULT_OUTPUT_PATH = Path("paper/figures/method_overview.png")
DEFAULT_MANIFEST_PATH = Path("paper/figures/IMAGE2_FIGURES.json")
DEFAULT_SIZE = "1536x1024"

PROMPT_SCAFFOLD = """Use case: scientific-educational
Asset type: Figure 1 teaser / conceptual overview for an EMNLP/ACL/NeurIPS-style academic manuscript.

General style:
- EMNLP/ACL/NeurIPS/CS paper method figure, full-width two-column landscape, 1536x1024 or 1920x1088.
- Clean Figma-style block diagram / block-based Figma style with rounded cards, neat alignment, soft pastel fills, dark-gray 2px borders, and compact information density.
- Compact, information-rich, suitable for a PDF page-width figure; little wasted space but not crowded.
- Tidy rounded handwritten or friendly sans-serif feel is acceptable only if it remains crisp and readable; no messy sketch fonts.
- Moderate badge/icon use only when semantically useful; a few simple recognizable icons are fine, not a logo wall.
- No heavy shadows, no gradients, no photorealism, no glassmorphism, no messy Excalidraw look.
- Large readable labels, short phrases, balanced hierarchy, flat vector-like raster rendering on warm white #fbfaf7.

Style intent:
- Clean, dense, modular, Figma-like, mostly rounded cards, low-saturation pastel blocks.
- Use small badges/icons sparingly; avoid empty space while preserving alignment.
- It should look like a main figure in an EMNLP/ACL/NeurIPS paper, not a marketing graphic, stock illustration, dashboard screenshot, or casual whiteboard.

Pinned content that must appear exactly:
- Title: "{figure_title}"
- Show: "{input_label}" -> "{mechanism_label}" -> "{verification_label}" -> "Reusable state/library" -> "Agent execution" -> "{output_label}" -> "{evidence_label}".
- Components/chips: "Baseline/status quo", "Proposed method", "Accepted item", "Rejected item", "{benefit_label}", "{failure_label}".
- SPELL EXACTLY every quoted label above. Do not invent alternate terminology, code identifiers, raw artifact paths, or extra labels.

Layout variant:
- Pick one variant ID and name it in the prompt. Swap only this block when generating variants.
- 01 central hero: huge central memory/wiki/library card, source factory on the left, agent/output board on the right, benchmark strip at bottom.
- 02 horizontal swimlanes: three clean lanes such as Build, Verify, Execute; use offset cards so it is not too rigid.
- 03 sankey funnel: many sources merge into distillation, narrow through gates, expand into library/state, then branch to outputs.
- 04 exploded entry: one accepted skill/memory/wiki entry pulled apart into Text, Visual, Recipe, Metadata plates with callout arrows.
- 05 layered architecture stack: bottom sources, middle reusable memory/library, top agent execution; use shelf-like overlapping slabs.
- 06 pipeline plus gallery: main pipeline across top, output gallery on right, compact benchmark/evidence cards along bottom.
- 07 modular dashboard: dense but paper-clean cards; central method card largest, side panel for domains/tasks/outputs.
- 08 radial hub-spoke: reusable library/state as center hub; sources feed from left arc; agent/results radiate right; evidence panel below.
- 09 zigzag pipeline: Z-shaped reading path with numbered step badges and compact insets.
- 10 research-poster dense: section headers, compact cards, mini charts, and small output thumbnails; still clean Figma and paper-friendly.
- 11 grayscale accent: mostly grayscale academic style with two pastel accent colors for proposed path and verification.
- 12 color-coded phases: peach acquisition, blue memory/library, green agent, lavender domains, yellow benchmark; overlapping phase tabs.
- 13 card deck: sources, skills, and outputs as tidy fanned decks; one accepted card expanded.
- 14 computation graph: nodes and grouped modules with thin arrows and rounded containers, like an ML systems diagram.
- 15 dataflow with sidebars: main flow through center, left source sidebar, right output sidebar, bottom benchmark/evidence sidebar.
- 16 timeline plus insets: left-to-right timeline with zoom boxes for the core mechanism and output/evidence.
- 17 nested containers: big containers for Offline Construction and Online Execution; nested subcards plus benchmark footer.
- 18 multi-panel A/B/C/D: A sources/build, B reusable state, C agent execution, D benchmark/evidence; panels overlap slightly and share arrows.
- 19 light blueprint: pale blue grid background, modular boxes, thin connector routes, neat badges, strong central method box.
- 20 polished Figma wireframe: component frames, auto-layout-like spacing, section tabs, chips, and carefully staggered components.

Negative prompt / Avoid:
- no concrete code snippets, raw paths, tiny unreadable text, character-level vertical text, or dense paragraphs
- no excessive logos or brand marks, no watermark
- no photorealistic scenes, stock photos, glassmorphism, heavy gradients, heavy shadows, texture, or arbitrary decorative blobs
- no messy whiteboard / Excalidraw-heavy sketch style
- no large empty areas, overlapping cards, squashed labels, inconsistent terminology, or extra captions that make it look like a dashboard

Figma tokens for camera-ready cleanup:
- Canvas 1536x1024 or 1920x1088; background #fbfaf7; stroke #1f2933 at 2px.
- Corner radius 10-16px; card padding 12-20px; card gap 12-24px.
- Pastels: acquisition #ffe2d1, parsing #fff2bd, memory/wiki #dcecff, agent #e2f7df, domains #eadfff, benchmark #fff1c9.
- Text sizes: title 38-52px, section headers 22-30px, card labels 16-22px, chips 12-16px.
"""


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
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = PROMPT_SCAFFOLD.format(
        figure_title=figure_title,
        input_label=input_label,
        mechanism_label=mechanism_label,
        verification_label=verification_label,
        output_label=output_label,
        benefit_label=benefit_label,
        evidence_label=evidence_label,
        failure_label=failure_label,
    )
    prompt_path.write_text(prompt, encoding="utf-8")
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
    prompt_path = project_path(project_root, prompt_file)
    output_path = project_path(project_root, output)
    manifest_path = project_path(project_root, manifest)
    prompt = prompt_path.read_text(encoding="utf-8").strip()
    if not prompt:
        raise RuntimeError(f"prompt file is empty: {prompt_path}")

    generation = generate_image(prompt=prompt, out=output_path, size=size, force=force)
    image_info = inspect_image(output_path)
    inspect_path = output_path.with_suffix(output_path.suffix + ".inspect.json")
    write_json(inspect_path, image_info)

    review_path = output_path.with_suffix(output_path.suffix + ".review.json")
    if review:
        review_image(image=output_path, out=review_path, prompt=prompt)

    sidecar_path = Path(str(generation.get("sidecar") or output_path.with_suffix(output_path.suffix + ".json")))
    provenance_path = output_path.with_suffix(output_path.suffix + ".provenance.json")
    model = str(generation.get("model") or "gpt-image-2")
    width = image_info.get("width")
    height = image_info.get("height")
    output_sha256 = str(image_info.get("sha256") or sha256_file(output_path))
    provenance = {
        "generator": "codex-image2",
        "model": model,
        "tool": "argus_skill.tools.image_tool",
        "prompt_path": relpath(project_root, prompt_path),
        "prompt_sha256": sha256_text(prompt),
        "output_path": relpath(project_root, output_path),
        "output_sha256": output_sha256,
        "sidecar_path": relpath(project_root, sidecar_path),
        "requested_size": size,
        "width": width,
        "height": height,
    }
    write_json(provenance_path, provenance)

    entry = {
        "figure_id": figure_id,
        "figure_type": figure_type,
        "source": "raster",
        "generator": "codex-image2",
        "model": model,
        "generator_model": model,
        "prompt_path": relpath(project_root, prompt_path),
        "output_path": relpath(project_root, output_path),
        "output_sha256": output_sha256,
        "sidecar_path": relpath(project_root, sidecar_path),
        "inspect_path": relpath(project_root, inspect_path),
        "review_path": relpath(project_root, review_path),
        "generation_provenance_path": relpath(project_root, provenance_path),
        "requested_size": size,
        "width": width,
        "height": height,
    }
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
