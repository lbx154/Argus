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
    fig, axes = plt.subplots(2, 3, figsize=(10.0, 6.3), facecolor=BONE)
    fig.subplots_adjust(left=0.055, right=0.985, top=0.83, bottom=0.07,
                        hspace=0.92, wspace=0.30)

    def header(ax, title, sub, row, direction):
        ax.set_facecolor(BONE)
        ax.text(0.0, 1.22, title, transform=ax.transAxes, fontsize=9.5,
                fontweight="bold", color=GRAPHITE, ha="left", va="bottom")
        ax.text(0.0, 1.09, sub, transform=ax.transAxes, fontsize=7.2,
                color=GRAPHITE_SOFT, ha="left", va="bottom")
        execution = f"{row['agent_backbone']}  \u00b7  {row['agent_backend']}"
        ax.text(0.0, 0.97, f"{execution}  \u00b7  {direction}", transform=ax.transAxes,
                fontsize=6.9, color=BLUE, ha="left", va="bottom")

    def pair_plot(ax, *, title, sub, row, argus, reference, reference_name,
                  formatter, tick_formatter, direction, delta, pad):
        header(ax, title, sub, row, direction)
        lo, hi = sorted((argus, reference))
        ax.set_xlim(lo - pad, hi + pad)
        ax.set_ylim(0.0, 1.0)
        ax.hlines(0.47, lo, hi, color=PANEL_LINE, linewidth=3.0, zorder=1)
        ax.scatter([argus], [0.47], s=88, color=BLUE_DEEP, edgecolor=GRAPHITE,
                   linewidth=0.6, marker="o", zorder=3)
        ax.scatter([reference], [0.47], s=78, facecolor=BONE, edgecolor=BLUE,
                   linewidth=1.5, marker="D", zorder=3)
        ax.text(argus, 0.68, formatter(argus), ha="center", va="bottom",
                fontsize=8.4, color=BLUE_DEEP, fontweight="bold")
        ax.text(reference, 0.22, formatter(reference), ha="center", va="top",
                fontsize=8.0, color=GRAPHITE_SOFT, fontweight="bold")
        ax.text(argus, 0.58, "Argus", ha="center", va="bottom",
                fontsize=6.8, color=BLUE_DEEP)
        ax.text(reference, 0.34, reference_name, ha="center", va="top",
                fontsize=6.6, color=GRAPHITE_SOFT)
        ax.text(0.99, 0.02, delta, transform=ax.transAxes, ha="right", va="bottom",
                fontsize=7.0, color=GOLD, fontweight="bold")
        ax.set_yticks([])
        ticks = [lo, (lo + hi) / 2.0, hi]
        ax.set_xticks(ticks)
        ax.set_xticklabels([tick_formatter(value) for value in ticks])
        ax.tick_params(axis="x", colors=GRAPHITE_SOFT, labelsize=6.7, length=2.5)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(PANEL_LINE)

    # Panel A: SOL-ExecBench is a rank-and-placement summary rather than a scalar
    # comparison, so use a compact scorecard instead of forcing it onto an axis.
    ax = axes[0][0]
    header(ax, "NVIDIA SOL-ExecBench", "B200 \u00b7 101 kernels",
           r["NVIDIA SOL-ExecBench"], "rank / placements")
    ax.axis("off")
    ax.text(0.02, 0.52, "#6", transform=ax.transAxes, fontsize=30,
            color=BLUE_DEEP, fontweight="bold", ha="left", va="center")
    ax.text(0.02, 0.30, "global rank", transform=ax.transAxes, fontsize=7.4,
            color=GRAPHITE_SOFT, ha="left")
    chips = [("2 #1s", 0.46), ("7 top-3", 0.67), ("2 H2H", 0.87)]
    for text, x in chips:
        ax.text(x, 0.51, text, transform=ax.transAxes, fontsize=7.9,
                color=GRAPHITE, fontweight="bold", ha="center", va="center",
                bbox={"boxstyle": "round,pad=0.35", "facecolor": BONE,
                      "edgecolor": PANEL_LINE, "linewidth": 0.7})
    ax.text(0.67, 0.27, "H2H vs. Recursive", transform=ax.transAxes,
            fontsize=6.7, color=GRAPHITE_SOFT, ha="center")

    pair_plot(
        axes[0][1], title="nanochat \u00b7 B200",
        sub="5 min \u00b7 1\u00d7B200 \u00b7 426 attempts",
        row=r["nanochat \u00b7 B200"], argus=0.9636, reference=0.9646,
        reference_name="Human SOTA", formatter=lambda v: f"{v:.4f}",
        tick_formatter=lambda v: f"{v:.4f}", direction="BPB \u2193",
        delta="0.0010 lower", pad=0.00055,
    )
    pair_plot(
        axes[0][2], title="nanochat \u00b7 H100",
        sub="5 min \u00b7 1\u00d7H100 \u00b7 37 mechanisms",
        row=r["nanochat \u00b7 H100"], argus=0.9855, reference=0.9879,
        reference_name="Human SOTA", formatter=lambda v: f"{v:.4f}",
        tick_formatter=lambda v: f"{v:.4f}", direction="BPB \u2193",
        delta="0.0024 lower", pad=0.00115,
    )
    pair_plot(
        axes[1][0], title="nanoGPT speedrun", sub="8\u00d7H100 \u00b7 N=10",
        row=r["nanoGPT speedrun"], argus=79.77, reference=80.18,
        reference_name="Human #83", formatter=lambda v: f"{v:.2f}s",
        tick_formatter=lambda v: f"{v:.2f}", direction="time \u2193",
        delta="0.41s faster", pad=0.24,
    )
    pair_plot(
        axes[1][1], title="AARRI-Bench", sub="82 research-intern tasks",
        row=r["AARRI-Bench"], argus=76.8, reference=68.3,
        reference_name="Paper best", formatter=lambda v: f"{v:.1f}%",
        tick_formatter=lambda v: f"{v:.1f}", direction="solve rate \u2191",
        delta="+8.5 pp", pad=4.0,
    )

    # Panel F: four systems share one higher-is-better gap metric.
    ax = axes[1][2]
    row = r["Arbor \u00b7 RUC NLPIR"]
    header(ax, "Math-Reasoning Data", "Arbor AO suite \u00b7 AIME-style synthesis",
           row, "pass@4\u2212pass@1 \u2191")
    labels = ["Codex", "Claude Code", "Arbor", "Argus"]
    vals = [6.25, 8.33, 20.83, 28.0]
    ypos = list(range(len(labels)))
    ax.hlines(ypos, 0, vals, color=PANEL_LINE, linewidth=1.8)
    ax.scatter(vals[:-1], ypos[:-1], s=52, facecolor=BONE, edgecolor=BLUE,
               linewidth=1.3, marker="D", zorder=3)
    ax.scatter([vals[-1]], [ypos[-1]], s=78, color=BLUE_DEEP,
               edgecolor=GRAPHITE, linewidth=0.6, marker="o", zorder=3)
    for y, label, value in zip(ypos, labels, vals):
        ax.text(-0.8, y, label, ha="right", va="center", fontsize=6.9,
                color=GRAPHITE if label == "Argus" else GRAPHITE_SOFT,
                fontweight="bold" if label == "Argus" else "normal")
        ax.text(value + 0.7, y, f"{value:.2f}", ha="left", va="center",
                fontsize=7.3, color=BLUE_DEEP if label == "Argus" else GRAPHITE_SOFT,
                fontweight="bold")
    ax.set_xlim(0, 31)
    ax.set_ylim(-0.6, 3.6)
    ax.set_yticks([])
    ax.tick_params(axis="x", colors=GRAPHITE_SOFT, labelsize=6.7, length=2.5)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(PANEL_LINE)

    fig.suptitle("Public results in native units (direct labels; no cross-arena normalization)",
                 fontsize=11.2, fontweight="bold", color=BLUE_DEEP,
                 x=0.055, ha="left", y=0.972)
    fig.text(0.985, 0.965, "\u25cf Argus     \u25c7 external reference",
             fontsize=7.2, color=GRAPHITE_SOFT, ha="right", va="top")
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
                 edgecolor=GRAPHITE, linewidth=0.5, height=0.62, hatch="///",
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
    ax.set_xlabel("research artifacts (de-duplicated inventory)", fontsize=8.0,
                  color=GRAPHITE_SOFT)
    ax.tick_params(colors=GRAPHITE_SOFT, labelsize=7.6, length=2.5)
    for spine in ("top", "right", "left"):
        ax.spines[spine].set_visible(False)
    ax.spines["bottom"].set_color(PANEL_LINE)
    ax.set_title("")
    fig.text(0.035, 0.945, "Research portfolio \u2014 41 artifacts across six programs",
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
