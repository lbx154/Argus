"""Role-aware figure-width rules in the deterministic layout review.

Two-column venues (EMNLP/AAAI) distinguish a single-column ``figure`` from a
full-width ``figure*``. The layout review now (a) allows up to TWO full-width
body floats (a teaser + a main pipeline) instead of one, (b) advises promoting a
teaser/pipeline/overview graphic that is stuck in a single column, and (c) skips
both checks for single-column venues (``two_column=False``), where ``figure*``
has no meaning.
"""
from __future__ import annotations

import json

from argus_skill.verticals.research.paper_layout_review import (
    _deterministic_assessment,
    _single_column_wide_role_figures,
    _vision_prompt,
)
from argus_skill.verticals.research.venue_profiles import AAAI_PROFILE, EMNLP_PROFILE, VenueProfile


def _single_col_venue() -> VenueProfile:
    return VenueProfile(
        key="NEURIPS",
        display_name="NeurIPS 2026",
        body_page_limit=9,
        conclusion_underfill_page=8,
        conclusion_max_page=9,
        references_min_page=10,
        two_column=False,
    )


def _assess(tex: str, venue) -> set[str]:
    result = _deterministic_assessment(
        tex_text=tex, log_text="", layout_text="", threshold=3.5, venue=venue
    )
    return {issue["code"] for issue in result["issues"]}


def _issues(tex: str, venue) -> list[dict]:
    return _deterministic_assessment(
        tex_text=tex, log_text="", layout_text="", threshold=3.5, venue=venue
    )["issues"]


_TEASER = (
    r"\begin{figure*}[t]\includegraphics[width=\textwidth]{figures/teaser.png}"
    r"\caption{Teaser.}\label{fig:teaser}\end{figure*}"
)
_PIPELINE_WIDE = (
    r"\begin{figure*}[t]\includegraphics[width=\textwidth]{figures/method_pipeline.png}"
    r"\caption{Pipeline.}\label{fig:pipeline}\end{figure*}"
)
_PIPELINE_SINGLE = (
    r"\begin{figure}[t]\includegraphics[width=\linewidth]{figures/method_pipeline.png}"
    r"\caption{Pipeline.}\label{fig:pipeline}\end{figure}"
)
_ABLATION_SINGLE = (
    r"\begin{figure}[t]\includegraphics[width=\linewidth]{figures/ablation_lr.pdf}"
    r"\caption{Ablation.}\label{fig:ablation}\end{figure}"
)


# ---- no figure quota ------------------------------------------------------

def test_a_paper_is_not_failed_for_the_number_of_figures_it_carries() -> None:
    """Six body figures used to fail the layout review outright, and three
    full-width ones with it, while accepted work in these areas carries between
    four and twenty-six. Five of seven campaigns had settled on exactly one
    figure. Whether a figure earns its space is what the argument needs it to
    show; the compiled page is what layout review can actually see.
    """
    many = "".join(
        r"\begin{figure}[t]\includegraphics[width=\linewidth]{figures/f%d.pdf}"
        r"\caption{C%d.}\label{fig:f%d}\end{figure}" % (i, i, i)
        for i in range(8)
    )
    wide = (
        r"\begin{figure*}[t]\includegraphics[width=\textwidth]{figures/overview2.png}"
        r"\caption{X.}\label{fig:x}\end{figure*}"
    )
    codes = _assess(_TEASER + _PIPELINE_WIDE + wide + many, EMNLP_PROFILE)
    assert "too_many_body_figures" not in codes
    assert "too_many_wide_figures" not in codes


def test_vision_prompt_states_no_quota() -> None:
    for venue in (EMNLP_PROFILE, AAAI_PROFILE):
        prompt = _vision_prompt(deterministic={}, threshold=3.5, venue=venue)
        assert "quota" in prompt
        assert "at most one full-width figure*" not in prompt


# ---- single-column teaser/pipeline advisory --------------------------------

def test_single_column_pipeline_flagged_as_advisory() -> None:
    issues = _issues(_PIPELINE_SINGLE + _ABLATION_SINGLE, EMNLP_PROFILE)
    codes = {i["code"] for i in issues}
    assert "wide_role_figure_single_column" in codes
    flagged = next(i for i in issues if i["code"] == "wide_role_figure_single_column")
    # Advisory, not a hard gate.
    assert flagged.get("hard_gate") is not True
    assert flagged["severity"] == "major"


def test_ablation_only_single_column_not_flagged() -> None:
    codes = _assess(_ABLATION_SINGLE, EMNLP_PROFILE)
    assert "wide_role_figure_single_column" not in codes


def test_pipeline_as_figure_star_not_flagged() -> None:
    codes = _assess(_PIPELINE_WIDE + _ABLATION_SINGLE, EMNLP_PROFILE)
    assert "wide_role_figure_single_column" not in codes


def test_caption_only_mention_not_flagged() -> None:
    # Role keyword only in the caption, not the graphic path/label.
    tex = (
        r"\begin{figure}[t]\includegraphics[width=\linewidth]{figures/results_curve.pdf}"
        r"\caption{Our overall pipeline system results.}\label{fig:results}\end{figure}"
    )
    assert _single_column_wide_role_figures(tex) == []
    assert "wide_role_figure_single_column" not in _assess(tex, AAAI_PROFILE)


# ---- single-column venues opt out of both checks ---------------------------

def test_single_column_venue_skips_wide_checks() -> None:
    venue = _single_col_venue()
    # Many "figure*" and a single-column pipeline: neither rule fires for a
    # single-column venue.
    tex = _TEASER + _PIPELINE_WIDE + _PIPELINE_SINGLE
    codes = _assess(tex, venue)
    assert "too_many_wide_figures" not in codes
    assert "wide_role_figure_single_column" not in codes


# ---- two_column field (de)serialization -----------------------------------

def test_two_column_defaults_true_and_round_trips() -> None:
    assert EMNLP_PROFILE.two_column is True
    assert AAAI_PROFILE.two_column is True
    sc = _single_col_venue()
    assert sc.two_column is False
    rt = VenueProfile.from_dict(json.loads(json.dumps(sc.to_dict())))
    assert rt == sc and rt.two_column is False
