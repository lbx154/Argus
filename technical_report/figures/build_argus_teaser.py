#!/usr/bin/env python3
"""Build the paper's vector teaser from committed evidence and runtime semantics."""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import subprocess
from pathlib import Path


HERE = Path(__file__).resolve().parent
REPORT = HERE.parent
RESULTS = REPORT / "evidence" / "website_results.json"
PAPERS = REPORT / "evidence" / "paper_inventory.json"
HTML_OUT = HERE / "argus_teaser.html"
PDF_OUT = HERE / "argus_teaser.pdf"
MANIFEST_OUT = HERE / "argus_teaser.json"


SHORT_NAMES = {
    "NVIDIA SOL-ExecBench": "SOL-ExecBench",
    "nanochat · B200": "nanochat · B200",
    "nanochat · H100": "nanochat · H100",
    "nanoGPT speedrun": "nanoGPT speedrun",
    "AARRI-Bench": "AARRI-Bench",
    "Arbor · RUC NLPIR": "Arbor comparison",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def result_cards(payload: dict) -> str:
    cards: list[str] = []
    for row in payload["results"]:
        execution = f"{row['agent_backbone']} · {row['agent_backend']}"
        cards.append(
            f"""
            <div class="metric-card">
              <div class="metric-head"><span>{html.escape(SHORT_NAMES[row['arena']])}</span><i>{execution}</i></div>
              <strong>{html.escape(row['result'])}</strong>
              <small>{html.escape(row['protocol'])}</small>
              <p>{html.escape(row['human_comparison'])}</p>
            </div>
            """.strip()
        )
    return "\n".join(cards)


def build_html(results: dict, papers: dict) -> str:
    totals = papers["totals"]
    cards = result_cards(results)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Argus paper teaser</title>
<style>
@page {{ size: 12in 5.8in; margin: 0; }}
* {{ box-sizing: border-box; }}
html, body {{ margin: 0; width: 100%; height: 100%; }}
body {{ background: #fff; color: #1f2933; font-family: Arial, Helvetica, sans-serif; print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
.canvas {{ width: 12in; height: 5.8in; padding: .22in .28in .15in; border-top: .06in solid #315bce; display: grid; grid-template-rows: auto 1fr auto; gap: .11in; }}
.task-shape {{ display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: .22in; }}
.task-shape h1 {{ margin: 0; color: #173b70; font-size: 22pt; line-height: 1; }}
.task-shape p {{ margin: .06in 0 0; color: #5e6875; font-size: 11pt; }}
.criteria {{ display: flex; gap: .08in; }}
.criteria span {{ border: 1px solid #cbd5df; background: #f8fafc; border-radius: 99px; padding: .06in .11in; font-size: 9pt; font-weight: 700; }}
.panels {{ display: grid; grid-template-columns: 1.42fr 1fr; gap: .16in; min-height: 0; }}
.panel {{ border: 1px solid #d8e0e8; border-radius: .11in; background: #f8fafc; padding: .16in; min-height: 0; }}
.panel-title {{ display: flex; align-items: center; gap: .08in; margin-bottom: .12in; font-size: 12pt; }}
.panel-title b {{ width: .24in; height: .24in; border-radius: 50%; display: grid; place-items: center; background: #173b70; color: white; font-size: 8pt; }}
.architecture {{ display: grid; grid-template-rows: auto auto auto 1fr auto auto; }}
.flow {{ display: grid; grid-template-columns: 1.02fr auto 2.8fr auto 1.10fr auto 1.15fr; gap: .07in; align-items: stretch; }}
.node, .runtime, .gate {{ border: 1px solid #c9d4df; background: white; border-radius: .08in; padding: .12in; display: grid; align-content: center; text-align: center; }}
.node strong, .gate strong, .runtime > strong {{ color: #173b70; font-size: 11pt; }}
.node small, .gate small {{ color: #687380; font-size: 8pt; margin-top: .04in; }}
.runtime {{ grid-template-rows: auto 1fr; gap: .08in; }}
.roles {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: .05in; }}
.roles span {{ border-radius: .06in; background: #edf2ff; color: #315bce; padding: .10in .04in; font-size: 8.7pt; font-weight: 700; }}
.gate {{ border-color: #c38a20; background: #fff6df; }}
.arrow {{ display: grid; place-items: center; color: #315bce; font-size: 20pt; font-weight: 700; }}
.state {{ margin-top: .10in; display: grid; grid-template-columns: auto 1fr auto; gap: .10in; align-items: center; }}
.state > strong {{ color: #5e6875; font-size: 9pt; }}
.state-list {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: .05in; }}
.state-list span {{ border: 1px solid #d8e0e8; background: white; border-radius: .05in; padding: .07in .03in; text-align: center; font-size: 8.4pt; font-weight: 700; }}
.next {{ color: #c38a20; font-size: 9pt; font-weight: 700; }}
.value-chain {{ align-self: center; display: grid; grid-template-columns: 1fr auto 1.2fr auto 1.15fr auto 1fr; gap: .06in; align-items: center; padding: .06in .04in; }}
.value-chain div {{ text-align: center; }}
.value-chain strong {{ color: #173b70; font-size: 8.7pt; }}
.value-chain small {{ display: block; color: #687380; font-size: 6.9pt; margin-top: .025in; }}
.value-chain i {{ color: #c38a20; font-size: 14pt; font-style: normal; text-align: center; }}
.trajectory {{ align-self: center; border-top: 1px solid #d8e0e8; border-bottom: 1px solid #d8e0e8; padding: .12in 0; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: .12in; }}
.trajectory > strong {{ color: #173b70; font-size: 9pt; }}
.trajectory-steps {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: .05in; }}
.trajectory-steps span {{ position: relative; border-radius: .05in; background: #edf2ff; color: #315bce; padding: .07in .02in; text-align: center; font-size: 8pt; font-weight: 700; }}
.trajectory-steps span:not(:last-child)::after {{ content: '→'; position: absolute; right: -.065in; color: #8ca3c9; }}
.trajectory small {{ color: #687380; font-size: 7.4pt; text-align: right; }}
.formula-strip {{ margin-top: .10in; display: grid; grid-template-columns: 1.25fr 1fr; gap: .08in; align-self: end; }}
.formula {{ border-left: .045in solid #315bce; background: white; padding: .09in .12in; }}
.formula:nth-child(2) {{ border-left-color: #c38a20; }}
.formula code {{ display: block; color: #173b70; font: 700 9pt 'Courier New', monospace; white-space: nowrap; }}
.formula small {{ color: #687380; font-size: 7.5pt; }}
.evidence {{ display: grid; grid-template-rows: auto 1fr auto; }}
.metrics {{ display: grid; grid-template-columns: 1fr 1fr; grid-template-rows: repeat(3, 1fr); gap: .07in; min-height: 0; }}
.metric-card {{ border: 1px solid #d8e0e8; border-left: .045in solid #315bce; background: white; border-radius: .06in; padding: .08in .10in; display: grid; grid-template-rows: auto auto auto 1fr; }}
.metric-head {{ display: flex; justify-content: space-between; gap: .05in; align-items: start; }}
.metric-head span {{ font-size: 8.5pt; font-weight: 700; }}
.metric-head i {{ color: #6d7783; font-size: 6.7pt; font-style: normal; text-align: right; }}
.metric-card strong {{ color: #173b70; font-size: 16pt; line-height: 1.05; margin-top: .025in; }}
.metric-card small {{ color: #697480; font-size: 7pt; }}
.metric-card p {{ margin: .025in 0 0; color: #4f5a66; font-size: 7pt; line-height: 1.15; }}
.portfolio {{ margin-top: .08in; border: 1px solid #d8e0e8; background: #fff; border-radius: .07in; padding: .08in .11in; display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: .10in; }}
.portfolio strong {{ color: #c38a20; font-size: 18pt; }}
.portfolio span {{ font-size: 8.7pt; font-weight: 700; }}
.portfolio small {{ color: #687380; font-size: 7.3pt; text-align: right; }}
footer {{ border-top: 1px solid #d8e0e8; padding-top: .08in; display: flex; justify-content: space-between; color: #66717d; font-size: 7.6pt; }}
</style>
</head>
<body>
<main class="canvas">
  <header class="task-shape">
    <div><h1>Dense-intelligence research</h1><p>Continuous proposal—execution—verification over durable state</p></div>
    <div></div>
    <div class="criteria"><span>Invention</span><span>Long horizon</span><span>Verifiable</span></div>
  </header>
  <section class="panels">
    <article class="panel architecture">
      <div class="panel-title"><b>A</b><strong>Runtime architecture and fixed-model evolution</strong></div>
      <div class="flow">
        <div class="node"><strong>Objective</strong><small>task · evaluator · budget</small></div><div class="arrow">→</div>
        <div class="runtime"><strong>Persistent research runtime</strong><div class="roles"><span>Manager</span><span>Planner</span><span>Engineer</span><span>Reviewer</span></div></div><div class="arrow">→</div>
        <div class="gate"><strong>Evidence gate</strong><small>artifacts · logs · verdict</small></div><div class="arrow">→</div>
        <div class="node"><strong>Retained capability</strong><small>auditable and revisable</small></div>
      </div>
      <div class="state"><strong>Runtime state</strong><div class="state-list"><span>Memory</span><span>Skills</span><span>Tools</span><span>Verifiers</span><span>Routing</span></div><div class="next">↺ next mission</div></div>
      <div class="value-chain"><div><strong>TOKEN</strong><small>reasoning/action budget</small></div><i>→</i><div><strong>Dense intelligence</strong><small>continuous verified work</small></div><i>→</i><div><strong>Research artifacts</strong><small>code · data · proofs · papers</small></div><i>→</i><div><strong>Research value</strong><small>auditable and reusable results</small></div></div>
      <div class="trajectory"><strong>Mission trajectory τt</strong><div class="trajectory-steps"><span>Plan</span><span>Retrieve</span><span>Build</span><span>Experiment</span><span>Review</span><span>Distill</span></div><small>bounded outcome · persisted evidence</small></div>
      <div class="formula-strip">
        <div class="formula"><code>ρI(T) = 1/T ∫ Ṅtok(t) ηr(t) ηa(t) ηv(t) dt</code><small>density of useful reasoning, action, and verification—not token volume alone</small></div>
        <div class="formula"><code>Ht+1 = U(Ht, τt, Et),  θt+1 = θt</code><small>evidence updates the runtime while model parameters remain fixed online</small></div>
      </div>
    </article>
    <article class="panel evidence">
      <div class="panel-title"><b>B</b><strong>Public evidence in task-native units</strong></div>
      <div class="metrics">{cards}</div>
      <div class="portfolio"><strong>{totals['papers']}</strong><span>de-duplicated research artifacts across {totals['programs']} programs</span><small>{totals['manuscript']} manuscripts · {totals['draft']} drafts</small></div>
    </article>
  </section>
  <footer><span>Architecture is schematic; result cards retain task-native units and are not cross-normalized.</span><span>All six runs: GPT-5.5 · Codex</span></footer>
</main>
</body>
</html>
"""


def main() -> int:
    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    papers = json.loads(PAPERS.read_text(encoding="utf-8"))
    rendered = build_html(results, papers)
    HTML_OUT.write_text(rendered, encoding="utf-8")

    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise SystemExit("google-chrome or chromium is required to build argus_teaser.pdf")
    command = [
        chrome,
        "--headless=new",
        "--no-sandbox",
        "--disable-gpu",
        "--hide-scrollbars",
        "--virtual-time-budget=2500",
        f"--print-to-pdf={PDF_OUT}",
        "--print-to-pdf-no-header",
        HTML_OUT.as_uri(),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0 or not PDF_OUT.is_file():
        raise SystemExit(f"teaser render failed: {completed.stderr}")

    manifest = {
        "figure": "argus_teaser",
        "reader_question": "How does Argus turn a dense-intelligence objective into retained capability, and what public evidence currently supports the system?",
        "claim": "Argus couples role-separated research with an evidence gate and reports heterogeneous public outcomes without cross-normalizing their metrics.",
        "sources": {
            "results": str(RESULTS.relative_to(REPORT)),
            "results_sha256": sha256(RESULTS),
            "paper_inventory": str(PAPERS.relative_to(REPORT)),
            "paper_inventory_sha256": sha256(PAPERS),
        },
        "editable_source": HTML_OUT.name,
        "vector_export": PDF_OUT.name,
        "renderer": Path(chrome).name,
        "html_sha256": sha256(HTML_OUT),
    }
    MANIFEST_OUT.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(PDF_OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
