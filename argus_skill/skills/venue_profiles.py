"""Venue profile — the single seam for paper-format facts that differ by
publication venue (EMNLP/ACL vs AAAI).

Why this exists
---------------

The harness was built EMNLP/ACL-first: the page budget (Conclusion by page 8,
References on page 9+), the mandatory ``Limitations``/``Ethical Considerations``
end-matter, the ``Anonymous EMNLP Submission`` author block, the ACL style
files, and the ``emnlp-academic-language-v2`` rubric were all hardcoded as bare
constants spread across ``paper_layout_review``, ``stage_check``,
``stage_checklists``, ``paper_structural_minimums`` and the academic-language
review. An ``aaai2026`` run would still have been graded against EMNLP rules.

AAAI-2026 differs on every axis the format layer enforces (verified against the
official AAAI-26 submission instructions and the ``aaai2026.sty`` LaTeX kit):

* **Page budget** — 7 pages of *technical content*; References (and the
  reproducibility checklist) go on additional pages that do **not** count
  toward the 7. So Conclusion lands by page 7 and References start on page 8+
  (EMNLP = 8 / 9).
* **No mandatory ``Limitations``/``Ethics`` sections** (those are ACL/ARR).
* **Reproducibility checklist** belongs in the PDF *after* References.
* **LaTeX**: ``\\documentclass[letterpaper]{article}`` +
  ``\\usepackage[submission]{aaai2026}`` (anonymous review) with mandatory
  ``times``/``helvet``/``courier`` and a ``\\pdfinfo{... /TemplateVersion ...}``
  block. ``aaai2026.sty`` **sets the bibliographystyle itself** — emitting
  ``\\bibliographystyle`` is an *error* ("Illegal, another \\bibstyle command").
  ``hyperref`` and ``navigator`` are incompatible (forbidden). ``\\nocopyright``
  is forbidden for accepted papers.
* **Anonymous block** renders as the literal "Anonymous submission".

This module centralizes those facts as a frozen :class:`VenueProfile`, exposes a
small registry, and resolves the active profile from
``research/PIPELINE_STATE.json``'s ``target_venue`` field (the previously dead
seam) — defaulting to EMNLP so existing behavior is byte-identical.

The EMNLP profile here **must** reproduce today's constants exactly; the gates
default to EMNLP when no venue is resolved, so an unconfigured project keeps its
current behavior.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class VenueProfile:
    """Format facts for a single publication venue.

    Every field that the format gates branch on lives here. Numbers are
    page numbers (1-indexed, as ``pdfinfo``/``pdftotext`` report them).
    """

    # ---- identity -------------------------------------------------------
    key: str                      # canonical key: "EMNLP" | "AAAI"
    display_name: str             # human label: "EMNLP 2026" | "AAAI 2026"

    # ---- body page geometry (the load-bearing layout numbers) -----------
    # Conclusion appearing before ``conclusion_underfill_page`` => the body
    # is underfilled. Conclusion after ``conclusion_max_page`` => overflow.
    # References must begin on ``references_min_page`` or later. Material
    # after References (appendix / reproducibility checklist) is uncapped.
    body_page_limit: int
    conclusion_underfill_page: int
    conclusion_max_page: int
    references_min_page: int

    # ---- end-matter contract -------------------------------------------
    # Sections that MUST appear after Conclusion (ACL: Limitations + Ethics;
    # AAAI: none). ``post_reference_sections`` are sections legitimately
    # allowed AFTER References (AAAI: the reproducibility checklist).
    mandatory_end_sections: tuple[str, ...] = ()
    post_reference_sections: tuple[str, ...] = ()

    # ---- LaTeX template / style ----------------------------------------
    documentclass: str = r"\documentclass[11pt]{article}"
    style_package: str = "acl"           # \usepackage[..]{<style_package>}
    review_option: str = "review"        # the anonymous-review package option
    review_mode_macro: str = r"\usepackage[review]{acl}"
    style_clone_url: str = "https://github.com/acl-org/acl-style-files"
    style_files: tuple[str, ...] = ("acl.sty", "acl_natbib.bst")
    anon_author_string: str = "Anonymous EMNLP Submission"
    bib_style: str = "acl_natbib"
    # ACL authors emit \bibliographystyle{acl_natbib}; AAAI must NOT (the
    # aaai2026 class sets it and a manual command errors).
    emit_bibliographystyle: bool = True
    forbidden_packages: tuple[str, ...] = ()

    # ---- AAAI-only structural requirements (all default off => EMNLP) ---
    requires_style_package: bool = False        # \usepackage{aaai2026} present
    requires_pdfinfo: bool = False              # \pdfinfo{...} block present
    forbids_nocopyright: bool = False           # \nocopyright forbidden
    forbids_thanks_in_titleblock: bool = False  # \thanks in title forbidden
    requires_reproducibility_checklist: bool = False

    # ---- review rubric / persona ---------------------------------------
    academic_language_rubric_id: str = "emnlp-academic-language-v2"
    reviewer_persona: str = "EMNLP"
    review_skill_path: str = "reviewer/emnlp-academic-language-review.md"

    # ---- shared quality heuristics (kept equal across venues for now) ---
    min_verified_bib_entries: int = 35
    min_cited_keys: int = 30
    abstract_word_floor: int = 170
    abstract_word_floor_is_hard: bool = True

    # convenience: secondary keys that resolve to this profile
    aliases: tuple[str, ...] = field(default_factory=tuple)

    def page_budget_line(self) -> str:
        """One-line page-budget description for agent-facing prose."""
        return (
            f"body ≤{self.body_page_limit} pages, Conclusion by page "
            f"{self.conclusion_max_page}, References start on page "
            f"{self.references_min_page}+ (material after References is uncapped)"
        )

    def end_matter_boundary_pattern(self) -> str:
        """Regex for post-Conclusion body end-matter that must NOT share a
        rendered page with References.

        Returns the same set for both venues: AAAI mandates none of these,
        but if an author includes a Limitations/Ethics section it is still
        body matter. AAAI's reproducibility checklist legitimately follows
        References, so it is deliberately excluded here.
        """
        terms = (
            "Limitations",
            "Ethical Considerations",
            "Ethics",
            "Release and Reproducibility",
        )
        return r"\b(?:" + "|".join(terms) + r")\b"

    def end_matter_prose(self) -> str:
        """Human description of what legitimately follows the Conclusion."""
        if self.mandatory_end_sections:
            return f"{' and '.join(self.mandatory_end_sections)} after the Conclusion"
        if self.requires_reproducibility_checklist:
            return (
                "the reproducibility checklist after the References "
                "(no mandatory Limitations/Ethics)"
            )
        return "any end matter after the Conclusion"

    def review_linenumber_prose(self) -> str:
        """Describe the venue's legitimate anonymous-review line-number artifact."""
        return (
            f"Anonymous review-mode line numbers from `{self.review_mode_macro}` are "
            "acceptable submission artifacts and must not be treated as debug gutters."
        )

    def draft_section_tail(self) -> str:
        """The end-of-paper section order after the main body, for prose.

        EMNLP: Conclusion + Limitations + Ethics + Reproducibility appendix.
        AAAI: Conclusion, then References, then a Reproducibility Checklist
        (no mandatory Limitations/Ethics).
        """
        if self.mandatory_end_sections:
            tail = ", ".join(self.mandatory_end_sections)
            return f"Conclusion, {tail}, Reproducibility appendix"
        if self.requires_reproducibility_checklist:
            return "Conclusion, then References, then a Reproducibility Checklist"
        return "Conclusion"




# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

EMNLP_PROFILE = VenueProfile(
    key="EMNLP",
    display_name="EMNLP 2026",
    body_page_limit=8,
    conclusion_underfill_page=7,
    conclusion_max_page=8,
    references_min_page=9,
    mandatory_end_sections=("Limitations", "Ethical Considerations"),
    post_reference_sections=("Appendix",),
    documentclass=r"\documentclass[11pt]{article}",
    style_package="acl",
    review_option="review",
    review_mode_macro=r"\usepackage[review]{acl}",
    style_clone_url="https://github.com/acl-org/acl-style-files",
    style_files=("acl.sty", "acl_natbib.bst"),
    anon_author_string="Anonymous EMNLP Submission",
    bib_style="acl_natbib",
    emit_bibliographystyle=True,
    forbidden_packages=(),
    academic_language_rubric_id="emnlp-academic-language-v2",
    reviewer_persona="EMNLP",
    review_skill_path="reviewer/emnlp-academic-language-review.md",
    aliases=("ACL", "ARR", "FINDINGS"),
)

AAAI_PROFILE = VenueProfile(
    key="AAAI",
    display_name="AAAI 2026",
    # AAAI-26: 7 pages of technical content; References + reproducibility
    # checklist go on additional, uncounted pages.
    body_page_limit=7,
    conclusion_underfill_page=6,
    conclusion_max_page=7,
    references_min_page=8,
    # AAAI does not mandate Limitations/Ethics. The reproducibility
    # checklist legitimately follows References.
    mandatory_end_sections=(),
    post_reference_sections=("Reproducibility Checklist", "Appendix"),
    documentclass=r"\documentclass[letterpaper]{article}",
    style_package="aaai2026",
    review_option="submission",
    review_mode_macro=r"\usepackage[submission]{aaai2026}",
    style_clone_url="https://aaai.org/conference/aaai/aaai-26/",
    style_files=("aaai2026.sty", "aaai2026.bst"),
    anon_author_string="Anonymous submission",
    bib_style="aaai2026",
    # aaai2026.sty sets the bibliographystyle; emitting one is an error.
    emit_bibliographystyle=False,
    forbidden_packages=("hyperref", "navigator"),
    requires_style_package=True,
    requires_pdfinfo=True,
    forbids_nocopyright=True,
    forbids_thanks_in_titleblock=True,
    requires_reproducibility_checklist=True,
    academic_language_rubric_id="aaai-academic-language-v2",
    reviewer_persona="AAAI",
    review_skill_path="reviewer/aaai-academic-language-review.md",
    # AAAI has no official abstract word limit — keep a soft advisory floor.
    abstract_word_floor=150,
    abstract_word_floor_is_hard=False,
    aliases=(),
)


