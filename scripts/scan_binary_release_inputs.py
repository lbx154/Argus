#!/usr/bin/env python3
"""Scan only binary-release inputs for credential material before freezing."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INPUTS = (
    ROOT / "argus_skill",
    ROOT / "frontend" / "tui" / "bundle",
    ROOT / "frontend" / "web" / "dist",
    ROOT / "packaging" / "npm" / "cli",
)
PATTERNS = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    "aws-access-key": re.compile(rb"AKIA[0-9A-Z]{16}"),
    "openai-key": re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    "npm-token": re.compile(rb"npm_[A-Za-z0-9]{20,}"),
}


def main() -> int:
    findings: list[str] = []
    for base in INPUTS:
        for path in base.rglob("*"):
            if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
                continue
            try:
                payload = path.read_bytes()
            except OSError:
                continue
            for label, pattern in PATTERNS.items():
                if pattern.search(payload):
                    findings.append(f"{path.relative_to(ROOT)}: {label}")
    if findings:
        raise SystemExit("binary release input scan failed:\n" + "\n".join(findings))
    print("binary release input credential scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
