"""Host-only reuse of assessments whose entire evidence is an unchanged PDF.

Scientific judgments can depend on changing code, raw data, or external sources;
they are deliberately not memoized here. Neither are integrated verdicts.
"""
from __future__ import annotations

import filecmp
import json
from pathlib import Path
from typing import Any

from ..planner.planner import file_state


def pdf_state(workdir: Path) -> str:
    """The rendered PDF's file state, or "" (disabling reuse) when it cannot be read."""
    try:
        return file_state(workdir / "paper" / "main.pdf")
    except OSError:
        return ""


def same_pdf(workdir: Path, copy_root: Path, state: str) -> bool:
    """Whether ``copy_root`` holds, byte for byte, the PDF that had ``state``."""
    source = workdir / "paper" / "main.pdf"
    try:
        return pdf_state(workdir) == state and filecmp.cmp(
            source, copy_root / "paper" / "main.pdf", shallow=False
        )
    except OSError:
        return False


def paper_pass_key(*, pdf_state: str, prompt: str, config: Any, runner: Any) -> str:
    if not pdf_state or not config.model:
        return ""
    workdir = Path(config.artifact_root or config.working_dir or ".").resolve()
    state_root = Path(config.vertical_state_root or workdir).resolve()
    policy_files: dict[str, str | None] = {}
    for path in (workdir / "research/VENUE_PROFILE.json", state_root / "goal_contract.json"):
        try:
            policy_files[str(path)] = file_state(path)
        except FileNotFoundError:
            policy_files[str(path)] = None
        except OSError:
            return ""
    return json.dumps(
        {
            "pdf": pdf_state,
            "prompt": prompt,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "extra_args": config.extra_args,
            "policy_context": config.review_policy_context,
            "policy_files": policy_files,
            "backend": str(getattr(runner, "backend", type(runner).__qualname__)),
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def identical_snapshot_pair(comparison: Any) -> bool:
    """Compare the two immutable trees byte for byte, not the saved manifest's claim."""
    before, after = Path(comparison.before_paper), Path(comparison.after_paper)
    try:
        files = _files(before)
        return bool(files) and files == _files(after) and all(
            filecmp.cmp(before / name, after / name, shallow=False) for name in files
        )
    except OSError:
        return False


def _files(root: Path) -> list[Path]:
    return sorted(path.relative_to(root) for path in root.rglob("*") if path.is_file())
