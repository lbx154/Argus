"""Venue profile — the single seam for paper-format facts that differ by
publication venue (EMNLP/ACL, AAAI, and Frontiers in Sleep).

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
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VenueProfile:
    """Format facts for a single publication venue.

    Every field that the format gates branch on lives here. Numbers are
    page numbers (1-indexed, as ``pdfinfo``/``pdftotext`` report them).
    """

    # ---- identity -------------------------------------------------------
    key: str
    display_name: str

    # ---- body page geometry (the load-bearing layout numbers) -----------
    # Conclusion appearing before ``conclusion_underfill_page`` => the body
    # is underfilled. Conclusion after ``conclusion_max_page`` => overflow.
    # References must begin on ``references_min_page`` or later. Material
    # after References (appendix / reproducibility checklist) is uncapped.
    body_page_limit: int | None
    conclusion_underfill_page: int | None
    conclusion_max_page: int | None
    references_min_page: int | None

    # ---- column layout --------------------------------------------------
    # Two-column venues (EMNLP/ACL/AAAI/CVPR-style) distinguish a single-column
    # ``figure`` from a full-width ``figure*``; the layout review enforces that
    # teaser/pipeline visuals use ``figure*``. Single-column venues (NeurIPS,
    # ICML, …) have no such distinction, so those checks are skipped for them.
    two_column: bool = True

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

    # ---- journal-wide manuscript requirements ---------------------------
    main_text_word_limit: int | None = None
    requires_single_spacing: bool = False
    requires_line_numbers: bool = False
    review_model: str = "double-anonymized"
    requires_real_author_metadata: bool = False
    requires_ai_disclosure: bool = False
    requires_figure_alt_text: bool = False
    layout_format_persona: str = "two-column conference paper"

    # ---- review rubric / persona ---------------------------------------
    academic_language_rubric_id: str = "emnlp-academic-language-v2"
    reviewer_persona: str = "EMNLP"
    review_skill_path: str = "reviewer/emnlp-academic-language-review.md"

    # ---- figure (image-2) style persona --------------------------------
    # The venue family used when prompting/reviewing paper figures. EMNLP
    # figures read as "EMNLP/ACL/NeurIPS" method figures; AAAI as "AAAI".
    # image_tool builds its prompt/rubric skeleton and fills the venue from
    # here, so a figure is never prompted or graded against the wrong venue.
    figure_style_persona: str = "EMNLP/ACL/NeurIPS"

    # ---- shared quality heuristics (kept equal across venues for now) ---
    min_verified_bib_entries: int = 35
    min_cited_keys: int = 30
    abstract_word_floor: int = 170
    abstract_word_floor_is_hard: bool = True

    # convenience: secondary keys that resolve to this profile
    aliases: tuple[str, ...] = field(default_factory=tuple)

    # Built-in skill files (basenames) that are SPECIFIC to this venue. The
    # cross-venue matcher filter suppresses the *other* venue's files so an
    # AAAI project never matches the EMNLP drafting/preflight/router/review
    # skills and vice versa (and so the AAAI siblings never dilute EMNLP
    # matching). Venue-neutral skills (infrastructure review, image-2, figure
    # studio, audits) are NOT listed here and stay matchable for both.
    venue_skill_files: tuple[str, ...] = ()

    @property
    def has_fixed_page_budget(self) -> bool:
        """Whether this venue enforces a numbered main-body page boundary."""

        return all(
            value is not None
            for value in (
                self.body_page_limit,
                self.conclusion_underfill_page,
                self.conclusion_max_page,
                self.references_min_page,
            )
        )

    def page_budget_line(self) -> str:
        """One-line page-budget description for agent-facing prose."""
        if not self.has_fixed_page_budget:
            if self.main_text_word_limit is not None:
                return (
                    f"no fixed page limit; main text ≤{self.main_text_word_limit:,} "
                    "words (pagination judged for readability)"
                )
            return "no fixed page limit (pagination judged for readability)"
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
        if self.requires_line_numbers:
            return (
                f"Review line numbers from `{self.review_mode_macro}` are required "
                "submission artifacts and must not be treated as debug gutters."
            )
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

    # ---- (de)serialization for dynamic / project-local venue profiles ----
    def to_dict(self) -> dict:
        """JSON-serializable dict of every field (tuples render as arrays)."""
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "VenueProfile":
        """Build a profile from a plain dict (e.g. a researched
        ``research/VENUE_PROFILE.json``), fail-soft per field.

        Each field is coerced to its declared type. Unknown keys are ignored;
        missing optional fields keep the dataclass default. Explicit ``null`` is
        preserved for nullable fields. Raises ``ValueError`` on a non-dict
        payload or a missing required field.
        """
        import dataclasses
        from typing import get_args, get_origin, get_type_hints

        if not isinstance(payload, dict):
            raise ValueError("VenueProfile payload must be a dict")
        type_hints = get_type_hints(cls)
        kwargs: dict = {}
        for f in dataclasses.fields(cls):
            name = f.name
            has_default = (
                f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING
            )
            raw = payload.get(name)
            annotation = type_hints[name]
            annotation_args = get_args(annotation)
            declared_types = set(annotation_args) or {annotation}
            if name not in payload:
                if has_default:
                    continue  # let the dataclass supply its own default
                raise ValueError(f"VenueProfile requires field {name!r}")
            if raw is None:
                if type(None) in declared_types:
                    kwargs[name] = None
                    continue
                if has_default:
                    continue
                raise ValueError(f"VenueProfile requires non-null field {name!r}")
            if f.default is not dataclasses.MISSING:
                default = f.default
            elif f.default_factory is not dataclasses.MISSING:
                default = f.default_factory()
            else:
                default = None
            if bool in declared_types:
                kwargs[name] = bool(raw)
            elif int in declared_types:
                kwargs[name] = int(raw)
            elif get_origin(annotation) is tuple:
                kwargs[name] = (
                    tuple(str(x).strip() for x in raw if str(x).strip())
                    if isinstance(raw, (list, tuple))
                    else default
                )
            elif str in declared_types:
                kwargs[name] = str(raw)
            else:
                kwargs[name] = raw
        return cls(**kwargs)

    @classmethod
    def from_json(cls, path: "Path | str") -> "VenueProfile":
        """Load a profile from a ``VENUE_PROFILE.json`` file (raises on
        unreadable / malformed / invalid)."""
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot load VenueProfile from {path}") from exc
        return cls.from_dict(payload)




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
    figure_style_persona="EMNLP/ACL/NeurIPS",
    aliases=("ACL", "ARR", "FINDINGS"),
    venue_skill_files=(
        "emnlp-paper-drafting.md",
        "emnlp-format-preflight.md",
        "emnlp-paper-skill-router.md",
        "emnlp-academic-language-review.md",
    ),
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
    figure_style_persona="AAAI",
    # AAAI has no official abstract word limit — keep a soft advisory floor.
    abstract_word_floor=150,
    abstract_word_floor_is_hard=False,
    aliases=(),
    venue_skill_files=(
        "aaai-paper-drafting.md",
        "aaai-format-preflight.md",
        "aaai-paper-skill-router.md",
        "aaai-academic-language-review.md",
    ),
)

FRONTIERS_SLEEP_PROFILE = VenueProfile(
    key="FRONTIERS_SLEEP",
    display_name="Frontiers in Sleep",
    # Frontiers Hypothesis and Theory uses a word limit, not a numbered body-page
    # boundary. ``None`` is deliberate: do not emulate "unlimited" with a large
    # integer, because that reintroduces false underfill/overflow verdicts.
    body_page_limit=None,
    conclusion_underfill_page=None,
    conclusion_max_page=None,
    references_min_page=None,
    mandatory_end_sections=(),
    post_reference_sections=("Supplementary Material",),
    documentclass=r"\documentclass[utf8]{FrontiersinHarvard}",
    style_package="FrontiersinHarvard",
    review_option="",
    review_mode_macro=r"\linenumbers",
    style_clone_url="https://www.frontiersin.org/design/zip/Frontiers_LaTeX_Templates.zip",
    style_files=("FrontiersinHarvard.cls", "Frontiers-Harvard.bst"),
    anon_author_string="Real author metadata required",
    bib_style="Frontiers-Harvard",
    emit_bibliographystyle=True,
    main_text_word_limit=12_000,
    requires_single_spacing=True,
    requires_line_numbers=True,
    review_model="single-anonymized",
    requires_real_author_metadata=True,
    requires_ai_disclosure=True,
    requires_figure_alt_text=True,
    layout_format_persona="single-column biomedical journal manuscript",
    academic_language_rubric_id="frontiers-sleep-academic-language-v1",
    reviewer_persona="Frontiers in Sleep",
    review_skill_path="reviewer/academic-paper-peer-review-benchmark.md",
    figure_style_persona="Frontiers biomedical journal",
    abstract_word_floor=150,
    abstract_word_floor_is_hard=False,
    min_verified_bib_entries=15,
    min_cited_keys=15,
    aliases=("FRONTIERS", "FRONTIERS IN SLEEP", "FRSLE"),
    venue_skill_files=(),
)


# Registry keyed by canonical key. Lookups are case-insensitive and also
# honor each profile's aliases (so "ACL"/"ARR" -> EMNLP).
VENUE_PROFILES: dict[str, VenueProfile] = {
    EMNLP_PROFILE.key: EMNLP_PROFILE,
    AAAI_PROFILE.key: AAAI_PROFILE,
    FRONTIERS_SLEEP_PROFILE.key: FRONTIERS_SLEEP_PROFILE,
}

DEFAULT_VENUE_KEY = "EMNLP"

# Env override (highest precedence) — handy for tests and one-off runs.
_VENUE_ENV = "ARGUS_SKILL_VENUE"


def _alias_index() -> dict[str, VenueProfile]:
    index: dict[str, VenueProfile] = {}
    for profile in VENUE_PROFILES.values():
        index[profile.key.upper()] = profile
        index[_normalize_venue_key(profile.key)] = profile
        for alias in profile.aliases:
            index[alias.upper()] = profile
            index[_normalize_venue_key(alias)] = profile
    return index


def _normalize_venue_key(key: str) -> str:
    """Collapse a venue token to its canonical form: uppercase, drop separators,
    and strip a trailing 2- or 4-digit year — so ``aaai2026`` / ``AAAI 2026`` /
    ``AAAI-26`` all reduce to ``AAAI`` (and ``EMNLP 2026`` -> ``EMNLP``)."""
    compact = re.sub(r"[^A-Z0-9]", "", key.upper())
    return re.sub(r"(?:20)?\d{2}$", "", compact)


def get_venue_profile(key: str | None) -> VenueProfile:
    """Return the profile for ``key`` (case-insensitive, alias- and variant-aware).

    Tolerates natural planner tokens (for example ``aaai2026`` and
    ``Frontiers in Sleep``). An empty key preserves the historical EMNLP default.
    A non-empty unknown key raises ``KeyError``: silently grading it as EMNLP would
    be a false venue certification.
    """
    if not key:
        return VENUE_PROFILES[DEFAULT_VENUE_KEY]
    index = _alias_index()
    raw = str(key).strip().upper()
    profile = index.get(raw) or index.get(_normalize_venue_key(raw))
    if profile is not None:
        return profile
    raise KeyError(
        f"venue {key!r} matched no known profile; known venues/aliases: "
        f"{', '.join(sorted(index))}"
    )


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

    Precedence: ``ARGUS_SKILL_VENUE`` env override > a project-local researched
    ``research/VENUE_PROFILE.json`` (dynamic venue) > ``target_venue`` in
    ``research/PIPELINE_STATE.json`` (built-in registry) > EMNLP default.
    """
    env_key = os.environ.get(_VENUE_ENV)
    if env_key:
        return get_venue_profile(env_key)
    local = load_local_venue_profile(project_root)
    if local is not None:
        return local
    return get_venue_profile(_venue_key_from_pipeline_state(project_root))


