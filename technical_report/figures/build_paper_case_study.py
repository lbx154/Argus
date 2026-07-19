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
    venue = str(paper["venue_format"]).split("/")[0]
    return f"""
      <article class="paper-card" style="--accent:{esc(finding['accent'])}">
        <div class="paper-icon">{icon_svg(str(finding['icon']), str(finding['accent']))}</div>
        <div class="paper-copy">
          <div class="paper-top"><span>{esc(paper['domain'])}</span><i>{esc(venue)} · {paper['final_pages']}p</i></div>
          <h3>{esc(finding['display_title'])}</h3>
          <strong>{esc(finding['headline'])}</strong>
          <p class="finding">{esc(finding['finding'])}</p>
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
        <div class="role manager"><b>M</b><strong>Manager</strong><span>route · advance · rollback</span></div>
        <div class="role planner"><b>P</b><strong>Planner</strong><span>bounded mission · success contract</span></div>
        <div class="role engineer"><b>E</b><strong>Engineer</strong><span>experiment · code · manuscript</span></div>
        <div class="role reviewer"><b>R</b><strong>Reviewer</strong><span>done · continue · blocked</span></div>
        <div class="persistent-state">
          <small>Persistent research state</small>
          <strong>One campaign, many sessions</strong>
          <div><span>Objective</span><span>Evidence</span><span>Failures</span><span>Skills / Wiki</span><span>Manuscript</span></div>
        </div>
        <div class="loop-note">Reviewer-admitted artifacts become the next Planner input ↺</div>
      </div>
    """


