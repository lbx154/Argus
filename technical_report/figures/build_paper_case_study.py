#!/usr/bin/env python3
"""Build editable HTML sources for the autonomous paper-production case study."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPORT = HERE.parent
DATA = REPORT / "evidence" / "paper_case_study"
SUMMARY_PATH = DATA / "paper_trajectory_summary.json"
TRANSITIONS_PATH = DATA / "stage_transitions.csv"
FINDINGS_PATH = DATA / "paper_scientific_findings.json"
TRACE_PATH = DATA / "mm_hallucination_trace.json"

OVERVIEW_HTML = HERE / "paper_case_study.html"
TRAJECTORY_HTML = HERE / "paper_case_trajectory.html"
MACROS_PATH = HERE / "paper_case_study_metrics.tex"
OVERVIEW_PROVENANCE = HERE / "paper_case_study.provenance.json"
TRAJECTORY_PROVENANCE = HERE / "paper_case_trajectory.provenance.json"
THUMBNAILS = {
    "bench-fragile-leaderboard": "assets/paper_thumbnails/bench-fragile.png",
    "cv-compositional-match": "assets/paper_thumbnails/cv-compositional.png",
    "cv-frontier": "assets/paper_thumbnails/cv-frontier.png",
    "mm-gui-agent": "assets/paper_thumbnails/mm-gui.png",
    "mm-hallucination": "assets/paper_thumbnails/mm-hallucination.png",
    "quant-vocab-matrix": "assets/paper_thumbnails/quant-vocab.png",
}

STAGES = [
    "research",
    "plan",
    "benchmark",
    "run",
    "analysis",
    "draft",
    "review",
    "submission",
]
STAGE_COLORS = {
    "research": "#DCE7FA",
    "plan": "#BFD3F5",
    "benchmark": "#91B1E5",
    "run": "#648BCF",
    "analysis": "#8DCFC2",
    "draft": "#E4D39D",
    "review": "#E8AD76",
    "submission": "#C38A20",
}


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def load_data() -> tuple[dict, list[dict[str, str]], dict, dict]:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    findings = json.loads(FINDINGS_PATH.read_text(encoding="utf-8"))
    trace = json.loads(TRACE_PATH.read_text(encoding="utf-8"))
    with TRANSITIONS_PATH.open(encoding="utf-8", newline="") as handle:
        transitions = list(csv.DictReader(handle))
    return summary, transitions, findings, trace


def write_macros(aggregate: dict) -> None:
    values = {
        "PaperCasePapers": aggregate["papers"],
        "PaperCaseCompleted": aggregate["pipeline_complete"],
        "PaperCaseCampaignHours": round(aggregate["aggregate_campaign_hours"]),
        "PaperCaseMissions": aggregate["missions"],
        "PaperCaseRounds": aggregate["engineer_rounds"],
        "PaperCaseContinues": aggregate["review_continue"],
        "PaperCaseSessionRolls": aggregate["session_rolls"],
        "PaperCaseRollbacks": aggregate["stage_rollbacks"],
        "PaperCaseReviewSnapshots": aggregate["review_snapshots"],
        "PaperCaseAssurancePass": aggregate["submission_assurance_pass"],
    }
    MACROS_PATH.write_text(
        "".join(f"\\newcommand{{\\{name}}}{{{value}}}\n" for name, value in values.items()),
        encoding="utf-8",
    )


def icon_svg(kind: str, color: str) -> str:
    common = f'stroke="{color}" stroke-width="2" fill="none" stroke-linecap="round" stroke-linejoin="round"'
    shapes = {
        "audit": f'<path {common} d="M5 21V12M12 21V7M19 21V3"/><path {common} d="M3 21h20M4 9l6-4 6 2 5-5"/>',
        "composition": f'<rect {common} x="3" y="4" width="7" height="7" rx="1.5"/><rect {common} x="14" y="13" width="7" height="7" rx="1.5"/><path {common} d="M9 10l6 4M14 6h6v6M4 14v6h6"/>',
        "gate": f'<path {common} d="M4 21V4h12v17M16 8h4v9h-4M8 8h4M8 12h4M8 16h4"/><path {common} d="M1 12h3M20 12h3"/>',
        "cursor": f'<path {common} d="M5 3l13 11-6 1 3 6-3 1-3-6-4 4z"/><circle {common} cx="19" cy="6" r="3"/>',
        "eye": f'<path {common} d="M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z"/><circle {common} cx="12" cy="12" r="2.5"/><path {common} d="M4 4l16 16"/>',
        "matrix": f'<rect {common} x="3" y="3" width="18" height="18" rx="2"/><path {common} d="M9 3v18M15 3v18M3 9h18M3 15h18"/><circle cx="12" cy="12" r="2.2" fill="{color}"/>',
    }
    return f'<svg viewBox="0 0 24 24" aria-hidden="true">{shapes[kind]}</svg>'


def paper_card(paper: dict, finding: dict) -> str:
    return f"""
      <article class="paper-card" style="--accent:{esc(finding['accent'])}">
        <div class="paper-page"><img src="{THUMBNAILS[paper['project']]}" alt="First page of {esc(paper['title'])}"></div>
        <div class="paper-copy">
          <div class="paper-top"><span>{esc(paper['domain'])}</span></div>
          <h3>{esc(finding['display_title'])}</h3>
          <strong>{esc(finding['headline'])}</strong>
        </div>
      </article>
    """


def role_loop() -> str:
    return """
      <div class="loop-shell">
        <svg class="loop-arrows" viewBox="0 0 360 390" aria-hidden="true">
          <path d="M103 59 C164 18 254 26 304 72"/>
          <path d="M320 105 C350 176 325 280 268 318"/>
          <path d="M232 340 C158 365 74 326 48 266"/>
          <path d="M40 232 C14 151 39 88 92 58"/>
        </svg>
        <div class="role manager"><img src="assets/anime/manager.png"><strong>Manager</strong><span>govern</span></div>
        <div class="role planner"><img src="assets/anime/planner.png"><strong>Planner</strong><span>plan</span></div>
        <div class="role engineer"><img src="assets/anime/engineer.png"><strong>Engineer</strong><span>build</span></div>
        <div class="role reviewer"><img src="assets/anime/reviewer.png"><strong>Reviewer</strong><span>review</span></div>
        <div class="persistent-state">
          <small>Persistent state</small>
          <strong>One campaign</strong>
          <p>objective · evidence</p>
          <p>wiki · manuscript</p>
        </div>
      </div>
    """


def overview_html(summary: dict, findings: dict) -> str:
    papers = summary["papers"]
    aggregate = summary["aggregate"]
    finding_map = findings["papers"]
    cards = "".join(paper_card(paper, finding_map[paper["project"]]) for paper in papers)
    metrics = [
        (f"{aggregate['pipeline_complete']}/{aggregate['papers']}", "papers completed", "#173B70"),
        (f"{round(aggregate['aggregate_campaign_hours']):,}", "campaign-hours", "#315BCE"),
        (f"{aggregate['engineer_rounds']:,}", "Engineer rounds", "#C38A20"),
        (f"{aggregate['review_continue']:,}", "Reviewer revisions", "#287D70"),
        (f"{aggregate['session_rolls']:,}", "session rolls", "#7766A6"),
        (f"{aggregate['stage_rollbacks']:,}", "Stage rollbacks", "#B43F55"),
    ]
    metric_html = "".join(
        f'<div class="metric" style="--c:{color}"><b>{value}</b><span>{label}</span></div>'
        for value, label, color in metrics
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Argus autonomous paper portfolio</title>
<style>
@page {{ size: 12in 5.75in; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; width:12in; height:5.75in; }}
body {{ font-family:Arial,Helvetica,sans-serif; color:#24465D; background:#FBF7EE; print-color-adjust:exact; -webkit-print-color-adjust:exact; }}
.canvas {{ position:relative; width:12in; height:5.75in; padding:15px 20px 52px; display:grid; grid-template-rows:38px minmax(0,1fr) 58px; gap:8px; border:2px solid #24465D; overflow:hidden; background:#FBF7EE; }}
.mountain-strip {{ position:absolute; left:0; bottom:0; width:100%; height:54px; object-fit:cover; object-position:center bottom; opacity:.92; z-index:0; }}
header,.paper-grid,.metrics {{ position:relative; z-index:2; }}
header {{ display:flex; align-items:center; justify-content:space-between; }}
header strong {{ color:#173B70; font-size:17px; }}
header span {{ color:#667482; font-size:10.5px; font-weight:700; }}
.paper-grid {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:8px; min-height:0; }}
.paper-card {{ border:1px solid #24465D; border-top:5px solid var(--accent); border-radius:10px; background:#FFFDF8; padding:7px; display:grid; grid-template-rows:218px minmax(0,1fr); gap:6px; min-width:0; overflow:hidden; }}
.paper-page {{ border:1px solid #D8E0E8; border-radius:5px; background:#EEF2F5; overflow:hidden; display:grid; place-items:center; }}
.paper-page img {{ width:100%; height:100%; object-fit:contain; object-position:center top; }}
.paper-copy {{ min-width:0; }}
.paper-top {{ color:var(--accent); font-size:9.3px; font-weight:800; line-height:1.05; }}
.paper-card h3 {{ margin:4px 0 5px; color:#1E2732; font-size:11.5px; line-height:1.08; }}
.paper-card > .paper-copy > strong {{ display:block; color:var(--accent); font-size:13.5px; line-height:1.08; margin:0; }}
.metrics {{ display:grid; grid-template-columns:.82fr repeat(6,1fr); gap:7px; }}
.metric-label {{ border:1px solid #24465D; border-radius:8px; background:#F5E6C8; padding:8px 9px; }}
.metric-label b {{ display:block; color:#173B70; font-size:12px; }} .metric-label span {{ display:block; color:#667482; font-size:9px; margin-top:4px; font-weight:700; }}
.metric {{ position:relative; border:1px solid #24465D; border-radius:8px; background:rgba(255,253,248,.96); padding:7px 7px 6px 11px; }}
.metric::before {{ content:""; position:absolute; left:0; top:0; bottom:0; width:5px; border-radius:10px 0 0 10px; background:var(--c); }}
.metric b {{ display:block; color:var(--c); font-size:16px; line-height:1; margin-bottom:3px; }}
.metric span {{ color:#5C6B79; font-size:9.4px; font-weight:700; line-height:1.06; }}
</style></head><body><main class="canvas"><img class="mountain-strip" src="assets/anime/mountain_strip.png" alt="">
<header><strong>Six completed manuscripts · real PDF first pages</strong><span>Scientific result on each card · Argus production process below</span></header>
<section class="paper-grid">{cards}</section>
<footer class="metrics"><div class="metric-label"><b>ARGUS</b><span>production process</span></div>{metric_html}</footer>
</main></body></html>"""


