"""codex live web-search to build a VenueProfile for a NON-standard venue.

When ``target_venue`` is not a built-in (EMNLP/AAAI), the pipeline would
otherwise grade the paper against the EMNLP default. This module runs ONE codex
call with native live ``web_search`` + shell (full_auto) that researches the
venue's official submission format and writes ``research/VENUE_PROFILE.json`` —
cached so it runs once. Fail-open: any error leaves no file and the caller falls
back to the EMNLP default (with a warning), never blocking the loop.

Mirrors :mod:`argus_skill.skills.idea_search` (same live-search + run-once +
fail-open discipline). The detailed field playbook lives in the
``engineer/venue-format-research.md`` skill; the prompt here inlines the
essentials so the one-off ``run_exec`` call is self-contained.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .venue_profiles import (
    _venue_key_from_pipeline_state,
    is_builtin_venue,
    load_local_venue_profile,
    venue_profile_path,
)

log = logging.getLogger(__name__)


def _target_venue(workdir: Any) -> str | None:
    try:
        return _venue_key_from_pipeline_state(Path(workdir))
    except Exception:  # noqa: BLE001
        return None


def needs_venue_research(workdir: Any) -> bool:
    """True when the project's target venue is non-built-in AND not yet cached."""
    try:
        venue = _target_venue(workdir)
        if not venue or is_builtin_venue(venue):
            return False
        return not venue_profile_path(Path(workdir)).is_file()
    except Exception:  # noqa: BLE001 — never let the guard raise
        return False


def _build_prompt(venue: str) -> str:
    return (
        "You are preparing a paper for a specific publication venue and must "
        "capture its OFFICIAL format so the paper is written and reviewed "
        "correctly.\n\n"
        f"Target venue: {venue}\n\n"
        "Using LIVE web_search, find this venue's OFFICIAL submission "
        "instructions / author kit (call-for-papers, author guidelines, or the "
        "official LaTeX template). Extract its format facts — do NOT guess from "
        "memory; cite the official page.\n\n"
        "Then WRITE research/VENUE_PROFILE.json (a flat JSON object) with these "
        "fields (fill every format-critical one; omit a field to accept its "
        "default):\n"
        '  key (UPPERCASE, e.g. "NEURIPS"), display_name (e.g. "NeurIPS 2026"),\n'
        "  body_page_limit (int), conclusion_max_page (= body_page_limit), "
        "conclusion_underfill_page (usually body-1), references_min_page "
        "(usually body+1),\n"
        "  two_column (bool; true for EMNLP/AAAI/CVPR-style two-column kits, "
        "false for single-column kits like NeurIPS/ICML/ICLR),\n"
        "  mandatory_end_sections (list; [] if none), post_reference_sections "
        "(list),\n"
        "  documentclass, style_package, style_files (list), style_clone_url, "
        "review_mode_macro, anon_author_string, bib_style,\n"
        "  emit_bibliographystyle (bool; false if the style sets it itself), "
        "forbidden_packages (list),\n"
        "  requires_style_package / requires_pdfinfo / "
        "requires_reproducibility_checklist (bools),\n"
        '  reviewer_persona (venue name), figure_style_persona (same), '
        "abstract_word_floor (int), abstract_word_floor_is_hard (bool).\n\n"
        "Validate it loads:\n"
        "  python -c \"from argus_skill.skills.venue_profiles import "
        "resolve_venue_profile as r; p=r('.'); print(p.key, p.page_budget_line())\"\n\n"
        "Also write paper/TEMPLATE_SOURCE.md recording the official URLs used, "
        "the extracted values, and `source: official | mirror (unverified)`. If "
        "a fact cannot be confirmed, record the uncertainty and pick the most "
        "official value. You are done when research/VENUE_PROFILE.json exists "
        "and loads."
    )


def research_venue_profile(
    runner: Any, workdir: Any, *, model: str = "gpt-5.5"
) -> bool:
    """Run ONE codex live-web-search + shell round to research the target
    venue's format and write ``research/VENUE_PROFILE.json``.

    Returns True if a loadable profile now exists (either freshly built or
    already cached). Never raises (fail-open).
    """
    try:
        if load_local_venue_profile(Path(workdir)) is not None:
            return True  # already researched / cached
        if runner is None or not hasattr(runner, "run_exec"):
            return False
        if not needs_venue_research(workdir):
            return False
        venue = _target_venue(workdir) or ""
        from ..core.models import RunnerOptions

        log.info("venue-research: codex live web-search for venue %r", venue)
        runner.run_exec(
            prompt=_build_prompt(venue),
            options=RunnerOptions(
                model=model,
                reasoning_effort="high",
                skip_git_repo_check=True,
                full_auto=True,
                live_search=True,
            ),
            run_label="venue-research",
        )
        # Verify the agent actually produced a loadable profile.
        return load_local_venue_profile(Path(workdir)) is not None
    except Exception:  # noqa: BLE001 — must never break the loop
        log.debug("venue-research failed (fail-open)", exc_info=True)
        return False
