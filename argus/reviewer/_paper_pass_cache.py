"""Host-only reuse of assessments whose entire evidence is an unchanged PDF.

Scientific judgments can depend on changing code, raw data, or external sources;
they are deliberately not memoized here. Neither are integrated verdicts.
"""
from __future__ import annotations

import filecmp
import json
from pathlib import Path
from typing import Any

from ..core.file_facts import file_facts


def pdf_facts(workdir: Path) -> list[int]:
    """The rendered PDF's ``file_facts``, or [] (disabling reuse) when it cannot be read."""
    try:
        return file_facts(workdir / "paper" / "main.pdf")
    except OSError:
        return []


def same_pdf(workdir: Path, copy_root: Path, facts: list[int]) -> bool:
    """Whether ``copy_root`` holds, byte for byte, the PDF that had ``facts``."""
    source = workdir / "paper" / "main.pdf"
    try:
        return pdf_facts(workdir) == facts and filecmp.cmp(
            source, copy_root / "paper" / "main.pdf", shallow=False
        )
    except OSError:
        return False


def paper_pass_key(*, pdf: list[int], prompt: str, config: Any, runner: Any) -> str:
    if not pdf or not config.model:
        return ""
    workdir = Path(config.artifact_root or config.working_dir or ".").resolve()
    state_root = Path(config.vertical_state_root or workdir).resolve()
    policy_files: dict[str, list[int] | None] = {}
    for path in (workdir / "research/VENUE_PROFILE.json", state_root / "goal_contract.json"):
        try:
            policy_files[str(path)] = file_facts(path)
        except FileNotFoundError:
            policy_files[str(path)] = None
        except OSError:
            return ""
    return json.dumps(
        {
            "pdf": pdf,
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