def stage_plot_svg(transitions: list[dict[str, str]], trace: dict) -> str:
    rows = sorted(
        (row for row in transitions if row["project"] == trace["project"]),
        key=lambda row: int(row["sequence"]),
    )
    width, height = 1100, 300
    left, right, top, bottom = 92, 24, 20, 42
    plot_w, plot_h = width - left - right, height - top - bottom
    total = float(trace["campaign_hours"])

    def x(hours: float) -> float:
        return left + hours / total * plot_w

    def y(stage: str) -> float:
        index = STAGES.index(stage)
        return top + (len(STAGES) - 1 - index) / (len(STAGES) - 1) * plot_h

    pieces: list[str] = []
    windows = trace["windows_hours"]
    bands = [
        (windows["no_go_start"], windows["negative_scope_locked"], "#FCEBED", "no-go ×7", 14),
        (windows["negative_scope_locked"], windows["first_submission_stage"], "#EAF2FF", "pivot", 31),
        (windows["submission_repair_start"], windows["final_completion"], "#FFF2DA", "repair ×2", 14),
    ]
    for start, end, color, label, label_y in bands:
        bx = x(float(start))
        bw = x(float(end)) - bx
        pieces.append(f'<rect x="{bx:.1f}" y="{top}" width="{bw:.1f}" height="{plot_h}" rx="6" fill="{color}"/>')
        pieces.append(f'<text x="{bx + bw / 2:.1f}" y="{top + label_y}" text-anchor="middle" class="band-label">{esc(label)}</text>')

    for stage in STAGES:
        sy = y(stage)
        pieces.append(f'<line x1="{left}" y1="{sy:.1f}" x2="{width-right}" y2="{sy:.1f}" class="grid"/>')
        pieces.append(f'<text x="{left-10}" y="{sy+4:.1f}" text-anchor="end" class="stage-label">{stage.title()}</text>')

    for tick in (0, 40, 80, 120, 160):
        tx = x(float(tick))
        pieces.append(f'<line x1="{tx:.1f}" y1="{top}" x2="{tx:.1f}" y2="{height-bottom+5}" class="tick"/>')
        pieces.append(f'<text x="{tx:.1f}" y="{height-11}" text-anchor="middle" class="tick-label">{tick} h</text>')

    current_stage = rows[0]["from_stage"] if rows else "research"
    current_x = x(0.0)
    rollback_points: list[tuple[float, float]] = []
    for row in rows:
        event_x = x(float(row["elapsed_hours"]))
        current_y = y(current_stage)
        pieces.append(
            f'<line x1="{current_x:.1f}" y1="{current_y:.1f}" x2="{event_x:.1f}" y2="{current_y:.1f}" '
            f'stroke="{STAGE_COLORS[current_stage]}" stroke-width="8" stroke-linecap="round"/>'
        )
        direction = row["direction"]
        next_stage = row["to_stage"]
        if direction in {"advance", "rollback"} and next_stage != current_stage:
            next_y = y(next_stage)
            color = "#B43F55" if direction == "rollback" else "#315BCE"
            pieces.append(f'<line x1="{event_x:.1f}" y1="{current_y:.1f}" x2="{event_x:.1f}" y2="{next_y:.1f}" stroke="{color}" stroke-width="3"/>')
            if direction == "rollback":
                rollback_points.append((event_x, next_y))
            current_stage = next_stage
        current_x = event_x

    final_y = y(current_stage)
    pieces.append(f'<circle cx="{x(total):.1f}" cy="{final_y:.1f}" r="8" fill="#C38A20" stroke="white" stroke-width="3"/>')
    for px, py in rollback_points:
        pieces.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5.5" fill="#B43F55" stroke="white" stroke-width="2"/>')

    return f"""<svg class="stage-plot" viewBox="0 0 {width} {height}" role="img" aria-label="Manager-controlled Stage trajectory over 163.6 campaign-hours">
      <style>.grid{{stroke:#E1E7EC;stroke-width:1}}.tick{{stroke:#D8E0E7;stroke-width:1;stroke-dasharray:3 5}}.stage-label{{font:700 13px Arial;fill:#536272}}.tick-label{{font:12px Arial;fill:#71808E}}.band-label{{font:700 11px Arial;fill:#66717D;letter-spacing:.02em}}</style>
      {''.join(pieces)}
    </svg>"""