# Registry keyed by canonical key. Lookups are case-insensitive and also
# honor each profile's aliases (so "ACL"/"ARR" -> EMNLP).
VENUE_PROFILES: dict[str, VenueProfile] = {
    EMNLP_PROFILE.key: EMNLP_PROFILE,
    AAAI_PROFILE.key: AAAI_PROFILE,
}

DEFAULT_VENUE_KEY = "EMNLP"

# Env override (highest precedence) — handy for tests and one-off runs.
_VENUE_ENV = "ARGUS_SKILL_VENUE"


def _alias_index() -> dict[str, VenueProfile]:
    index: dict[str, VenueProfile] = {}
    for profile in VENUE_PROFILES.values():
        index[profile.key.upper()] = profile
        for alias in profile.aliases:
            index[alias.upper()] = profile
    return index


def get_venue_profile(key: str | None) -> VenueProfile:
    """Return the profile for ``key`` (case-insensitive, alias-aware).

    Unknown / empty keys fall back to the EMNLP default so callers never
    have to guard against ``None``.
    """
    if not key:
        return VENUE_PROFILES[DEFAULT_VENUE_KEY]
    return _alias_index().get(str(key).strip().upper(), VENUE_PROFILES[DEFAULT_VENUE_KEY])


def _venue_key_from_pipeline_state(project_root: Path) -> str | None:
    state_path = project_root / "research" / "PIPELINE_STATE.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get("target_venue") or data.get("venue")
    return str(value) if value else None


def resolve_venue_profile(project_root: Path) -> VenueProfile:
    """Resolve the active venue profile for a project.

    Precedence: ``ARGUS_SKILL_VENUE`` env override > ``target_venue`` in
    ``research/PIPELINE_STATE.json`` > EMNLP default.
    """
    env_key = os.environ.get(_VENUE_ENV)
    if env_key:
        return get_venue_profile(env_key)
    return get_venue_profile(_venue_key_from_pipeline_state(project_root))


__all__ = [
    "VenueProfile",
    "EMNLP_PROFILE",
    "AAAI_PROFILE",
    "VENUE_PROFILES",
    "DEFAULT_VENUE_KEY",
    "get_venue_profile",
    "resolve_venue_profile",
]
