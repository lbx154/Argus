"""codex web-search as an ADDITIONAL candidate source for research ideation.

argus's research stage works in two phases: GENERATE candidates
(``idea-discovery`` -> ``research/IDEA_CANDIDATES.md``) then SELECT the feasible
one (``idea-creator`` ranks + pilots, ``novelty-check`` de-dupes,
``signal-derisk`` validates). This module adds ONE MORE candidate *source*: a
single codex call with native live web_search that surfaces literature-grounded
gaps and appends them to the candidate pool. The existing selection machinery is
untouched — it simply ranks over a richer pool.

Design rules:
  * SOURCE only — never selects, never rewrites existing candidates; it APPENDS
    under a provenance marker so ``idea-creator`` merges both sources.
  * Operator constraints (train-free, target venue, "beat a baseline") are NOT
    baked in here — they stay operator-level and are applied downstream by
    ``idea-creator``'s tractability/feasibility ranking. The prompt only asks for
    honest feasibility notes.
  * Fail-open + run-once: any error returns 0 and never raises; a provenance
    marker prevents re-appending on later research rounds.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Provenance marker delimiting the codex-web-search block in IDEA_CANDIDATES.md.
SOURCE_MARKER = "<!-- source: codex-web-search -->"

_CANDIDATES_RELPATH = ("research", "IDEA_CANDIDATES.md")
_BRIEF_RELPATH = ("research", "RESEARCH_BRIEF.md")


def _candidates_path(workdir: Any) -> Path:
    return Path(workdir).joinpath(*_CANDIDATES_RELPATH)


def _already_seeded(workdir: Any) -> bool:
    """True if a codex-web-search block was already appended (run-once guard)."""
    try:
        p = _candidates_path(workdir)
        return p.is_file() and SOURCE_MARKER in p.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 — never let the guard raise
        return False


def _resolve_direction(workdir: Any, direction: str | None) -> str:
    """The broad research direction to search around: caller-provided objective,
    else the top of RESEARCH_BRIEF.md, else empty (caller decides to skip)."""
    if direction and direction.strip():
        return direction.strip()
    try:
        brief = Path(workdir).joinpath(*_BRIEF_RELPATH)
        if brief.is_file():
            text = brief.read_text(encoding="utf-8").strip()
            # first non-empty, non-heading paragraph is the framing
            for para in text.split("\n\n"):
                para = para.strip()
                if para and not para.startswith("#"):
                    return para[:1200]
    except Exception:  # noqa: BLE001
        pass
    return ""


def _build_prompt(direction: str, n: int) -> str:
    return (
        "You are a senior ML researcher doing candidate discovery for a paper.\n"
        f"Research direction:\n{direction}\n\n"
        "Using LIVE web_search, find REAL recent papers (roughly the last 18 "
        "months) closely related to this direction on arXiv / Semantic Scholar. "
        "Surface measured-but-unexplained gaps and openings that prior work has "
        "NOT closed.\n\n"
        f"Output EXACTLY {n} candidate ideas, each as a markdown block in this "
        "format (ids WS-1, WS-2, ...):\n\n"
        "## Candidate WS-1: <one-line mechanism/approach hypothesis>\n\n"
        "**Phenomenon / opening**: <what is measured or left open, and by whom>\n\n"
        "**Hypothesis**: <falsifiable claim about the mechanism/method>\n\n"
        "**Grounding**: <cite 1-2 REAL papers you found via search, "
        "title + year + arxiv id; state exactly what they did and the gap they leave>\n\n"
        "**Experiment sketch**: <setup / measurements / falsifier / rough budget>\n\n"
        "**Feasibility (honest)**: <needs training? what compute? does the core "
        "signal plausibly move on a modest setup? — just note it honestly>\n\n"
        "**Novelty bet**: <why this is not a re-measurement of the cited work>\n\n"
        "**Anticipated kill-argument**: <the strongest ~40-word rejection>\n\n"
        "Rules: cite ONLY papers you actually found via search (no fabricated ids); "
        "do NOT impose any hard constraint (training-free / must-beat-baseline / "
        "venue) — those are decided elsewhere; keep the whole answer under ~700 "
        "words. Output ONLY the candidate blocks, nothing else."
    )


def _extract_message(result: Any) -> str:
    """Last agent message from a RunnerResult-shaped object (fail-open to '')."""
    try:
        if getattr(result, "exit_code", 1) != 0:
            return ""
        msgs = getattr(result, "agent_messages", None) or []
        return str(msgs[-1]) if msgs else ""
    except Exception:  # noqa: BLE001
        return ""


def augment_idea_candidates(
    runner: Any,
    workdir: Any,
    *,
    direction: str | None = None,
    model: str = "gpt-5.5",
    n: int = 6,
) -> int:
    """Run ONE codex web-search ideation call and APPEND its candidates to
    ``research/IDEA_CANDIDATES.md``. Returns the number of candidate blocks
    appended (0 on any skip/error). Never raises.

    Reuses the ``RunnerOptions.live_search`` flag (-> codex ``web_search="live"``)
    so the call does real live literature search. Run-once guarded by
    :data:`SOURCE_MARKER`.
    """
    try:
        if runner is None or not hasattr(runner, "run_exec"):
            return 0
        if _already_seeded(workdir):
            return 0
        resolved = _resolve_direction(workdir, direction)
        if not resolved:
            return 0

        from ..core.models import RunnerOptions

        log.info(
            "idea-search: running codex live web-search (model=%s, n=%d) for %r",
            model, n, resolved[:80],
        )
        result = runner.run_exec(
            prompt=_build_prompt(resolved, n),
            options=RunnerOptions(
                model=model,
                reasoning_effort="high",
                skip_git_repo_check=True,
                full_auto=True,
                live_search=True,
            ),
            run_label="idea-search",
        )
        body = _extract_message(result).strip()
        if "## Candidate" not in body:
            return 0

        block = (
            f"\n\n{SOURCE_MARKER}\n"
            "## Web-search candidates (codex live search — merge & rank with the above)\n\n"
            f"{body}\n"
        )
        path = _candidates_path(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(block)

        count = body.count("## Candidate ")
        log.info("idea-search: appended %d web-search candidate(s) to %s", count, path)
        return count
    except Exception:  # noqa: BLE001 — a candidate SOURCE must never break the loop
        log.debug("idea-search augment failed", exc_info=True)
        return 0