def overview_html(summary: dict, findings: dict) -> str:
    papers = summary["papers"]
    aggregate = summary["aggregate"]
    finding_map = findings["papers"]
    cards = "".join(paper_card(paper, finding_map[paper["project"]]) for paper in papers)
    metrics = [
        (f"{aggregate['pipeline_complete']}/{aggregate['papers']}", "canonical pipelines completed", "#173B70"),
        (f"{round(aggregate['aggregate_campaign_hours']):,}", "aggregate campaign-hours", "#315BCE"),
        (f"{aggregate['engineer_rounds']:,}", "Engineer rounds", "#C38A20"),
        (f"{aggregate['review_continue']:,}", "Reviewer revisions", "#287D70"),
        (f"{aggregate['session_rolls']:,}", "session rolls", "#7766A6"),
        (f"{aggregate['stage_rollbacks']:,}", "Manager Stage rollbacks", "#B43F55"),
    ]
    metric_html = "".join(
        f'<div class="metric" style="--c:{color}"><b>{value}</b><span>{label}</span></div>'
        for value, label, color in metrics
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Argus autonomous paper portfolio</title>
<style>
@page {{ size: 12in 7.15in; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin:0; width:12in; height:7.15in; }}
body {{ font-family:Arial,Helvetica,sans-serif; color:#202932; background:#FFFFFF; print-color-adjust:exact; -webkit-print-color-adjust:exact; }}
.canvas {{ width:12in; height:7.15in; padding:20px 24px 18px; display:grid; grid-template-rows:78px minmax(0,1fr) 66px; gap:10px; border-top:5px solid #315BCE; overflow:hidden; }}
header {{ display:flex; align-items:flex-start; justify-content:space-between; }}
.kicker {{ color:#315BCE; font-size:12px; font-weight:800; letter-spacing:.13em; text-transform:uppercase; margin-bottom:3px; }}
h1 {{ margin:0; font-size:25px; line-height:1.04; letter-spacing:-.022em; color:#1E2732; }}
header p {{ margin:5px 0 0; color:#607080; font-size:14px; }}
.header-tag {{ margin-top:2px; border:1px solid #D6DEE6; border-radius:999px; padding:6px 10px; color:#526273; background:#F8FAFC; font-size:12px; font-weight:700; }}
.body {{ display:grid; grid-template-columns:330px 1fr; gap:10px; min-height:0; }}
.panel {{ border:1px solid #D5DEE6; border-radius:11px; background:#F8FAFC; padding:10px; min-height:0; }}
.panel-head {{ display:flex; align-items:center; justify-content:space-between; margin-bottom:5px; }}
.panel-head strong {{ font-size:15px; color:#173B70; }}
.panel-head span {{ color:#71808E; font-size:11px; }}
.mechanism {{ position:relative; overflow:hidden; }}
.loop-shell {{ height:386px; position:relative; margin-top:0; }}
.loop-arrows {{ position:absolute; left:0; top:0; width:100%; height:350px; }}
.loop-arrows path {{ fill:none; stroke:#8EA2B7; stroke-width:2.2; stroke-dasharray:5 5; }}
.role {{ position:absolute; width:126px; min-height:68px; border:1px solid #D6DEE6; border-top:4px solid var(--c); border-radius:9px; background:#fff; padding:8px 7px 6px 36px; }}
.role b {{ position:absolute; left:8px; top:9px; width:21px; height:21px; border-radius:6px; display:grid; place-items:center; background:var(--c); color:white; font-size:11px; }}
.role strong {{ display:block; color:var(--c); font-size:14px; margin-bottom:2px; }}
.role span {{ display:block; color:#5E6B78; font-size:10.5px; line-height:1.18; }}
.manager {{ --c:#B43F55; left:0; top:10px; }} .planner {{ --c:#315BCE; right:0; top:22px; }}
.engineer {{ --c:#C38A20; right:0; top:262px; }} .reviewer {{ --c:#287D70; left:0; top:250px; }}
.persistent-state {{ position:absolute; left:78px; top:88px; width:150px; height:150px; border:2px solid #315BCE; border-radius:14px; background:#EDF3FF; padding:9px 8px; text-align:center; overflow:hidden; }}
.persistent-state small {{ color:#315BCE; font-size:10px; font-weight:800; text-transform:uppercase; letter-spacing:.05em; }}
.persistent-state strong {{ display:block; margin:4px 0 7px; color:#173B70; font-size:14px; }}
.persistent-state div {{ display:flex; flex-wrap:wrap; justify-content:center; gap:4px; }}
.persistent-state span {{ border:1px solid #C9D8F6; background:white; border-radius:999px; padding:3px 5px; color:#4E6280; font-size:9.5px; font-weight:700; }}
.loop-note {{ position:absolute; left:6px; right:6px; bottom:0; border:1px dashed #9AAABB; background:white; border-radius:7px; padding:7px; text-align:center; color:#45586A; font-size:10.5px; font-weight:700; }}
.portfolio {{ display:grid; grid-template-rows:auto minmax(0,1fr); overflow:hidden; }}
.paper-grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); grid-template-rows:repeat(2,minmax(0,1fr)); gap:7px; min-height:0; overflow:hidden; }}
.paper-card {{ border:1px solid #D6DEE6; border-left:5px solid var(--accent); border-radius:9px; background:#fff; padding:8px; display:grid; grid-template-columns:32px 1fr; gap:7px; min-height:0; overflow:hidden; }}
.paper-icon {{ width:29px; height:29px; border-radius:8px; background:#F4F7FA; display:grid; place-items:center; }}
.paper-icon svg {{ width:20px; height:20px; }}
.paper-copy {{ min-width:0; }}
.paper-top {{ display:flex; justify-content:space-between; gap:5px; color:#6A7783; font-size:10.5px; font-weight:700; }}
.paper-top span {{ color:var(--accent); }} .paper-top i {{ font-style:normal; white-space:nowrap; }}
.paper-card h3 {{ margin:3px 0 4px; color:#1E2732; font-size:14.5px; line-height:1.08; }}
.paper-card > .paper-copy > strong {{ display:block; color:var(--accent); font-size:16.5px; line-height:1.06; margin:5px 0 4px; }}
.finding {{ margin:0; color:#3F4D5A; font-size:11.5px; line-height:1.19; }}
.metrics {{ display:grid; grid-template-columns:repeat(6,1fr); gap:8px; }}
.metric {{ position:relative; border:1px solid #D5DEE6; border-radius:8px; background:#F8FAFC; padding:7px 8px 6px 12px; }}
.metric::before {{ content:""; position:absolute; left:0; top:0; bottom:0; width:5px; border-radius:10px 0 0 10px; background:var(--c); }}
.metric b {{ display:block; color:var(--c); font-size:20px; line-height:1; margin-bottom:3px; }}
.metric span {{ color:#5C6B79; font-size:10.5px; font-weight:700; line-height:1.06; }}
</style></head><body><main class="canvas">
<header><div><div class="kicker">Autonomous research production · six observed campaigns</div><h1>One persistent runtime produces six scientific manuscripts</h1><p>Repeated planning, execution, review, rollback, and session continuation connect each research question to a measured result.</p></div><div class="header-tag">2 AAAI-formatted · 4 ACL-formatted</div></header>
<section class="body">
  <article class="panel mechanism"><div class="panel-head"><strong>(a) Recurrent role loop</strong><span>schematic</span></div>{role_loop()}</article>
  <article class="panel portfolio"><div class="panel-head"><strong>(b) Six scientific outputs</strong><span>task-native findings</span></div><div class="paper-grid">{cards}</div></article>
</section>
<footer class="metrics">{metric_html}</footer>
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
    colors = {"M": "#B43F55", "P": "#315BCE", "E": "#C38A20", "R": "#287D70"}
    return "".join(f'<b style="--c:{colors[role]}">{role}</b>' for role in roles)


def trajectory_html(transitions: list[dict[str, str]], trace: dict) -> str:
    no_go_chips = "".join(f"<span>{esc(name)}</span>" for name in trace["no_go_branches"])
    repairs = "".join(f"<li>{esc(name)}</li>" for name in trace["submission_repairs"])
    plot = stage_plot_svg(transitions, trace)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Argus representative autonomous paper trajectory</title>
<style>
@page {{ size:12in 7.1in; margin:0; }}
* {{ box-sizing:border-box; }} html,body {{ margin:0; width:12in; height:7.1in; }}
body {{ font-family:Arial,Helvetica,sans-serif; color:#202932; background:#FFFFFF; print-color-adjust:exact; -webkit-print-color-adjust:exact; }}
.canvas {{ width:12in; height:7.1in; padding:20px 24px 16px; border-top:5px solid #315BCE; display:grid; grid-template-rows:88px 248px minmax(0,1fr) 42px; gap:10px; overflow:hidden; }}
header {{ display:flex; justify-content:space-between; align-items:flex-start; }}
.kicker {{ color:#315BCE; font-size:12px; font-weight:800; letter-spacing:.13em; text-transform:uppercase; margin-bottom:3px; }}
h1 {{ margin:0; font-size:24px; line-height:1.03; letter-spacing:-.022em; max-width:730px; }}
header p {{ margin:5px 0 0; color:#607080; font-size:13.5px; max-width:730px; }}
.summary {{ display:grid; grid-template-columns:repeat(3,1fr); gap:5px; width:352px; }}
.summary div {{ border:1px solid #D6DEE6; background:#F8FAFC; border-radius:7px; padding:5px 7px; }}
.summary b {{ display:block; color:#173B70; font-size:16px; line-height:1; }}
.summary span {{ color:#677482; font-size:9.5px; font-weight:700; }}
.plot-panel {{ border:1px solid #D5DEE6; border-radius:11px; background:#F8FAFC; padding:7px 10px 4px; }}
.plot-head {{ display:flex; justify-content:space-between; align-items:baseline; margin-bottom:1px; }}
.plot-head strong {{ color:#173B70; font-size:14px; }} .plot-head span {{ color:#71808E; font-size:10.5px; }}
.stage-plot {{ width:100%; height:214px; display:block; }}
.episodes-wrap {{ display:grid; grid-template-rows:18px minmax(0,1fr); gap:4px; min-height:0; overflow:hidden; }}
.episodes-title {{ display:flex; align-items:baseline; justify-content:space-between; padding:0 2px; }}
.episodes-title strong {{ color:#173B70; font-size:14px; }} .episodes-title span {{ color:#71808E; font-size:10.5px; }}
.episodes {{ display:grid; grid-template-columns:1.35fr .88fr 1fr 1fr .92fr; gap:6px; min-height:0; overflow:hidden; }}
.episode {{ border:1px solid #D6DEE6; border-top:4px solid var(--c); border-radius:9px; background:#fff; padding:7px 8px; position:relative; overflow:hidden; }}
.episode-head {{ display:flex; align-items:center; justify-content:space-between; gap:6px; margin-bottom:4px; }}
.episode-head small {{ color:var(--c); font-size:9.5px; font-weight:800; letter-spacing:.07em; text-transform:uppercase; }}
.badges {{ display:flex; gap:2px; }} .badges b {{ width:18px; height:18px; border-radius:5px; display:grid; place-items:center; background:var(--c); color:white; font-size:9px; }}
.episode h3 {{ margin:0 0 4px; font-size:14.5px; line-height:1.07; color:#1E2732; }}
.episode p {{ margin:0; color:#4D5A67; font-size:10.8px; line-height:1.19; }}
.episode strong {{ color:var(--c); }}
.chips {{ display:flex; flex-wrap:wrap; gap:3px; margin-top:5px; }} .chips span {{ border:1px solid #E4B9C1; background:#FFF5F6; border-radius:999px; padding:2px 4px; color:#8B3E4C; font-size:8.3px; font-weight:700; }}
.pivot {{ display:grid; place-items:center; text-align:center; min-height:67px; border:1px solid #C8D7F4; background:#F0F5FF; border-radius:7px; margin:6px 0 5px; padding:5px; color:#315BCE; font-size:11.5px; font-weight:800; }}
.pivot i {{ display:block; color:#8A98A7; font-style:normal; font-size:18px; line-height:.7; }}
.matrix {{ display:grid; grid-template-columns:repeat(3,1fr); gap:3px; margin:6px 0 5px; }} .matrix span {{ background:#EDF4FF; border:1px solid #C9D8F6; border-radius:4px; padding:3px 2px; text-align:center; color:#315BCE; font-size:9px; font-weight:800; }}
.episode ul {{ margin:5px 0 5px 14px; padding:0; color:#4D5A67; font-size:9.7px; line-height:1.16; }}
.paper-output {{ margin-top:6px; border:1px solid #E2C77E; background:#FFF8E7; border-radius:7px; padding:5px; text-align:center; }} .paper-output b {{ color:#8B6515; font-size:18px; }} .paper-output span {{ display:block; color:#6E5A2D; font-size:9.5px; font-weight:700; }}
footer {{ border:1px dashed #8DA1B4; border-radius:8px; background:#F2F6F9; display:flex; align-items:center; justify-content:center; gap:6px; color:#43576A; font-size:11px; font-weight:700; }}
footer b {{ color:#173B70; }}
</style></head><body><main class="canvas">
<header><div><div class="kicker">Representative paper campaign · measured state trajectory</div><h1>A failed method search becomes a rigorous negative-results paper</h1><p>The multimodal-hallucination project repeatedly changes route without losing the objective, prior failures, official results, or manuscript state.</p></div>
<div class="summary"><div><b>{trace['campaign_hours']:.1f} h</b><span>campaign-hours</span></div><div><b>{trace['engineer_rounds']}</b><span>Engineer rounds</span></div><div><b>{trace['reviewer_revisions']}</b><span>Reviewer revisions</span></div><div><b>{trace['session_rolls']}</b><span>session rolls</span></div><div><b>{trace['early_no_go_rollbacks']}</b><span>early rollbacks</span></div><div><b>{trace['submission_rollbacks']}</b><span>late rollbacks</span></div></div></header>
<section class="plot-panel"><div class="plot-head"><strong>(a) The canonical Stage state is recurrent, not linear</strong><span>red points denote Manager/Reviewer rollback decisions</span></div>{plot}</section>
<section class="episodes-wrap"><div class="episodes-title"><strong>(b) Role-resolved scientific episodes</strong><span>the same persistent campaign state crosses every episode</span></div><div class="episodes">
  <article class="episode" style="--c:#B43F55"><div class="episode-head"><small>Hypothesis pruning</small><div class="badges">{role_badges('R','M','P')}</div></div><h3><strong>7 no-go gates</strong> prune weak routes</h3><p>Official pilots expose missing signal or base-identical behavior. Reviewer rejects; Manager rolls back; Planner retires the branch.</p><div class="chips">{no_go_chips}</div></article>
  <article class="episode" style="--c:#315BCE"><div class="episode-head"><small>Scientific pivot</small><div class="badges">{role_badges('P','M')}</div></div><h3>Change the claim, retain the evidence</h3><div class="pivot">positive method<i>↓</i>negative-results audit</div><p>Failed routes become study objects.</p></article>
  <article class="episode" style="--c:#C38A20"><div class="episode-head"><small>Canonical experiment</small><div class="badges">{role_badges('E','R')}</div></div><h3><strong>{trace['canonical_cells']} cells · {trace['canonical_scored_rows']:,} rows</strong></h3><div class="matrix"><span>POPE</span><span>AMBER</span><span>HallusionBench</span><span>5 methods</span><span>official scorers</span><span>paired audits</span></div><p>Engineer completes the matrix; Reviewer binds claims to official outputs.</p></article>
  <article class="episode" style="--c:#287D70"><div class="episode-head"><small>Paper construction</small><div class="badges">{role_badges('E','R','M')}</div></div><h3>Analysis → draft → review</h3><p>Measured no-op and degradation modes become a scoped paper with synchronized claims, citations, figures, and layout.</p><div class="paper-output"><b>{trace['final_pages']} pages</b><span>{esc(trace['final_format'])}-formatted manuscript</span></div></article>
  <article class="episode" style="--c:#B43F55"><div class="episode-head"><small>Submission recovery</small><div class="badges">{role_badges('R','M','E')}</div></div><h3><strong>2 late rollbacks</strong> repair the package</h3><ul>{repairs}</ul><p>The same 15-cell evidence is rebound and re-reviewed.</p></article>
</div></section>
<footer><b>Durable state:</b> failed hypotheses + canonical runs + reviewer decisions + manuscript artifacts survive 29 session replacements and remain available to the next mission.</footer>
</main></body></html>"""


def write_provenance() -> None:
    overview = {
        "figure_id": "autonomous-paper-portfolio",
        "reader_question": "What scientific work did Argus produce, and how does the recurrent runtime connect to those outputs?",
        "claim": "Six canonical pipelines completed across six domains through repeated role handoffs, review, rollback, and session continuation.",
        "evidence": [SUMMARY_PATH.name, FINDINGS_PATH.name],
        "encoding": "A recurrent four-role state loop is paired with six paper cards that report task-native findings and process counts.",
        "scope": "Observed Argus campaigns; paper-format completion is not venue acceptance or a human-quality comparison.",
        "target_size": "12 x 7.15 inch source canvas; approximately 4.2 inches high at manuscript width",
        "visual_style": "shared Argus paper palette, white background, compact panels, and fixed role colors",
        "editable_source": [Path(__file__).name, OVERVIEW_HTML.name],
        "export": ["paper_case_study.pdf", "paper_case_study.svg", "paper_case_study.png"],
    }
    trajectory = {
        "figure_id": "representative-paper-trajectory",
        "reader_question": "How does one Argus campaign recover from failed hypotheses and late submission defects?",
        "claim": "The representative campaign uses seven early no-go rollbacks, a scientific scope pivot, and two late submission repairs to reach a completed manuscript.",
        "evidence": [TRANSITIONS_PATH.name, TRACE_PATH.name],
        "encoding": "An actual Stage-versus-time trace is linked to role-resolved episodes and retained scientific artifacts.",
        "scope": "One 163.6-hour multimodal-hallucination campaign; role labels summarize structured events rather than private model reasoning.",
        "target_size": "12 x 7.1 inch source canvas; approximately 4.2 inches high at manuscript width",
        "visual_style": "shared Argus paper palette, white background, compact panels, and rollback red reserved for failure transitions",
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
