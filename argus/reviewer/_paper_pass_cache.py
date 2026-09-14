"""Host-only reuse of assessments whose entire evidence is an unchanged PDF.

Scientific judgments can depend on changing code, raw data, or external sources;
they are deliberately not memoized here. Neither are integrated verdicts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def pdf_sha256(workdir: Path) -> str:
    """Return exact rendered bytes, or disable reuse if they cannot be read."""
    try:
        with (workdir / "paper" / "main.pdf").open("rb") as stream:
            digest = hashlib.sha256()
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        return ""


def paper_pass_key(*, pdf_digest: str, prompt: str, config: Any, runner: Any) -> str:
    if not pdf_digest or not config.model:
        return ""
    workdir = Path(config.artifact_root or config.working_dir or ".").resolve()
    state_root = Path(config.vertical_state_root or workdir).resolve()
    policy_files: dict[str, str] = {}
    for path in (workdir / "research/VENUE_PROFILE.json", state_root / "goal_contract.json"):
        try:
            policy_files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except FileNotFoundError:
            policy_files[str(path)] = "absent"
        except OSError:
            return ""
    payload = {
        "pdf": pdf_digest,
        "prompt": prompt,
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "extra_args": config.extra_args,
        "policy_context": config.review_policy_context,
        "policy_files": policy_files,
        "backend": str(getattr(runner, "backend", type(runner).__qualname__)),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def identical_snapshot_pair(comparison: Any) -> bool:
    """Check actual immutable trees, not just the saved manifest's claim."""
    if comparison.before_sha256 != comparison.after_sha256:
        return False
    from ..core.manuscript_narrative_runtime import manuscript_closure_sha256

    try:
        return (
            manuscript_closure_sha256(comparison.before_paper.parent)
            == manuscript_closure_sha256(comparison.after_paper.parent)
            == comparison.before_sha256
        )
    except (OSError, ValueError):
        return False
