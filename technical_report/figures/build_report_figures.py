#!/usr/bin/env python3
"""Deterministic data-figure builder for the Argus technical report.

This script renders the two source-controlled DATA figures from committed
public-safe evidence bundles:

  1. ``public_results``   -- six-arena results as small multiples (units differ;
                             panels are never cross-normalized).
  2. ``paper_portfolio``  -- six-program paper counts with manuscript/draft split.

No image-model call is required: both figures are drawn with matplotlib from
data that already lives in the repository. Output is deterministic -- running
the script twice produces byte-identical PDF/PNG files (timestamps are stripped
and all geometry is fixed), so the SHA-256 digests recorded in
``REPORT_FIGURES.json`` are reproducible.

The six STRUCTURAL/concept figures (master_spine, dense_intelligence,
system_planes, argus_architecture, mission_lifecycle, long_horizon_reliability)
are NOT drawn here: they are produced by the gpt-image-2 image model and carry
their own provenance in ``IMAGE2_FIGURES.json`` (see
``build_ai_figure_provenance.py`` and ``validate_ai_figures.py``).

Usage::

    python technical_report/figures/build_report_figures.py

Palette follows the Argus website's Blue-Gold narrative: bone-white page,
graphite ink, system blue / deep blue accents, and a gold frontier accent.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

# --------------------------------------------------------------------------- #
# Determinism: no embedded timestamps, fixed fonts, no user-config interference.
# --------------------------------------------------------------------------- #
matplotlib.rcParams.update(
    {
        "svg.hashsalt": "argus-report-figures",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "font.family": "DejaVu Sans",
        "font.size": 9.0,
        "axes.unicode_minus": False,
        "figure.dpi": 100,
        "savefig.dpi": 200,
    }
)

# Blue-Gold palette (matches the Argus website / expanding-frontier narrative).
BONE = "#FBFAF6"
GRAPHITE = "#24272B"
GRAPHITE_SOFT = "#4A4F55"
BLUE = "#315BCE"
BLUE_DEEP = "#214884"
GOLD = "#C38A20"
PANEL_LINE = "#D8D6CE"
EVIDENCE_LINE = "#9AAE93"

HERE = Path(__file__).resolve().parent
EVIDENCE = HERE.parent / "evidence"
METADATA_PATH = HERE / "REPORT_FIGURES.json"

# Blank document-info dict so the PDF backend embeds no creation/mod date.
_PDF_METADATA = {
    "Title": "",
    "Author": "Argus Team",
    "Subject": "",
    "Creator": "",
    "Producer": "",
    "CreationDate": None,
}
_PNG_METADATA = {"Software": None}


def _save(fig, stem: str) -> dict:
    pdf_path = HERE / f"{stem}.pdf"
    png_path = HERE / f"{stem}.png"
    fig.savefig(pdf_path, facecolor=BONE, metadata=_PDF_METADATA)
    fig.savefig(png_path, facecolor=BONE, metadata=_PNG_METADATA)
    plt.close(fig)
    return {
        "pdf": pdf_path.name,
        "png": png_path.name,
        "pdf_sha256": _sha256(pdf_path),
        "png_sha256": _sha256(png_path),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _results_data() -> dict:
    data = json.loads((EVIDENCE / "website_results.json").read_text("utf-8"))
    return {r["arena"]: r for r in data["results"]}


def build_public_results() -> dict:
    r = _results_data()
    fig, axes = plt.subplots(2, 3, figsize=(9.4, 7.0), facecolor=BONE)
    fig.subplots_adjust(left=0.07, right=0.985, top=0.80, bottom=0.07,
                        hspace=1.15, wspace=0.34)

    def style(ax, title, sub, tier, better):
        ax.set_facecolor(BONE)
        ax.text(0.0, 1.26, title, transform=ax.transAxes, fontsize=8.8,
                fontweight="bold", color=GRAPHITE, ha="left", va="bottom")
        ax.text(0.0, 1.13, sub, transform=ax.transAxes, fontsize=6.8,
                color=GRAPHITE_SOFT, ha="left", va="bottom")
        tag = "artifact digest" if tier == "local_artifact" else "website snapshot"
        tcol = EVIDENCE_LINE if tier == "local_artifact" else BLUE
        ax.text(0.0, 1.00, f"{tag}  \u00b7  {better}", transform=ax.transAxes,
                fontsize=6.5, color=tcol, ha="left", va="bottom")
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(PANEL_LINE)
        ax.tick_params(colors=GRAPHITE_SOFT, labelsize=7.0, length=2.5)

    def label_bars(ax, bars, values, fmt, dy=0.0):
        for b, v in zip(bars, values):
            ax.text(b.get_x() + b.get_width() / 2,
                    b.get_height() + dy,
                    fmt.format(v), ha="center", va="bottom", fontsize=6.9,
                    color=GRAPHITE, fontweight="bold")

    # Panel A: SOL-ExecBench (counts, same unit).
    ax = axes[0][0]
    cats = ["#1\nfinishes", "Top-3\nplacements", "H2H wins\nvs Recursive"]
    vals = [2, 7, 2]
    cols = [BLUE_DEEP, BLUE_DEEP, BLUE]
    bars = ax.bar(cats, vals, color=cols, width=0.62, edgecolor=GRAPHITE,
                  linewidth=0.4)
    label_bars(ax, bars, vals, "{:d}", dy=0.08)
    ax.set_ylim(0, 8.4)
    ax.set_ylabel("kernels (of 101)", fontsize=7.0, color=GRAPHITE_SOFT)
    style(ax, "NVIDIA SOL-ExecBench", "B200 \u00b7 101 kernels \u00b7 Global #6",
          r["NVIDIA SOL-ExecBench"]["corroboration"], "rank")

    # Panel B: nanochat B200 (BPB, lower better).
    ax = axes[0][1]
    vals = [0.9636, 0.9646]
    bars = ax.bar(["Argus", "Human\nSOTA"], vals, color=[BLUE_DEEP, BLUE],
                  width=0.55, edgecolor=GRAPHITE, linewidth=0.4)
    ax.set_ylim(0.960, 0.966)
    label_bars(ax, bars, vals, "{:.4f}", dy=0.00012)
    ax.set_ylabel("val BPB", fontsize=7.0, color=GRAPHITE_SOFT)
    style(ax, "nanochat \u00b7 B200", "5 min \u00b7 1\u00d7B200 \u00b7 426 attempts",
          r["nanochat \u00b7 B200"]["corroboration"], "lower better")

    # Panel C: nanochat H100 (BPB, lower better).
    ax = axes[0][2]
    vals = [0.9855, 0.9879]
    bars = ax.bar(["Argus", "Human\nSOTA"], vals, color=[BLUE_DEEP, BLUE],
                  width=0.55, edgecolor=GRAPHITE, linewidth=0.4)
    ax.set_ylim(0.982, 0.989)
    label_bars(ax, bars, vals, "{:.4f}", dy=0.00014)
    ax.set_ylabel("val BPB", fontsize=7.0, color=GRAPHITE_SOFT)
    style(ax, "nanochat \u00b7 H100", "5 min \u00b7 1\u00d7H100 \u00b7 37 mechanisms",
          r["nanochat \u00b7 H100"]["corroboration"], "lower better")

    # Panel D: nanoGPT speedrun (seconds, lower better).
    ax = axes[1][0]
    vals = [79.77, 80.18]
    bars = ax.bar(["Argus", "Human #83\n(same device)"], vals,
                  color=[BLUE_DEEP, BLUE], width=0.55, edgecolor=GRAPHITE,
                  linewidth=0.4)
    ax.set_ylim(79.0, 80.6)
    label_bars(ax, bars, vals, "{:.2f}s", dy=0.03)
    ax.set_ylabel("wall-clock (s)", fontsize=7.0, color=GRAPHITE_SOFT)
    style(ax, "nanoGPT speedrun", "8\u00d7H100 \u00b7 N=10",
          r["nanoGPT speedrun"]["corroboration"], "lower better")

    # Panel E: AARRI-Bench (percent, higher better).
    ax = axes[1][1]
    vals = [76.8, 68.3]
    bars = ax.bar(["Argus\n63/82", "Paper-reported\nbest"], vals,
                  color=[BLUE_DEEP, BLUE], width=0.55, edgecolor=GRAPHITE,
                  linewidth=0.4)
    ax.set_ylim(0, 100)
    label_bars(ax, bars, vals, "{:.1f}%", dy=1.0)
    ax.set_ylabel("solve rate (%)", fontsize=7.0, color=GRAPHITE_SOFT)
    style(ax, "AARRI-Bench", "82 research-intern tasks",
          r["AARRI-Bench"]["corroboration"], "higher better")

    # Panel F: Arbor (gap metric, grouped systems).
    ax = axes[1][2]
    labels = ["Argus", "Arbor", "Claude\nCode", "Codex"]
    vals = [28.0, 20.83, 8.33, 6.25]
    cols = [BLUE_DEEP, BLUE, GRAPHITE_SOFT, PANEL_LINE]
    bars = ax.bar(labels, vals, color=cols, width=0.68, edgecolor=GRAPHITE,
                  linewidth=0.4)
    ax.set_ylim(0, 31)
    label_bars(ax, bars, vals, "{:.2f}", dy=0.3)
    ax.set_ylabel("gap score", fontsize=7.0, color=GRAPHITE_SOFT)
    style(ax, "Arbor \u00b7 RUC NLPIR", "Math-Reasoning Data",
          r["Arbor \u00b7 RUC NLPIR"]["corroboration"], "site-reported metric")

    fig.suptitle("Public results across six arenas (units differ; panels are "
                 "not cross-normalized)", fontsize=10.5, fontweight="bold",
                 color=BLUE_DEEP, x=0.07, ha="left", y=0.965)
    return _save(fig, "public_results")


# --------------------------------------------------------------------------- #
# Figure 4: research portfolio.
# --------------------------------------------------------------------------- #
def build_paper_portfolio() -> dict:
    data = json.loads((EVIDENCE / "paper_inventory.json").read_text("utf-8"))
    counts = {}
    for p in data["papers"]:
        m, d = counts.setdefault(p["program"], [0, 0])
        if p["status"] == "manuscript":
            counts[p["program"]][0] += 1
        else:
            counts[p["program"]][1] += 1

    programs = sorted(data["programs"], key=lambda k: sum(counts[k]))
    manuscripts = [counts[p][0] for p in programs]
    drafts = [counts[p][1] for p in programs]

    fig, ax = plt.subplots(figsize=(9.0, 5.0), facecolor=BONE)
    fig.subplots_adjust(left=0.30, right=0.965, top=0.80, bottom=0.13)
    ax.set_facecolor(BONE)

    ypos = range(len(programs))
    b1 = ax.barh(ypos, manuscripts, color=BLUE_DEEP, edgecolor=GRAPHITE,
                 linewidth=0.5, height=0.62, label="Manuscripts (35)")
    b2 = ax.barh(ypos, drafts, left=manuscripts, color=BLUE,
                 edgecolor=GRAPHITE, linewidth=0.5, height=0.62,
                 label="Drafts (6)")

    for i, p in enumerate(programs):
        m, d = counts[p]
        if m:
            ax.text(m / 2, i, str(m), ha="center", va="center", fontsize=8.2,
                    color=BONE, fontweight="bold")
        if d:
            ax.text(m + d / 2, i, str(d), ha="center", va="center",
                    fontsize=8.2, color=GRAPHITE, fontweight="bold")
        ax.text(m + d + 0.25, i, f"{m + d}", ha="left", va="center",
                fontsize=8.4, color=GRAPHITE, fontweight="bold")

    ax.set_yticks(list(ypos))
    ax.set_yticklabels(programs, fontsize=8.2, color=GRAPHITE)
    ax.set_xlim(0, 18)
    ax.set_xlabel("papers (de-duplicated inventory)", fontsize=8.0,
                  color=GRAPHITE_SOFT)
    ax.tick_params(colors=GRAPHITE_SOFT, labelsize=7.6, length=2.5)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(PANEL_LINE)
    ax.set_title("")
    fig.text(0.035, 0.945, "Research portfolio \u2014 41 papers across six programs",
             fontsize=11.0, fontweight="bold", color=BLUE_DEEP, ha="left",
             va="center")
    fig.text(0.035, 0.878,
             "35 manuscripts + 6 drafts   \u00b7   human-authored baselines only"
             "   \u00b7   de-duplicated inventory, not accepted papers",
             fontsize=7.4, color=GRAPHITE_SOFT, ha="left", va="center")
    ax.legend(loc="lower right", fontsize=7.8, frameon=True, facecolor=BONE,
              edgecolor=PANEL_LINE)
    return _save(fig, "paper_portfolio")

def main() -> None:
    figures = {
        "public_results": build_public_results(),
        "paper_portfolio": build_paper_portfolio(),
    }
    metadata = {
        "schema": "argus-report-figures/v1",
        "description": (
            "Deterministic, source-controlled DATA figures generated by "
            "build_report_figures.py from committed source-grounded "
            "specifications and evidence bundles. No image-model call is used; "
            "digests are reproducible across runs. The six structural figures "
            "are image-2 outputs recorded separately in IMAGE2_FIGURES.json."
        ),
        "palette": {
            "bone_white": BONE,
            "graphite": GRAPHITE,
            "system_blue": BLUE,
            "deep_blue": BLUE_DEEP,
            "frontier_gold": GOLD,
        },
        "source_evidence": [
            "technical_report/evidence/website_results.json",
            "technical_report/evidence/paper_inventory.json",
        ],
        "generator": "technical_report/figures/build_report_figures.py",
        "figures": figures,
    }
    METADATA_PATH.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for name, info in figures.items():
        print(f"{name:20s} pdf={info['pdf_sha256'][:12]}  png={info['png_sha256'][:12]}")
    print(f"metadata -> {METADATA_PATH.name}")


if __name__ == "__main__":
    main()
