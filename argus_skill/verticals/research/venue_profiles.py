"""Venue profile — the single seam for paper-format facts that differ by
publication venue.

There are NO built-in venues. Every venue's format facts come from live
research against the venue's official author kit: the venue-format research
step (``venue_research.py``) reads the official call for papers and template,
then writes the project-local ``research/VENUE_PROFILE.json``. This module
defines the :class:`VenueProfile` container those researched facts load into
and resolves the active profile for a project.

There is deliberately NO implicit venue. An unconfigured research project must
first select a currently open, domain-appropriate venue and persist a
researched ``research/VENUE_PROFILE.json``. Silently grading a paper against
another venue's rules is a false certification.
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
    """Format facts for a single publication venue, researched from the
    venue's official author kit.

    Numbers are page numbers (1-indexed, as ``pdfinfo``/``pdftotext`` report
    them).
    """

    # ---- identity -------------------------------------------------------
    key: str
    display_name: str

    # ---- body page geometry (the load-bearing layout numbers) -----------
    # Conclusion after ``conclusion_max_page`` => overflow. References must
    # begin on ``references_min_page`` or later. Material after References
    # (appendix / reproducibility checklist) is uncapped.
    body_page_limit: int | None
    conclusion_underfill_page: int | None
    conclusion_max_page: int | None
    references_min_page: int | None

    # ---- column layout --------------------------------------------------
    # Two-column venues distinguish a single-column ``figure`` from a
    # full-width ``figure*``; single-column venues have no such distinction.
    two_column: bool = True

    # ---- end-matter contract -------------------------------------------
    # Sections that MUST appear after Conclusion, and sections legitimately
    # allowed AFTER References.
    mandatory_end_sections: tuple[str, ...] = ()
    post_reference_sections: tuple[str, ...] = ()

    # ---- LaTeX template / style ----------------------------------------
    documentclass: str = r"\documentclass{article}"
    style_package: str = ""
    review_option: str = ""
    review_mode_macro: str = ""
    style_clone_url: str = ""
    style_files: tuple[str, ...] = ()
    anon_author_string: str = "Anonymous authors"
    bib_style: str = ""
    # Whether the selected venue expects an explicit bibliography-style command.
    emit_bibliographystyle: bool = True
    forbidden_packages: tuple[str, ...] = ()

    # ---- optional venue structural requirements -------------------------
    requires_style_package: bool = False
    requires_pdfinfo: bool = False
    forbids_nocopyright: bool = False
    forbids_thanks_in_titleblock: bool = False
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
    academic_language_rubric_id: str = "venue-academic-language-v1"
    reviewer_persona: str = "selected venue reviewer"
    review_skill_path: str = "reviewer/venue-academic-language-review.md"

    # ---- figure style persona -------------------------------------------
    figure_style_persona: str = "selected venue"

    # convenience: secondary keys that resolve to this profile
    aliases: tuple[str, ...] = field(default_factory=tuple)

    venue_skill_files: tuple[str, ...] = (
        "venue-paper-drafting.md",
        "venue-format-preflight.md",
        "venue-academic-language-review.md",
    )

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

        A reproducibility checklist may legitimately follow References at some
        venues, so it is deliberately excluded here.
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
        """The end-of-paper section order after the main body, for prose."""
        if self.mandatory_end_sections:
            tail = ", ".join(self.mandatory_end_sections)
            return f"Conclusion, {tail}, Reproducibility appendix"
        if self.requires_reproducibility_checklist:
            return "Conclusion, then References, then a Reproducibility Checklist"
        return "Conclusion"

    # ---- (de)serialization for researched venue profiles -----------------
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


def _normalize_venue_key(key: str) -> str:
    """Collapse a venue token to its canonical form: uppercase, drop separators,
    and strip a trailing 2- or 4-digit year — so ``neurips2026`` /
    ``NeurIPS 2026`` / ``NeurIPS-26`` all reduce to ``NEURIPS``."""
    compact = re.sub(r"[^A-Z0-9]", "", key.upper())
    return re.sub(r"(?:20)?\d{2}$", "", compact)


def _venue_key_from_pipeline_state(project_root: Path) -> str | None:
    from ...core.pipeline_state import read_pipeline_state

    try:
        data = read_pipeline_state(project_root)
    except (OSError, ValueError):
        return None
    value = data.get("target_venue") or data.get("venue")
    return str(value) if value else None


def resolve_venue_profile(
    project_root: Path | str | os.PathLike[str],
) -> VenueProfile:
    """Resolve the active venue profile for a project.

    The only source of venue format facts is the project-local researched
    ``research/VENUE_PROFILE.json`` (written by the live venue-format research
    step from the venue's official author kit). A missing or mismatched
    profile raises ``KeyError`` so venue-dependent work cannot silently use
    the wrong template.

    Accept ordinary path-like inputs because the documented validation command
    intentionally calls this resolver as ``resolve_venue_profile('.')``.
    """
    project_root = Path(project_root)
    state_key = _venue_key_from_pipeline_state(project_root)
    local = load_local_venue_profile(project_root)
    if local is not None and (
        not state_key
        or _normalize_venue_key(local.key) == _normalize_venue_key(state_key)
    ):
        return local
    if not state_key:
        raise KeyError(
            "no target venue selected: do not infer or search for one; ask the "
            "operator to name a venue or explicitly request venue discovery before "
            "venue-dependent paper work"
        )
    raise KeyError(
        f"venue {state_key!r} has no researched profile yet: run the venue-format "
        "research step against the venue's official author kit to write "
        "research/VENUE_PROFILE.json"
    )


VENUE_PROFILE_FILENAME = "VENUE_PROFILE.json"


def venue_profile_path(project_root: Path) -> Path:
    """Path to a project's researched venue profile."""
    return Path(project_root) / "research" / VENUE_PROFILE_FILENAME


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


__all__ = [
    "VenueProfile",
    "resolve_venue_profile",
    "load_local_venue_profile",
    "venue_profile_path",
]
