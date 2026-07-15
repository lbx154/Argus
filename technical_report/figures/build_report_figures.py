#!/usr/bin/env python3
"""Deterministic report-figure builder for the Argus technical report.

This script renders six source-controlled figures from committed source-grounded
specifications and public-safe evidence bundles:

  1. ``master_spine``       -- the expanding-frontier causal chain and the four
                               runtime roles (Manager / Planner / Engineer /
                               Reviewer).
  2. ``dense_intelligence``  -- explanatory decision/execution/verification
                               continuity schematic (not a performance score).
  3. ``system_planes``      -- control / execution / evidence plane interfaces.
  4. ``mission_lifecycle``  -- mission state transitions and recovery edges.
  5. ``public_results``     -- six-arena results as small multiples (units differ;
                               panels are never cross-normalized).
  6. ``paper_portfolio``    -- six-program paper counts with manuscript/draft split.

No image-model call is required: every figure is drawn with matplotlib from data
that already lives in the repository. Output is deterministic -- running the
script twice produces byte-identical PDF/PNG files (timestamps are stripped and
all geometry is fixed), so the SHA-256 digests recorded in
``REPORT_FIGURES.json`` are reproducible.

Usage::

    python technical_report/figures/build_report_figures.py

Palette follows the Argus website's Blue-Gold narrative: bone-white page,
graphite ink, system blue / deep blue accents, and a gold frontier accent.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from argus_skill.core.event_catalog import EventType

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
BLUE_SOFT = "#EAF0FF"
GOLD = "#C38A20"
GOLD_SOFT = "#FFF3D6"
PANEL_FILL = "#F2F1EC"
PANEL_LINE = "#D8D6CE"
EVIDENCE_FILL = "#EAEEE8"
EVIDENCE_LINE = "#9AAE93"
RECOVERY = "#8A5A3B"  # muted terracotta for recovery / fault edges

MASTER_SPINE_STAGES = (
    ("Unknown objective", "OOD problem or deeper challenge"),
    ("Dense Intelligence Runtime", "continuous organized research work"),
    ("Evidence Gate", "artifacts \u00b7 measurements \u00b7 failures \u00b7 verdicts"),
    ("Runtime Evolution", "memory \u00b7 skills \u00b7 tools \u00b7 verifiers \u00b7 routing \u00b7 evaluations"),
    ("Expanded OOD Frontier", "the next unknown task does not start from zero"),
)

MASTER_SPINE_ROLES = (
    ("Manager", "intent \u00b7 lifetime \u00b7 stage"),
    ("Planner", "decompose \u00b7 schedule \u00b7 re-plan"),
    ("Engineer", "retrieve \u00b7 build \u00b7 experiment"),
    ("Reviewer", "inspect evidence \u00b7 decide"),
)

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


def _new_axes(width: float, height: float):
    fig = plt.figure(figsize=(width, height), facecolor=BONE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(BONE)
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")
    return fig, ax


def _box(ax, x, y, w, h, *, face, edge, lw=1.1, radius=1.6):
    patch = FancyBboxPatch(
        (x, y),
        w,
        h,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        linewidth=lw,
        facecolor=face,
        edgecolor=edge,
        mutation_aspect=1.0,
        zorder=2,
    )
    ax.add_patch(patch)


def _text(ax, x, y, s, *, size=9, color=GRAPHITE, weight="normal", ha="center",
          va="center", style="normal"):
    ax.text(
        x,
        y,
        s,
        fontsize=size,
        color=color,
        fontweight=weight,
        ha=ha,
        va=va,
        style=style,
        zorder=3,
    )


def _arrow(ax, x1, y1, x2, y2, *, color=GRAPHITE_SOFT, lw=1.2, style="-|>",
           mutation=12, ls="-", connection="arc3,rad=0.0"):
    arrow = FancyArrowPatch(
        (x1, y1),
        (x2, y2),
        arrowstyle=style,
        mutation_scale=mutation,
        linewidth=lw,
        color=color,
        linestyle=ls,
        connectionstyle=connection,
        zorder=1.5,
    )
    ax.add_patch(arrow)


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


# --------------------------------------------------------------------------- #
# Figure 1: three-plane architecture.
# --------------------------------------------------------------------------- #
def build_system_planes() -> dict:
    fig, ax = _new_axes(7.6, 5.6)

    bands = [
        (
            "CONTROL PLANE",
            "research judgment \u00b7 what to run, whether it is done",
            68.5,
            26.0,
            BLUE_SOFT,
            BLUE_DEEP,
            [
                ("Manager", "front door \u00b7 vertical\nselect \u00b7 stage authority"),
                ("Planner (L4)", "forward scheduling\ntask DAG \u00b7 backlog"),
                ("LifeSupervisor", "7\u00d724 mission loop\nbudget \u00b7 drain"),
            ],
        ),
        (
            "EXECUTION PLANE",
            "one mission \u00b7 real tools, providers, budgets",
            38.5,
            26.0,
            PANEL_FILL,
            GRAPHITE_SOFT,
            [
                ("SkillLoop", "match / distill skill\nbuild prompt"),
                ("Engineer (L1)", "round of real work\nbounded session"),
                ("Reviewer (L2)", "structured verdict\ndone / continue / blocked"),
                ("Run gateway", "codex / claude / copilot\ncall-id \u00b7 quota \u00b7 price"),
            ],
        ),
        (
            "EVIDENCE PLANE",
            "measurement integrity \u00b7 append-only, auditable",
            8.5,
            26.0,
            EVIDENCE_FILL,
            EVIDENCE_LINE,
            [
                ("Event tape", f"events.jsonl\n{len(EventType)} typed events"),
                ("Usage ledger", "tokens \u00b7 premium\ncost \u00b7 budget"),
                ("Secret guard", "credential redaction\npre-persist"),
                ("Provenance", "artifact SHA-256\nproject wiki"),
            ],
        ),
    ]

    for title, subtitle, y, h, face, edge, comps in bands:
        _box(ax, 3.0, y, 94.0, h, face=face, edge=edge, lw=1.4, radius=2.2)
        _text(ax, 5.6, y + h - 4.2, title, size=10.5, color=edge, weight="bold",
              ha="left")
        _text(ax, 5.6, y + h - 8.4, subtitle, size=7.8, color=GRAPHITE_SOFT,
              ha="left", style="italic")
        n = len(comps)
        span = 88.0
        cw = span / n - 3.0
        for i, (name, desc) in enumerate(comps):
            cx = 6.0 + i * (span / n)
            _box(ax, cx, y + 2.4, cw, h - 13.5, face=BONE, edge=edge, lw=1.0)
            _text(ax, cx + cw / 2, y + h - 12.0, name, size=8.6, weight="bold",
                  color=GRAPHITE)
            _text(ax, cx + cw / 2, y + (h - 13.5) / 2 + 0.5, desc, size=7.0,
                  color=GRAPHITE_SOFT)

    # Cross-plane arrows: dispatch down, evidence up.
    _arrow(ax, 24, 68.0, 24, 64.9, color=BLUE_DEEP, lw=1.7)
    _text(ax, 21.5, 66.4, "dispatch", size=7.4, color=BLUE_DEEP, ha="right")
    _arrow(ax, 24, 38.0, 24, 34.9, color=GRAPHITE_SOFT, lw=1.7)
    _text(ax, 21.5, 36.4, "emit", size=7.4, color=GRAPHITE_SOFT, ha="right")

    _arrow(ax, 76, 34.9, 76, 38.0, color=EVIDENCE_LINE, lw=1.7)
    _arrow(ax, 76, 64.9, 76, 68.0, color=EVIDENCE_LINE, lw=1.7)
    _text(ax, 78.5, 66.4, "read-back", size=7.4, color=EVIDENCE_LINE, ha="left")
    _text(ax, 78.5, 36.4, "read-back", size=7.4, color=EVIDENCE_LINE, ha="left")

    _text(ax, 50, 97.4, "Argus three-plane architecture", size=12,
          weight="bold", color=BLUE_DEEP)
    _text(ax, 50, 2.0,
          "Judgment stays in the control and execution planes; the evidence "
          "plane records but never decides.",
          size=7.2, color=GRAPHITE_SOFT, style="italic")

    return _save(fig, "system_planes")


# --------------------------------------------------------------------------- #
# Figure 2: mission lifecycle + recovery edges.
# --------------------------------------------------------------------------- #
def build_mission_lifecycle() -> dict:
    fig, ax = _new_axes(8.2, 6.2)

    # Primary spine (left) + plan-next loop (right). Bottom bands: recovery,
    # then the daemon boundary. Coordinates fixed for determinism.
    nodes = {
        "claim": (26, 90, "Claim backlog item", "pending \u2192 running\n(atomic claim)"),
        "mission": (26, 71, "Run mission", "Engineer \u2194 Reviewer\nbounded session reuse"),
        "verdict": (26, 52, "Reviewer verdict", "sole completion\nauthority"),
        "done": (26, 33, "done", "achievement\ncertified"),
        "plannext": (73, 52, "Plan next work", "Planner \u00b7 task DAG\nor project done"),
        "backlog": (73, 71, "Backlog / continuous", "STANDING 7\u00d724\nor BOUNDED end"),
    }
    colors = {
        "claim": (BLUE_SOFT, BLUE_DEEP),
        "mission": (BLUE_SOFT, BLUE_DEEP),
        "verdict": (BLUE_SOFT, BLUE_DEEP),
        "done": (EVIDENCE_FILL, EVIDENCE_LINE),
        "plannext": (PANEL_FILL, GRAPHITE_SOFT),
        "backlog": (PANEL_FILL, GRAPHITE_SOFT),
    }
    w, h = 30, 12
    for key, (x, y, title, sub) in nodes.items():
        face, edge = colors[key]
        _box(ax, x - w / 2, y - h / 2, w, h, face=face, edge=edge, lw=1.3)
        _text(ax, x, y + 2.4, title, size=9.0, weight="bold", color=GRAPHITE)
        _text(ax, x, y - 2.8, sub, size=7.0, color=GRAPHITE_SOFT)

    # Primary spine edges.
    _arrow(ax, 26, 84, 26, 77, color=BLUE_DEEP, lw=1.7)
    _arrow(ax, 26, 65, 26, 58, color=BLUE_DEEP, lw=1.7)
    _arrow(ax, 26, 46, 26, 39, color=BLUE_DEEP, lw=1.7)
    _text(ax, 27.8, 42.5, "done", size=7.2, color=BLUE_DEEP, ha="left")

    # continue: verdict -> mission (loop back up on the left).
    _arrow(ax, 15, 49, 15, 68, color=BLUE_DEEP, lw=1.4,
           connection="arc3,rad=0.0")
    _arrow(ax, 20, 52.5, 12, 55, color=BLUE_DEEP, lw=1.4)
    _arrow(ax, 12, 67, 20, 70.5, color=BLUE_DEEP, lw=1.4)
    _text(ax, 9.5, 60, "continue\n(+ next step)", size=7.0, color=BLUE_DEEP,
          ha="right")

    # plan-next loop (right side).
    _arrow(ax, 41, 33, 58, 49, color=GRAPHITE_SOFT, lw=1.4,
           connection="arc3,rad=-0.22")
    _text(ax, 52, 38.5, "project done?", size=7.0, color=GRAPHITE_SOFT)
    _arrow(ax, 73, 58, 73, 65, color=GRAPHITE_SOFT, lw=1.5)
    _text(ax, 75, 61.5, "next batch", size=7.0, color=GRAPHITE_SOFT, ha="left")
    _arrow(ax, 58, 71, 41, 71, color=GRAPHITE_SOFT, lw=1.5)
    _text(ax, 49.5, 73.0, "next task", size=7.0, color=GRAPHITE_SOFT)

    # final-submission re-route (premature project_done override).
    _arrow(ax, 65, 49, 41, 49, color=RECOVERY, lw=1.3, ls=(0, (4, 2)),
           connection="arc3,rad=0.30")
    _text(ax, 53, 44.5, "uncertified \u2192 final-submission",
          size=6.7, color=RECOVERY)

    # Recovery / terminal-status band (horizontal, no right-edge clipping).
    _box(ax, 4, 12.0, 92, 12.5, face=BONE, edge=RECOVERY, lw=1.2, radius=2.0)
    _text(ax, 7, 22.0, "RECOVERY \u00b7 terminal / paused statuses", size=8.4,
          weight="bold", color=RECOVERY, ha="left")
    recover_states = [
        "paused_budget", "provider_cooldown", "provider_fence",
        "infra_blocked", "backend_unavailable", "waiting (bg job)",
        "stall \u2192 blocked", "no_progress",
    ]
    for i, s in enumerate(recover_states):
        col = 7.0 + (i % 4) * 23.0
        row = 17.6 - (i // 4) * 4.2
        _text(ax, col, row, "\u2022 " + s, size=7.0, color=GRAPHITE, ha="left")
    _arrow(ax, 20, 46, 14, 25, color=RECOVERY, lw=1.2, ls=(0, (4, 2)),
           connection="arc3,rad=0.15")
    _text(ax, 5.5, 26.2, "pause / park", size=6.6, color=RECOVERY, ha="left")
    _arrow(ax, 82, 25, 82, 46, color=RECOVERY, lw=1.2, ls=(0, (4, 2)),
           connection="arc3,rad=0.15")
    _text(ax, 84, 33, "recheck /\nre-plan", size=6.6, color=RECOVERY, ha="left")

    # Daemon boundary banner (two lines so nothing is clipped).
    _box(ax, 4, 2.0, 92, 7.6, face=PANEL_FILL, edge=GRAPHITE_SOFT, lw=1.0,
         radius=1.6)
    _text(ax, 7, 7.2,
          "Daemon boundary:  SIGTERM \u2192 drain to the mission boundary "
          "(no mid-mission kill)  \u00b7  blue/green handoff on source change",
          size=7.0, color=GRAPHITE_SOFT, ha="left")
    _text(ax, 7, 3.9,
          "Resume adopts only a campaign whose Manager handoff identity "
          "(objective hash + vertical + lineage) matches \u2014 no re-divide.",
          size=7.0, color=GRAPHITE_SOFT, ha="left")

    _text(ax, 50, 97.7, "Mission lifecycle and recovery edges", size=12,
          weight="bold", color=BLUE_DEEP)
    return _save(fig, "mission_lifecycle")


# --------------------------------------------------------------------------- #
# Figure 3: six-arena public results (small multiples).
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Figure 5: master spine -- the expanding-frontier causal chain.
# --------------------------------------------------------------------------- #
def build_master_spine() -> dict:
    fig, ax = _new_axes(10.4, 6.2)
    _text(
        ax, 5, 96, "ARGUS \u00b7 TECHNICAL SPINE",
        size=7.8, color=GOLD, weight="bold", ha="left",
    )
    _text(
        ax, 5, 90, "Every run expands the frontier.",
        size=15, color=BLUE_DEEP, weight="bold", ha="left",
    )

    # Widths are sized per-stage so long titles (e.g. "Expanded OOD Frontier")
    # and the role/runtime-label grids they host both fit without collisions.
    stage_x = (1, 17, 43, 59, 83)
    stage_w = (14, 24, 14, 22, 16)
    box_y, box_h = 38, 32
    # Manual two-line wraps (display only) so the longer subtitles do not
    # spill past their own stage into a neighbour.
    subtitle_wraps = {
        0: "OOD problem or\ndeeper challenge",
        2: "artifacts \u00b7 measurements \u00b7\nfailures \u00b7 verdicts",
        3: "memory \u00b7 skills \u00b7 tools \u00b7 verifiers\n\u00b7 routing \u00b7 evaluations",
        4: "the next unknown task\ndoes not start from zero",
    }
    for index, ((title, subtitle), x, width) in enumerate(
        zip(MASTER_SPINE_STAGES, stage_x, stage_w, strict=True)
    ):
        face = BLUE_SOFT if index in {1, 3} else BONE
        edge = GOLD if index in {2, 4} else BLUE
        _box(ax, x, box_y, width, box_h, face=face, edge=edge, lw=1.3)
        _text(ax, x + width / 2, 67, title, size=8.2, weight="bold")
        rendered_subtitle = subtitle_wraps.get(index, subtitle)
        if index == 1:
            _text(ax, x + width / 2, 61.5, rendered_subtitle, size=6.2,
                  color=GRAPHITE_SOFT)
        else:
            _text(ax, x + width / 2, 40, rendered_subtitle, size=5.6,
                  color=GRAPHITE_SOFT)
        if index < len(MASTER_SPINE_STAGES) - 1:
            # Derive the connector endpoints from the actual gap between this
            # box's right edge and the next box's left edge (rather than a
            # fixed +1/-1 inset) so the arrow always has a visible,
            # non-zero span regardless of how the stage boxes are laid out.
            next_x = stage_x[index + 1]
            gap = next_x - (x + width)
            margin = gap * 0.2
            _arrow(ax, x + width + margin, 55, next_x - margin, 55, color=BLUE)

    role_w, role_h = 7.5, 6.5
    role_positions = ((19.5, 51), (31, 51), (19.5, 42.5), (31, 42.5))
    for (name, detail), (x, y) in zip(
        MASTER_SPINE_ROLES, role_positions, strict=True
    ):
        _box(ax, x, y, role_w, role_h, face=BONE,
             edge=GOLD if name == "Reviewer" else BLUE)
        _text(ax, x + role_w / 2, y + 4.1, name, size=6.0, weight="bold")
        _text(ax, x + role_w / 2, y + 1.7, detail, size=4.8,
              color=GRAPHITE_SOFT)

    runtime_labels = ("Memory", "Skills", "Tools", "Verifiers", "Routing", "Evaluations")
    for index, label in enumerate(runtime_labels):
        row, col = divmod(index, 2)
        _text(ax, 65 + col * 10, 61 - row * 7, label, size=5.8, color=BLUE_DEEP)

    _text(
        ax, 50, 27,
        "H(t+1) = U(H(t), trajectory, evidence)",
        size=9.0, color=BLUE_DEEP, weight="bold",
    )
    _text(
        ax, 50, 20, "model parameters remain fixed",
        size=7.2, color=GRAPHITE_SOFT,
    )
    _text(
        ax, 50, 13,
        "capability is not guaranteed to grow every run",
        size=6.4, color=GRAPHITE_SOFT, style="italic",
    )
    _arrow(ax, 97, 33, 3, 33, color=GOLD, lw=1.5, connection="arc3,rad=-0.22")
    return _save(fig, "master_spine")


# --------------------------------------------------------------------------- #
# Figure 6: dense intelligence -- explanatory continuity schematic.
# --------------------------------------------------------------------------- #
def build_dense_intelligence() -> dict:
    fig, ax = _new_axes(9.4, 4.8)
    _text(ax, 5, 94, "DENSE INTELLIGENCE", size=7.8, color=GOLD, weight="bold", ha="left")
    _text(
        ax, 5, 87,
        "Continuity is useful only when decision, execution, and verification remain coupled.",
        size=11.5, color=BLUE_DEEP, weight="bold", ha="left",
    )

    labels = ("decision", "execution", "verification", "state retention")
    for row, (title, subtitle) in enumerate(
        (
            ("Episodic research", "useful work separated by context recovery"),
            ("Argus Life", "continuous role loop over persisted project state"),
        )
    ):
        y = 61 - row * 27
        _text(ax, 6, y + 10, title, size=8.0, weight="bold", ha="left")
        _text(ax, 6, y + 5, subtitle, size=6.3, color=GRAPHITE_SOFT, ha="left")
        for index, label in enumerate(labels):
            x = 36 + index * 15
            active = row == 1 or index in {0, 2}
            _box(
                ax, x, y, 12, 9,
                face=BLUE_SOFT if active else PANEL_FILL,
                edge=BLUE if active else PANEL_LINE,
            )
            _text(ax, x + 6, y + 4.5, label, size=5.8)
            if index < len(labels) - 1:
                _arrow(ax, x + 12.5, y + 4.5, x + 14.5, y + 4.5, color=GOLD if row == 1 else PANEL_LINE)

    _text(
        ax, 50, 5,
        "conceptual model \u00b7 not a reported benchmark",
        size=6.5, color=GRAPHITE_SOFT, style="italic",
    )
    return _save(fig, "dense_intelligence")


def main() -> None:
    figures = {
        "master_spine": build_master_spine(),
        "dense_intelligence": build_dense_intelligence(),
        "system_planes": build_system_planes(),
        "mission_lifecycle": build_mission_lifecycle(),
        "public_results": build_public_results(),
        "paper_portfolio": build_paper_portfolio(),
    }
    metadata = {
        "schema": "argus-report-figures/v1",
        "description": (
            "Deterministic, source-controlled report figures generated by "
            "build_report_figures.py from committed source-grounded "
            "specifications and evidence bundles. "
            "No image-model call is used; digests are reproducible across runs."
        ),
        "palette": {
            "bone_white": BONE,
            "graphite": GRAPHITE,
            "system_blue": BLUE,
            "deep_blue": BLUE_DEEP,
            "frontier_gold": GOLD,
        },
        "source_evidence": [
            "argus_skill/core/event_catalog.py",
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
