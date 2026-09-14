from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOTS = (
    ROOT / "argus",
    ROOT / "frontend" / "core" / "src",
    ROOT / "frontend" / "tui" / "src",
    ROOT / "frontend" / "web" / "src",
    ROOT / "tests",
    ROOT / "pyproject.toml",
    ROOT / "README.md",
    ROOT / "README.zh-CN.md",
)

# Split literals so this test does not flag its own policy definitions.
PRIVATE_MARKERS = (
    "/home/" + "argustest",
    "/scratch/" + "recursive",
    "/opt/conda/envs/" + "ptca",
    "dashing-" + "stork",
    "ssh " + "ds",
    "ssh " + "h100",
    "127.0.0.1:" + "2232",
    "local port " + "2210",
    "ssh -p " + "2231",
    "pod/" + "argus-kbench-evalsrv",
    "/tmp/" + "argus-night",
)
EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@([A-Z0-9.-]+\.[A-Z]{2,})\b", re.IGNORECASE)
PUBLIC_EMAIL_DOMAINS = {
    "argus.invalid",
    "example.com",
    "example.invalid",
    "example.org",
    "github.com",
}


def _source_files() -> list[Path]:
    files: list[Path] = []
    for root in SCAN_ROOTS:
        if root.is_file():
            files.append(root)
            continue
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.suffix in {".md", ".py", ".toml", ".ts", ".tsx", ".js"}
        )
    return files


def test_public_source_has_no_private_deployment_markers() -> None:
    failures: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for marker in PRIVATE_MARKERS:
            if marker in text:
                failures.append(f"{path.relative_to(ROOT)}: {marker}")
        for match in EMAIL_PATTERN.finditer(text):
            domain = match.group(1).lower()
            if domain not in PUBLIC_EMAIL_DOMAINS:
                failures.append(f"{path.relative_to(ROOT)}: non-public email domain {domain}")

    assert failures == [], "private deployment markers found:\n" + "\n".join(failures)
