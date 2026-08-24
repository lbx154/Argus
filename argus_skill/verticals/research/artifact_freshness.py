"""Freshness gate for paper reviews and submission certifications.

This gate prevents a certification from outliving the manuscript it certified.
In campaign run-01, a 20:05 review certified a 10-page paper titled
"Target-Conditional Rank-Preserving Calibration for Test-Time Adaptation" and
verified the fragment ``0.035353``. By 22:07, ``paper/main.pdf`` was a different
7-page paper with a different title and zero copies of that fragment, while the
old ready certification remained on disk.

Review machinery already records ``source_snapshots`` with per-file SHA-256
digests. This gate enforces those snapshots and, when an artifact also records a
manuscript PDF path, cheaply rechecks page counts and verified text fragments.
Unavailable PDF tools are an environment limitation and are skipped; an
observed mismatch is a paper defect and blocks completion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterator


def _walk(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _snapshot_lists(value: Any) -> Iterator[list[Any]]:
    if isinstance(value, dict):
        snapshots = value.get("source_snapshots")
        if isinstance(snapshots, list):
            yield snapshots
        for child in value.values():
            yield from _snapshot_lists(child)
    elif isinstance(value, list):
        for child in value:
            yield from _snapshot_lists(child)


def _resolve_inside(project_root: Path, raw: Any) -> Path | None:
    value = str(raw or "").strip()
    if not value:
        return None
    candidate = Path(value).expanduser()
    resolved = (
        candidate.resolve()
        if candidate.is_absolute()
        else (project_root / candidate).resolve()
    )
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError:
        return None
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _short(value: Any) -> str:
    text = str(value or "<missing>")
    return text if text == "<missing>" else text[:12]


def _manuscript_pdfs(project_root: Path, payload: Any) -> list[Path]:
    found: list[Path] = []
    for key, value in _walk(payload):
        if not isinstance(value, str) or not value.lower().endswith(".pdf"):
            continue
        name = Path(value).name.lower()
        if name != "main.pdf" and "manuscript" not in key.lower() and "pdf" not in key.lower():
            continue
        resolved = _resolve_inside(project_root, value)
        if resolved is not None and resolved.is_file() and resolved not in found:
            found.append(resolved)
    return found


def _expected_pages(payload: Any) -> list[int]:
    found: list[int] = []
    for key, value in _walk(payload):
        if key not in {"pages", "page_count", "total_pages"}:
            continue
        if isinstance(value, bool):
            continue
        try:
            pages = int(value)
        except (TypeError, ValueError):
            continue
        if pages > 0 and pages not in found:
            found.append(pages)
    return found


def _verified_fragments(payload: Any) -> list[str]:
    keys = {
        "checked_fragments",
        "verified_fragments",
        "verified_text_fragments",
        "verified_fragment",
        "verified_text_fragment",
    }
    found: list[str] = []
    for key, value in _walk(payload):
        if key not in keys:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item and item not in found:
                found.append(item)
    return found


def _pdf_pages(tool: str, pdf: Path) -> int | None:
    try:
        result = subprocess.run(
            [tool, str(pdf)], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"^Pages:\s*(\d+)\s*$", result.stdout, re.MULTILINE)
    return int(match.group(1)) if match else None


def _pdf_text(tool: str, pdf: Path) -> str | None:
    try:
        result = subprocess.run(
            [tool, str(pdf), "-"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def artifact_freshness_issues(project_root: Path) -> tuple[str, ...]:
    """Return blocking hash or manuscript-scalar mismatches in review artifacts."""
    root = project_root.resolve()
    issues: list[str] = []
    for directory_name in ("paper", "analysis"):
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for artifact in sorted(directory.rglob("*.json")):
            try:
                payload = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            snapshot_lists = list(_snapshot_lists(payload))
            if not snapshot_lists:
                continue
            artifact_name = artifact.relative_to(root).as_posix()
            for snapshots in snapshot_lists:
                for snapshot in snapshots:
                    if not isinstance(snapshot, dict):
                        continue
                    raw_path = snapshot.get("path")
                    expected = str(snapshot.get("sha256") or "")
                    source = _resolve_inside(root, raw_path)
                    display = str(raw_path or "<missing path>")
                    if source is None or not source.is_file():
                        issues.append(
                            f"{artifact_name}: stale source snapshot {display}: "
                            f"recorded sha256={_short(expected)}, actual sha256=<missing>"
                        )
                        continue
                    try:
                        actual = _sha256(source)
                    except OSError:
                        actual = "<unreadable>"
                    if actual != expected:
                        issues.append(
                            f"{artifact_name}: stale source snapshot {display}: "
                            f"recorded sha256={_short(expected)}, "
                            f"actual sha256={_short(actual)}"
                        )

            pdfs = _manuscript_pdfs(root, payload)
            if not pdfs:
                continue
            page_tool = shutil.which("pdfinfo")
            text_tool = shutil.which("pdftotext")
            expected_pages = _expected_pages(payload)
            fragments = _verified_fragments(payload)
            for pdf in pdfs:
                pdf_name = pdf.relative_to(root).as_posix()
                if page_tool:
                    actual_pages = _pdf_pages(page_tool, pdf)
                    if actual_pages is not None:
                        for expected_page_count in expected_pages:
                            if actual_pages != expected_page_count:
                                issues.append(
                                    f"{artifact_name}: manuscript page-count mismatch for "
                                    f"{pdf_name}: recorded {expected_page_count}, actual "
                                    f"{actual_pages}"
                                )
                if text_tool and fragments:
                    text = _pdf_text(text_tool, pdf)
                    if text is not None:
                        for fragment in fragments:
                            if fragment not in text:
                                issues.append(
                                    f"{artifact_name}: verified fragment missing from "
                                    f"{pdf_name}: {fragment!r}"
                                )

    return tuple(dict.fromkeys(issues))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    issues = artifact_freshness_issues(args.project_root)
    if args.json:
        print(json.dumps({"ok": not issues, "issues": list(issues)}, indent=2))
    elif issues:
        for issue in issues:
            print(f"ERROR: {issue}")
    else:
        print("review artifact freshness: PASS")
    return 0 if not issues else 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