VENUE_PROFILE_FILENAME = "VENUE_PROFILE.json"


def venue_profile_path(project_root: Path) -> Path:
    """Path to a project's dynamic (researched) venue profile."""
    return Path(project_root) / "research" / VENUE_PROFILE_FILENAME


def is_builtin_venue(key: object) -> bool:
    """True when ``key`` (case/alias/variant-insensitive) names a BUILT-IN venue.

    Used to decide whether a non-standard ``target_venue`` needs the online
    venue-format research step (only when it is NOT a built-in and has no
    cached ``VENUE_PROFILE.json``).
    """
    if not key:
        return False
    index = _alias_index()
    raw = str(key).strip().upper()
    return raw in index or _normalize_venue_key(raw) in index


def load_local_venue_profile(project_root: Path) -> "VenueProfile | None":
    """Return the project-local researched profile if present + valid, else
    ``None`` (fail-soft — a corrupt cache must never crash resolution)."""
    path = venue_profile_path(project_root)
    if not path.is_file():
        return None
    try:
        return VenueProfile.from_json(path)
    except Exception as exc:  # noqa: BLE001
        log.warning("ignoring invalid %s: %s", path, exc)
        return None


def write_venue_profile(project_root: Path, profile: VenueProfile) -> Path:
    """Atomically persist a researched profile to research/VENUE_PROFILE.json."""
    import tempfile

    path = venue_profile_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(profile.to_dict(), indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    return path


def cross_venue_excluded_skill_files(active: VenueProfile) -> set[str]:
    """Skill-file basenames to hide from the matcher for ``active``.

    Returns every *other* venue's venue-specific skill files so an AAAI
    project never matches the EMNLP drafting/preflight/router/review skills
    (and vice versa), and so each venue's siblings never dilute the other's
    matching. Venue-neutral skills are never excluded.
    """
    excluded: set[str] = set()
    for profile in VENUE_PROFILES.values():
        if profile.key != active.key:
            excluded.update(profile.venue_skill_files)
    return excluded


def venue_excluded_skill_files(project_root: Path) -> set[str]:
    """Convenience: resolve the venue for ``project_root`` and return the
    cross-venue skill-file exclusion set for the matcher.

    Skill matching is not a venue-certification gate. If a non-empty venue is
    unknown, exclude *all* venue-specific skills instead of crashing the whole
    agent loop or silently selecting EMNLP. The actual checklist/review resolver
    still raises ``KeyError`` and therefore fails closed.
    """
    try:
        active = resolve_venue_profile(project_root)
    except KeyError:
        log.error(
            "unknown venue for %s; excluding all venue-specific skills until "
            "the venue is corrected",
            project_root,
        )
        return {
            filename
            for profile in VENUE_PROFILES.values()
            for filename in profile.venue_skill_files
        }
    return cross_venue_excluded_skill_files(active)


__all__ = [
    "VenueProfile",
    "EMNLP_PROFILE",
    "AAAI_PROFILE",
    "FRONTIERS_SLEEP_PROFILE",
    "VENUE_PROFILES",
    "DEFAULT_VENUE_KEY",
    "get_venue_profile",
    "resolve_venue_profile",
    "cross_venue_excluded_skill_files",
    "venue_excluded_skill_files",
]
