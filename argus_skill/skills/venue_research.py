"""Live-search venue selection and VenueProfile construction.

When ``target_venue`` is absent, the agent selects a domain-appropriate CCF-A
conference whose submission deadline has not passed at runtime. When the target
is non-built-in, it researches that venue directly. In both cases it writes
``research/VENUE_SELECTION.md`` and ``research/VENUE_PROFILE.json`` from official
sources, cached so the search runs once. Failure leaves venue selection
unresolved; venue-dependent gates then fail closed instead of silently using an
unrelated default.

Mirrors :mod:`argus_skill.skills.idea_search` (same live-search + run-once +
fail-open discipline). The detailed field playbook lives in the
``engineer/venue-format-research.md`` skill; the prompt here inlines the
essentials so the one-off ``run_exec`` call is self-contained.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.models import RunnerOptions
from ..core.run_gateway import run_exec as gateway_run_exec
from .venue_profiles import (
    _normalize_venue_key,
    _venue_key_from_pipeline_state,
    is_builtin_venue,
    load_local_venue_profile,
)

log = logging.getLogger(__name__)
VENUE_RESEARCH_ATTEMPT_FILENAME = "VENUE_RESEARCH_ATTEMPT.json"


def _attempt_path(workdir: Any) -> Path:
    return Path(workdir) / "research" / VENUE_RESEARCH_ATTEMPT_FILENAME


def _attempt_key(venue: str | None) -> str:
    return " ".join(str(venue or "").strip().split()).casefold()


def _completed_attempt_matches(workdir: Any, venue: str | None) -> bool:
    try:
        payload = json.loads(_attempt_path(workdir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema_version") == 1
        and str(payload.get("target_venue") or "").casefold() == _attempt_key(venue)
        and payload.get("provider_call_completed") is True
    )


def _record_completed_attempt(workdir: Any, venue: str | None) -> None:
    path = _attempt_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_venue": _attempt_key(venue),
                "attempted_at": datetime.now(timezone.utc).isoformat(),
                "provider_call_completed": True,
                "profile_created": load_local_venue_profile(Path(workdir)) is not None,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _target_venue(workdir: Any) -> str | None:
    try:
        return _venue_key_from_pipeline_state(Path(workdir))
    except Exception:  # noqa: BLE001
        return None


def needs_venue_research(workdir: Any) -> bool:
    """True when venue selection/profile research is still required."""
    try:
        venue = _target_venue(workdir)
        local = load_local_venue_profile(Path(workdir))
        if local is not None and (
            not venue
            or _normalize_venue_key(local.key) == _normalize_venue_key(str(venue))
        ):
            return False
        if _completed_attempt_matches(workdir, venue):
            return False
        return not venue or not is_builtin_venue(venue)
    except Exception:  # noqa: BLE001 — never let the guard raise
        return False


def _build_prompt(venue: str | None) -> str:
    selection = (
        f"The operator/project already named this target venue: {venue}. "
        "Verify its current submission cycle, deadline, and official format."
        if venue
        else
        "No venue is selected. Using LIVE web_search and official sources, "
        "identify CCF-A conferences relevant to this paper's actual AI research "
        "area whose main/research-track submission deadline has not passed at "
        "the current UTC date. Compare scope fit, exact deadline/time "
        "zone, conference cycle, and evidence requirements; choose the best fit. "
        "Do not choose a closed deadline merely because a bundled template exists."
    )
    return (
        "You are selecting and configuring the publication venue for a research "
        "paper. The choice must be current, explicit, and source-backed.\n\n"
        f"{selection}\n\n"
        "Write research/VENUE_SELECTION.md with: the current UTC date, candidate "
        "CCF-A venues considered, official CCF classification source, official "
        "CFP/deadline URLs, deadline time zones, open/closed determination, scope "
        "fit, and the selected venue with rejection reasons for alternatives.\n\n"
        "Using LIVE web_search, find the selected venue's OFFICIAL submission "
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
        "Also update only the descriptive `target_venue` field in "
        "research/PIPELINE_STATE.json to the selected profile key. Do not edit "
        "`current_stage` or any stage status.\n\n"
        "Validate it loads:\n"
        "  python -c \"from argus_skill.skills.venue_profiles import "
        "resolve_venue_profile as r; p=r('.'); print(p.key, p.page_budget_line())\"\n\n"
        "Also write paper/TEMPLATE_SOURCE.md recording the official URLs used, "
        "the extracted values, and `source: official | mirror (unverified)`. If "
        "a fact cannot be confirmed, record the uncertainty. If no suitable "
        "currently open CCF-A venue can be verified, write the blocker to "
        "research/VENUE_SELECTION.md and do not fabricate a profile. You are done "
        "only when the selection is open, source-backed, and the profile loads."
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
        venue = _target_venue(workdir)
        log.info("venue-research: codex live web-search for venue %r", venue)
        result = gateway_run_exec(
            runner,
            prompt=_build_prompt(venue),
            options=RunnerOptions(
                model=model,
                reasoning_effort="high",
                working_dir=str(Path(workdir).expanduser().resolve()),
                skip_git_repo_check=True,
                full_auto=True,
                live_search=True,
            ),
            run_label="venue-research",
        )
        if (
            int(getattr(result, "exit_code", 0) or 0) != 0
            or getattr(result, "fatal_error", None)
        ):
            return False
        _record_completed_attempt(workdir, venue)
        # Verify the agent actually produced a loadable profile.
        return load_local_venue_profile(Path(workdir)) is not None
    except Exception:  # noqa: BLE001 — must never break the loop
        log.debug("venue-research failed (fail-open)", exc_info=True)
        return False
