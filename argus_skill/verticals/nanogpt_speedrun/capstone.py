"""Frozen-protocol and measured-result gate for NanoGPT Speedrun."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

from ...core.file_digest import sha256_file as _sha256
from ..metric_evidence import EvidenceError, validate_nanogpt_evidence

FREEZE_RELPATH = Path("research/NANOGPT_FREEZE.json")
REQUIRED_ROLES = frozenset({"harness", "metric", "data", "budget"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_frozen_protocol(project_root: object) -> list[str]:
    root = Path(str(project_root or ".")).resolve()
    manifest = root / FREEZE_RELPATH
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [f"missing {FREEZE_RELPATH.as_posix()}"]
    except (OSError, json.JSONDecodeError) as exc:
        return [f"invalid {FREEZE_RELPATH.as_posix()}: {exc}"]
    issues: list[str] = []
    if not isinstance(payload, dict) or payload.get("version") != 1:
        return [f"{FREEZE_RELPATH.as_posix()} must declare version 1"]
    try:
        target = float(payload.get("target_val_loss"))
    except (TypeError, ValueError):
        target = math.nan
    if not math.isfinite(target) or not math.isclose(target, 3.28, abs_tol=1e-12):
        issues.append("target_val_loss must remain exactly 3.28")
    hardware = payload.get("hardware")
    if not isinstance(hardware, dict):
        issues.append("hardware must declare gpu_count=8 and gpu_model=H100")
    else:
        if hardware.get("gpu_count") != 8:
            issues.append("hardware.gpu_count must remain 8")
        if "h100" not in str(hardware.get("gpu_model") or "").lower():
            issues.append("hardware.gpu_model must remain H100")
    frozen = payload.get("frozen_files")
    if not isinstance(frozen, list) or not frozen:
        issues.append("frozen_files must be a non-empty list")
        return issues
    roles: set[str] = set()
    for index, entry in enumerate(frozen):
        if not isinstance(entry, dict):
            issues.append(f"frozen_files[{index}] must be an object")
            continue
        role = str(entry.get("role") or "").strip().lower()
        relpath = str(entry.get("path") or "").strip()
        expected = str(entry.get("sha256") or "").strip().lower()
        roles.add(role)
        raw_path = Path(relpath)
        if role not in REQUIRED_ROLES:
            issues.append(f"frozen_files[{index}] has unsupported role {role!r}")
        if not relpath or raw_path.is_absolute() or ".." in raw_path.parts:
            issues.append(f"frozen_files[{index}] path must stay project-relative")
            continue
        if not _SHA256.fullmatch(expected):
            issues.append(f"frozen_files[{index}] has invalid sha256")
            continue
        try:
            path = (root / raw_path).resolve(strict=True)
            path.relative_to(root)
        except (OSError, ValueError):
            issues.append(f"frozen file is missing or escapes project: {relpath!r}")
            continue
        if not path.is_file() or path.stat().st_size <= 0:
            issues.append(f"frozen file is not a non-empty file: {relpath!r}")
        elif _sha256(path) != expected:
            issues.append(f"frozen file changed: {relpath!r}")
    missing_roles = sorted(REQUIRED_ROLES - roles)
    if missing_roles:
        issues.append(f"frozen_files missing roles: {', '.join(missing_roles)}")
    return issues


def validate_capstone(project_root: object, stage: str) -> list[str]:
    stage_name = (stage or "").strip().lower()
    if stage_name not in {"setup", "optimize", "measure", "report"}:
        return [f"unsupported NanoGPT stage: {stage_name!r}"]
    issues = validate_frozen_protocol(project_root)
    if stage_name in {"measure", "report"}:
        try:
            validate_nanogpt_evidence(Path(str(project_root or ".")).resolve())
        except EvidenceError as exc:
            issues.append(str(exc))
    if stage_name == "report":
        report = Path(str(project_root or ".")) / "RESULTS.md"
        if not report.is_file() or report.stat().st_size <= 0:
            issues.append("report requires non-empty RESULTS.md")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nanogpt-capstone")
    parser.add_argument("command", choices=["check"], nargs="?", default="check")
    parser.add_argument("--project-root", default=".")
    parser.add_argument("--stage", choices=["setup", "optimize", "measure", "report"], required=True)
    args = parser.parse_args(argv)
    issues = validate_capstone(args.project_root, args.stage)
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1
    print(f"NanoGPT capstone valid for {args.stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())