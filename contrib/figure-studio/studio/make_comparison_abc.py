#!/usr/bin/env python3
"""Compose A baseline | B optimized | C studio comparison sheets."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


SCENARIOS = (
    "scenario_1_marl_architecture",
    "scenario_2_attention_flow",
    "scenario_3_federated_protocol",
    "scenario_4_nas_search_space",
    "scenario_5_ablation_results",
)
COLUMN_WIDTH = 1000
HEADER_HEIGHT = 92
LABELS = ("A  baseline", "B  optimized", "C  studio")


def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _load_scaled(path: Path) -> Image.Image:
    with Image.open(path) as source:
        image = source.convert("RGB")
    height = max(1, round(image.height * COLUMN_WIDTH / image.width))
    return image.resize((COLUMN_WIDTH, height), Image.Resampling.LANCZOS)


def compose(scenario: str, root: Path, output_dir: Path) -> Path | None:
    inputs = (
        root / "baseline" / f"{scenario}.png",
        root / "optimized" / f"{scenario}.png",
        root / "studio" / f"{scenario}.png",
    )
    if not inputs[2].is_file():
        print(f"SKIP {scenario}: C image does not exist: {inputs[2]}")
        return None
    missing = [str(path) for path in inputs[:2] if not path.is_file()]
    if missing:
        print(f"SKIP {scenario}: missing A/B input(s): {', '.join(missing)}")
        return None

    columns = [_load_scaled(path) for path in inputs]
    image_height = max(image.height for image in columns)
    canvas = Image.new("RGB", (3 * COLUMN_WIDTH, HEADER_HEIGHT + image_height), "white")
    draw = ImageDraw.Draw(canvas)
    font = _font(36)
    for index, (label, image) in enumerate(zip(LABELS, columns, strict=True)):
        x = index * COLUMN_WIDTH
        box = draw.textbbox((0, 0), label, font=font)
        label_width = box[2] - box[0]
        draw.text((x + (COLUMN_WIDTH - label_width) / 2, 23), label, fill="#111827", font=font)
        canvas.paste(image, (x, HEADER_HEIGHT))
        if index:
            draw.line((x, 0, x, canvas.height), fill="#9CA3AF", width=2)

    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{scenario}_ABC.png"
    canvas.save(output, format="PNG", optimize=True)
    print(f"WROTE {output}")
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    output_dir = args.output_dir.resolve() if args.output_dir else root / "comparison"
    written = [compose(scenario, root, output_dir) for scenario in SCENARIOS]
    return 0 if any(path is not None for path in written) else 1


if __name__ == "__main__":
    raise SystemExit(main())
