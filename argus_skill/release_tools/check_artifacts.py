#!/usr/bin/env python3
"""Verify that checked-in Web/TUI production artifacts embed this release id."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "argus_skill" / "release_manifest.json"
TUI_BUNDLE = ROOT / "frontend" / "tui" / "bundle" / "argus.mjs"
WEB_INDEX = ROOT / "frontend" / "web" / "dist" / "index.html"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"missing release artifact: {path.relative_to(ROOT)} ({exc})") from exc


def _web_entry_assets(index: str) -> list[Path]:
    refs = re.findall(r'(?:src|href)="([^"]+\.(?:js|css))"', index)
    return [ROOT / "frontend" / "web" / "dist" / ref.lstrip("/") for ref in refs]


def check() -> list[str]:
    manifest = json.loads(_read(MANIFEST))
    expected = str(manifest.get("release_id") or "")
    if not expected:
        return ["release manifest has no release_id"]
    failures: list[str] = []
    if expected not in _read(TUI_BUNDLE):
        failures.append(
            f"frontend/tui/bundle/argus.mjs does not embed current release {expected}"
        )
    index = _read(WEB_INDEX)
    entry_assets = _web_entry_assets(index)
    if not entry_assets:
        failures.append("frontend/web/dist/index.html references no JS/CSS entry assets")
    else:
        js_assets = [path for path in entry_assets if path.suffix == ".js"]
        if not js_assets or not any(expected in _read(path) for path in js_assets):
            failures.append(
                f"frontend/web/dist entry bundle does not embed current release {expected}"
            )
    return failures


def main() -> int:
    try:
        failures = check()
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"release artifact check failed: {exc}", file=sys.stderr)
        return 1
    if failures:
        for failure in failures:
            print(f"release artifact check failed: {failure}", file=sys.stderr)
        return 1
    manifest = json.loads(_read(MANIFEST))
    print(f"release artifacts match package {manifest['package_version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