def role_badges(*roles: str) -> str:
    names = {"M": "manager", "P": "planner", "E": "engineer", "R": "reviewer"}
    return "".join(f'<img src="assets/anime/{names[role]}.png" alt="{role}">' for role in roles)


def trajectory_html(transitions: list[dict[str, str]], trace: dict) -> str:
    no_go_chips = "".join(f"<span>{esc(name)}</span>" for name in trace["no_go_branches"])
    repairs = "".join(f"<li>{esc(name)}</li>" for name in trace["submission_repairs"])
    plot = stage_plot_svg(transitions, trace)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Argus representative autonomous paper trajectory</title>
<style>
@page {{ size:12in 5.2in; margin:0; }}
* {{ box-sizing:border-box; }} html,body {{ margin:0; width:12in; height:5.2in; }}
body {{ font-family:Arial,Helvetica,sans-serif; color:#24465D; background:#FBF7EE; print-color-adjust:exact; -webkit-print-color-adjust:exact; }}
.canvas {{ position:relative; width:12in; height:5.2in; padding:14px 20px 48px; border:2px solid #24465D; display:grid; grid-template-rows:252px minmax(0,1fr); gap:8px; overflow:hidden; background:#FBF7EE; }}
.mountain-strip {{ position:absolute; left:0; bottom:0; width:100%; height:50px; object-fit:cover; object-position:center bottom; opacity:.92; z-index:0; }}
.plot-panel,.episodes-wrap {{ position:relative; z-index:2; }}
.plot-panel {{ border:1.5px solid #24465D; border-radius:11px; background:rgba(255,253,248,.96); padding:7px 10px 4px; display:grid; grid-template-rows:52px 1fr; }}
.plot-top {{ display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }}
.plot-head strong {{ display:block; color:#173B70; font-size:13px; margin-top:4px; }} .plot-head span {{ display:block; color:#71808E; font-size:9.5px; margin-top:5px; }}
.summary {{ display:grid; grid-template-columns:repeat(6,1fr); gap:4px; width:650px; }}
.summary div {{ border:1px solid #24465D; background:#FFFDF8; border-radius:7px; padding:5px 6px; text-align:center; }}
.summary b {{ display:block; color:#173B70; font-size:14px; line-height:1; }}
.summary span {{ color:#677482; font-size:8.5px; font-weight:700; }}
.stage-plot {{ width:100%; height:185px; display:block; }}
.episodes-wrap {{ display:grid; grid-template-rows:18px minmax(0,1fr); gap:4px; min-height:0; overflow:hidden; }}
.episodes-title {{ display:flex; align-items:baseline; justify-content:space-between; padding:0 2px; }}
.episodes-title strong {{ color:#173B70; font-size:12.5px; }} .episodes-title span {{ color:#71808E; font-size:9.5px; }}
.episodes {{ display:grid; grid-template-columns:1.35fr .88fr 1fr 1fr .92fr; gap:6px; min-height:0; overflow:hidden; }}
.episode {{ border:1px solid #24465D; border-top:4px solid var(--c); border-radius:10px; background:#FFFDF8; padding:7px 8px; position:relative; overflow:hidden; }}
.episode::before {{ content:attr(data-step); position:absolute; right:8px; bottom:-9px; color:var(--c); font-size:46px; font-weight:900; opacity:.10; }}
.episode:not(:last-child)::after {{ content:"→"; position:absolute; right:-9px; top:46%; z-index:4; color:#8B9BAA; font-size:17px; font-weight:900; }}
.episode-head {{ display:flex; align-items:center; justify-content:space-between; gap:6px; margin-bottom:4px; }}
.episode-head small {{ color:var(--c); font-size:9.5px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }}
.badges {{ display:flex; gap:2px; }} .badges img {{ width:22px; height:27px; object-fit:contain; margin-top:-4px; }}
.episode h3 {{ margin:0 0 4px; font-size:12.5px; line-height:1.07; color:#1E2732; }}
.episode p {{ margin:0; color:#4D5A67; font-size:10.8px; line-height:1.19; }}
.episode strong {{ color:var(--c); }}
.chips {{ display:flex; flex-wrap:wrap; gap:3px; margin-top:5px; }} .chips span {{ border:1px solid #E4B9C1; background:#FFF5F6; border-radius:999px; padding:2px 4px; color:#8B3E4C; font-size:8.3px; font-weight:700; }}
.pivot {{ display:grid; place-items:center; text-align:center; min-height:42px; border:1px solid #C8D7F4; background:#F0F5FF; border-radius:7px; margin:4px 0; padding:4px; color:#315BCE; font-size:11.5px; font-weight:800; }}
.pivot i {{ display:block; color:#8A98A7; font-style:normal; font-size:18px; line-height:.7; }}
.matrix {{ display:grid; grid-template-columns:repeat(3,1fr); gap:3px; margin:6px 0 5px; }} .matrix span {{ background:#EDF4FF; border:1px solid #C9D8F6; border-radius:4px; padding:3px 2px; text-align:center; color:#315BCE; font-size:9px; font-weight:800; }}
.episode ul {{ margin:5px 0 5px 14px; padding:0; color:#4D5A67; font-size:9.7px; line-height:1.16; }}
.paper-output {{ margin-top:4px; border:1px solid #E2C77E; background:#FFF8E7; border-radius:7px; padding:4px; text-align:center; }} .paper-output b {{ color:#8B6515; font-size:18px; }} .paper-output span {{ display:block; color:#6E5A2D; font-size:9.5px; font-weight:700; }}
</style></head><body><main class="canvas"><img class="mountain-strip" src="assets/anime/mountain_strip.png" alt="">
<section class="plot-panel"><div class="plot-top"><div class="plot-head"><strong>Stage trajectory</strong><span>red = rollback · gold = completion</span></div><div class="summary"><div><b>{trace['campaign_hours']:.1f} h</b><span>campaign</span></div><div><b>{trace['engineer_rounds']}</b><span>rounds</span></div><div><b>{trace['reviewer_revisions']}</b><span>revisions</span></div><div><b>{trace['session_rolls']}</b><span>sessions</span></div><div><b>{trace['early_no_go_rollbacks']}</b><span>early rollback</span></div><div><b>{trace['submission_rollbacks']}</b><span>late rollback</span></div></div></div>{plot}</section>
<section class="episodes-wrap"><div class="episodes-title"><strong>(b) Scientific episodes</strong></div><div class="episodes">
  <article class="episode" data-step="01" style="--c:#B43F55"><div class="episode-head"><small>Prune</small><div class="badges">{role_badges('R','M','P')}</div></div><h3><strong>7 no-go gates</strong></h3><div class="pivot">reject weak routes</div></article>
  <article class="episode" data-step="02" style="--c:#315BCE"><div class="episode-head"><small>Pivot</small><div class="badges">{role_badges('P','M')}</div></div><h3>Method → audit</h3><div class="pivot">retain evidence</div></article>
  <article class="episode" data-step="03" style="--c:#C38A20"><div class="episode-head"><small>Experiment</small><div class="badges">{role_badges('E','R')}</div></div><h3><strong>{trace['canonical_cells']} cells</strong></h3><div class="pivot">{trace['canonical_scored_rows']:,} rows</div></article>
  <article class="episode" data-step="04" style="--c:#287D70"><div class="episode-head"><small>Write</small><div class="badges">{role_badges('E','R','M')}</div></div><h3>Analyze → review</h3><div class="paper-output"><b>{trace['final_pages']} pages</b></div></article>
  <article class="episode" data-step="05" style="--c:#B43F55"><div class="episode-head"><small>Repair</small><div class="badges">{role_badges('R','M','E')}</div></div><h3><strong>2 late rollbacks</strong></h3><div class="pivot">rebind evidence</div></article>
</div></section>
</main></body></html>"""


def write_provenance() -> None:
    overview = {
        "figure_id": "autonomous-paper-portfolio",
        "reader_question": "What scientific work did Argus produce, and how does the recurrent runtime connect to those outputs?",
        "claim": "Six canonical pipelines completed across six domains through repeated role handoffs, review, rollback, and session continuation.",
        "evidence": [SUMMARY_PATH.name, FINDINGS_PATH.name],
        "encoding": "Six real first-page PDF thumbnails are paired with task-native scientific headlines; a separate bottom strip reports Argus production-process counts.",
        "scope": "Observed Argus campaigns; paper-format completion is not venue acceptance or a human-quality comparison.",
        "target_size": "12 x 5.75 inch source canvas; approximately 3.2 inches high at manuscript width",
        "visual_style": "shared Argus anime-editorial palette, cream paper, mountain-ridge motif, reusable role characters, and fixed role colors",
        "character_assets": ["assets/anime/manager.svg", "assets/anime/planner.svg", "assets/anime/engineer.svg", "assets/anime/reviewer.svg"],
        "visual_assets": list(THUMBNAILS.values()),
        "editable_source": [Path(__file__).name, OVERVIEW_HTML.name],
        "export": ["paper_case_study.pdf", "paper_case_study.svg", "paper_case_study.png"],
    }
    trajectory = {
        "figure_id": "representative-paper-trajectory",
        "reader_question": "How does one Argus campaign recover from failed hypotheses and late submission defects?",
        "claim": "The representative campaign uses seven early no-go rollbacks, a scientific scope pivot, and two late submission repairs to reach a completed manuscript.",
        "evidence": [TRANSITIONS_PATH.name, TRACE_PATH.name],
        "encoding": "An actual Stage-versus-time trace carries the main visual, with six campaign statistics above it and five role-resolved episodes below.",
        "scope": "One 163.6-hour multimodal-hallucination campaign; role labels summarize structured events rather than private model reasoning.",
        "target_size": "12 x 5.2 inch source canvas; approximately 2.9 inches high at manuscript width",
        "visual_style": "shared Argus anime-editorial palette, cream paper, mountain-ridge motif, reusable role characters, and rollback red reserved for failure transitions",
        "character_assets": ["assets/anime/manager.svg", "assets/anime/planner.svg", "assets/anime/engineer.svg", "assets/anime/reviewer.svg"],
        "editable_source": [Path(__file__).name, TRAJECTORY_HTML.name],
        "export": ["paper_case_trajectory.pdf", "paper_case_trajectory.svg", "paper_case_trajectory.png"],
    }
    OVERVIEW_PROVENANCE.write_text(json.dumps(overview, indent=2) + "\n", encoding="utf-8")
    TRAJECTORY_PROVENANCE.write_text(json.dumps(trajectory, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    summary, transitions, findings, trace = load_data()
    write_macros(summary["aggregate"])
    OVERVIEW_HTML.write_text(overview_html(summary, findings), encoding="utf-8")
    TRAJECTORY_HTML.write_text(trajectory_html(transitions, trace), encoding="utf-8")
    write_provenance()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
