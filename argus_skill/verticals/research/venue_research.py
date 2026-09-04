"""Live-search VenueProfile construction for an explicitly selected venue.

An absent ``target_venue`` never authorizes venue discovery. When an explicit
target venue has no researched profile yet, the agent researches only that
venue and writes ``research/VENUE_PROFILE.json`` from official sources, cached
so the search runs once. Failure leaves the venue unresolved; venue-dependent
checks then fail closed instead of silently choosing or using an unrelated
default.

The detailed field playbook lives in the ``engineer/venue-format-research.md``
skill; the prompt here inlines the essentials so the one-off ``run_exec`` call
is self-contained.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.models import RunnerOptions
from ...core.run_gateway import run_exec as gateway_run_exec
from .venue_profiles import (
    _normalize_venue_key,
    _venue_key_from_pipeline_state,
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
    """True when an explicit venue still needs a researched local profile.

    Never raises, and when it cannot answer it fails toward doing the work.
    ``False`` here means "confirmed: nothing to research", and nothing in the
    pipeline asks a second time — so a probe that merely broke must not borrow
    that answer, or the paper targets a venue whose CFP deadline, scope and
    official format were never verified. One extra search is the cheaper
    mistake. Note ``research_venue_profile``'s ``False`` means the opposite:
    the run failed, try again later.
    """
    venue: str | None = None
    try:
        venue = _target_venue(workdir)
        if not venue:
            return False
        local = load_local_venue_profile(Path(workdir))
        if local is not None and (
            _normalize_venue_key(local.key) == _normalize_venue_key(str(venue))
        ):
            return False
        if _completed_attempt_matches(workdir, venue):
            return False
        # No venue is built in: every explicit venue needs a researched
        # profile from its official author kit.
        return True
    except Exception as exc:  # noqa: BLE001 — never let the guard raise
        # A recorded completed attempt still short-circuits, so answering
        # "needs research" on a broken probe cannot turn into a retry loop:
        # the first run that reaches the provider writes the attempt file and
        # every later probe reads it before it can fail on anything else.
        try:
            settled = bool(venue) and _completed_attempt_matches(workdir, venue)
        except Exception:  # noqa: BLE001 — the short-circuit is best-effort too
            settled = False
        log.warning(
            "venue-research: could not determine whether venue %r needs "
            "research (%s: %s); %s",
            venue,
            type(exc).__name__,
            exc,
            "a completed attempt is on record, treating it as researched"
            if settled
            else "assuming it still does",
            exc_info=True,
        )
        return not settled


def _build_prompt(venue: str) -> str:
    return (
        "The operator/project explicitly selected this publication venue: "
        f"{venue}. Verify only this venue's current submission cycle, deadline, "
        "scope, and official format. Do not search for or select alternatives "
        "unless the operator explicitly requested venue discovery.\n\n"
        "Using LIVE web_search, find the venue's OFFICIAL submission "
        "instructions / author kit (call-for-papers, author guidelines, or the "
        "official LaTeX template). Extract its format facts — do NOT guess from "
        "memory; use the official page.\n\n"
        "Then WRITE research/VENUE_PROFILE.json (a flat JSON object) with these "
        "fields (fill every format-critical one; omit a field to accept its "
        "default):\n"
        '  key (UPPERCASE, e.g. "NEURIPS"), display_name (e.g. "NeurIPS 2026"),\n'
        "  body_page_limit (int), conclusion_max_page (= body_page_limit), "
        "conclusion_underfill_page (usually body-1), references_min_page "
        "(usually body+1),\n"
        "  two_column (bool; true for two-column kits, false for single-column "
        "kits),\n"
        "  mandatory_end_sections (list; [] if none), post_reference_sections "
        "(list),\n"
        "  documentclass, style_package, style_files (list), style_clone_url "
        "(the official author-kit URL), review_mode_macro, anon_author_string, "
        "bib_style,\n"
        "  emit_bibliographystyle (bool; false if the style sets it itself), "
        "forbidden_packages (list),\n"
        "  requires_style_package / requires_pdfinfo / "
        "requires_reproducibility_checklist (bools),\n"
        "  reviewer_persona (venue name), figure_style_persona (same).\n\n"
        "Also update only the descriptive `target_venue` field in "
        ".argus/PIPELINE_STATE.json to the selected profile key. Do not edit "
        "`current_stage` or any stage status.\n\n"
        "Validate it loads:\n"
        "  python -c \"from argus_skill.verticals.research.venue_profiles import "
        "resolve_venue_profile as r; p=r('.'); print(p.key, p.page_budget_line())\"\n\n"
        "Do not create any other venue report or template-source file. If a "
        "fact cannot be confirmed from official sources, leave that field at "
        "its default rather than guessing; if the explicit venue cannot be "
        "verified at all, do not fabricate a profile — report the blocker in "
        "your final message. You are done only when the venue is source-backed "
        "and the profile loads."
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
        if not venue:
            return False
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
