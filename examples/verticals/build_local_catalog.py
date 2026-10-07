"""Package one vertical directory as a Vertical Store archive plus a local catalog.

Usage:

    python examples/verticals/build_local_catalog.py NAME --version 0.1.0 --out DIR

Reads ``examples/verticals/argus_verticals/NAME`` (or ``--source``), writes
``DIR/NAME-VERSION.zip`` with members under ``argus_verticals/NAME/`` (the layout
``argus.verticals.store._verify_tree`` expects), and writes ``DIR/catalog.json``
in the shape ``argus.verticals.store._validate_entry`` accepts. Point
``ARGUS_VERTICAL_CATALOG`` at that file and ``argus verticals install NAME`` uses
the archive next to it instead of downloading anything.

The catalog is minimal on purpose: the fields the store requires, nothing else.
See docs/building-a-vertical.md for the walk-through.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def _purpose(stages: Path) -> str:
    """Read VERTICAL_PURPOSE from stages.py without importing it."""
    text = stages.read_text(encoding="utf-8")
    match = re.search(r"VERTICAL_PURPOSE\s*=\s*\(?\s*((?:\"[^\"]*\"\s*)+)\)?", text)
    if match is None:
        raise SystemExit(f"{stages}: VERTICAL_PURPOSE not found")
    return " ".join("".join(re.findall(r"\"([^\"]*)\"", match.group(1))).split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("name", help="vertical name, e.g. lab_notebook")
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--source", type=Path, default=None,
                        help="vertical directory (default: examples/verticals/argus_verticals/NAME)")
    parser.add_argument("--out", type=Path, required=True, help="directory for the zip and catalog.json")
    args = parser.parse_args(argv)

    name = args.name
    if not _NAME.fullmatch(name):
        raise SystemExit(f"{name!r} is not a valid vertical name (^[a-z][a-z0-9_]{{0,47}}$)")
    source = args.source or (Path(__file__).resolve().parent / "argus_verticals" / name)
    stages = source / "stages.py"
    if not stages.is_file():
        raise SystemExit(f"{source} has no stages.py")

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    archive_name = f"{name}-{args.version}.zip"
    archive = out / archive_name
    tree = f"argus_verticals/{name}"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            if "__pycache__" in path.parts:
                continue
            zf.write(path, f"{tree}/{path.relative_to(source).as_posix()}")

    data = archive.read_bytes()
    catalog = {
        "schema": 1,
        "verticals": {
            name: {
                "name": name,
                "version": args.version,
                "module": f"argus_verticals.{name}.stages",
                "paths": [tree],
                "purpose": _purpose(stages),
                "has_skills": (source / "skills").is_dir(),
                "archive": {
                    "file": archive_name,
                    "url": archive.as_uri(),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                },
            }
        },
    }
    catalog_path = out / "catalog.json"
    catalog_path.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"archive : {archive} ({len(data)} bytes)")
    print(f"catalog : {catalog_path}")
    print(f"install : ARGUS_VERTICAL_CATALOG={catalog_path} argus verticals install {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
