from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BRAND = ROOT / "docs" / "assets" / "brand"
SVG = BRAND / "svg"
PNG = BRAND / "png"

BODY = "#d7d9dc"
EYE = "#ffffff"
PUPIL = "#202326"
HIGHLIGHT = "#ffffff"
BACKGROUND = "#080a0b"

MARK_SPECS = {
    "regular": {
        "old_eye": "M286 266A42 42 0 1 0 202 266A42 42 0 1 0 286 266ZM274 248A12 12 0 1 0 250 248A12 12 0 1 0 274 248Z",
        "eye": "M140 266q46-80 102-80t110 80q-54 80-110 80t-102-80Z",
        "pupil": '<circle cx="244" cy="266" r="42" fill="{pupil}"/>',
        "highlight": '<circle cx="262" cy="248" r="12" fill="{highlight}"/>',
    },
    "small": {
        "old_eye": "M290 266A46 46 0 1 0 198 266A46 46 0 1 0 290 266ZM280 246A16 16 0 1 0 248 246A16 16 0 1 0 280 246Z",
        "eye": "M146 266q45-92 98-92t104 92q-51 92-104 92t-98-92Z",
        "pupil": '<circle cx="244" cy="266" r="46" fill="{pupil}"/>',
        "highlight": '<circle cx="264" cy="246" r="16" fill="{highlight}"/>',
    },
}


def dark_mark(source: Path, *, small: bool = False, background: bool = False) -> str:
    text = source.read_text(encoding="utf-8")
    spec = MARK_SPECS["small" if small else "regular"]
    text = text.replace("#000000", BODY).replace("#000", BODY)
    text = text.replace("#ffffff", BACKGROUND, 1) if background else text
    old_path = re.compile(
        rf'<path d="{re.escape(spec["old_eye"])}" fill="[^"]+" fill-rule="evenodd"\s*/>'
    )
    replacement = "\n".join(
        (
            f'<path d="{spec["eye"]}" fill="{EYE}"/>',
            spec["pupil"].format(pupil=PUPIL),
            spec["highlight"].format(highlight=HIGHLIGHT),
        )
    )
    text, count = old_path.subn(replacement, text)
    if count == 0:
        raise RuntimeError(f"could not find eye geometry in {source}")
    return text


def write_svg(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def rasterize(source: Path, output: Path, geometry: str) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "convert",
            "-background",
            "none",
            str(source),
            "-resize",
            geometry,
            "-strip",
            str(output),
        ],
        check=True,
    )


def generate_svgs() -> dict[str, Path]:
    outputs = {
        "mark": SVG / "argus-mark-dark.svg",
        "small": SVG / "argus-mark-small-dark.svg",
        "horizontal": SVG / "argus-logo-horizontal-dark.svg",
        "stacked": SVG / "argus-logo-stacked-dark.svg",
        "circle": SVG / "argus-mark-dark-circle.svg",
        "square": SVG / "argus-mark-dark-square.svg",
        "rounded": SVG / "argus-mark-dark-rounded-square.svg",
        "horizontal_bg": SVG / "argus-logo-horizontal-dark-background.svg",
    }
    write_svg(outputs["mark"], dark_mark(SVG / "argus-mark.svg"))
    write_svg(outputs["small"], dark_mark(SVG / "argus-mark-small.svg", small=True))
    write_svg(outputs["horizontal"], dark_mark(SVG / "argus-logo-horizontal.svg"))
    write_svg(outputs["stacked"], dark_mark(SVG / "argus-logo-stacked.svg"))
    write_svg(
        outputs["circle"],
        dark_mark(SVG / "argus-mark-white-circle.svg", background=True),
    )
    write_svg(
        outputs["square"],
        dark_mark(SVG / "argus-mark-white-square.svg", background=True),
    )
    write_svg(
        outputs["rounded"],
        dark_mark(SVG / "argus-mark-white-rounded-square.svg", background=True),
    )
    write_svg(
        outputs["horizontal_bg"],
        dark_mark(SVG / "argus-logo-horizontal-white.svg", background=True),
    )
    shutil.copyfile(outputs["horizontal"], ROOT / "docs" / "assets" / "argus-logo-horizontal-dark.svg")
    return outputs


def generate_pngs(svg: dict[str, Path]) -> None:
    mark_sizes = (16, 24, 32, 48, 64, 128, 256, 512, 1024)
    for size in mark_sizes:
        rasterize(svg["mark"], PNG / "dark" / "marks" / f"argus-mark-dark-{size}.png", f"{size}x{size}")
        for shape in ("circle", "square", "rounded"):
            rasterize(
                svg[shape],
                PNG / "dark-background" / "marks" / f"argus-mark-dark-{shape}-{size}.png",
                f"{size}x{size}",
            )
    for width in (600, 1200, 2400):
        rasterize(
            svg["horizontal"],
            PNG / "dark" / "horizontal" / f"argus-logo-horizontal-dark-{width}.png",
            f"{width}x",
        )
        rasterize(
            svg["horizontal_bg"],
            PNG / "dark-background" / "horizontal" / f"argus-logo-horizontal-dark-{width}.png",
            f"{width}x",
        )
    for size in (800, 1600):
        rasterize(
            svg["stacked"],
            PNG / "dark" / "stacked" / f"argus-logo-stacked-dark-{size}.png",
            f"{size}x{size}",
        )

    public = ROOT / "frontend" / "web" / "public"
    shutil.copyfile(svg["rounded"], public / "favicon-dark.svg")
    rasterize(svg["rounded"], public / "icon-dark-192.png", "192x192")
    rasterize(svg["rounded"], public / "icon-dark-512.png", "512x512")
    rasterize(svg["rounded"], public / "icon-maskable-dark-512.png", "512x512")
    rasterize(svg["rounded"], public / "apple-touch-icon-dark.png", "180x180")

    desktop = ROOT / "desktop-tauri" / "src"
    rasterize(svg["mark"], desktop / "argus-mark-dark-24.png", "24x24")
    rasterize(svg["mark"], desktop / "argus-mark-dark-128.png", "128x128")
    with tempfile.TemporaryDirectory() as directory:
        temp = Path(directory)
        frames = []
        for size in (16, 24, 32, 48, 64, 128, 256):
            frame = temp / f"icon-{size}.png"
            rasterize(svg["rounded"], frame, f"{size}x{size}")
            frames.append(str(frame))
        icon_dir = ROOT / "desktop-tauri" / "src-tauri" / "icons"
        for name in ("icon.ico", "icon-dark.ico"):
            subprocess.run(
                ["convert", *frames, "-strip", str(icon_dir / name)],
                check=True,
            )


def main() -> None:
    if shutil.which("convert") is None:
        raise SystemExit("ImageMagick 'convert' is required")
    svg = generate_svgs()
    generate_pngs(svg)


if __name__ == "__main__":
    main()
