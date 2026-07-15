# Argus Expanding-Frontier Narrative Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the Argus Technical Report and bilingual GitHub landing pages around the single technical spine `Dense Intelligence -> Evidence -> Runtime Evolution -> OOD Frontier`, with a light blue-gold visual system and unchanged evidence-backed public claims.

**Architecture:** The implementation introduces two deterministic narrative figures, then replaces the current report incrementally by act so every commit still builds. A source-level narrative contract grows alongside each act; the final README reuses the same master figure and causal language. Runtime code is never changed to fit the story: prose, figures, and equations must remain grounded in the committed implementation and evidence bundles.

**Tech Stack:** Python 3.11+, pytest, Matplotlib, LaTeX (`pdflatex` + `bibtex`), GitHub-flavored Markdown, `pdftotext`, `pdfinfo`, `pdftoppm`, SHA-256 manifests.

## Global Constraints

- Work in the existing isolated worktree `/home/argustest/.copilot/session-state/7ab35eb1-e9e4-411f-98f5-9ddb9b5fd70b/pro-readme-worktree`; invoke `superpowers:using-git-worktrees` before execution and confirm the worktree is clean.
- The approved spec is `docs/superpowers/specs/2026-07-15-argus-grand-narrative-design.md`.
- Preserve the title `Argus: Autonomous Research Generation and Understanding System`.
- Set the subtitle to `Dense Intelligence for an Expanding Research Frontier`.
- Set the document identity to `Technical Report 0.3`.
- Use four acts and exactly thirteen numbered main chapters.
- Keep the final report between 28 and 30 pages.
- Keep English README prose between 1,200 and 1,600 words; keep the Chinese README structurally and factually aligned.
- Use the light visual system: bone white `#FBFAF6`, system blue `#315BCE`, deep blue `#214884`, verified-frontier gold `#C38A20`, graphite body text.
- The cover must remain light; do not introduce a dark cover.
- `rho_DI(T)` is explanatory notation, not a measured benchmark.
- Runtime evolution must explicitly state `theta_(t+1) = theta_t`; do not imply online parameter training.
- Capability-set notation must not imply that every run succeeds or that practical capability is empirically monotone.
- Preserve all six result values, protocols, references, and evidence statuses from `technical_report/evidence/website_results.json`.
- Preserve `41 papers = 35 manuscripts + 6 drafts` and the six program counts from `technical_report/evidence/paper_inventory.json`.
- Do not change `argusbot.cn`, Argus runtime code, benchmark code, provider behavior, or evidence values.
- Keep `guardrail(s)` at no more than two occurrences; keep `dumb pipe`, `plumbing`, and `not smarter than` at zero.
- Do not introduce new dependencies.
- Write transient audit output only under the git-ignored
  `.superpowers/audit/grand-narrative/` directory; do not commit it.
- Every commit message must include:

```text
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b
```

---

## File Structure

### Report shell and sections

- Modify `technical_report/main.tex`: Technical Report 0.3 metadata, light cover, act-opener macro, final thirteen-section input order.
- Create `technical_report/sections/01_executive_thesis.tex`: complete causal thesis and master figure.
- Create `technical_report/sections/02_dense_intelligence.tex`: dense-intelligence problem and notation.
- Create `technical_report/sections/03_episodic_agents.tex`: why episodic/single-context agents do not compound.
- Create `technical_report/sections/04_argus_life.tex`: map the master spine to the Life runtime.
- Create `technical_report/sections/05_roles_planes.tex`: four roles and three implementation planes.
- Create `technical_report/sections/06_lifecycle_state.tex`: mission lifecycle, persisted state, `checkpoint.json`, bounded context.
- Create `technical_report/sections/07_evidence_review.tex`: evidence plane, measurement integrity, Reviewer authority.
- Create `technical_report/sections/08_reliability_resources.tex`: recovery, budgets, background work, GPU lease, workbench, deployment.
- Create `technical_report/sections/09_runtime_evolution.tex`: `H_t` runtime capability state and ownership of updates.
- Create `technical_report/sections/10_process_data.tex`: trajectory information beyond final artifacts.
- Create `technical_report/sections/11_ood_expansion.tex`: evidence-gated capability retention and OOD expansion.
- Create `technical_report/sections/12_frontier_evidence.tex`: six public arenas and 41-paper portfolio.
- Create `technical_report/sections/13_limitations_roadmap.tex`: limitations, failure modes, roadmap, conclusion.
- Modify `technical_report/sections/90_appendix.tex`: notation table plus existing interfaces, state, events, evidence, and inventory maps.
- Delete the superseded `technical_report/sections/01_executive_summary.tex` through `15_roadmap.tex` files after their content is migrated.

### Figures and generated artifacts

- Modify `technical_report/figures/build_report_figures.py`: add `build_master_spine()` and `build_dense_intelligence()`, recolor existing deterministic figures, keep all evidence values unchanged.
- Modify `technical_report/figures/REPORT_FIGURES.json`: generated by the figure builder; never hand-edit hashes.
- Create generated `technical_report/figures/master_spine.{pdf,png}`.
- Create generated `technical_report/figures/dense_intelligence.{pdf,png}`.
- Regenerate `mission_lifecycle`, `system_planes`, `public_results`, and `paper_portfolio` only through the builder.
- Regenerate `technical_report/argus-technical-report.pdf` from LaTeX source.

### README and tests

- Rewrite `README.md`: six-layer expanding-frontier landing page, 1,200–1,600 prose words.
- Rewrite `README.zh-CN.md`: same structure, claims, and evidence boundaries.
- Modify `tests/test_technical_report_figures.py`: master-spine, dense-intelligence, role, color, and reproducibility contracts.
- Create `tests/test_technical_report_narrative.py`: document identity, act structure, formulas, recurring spine, evidence values, README structure, and terminology contracts.

---

### Task 1: Build the Blue-Gold Narrative Figure System

**Files:**
- Modify: `technical_report/figures/build_report_figures.py`
- Modify: `tests/test_technical_report_figures.py`
- Generate: `technical_report/figures/master_spine.pdf`
- Generate: `technical_report/figures/master_spine.png`
- Generate: `technical_report/figures/dense_intelligence.pdf`
- Generate: `technical_report/figures/dense_intelligence.png`
- Regenerate: `technical_report/figures/REPORT_FIGURES.json`

**Interfaces:**
- Consumes: existing `_new_axes`, `_box`, `_text`, `_arrow`, `_save`, `EventType`, website result JSON, paper inventory JSON.
- Produces: `build_master_spine() -> dict`, `build_dense_intelligence() -> dict`, and manifest entries `master_spine` and `dense_intelligence`.

- [ ] **Step 1: Extend the rendered-text test helper and write failing figure tests**

Add these tests to `tests/test_technical_report_figures.py`:

```python
def test_master_spine_contains_causal_chain_and_four_roles(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_master_spine")

    required = {
        "Unknown objective",
        "Dense Intelligence Runtime",
        "Evidence Gate",
        "Runtime Evolution",
        "Expanded OOD Frontier",
        "Manager",
        "Planner",
        "Engineer",
        "Reviewer",
        "Memory",
        "Skills",
        "Tools",
        "Verifiers",
        "Routing",
        "Evaluations",
    }
    assert required <= set(text.splitlines())
    assert "Every run expands the frontier." in text


def test_master_spine_states_fixed_model_parameters(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_master_spine")

    assert "H(t+1) = U(H(t), trajectory, evidence)" in text
    assert "model parameters remain fixed" in text
    assert "capability is not guaranteed to grow every run" in text


def test_dense_intelligence_is_explanatory_not_a_score(monkeypatch) -> None:
    text = _figure_text(monkeypatch, "build_dense_intelligence")

    assert "decision" in text
    assert "execution" in text
    assert "verification" in text
    assert "conceptual model · not a reported benchmark" in text
    assert "Argus > human" not in text


def test_website_palette_is_used_by_report_figures() -> None:
    builder = _load_figure_builder()

    assert builder.BONE == "#FBFAF6"
    assert builder.BLUE == "#315BCE"
    assert builder.BLUE_DEEP == "#214884"
    assert builder.GOLD == "#C38A20"
```

- [ ] **Step 2: Run the new tests and confirm the intended failure**

Run:

```bash
pytest -q \
  tests/test_technical_report_figures.py::test_master_spine_contains_causal_chain_and_four_roles \
  tests/test_technical_report_figures.py::test_master_spine_states_fixed_model_parameters \
  tests/test_technical_report_figures.py::test_dense_intelligence_is_explanatory_not_a_score \
  tests/test_technical_report_figures.py::test_website_palette_is_used_by_report_figures
```

Expected: FAIL because `build_master_spine`, `build_dense_intelligence`, `BLUE`, `BLUE_DEEP`, and `GOLD` do not exist.

- [ ] **Step 3: Add the website-aligned palette and the master-spine data contract**

In `technical_report/figures/build_report_figures.py`, replace the report accent constants with:

```python
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
RECOVERY = "#8A5A3B"

MASTER_SPINE_STAGES = (
    ("Unknown objective", "OOD problem or deeper challenge"),
    ("Dense Intelligence Runtime", "continuous organized research work"),
    ("Evidence Gate", "artifacts · measurements · failures · verdicts"),
    ("Runtime Evolution", "memory · skills · tools · verifiers · routing · evaluations"),
    ("Expanded OOD Frontier", "the next unknown task does not start from zero"),
)

MASTER_SPINE_ROLES = (
    ("Manager", "intent · lifetime · stage"),
    ("Planner", "decompose · schedule · re-plan"),
    ("Engineer", "retrieve · build · experiment"),
    ("Reviewer", "inspect evidence · decide"),
)
```

Update existing indigo uses to `BLUE_DEEP` or `BLUE`; reserve `GOLD` for evidence/frontier accents. Do not change numeric values in result figures.
Replace the old `CALLOUT_FILL` constant and uses with `BLUE_SOFT`. Update the
generated manifest's `palette` metadata from `argus_indigo` to
`system_blue`, `deep_blue`, and `frontier_gold`; do not leave references to
removed `INDIGO` constants.

- [ ] **Step 4: Implement `build_master_spine()`**

Implement a deterministic 10.4 × 6.2 inch landscape figure using the existing drawing helpers:

```python
def build_master_spine() -> dict:
    fig, ax = _new_axes(10.4, 6.2)
    _text(
        ax, 5, 96, "ARGUS · TECHNICAL SPINE",
        size=7.8, color=GOLD, weight="bold", ha="left",
    )
    _text(
        ax, 5, 90, "Every run expands the frontier.",
        size=15, color=BLUE_DEEP, weight="bold", ha="left",
    )

    stage_x = (5, 25, 53, 76, 91)
    stage_w = (15, 22, 17, 17, 8)
    for index, ((title, subtitle), x, width) in enumerate(
        zip(MASTER_SPINE_STAGES, stage_x, stage_w, strict=True)
    ):
        face = BLUE_SOFT if index in {1, 3} else BONE
        edge = GOLD if index in {2, 4} else BLUE
        _box(ax, x, 42, width, 30, face=face, edge=edge, lw=1.3)
        _text(ax, x + width / 2, 65, title, size=8.2, weight="bold")
        _text(ax, x + width / 2, 48, subtitle, size=6.2, color=GRAPHITE_SOFT)
        if index < len(MASTER_SPINE_STAGES) - 1:
            _arrow(ax, x + width + 1, 57, stage_x[index + 1] - 1, 57, color=BLUE)

    role_positions = ((28, 54), (37, 54), (28, 45), (37, 45))
    for (name, detail), (x, y) in zip(
        MASTER_SPINE_ROLES, role_positions, strict=True
    ):
        _box(ax, x, y, 7.5, 6.5, face=BONE, edge=GOLD if name == "Reviewer" else BLUE)
        _text(ax, x + 3.75, y + 4.1, name, size=6.0, weight="bold")
        _text(ax, x + 3.75, y + 1.7, detail, size=4.8, color=GRAPHITE_SOFT)

    runtime_labels = ("Memory", "Skills", "Tools", "Verifiers", "Routing", "Evaluations")
    for index, label in enumerate(runtime_labels):
        row, col = divmod(index, 2)
        _text(ax, 79 + col * 8, 63 - row * 7, label, size=5.8, color=BLUE_DEEP)

    _text(
        ax, 50, 30,
        "H(t+1) = U(H(t), trajectory, evidence)",
        size=9.0, color=BLUE_DEEP, weight="bold",
    )
    _text(
        ax, 50, 24, "model parameters remain fixed",
        size=7.2, color=GRAPHITE_SOFT,
    )
    _text(
        ax, 50, 15,
        "capability is not guaranteed to grow every run",
        size=6.4, color=GRAPHITE_SOFT, style="italic",
    )
    _arrow(ax, 94, 39, 8, 39, color=GOLD, lw=1.5, connection="arc3,rad=-0.22")
    return _save(fig, "master_spine")
```

Adjust only coordinates needed to avoid overlap; keep every tested label exact.

- [ ] **Step 5: Implement `build_dense_intelligence()`**

Implement a two-track continuity schematic, not a performance chart:

```python
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
        "conceptual model · not a reported benchmark",
        size=6.5, color=GRAPHITE_SOFT, style="italic",
    )
    return _save(fig, "dense_intelligence")
```

The episodic track must not contain a human-vs-Argus inequality or superiority score.

- [ ] **Step 6: Register and generate all deterministic figures**

Add both builders to `main()`:

```python
figures = {
    "master_spine": build_master_spine(),
    "dense_intelligence": build_dense_intelligence(),
    "system_planes": build_system_planes(),
    "mission_lifecycle": build_mission_lifecycle(),
    "public_results": build_public_results(),
    "paper_portfolio": build_paper_portfolio(),
}
```

Run:

```bash
python technical_report/figures/build_report_figures.py
pytest -q tests/test_technical_report_figures.py
```

Expected: all figure tests PASS; `REPORT_FIGURES.json` contains six deterministic figure entries.

- [ ] **Step 7: Prove deterministic output**

Run:

```bash
audit=.superpowers/audit/grand-narrative
mkdir -p "$audit"
python technical_report/figures/build_report_figures.py
sha256sum technical_report/figures/{master_spine,dense_intelligence,system_planes,mission_lifecycle,public_results,paper_portfolio}.{pdf,png} technical_report/figures/REPORT_FIGURES.json > "$audit/figure-hash-1"
python technical_report/figures/build_report_figures.py
sha256sum technical_report/figures/{master_spine,dense_intelligence,system_planes,mission_lifecycle,public_results,paper_portfolio}.{pdf,png} technical_report/figures/REPORT_FIGURES.json > "$audit/figure-hash-2"
diff -u "$audit/figure-hash-1" "$audit/figure-hash-2"
```

Expected: no diff.

- [ ] **Step 8: Commit the figure foundation**

```bash
git add tests/test_technical_report_figures.py \
  technical_report/figures/build_report_figures.py \
  technical_report/figures/REPORT_FIGURES.json \
  technical_report/figures/master_spine.pdf \
  technical_report/figures/master_spine.png \
  technical_report/figures/dense_intelligence.pdf \
  technical_report/figures/dense_intelligence.png \
  technical_report/figures/system_planes.pdf \
  technical_report/figures/system_planes.png \
  technical_report/figures/mission_lifecycle.pdf \
  technical_report/figures/mission_lifecycle.png \
  technical_report/figures/public_results.pdf \
  technical_report/figures/public_results.png \
  technical_report/figures/paper_portfolio.pdf \
  technical_report/figures/paper_portfolio.png
git commit -m "docs(report): add expanding-frontier figure system" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 2: Rewrite the Cover and Act I — The Dense-Intelligence Problem

**Files:**
- Create: `tests/test_technical_report_narrative.py`
- Modify: `technical_report/main.tex`
- Create: `technical_report/sections/01_executive_thesis.tex`
- Create: `technical_report/sections/02_dense_intelligence.tex`
- Create: `technical_report/sections/03_episodic_agents.tex`
- Delete after replacement: `technical_report/sections/01_executive_summary.tex`
- Delete after replacement: `technical_report/sections/02_problem.tex`

**Interfaces:**
- Consumes: `master_spine.pdf`, `dense_intelligence.pdf`, approved spec, committed evidence JSON.
- Produces: Technical Report 0.3 identity, light cover, `\actopener`, Act I labels `sec:thesis`, `sec:dense`, `sec:episodic`.

- [ ] **Step 1: Write failing report-identity and Act-I contracts**

Create `tests/test_technical_report_narrative.py`:

```python
from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "technical_report"


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_report_identity_is_dense_intelligence_03() -> None:
    main = _read("technical_report/main.tex")

    assert "Technical Report 0.3" in main
    assert "Dense Intelligence for an Expanding Research Frontier" in main
    assert "Technical Report 0.2" not in main


def test_cover_is_light_blue_gold() -> None:
    main = _read("technical_report/main.tex")

    assert r"\definecolor{systemblue}{HTML}{315BCE}" in main
    assert r"\definecolor{deepblue}{HTML}{214884}" in main
    assert r"\definecolor{frontiergold}{HTML}{C38A20}" in main
    assert r"\pagecolor{bonewhite}" in main
    assert "Dark cover" not in main


def test_act_one_sections_and_master_spine_are_wired() -> None:
    main = _read("technical_report/main.tex")

    assert r"\input{sections/01_executive_thesis}" in main
    assert r"\input{sections/02_dense_intelligence}" in main
    assert r"\input{sections/03_episodic_agents}" in main
    thesis = _read("technical_report/sections/01_executive_thesis.tex")
    assert r"\includegraphics" in thesis
    assert "master_spine.pdf" in thesis
    assert "Every run expands the frontier." in thesis


def test_dense_intelligence_not_presented_as_measured_score() -> None:
    dense = _read("technical_report/sections/02_dense_intelligence.tex")

    assert r"\rho_{\mathrm{DI}}(T)" in dense
    assert "explanatory construct" in dense
    assert "not a reported benchmark metric" in dense
    assert "universal superiority" not in dense


def test_no_banned_rhetoric_in_report_source() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPORT / "sections").glob("*.tex"))
    )
    assert len(re.findall(r"\bguardrails?\b", source, flags=re.IGNORECASE)) <= 2
    assert not re.search(r"\bdumb pipe\b|\bplumbing\b|not smarter than", source, flags=re.IGNORECASE)
```

- [ ] **Step 2: Run the Act-I contracts and confirm failure**

Run:

```bash
pytest -q tests/test_technical_report_narrative.py
```

Expected: FAIL on report version, subtitle, new section inputs, master figure, and dense-intelligence notation.

- [ ] **Step 3: Replace report identity, palette, and cover**

In `technical_report/main.tex`:

1. Add:

```tex
\definecolor{systemblue}{HTML}{315BCE}
\definecolor{deepblue}{HTML}{214884}
\definecolor{frontiergold}{HTML}{C38A20}
\definecolor{goldsoft}{HTML}{FFF3D6}
```

2. Change PDF subject, header, and visible version to `Technical Report 0.3`.
3. Set the PDF subject and cover subtitle to `Dense Intelligence for an Expanding Research Frontier`.
4. Keep `\pagecolor{bonewhite}`.
5. Replace the current centered cover with a light left-aligned cover:

```tex
\thispagestyle{empty}
\vspace*{0.6cm}
{\small\bfseries\ttfamily\color{frontiergold} ARGUS}\\[2.0cm]
{\Huge\bfseries\color{deepblue}
  Autonomous Research Generation\\[0.15cm]
  and Understanding System}\\[0.7cm]
{\Large\color{graphite}
  Dense Intelligence for an Expanding Research Frontier}\\[1.2cm]
\begin{center}
  \includegraphics[width=0.62\textwidth]{figures/master_spine.pdf}
\end{center}
\vfill
{\color{systemblue}\rule{\textwidth}{0.7pt}}\\[0.2cm]
{\small\ttfamily\color{systemblue} TECHNICAL REPORT 0.3 \hfill JULY 2026}
```

Do not use a dark page background.

- [ ] **Step 4: Add the act-opener macro**

Add to `technical_report/main.tex`:

```tex
\newcommand{\actopener}[3]{%
  \clearpage
  {\small\bfseries\ttfamily\color{frontiergold} #1}\par
  \vspace{0.35cm}
  {\LARGE\bfseries\color{deepblue} #2}\par
  \vspace{0.25cm}
  {\large\color{graphitesoft} #3}\par
  \vspace{0.35cm}
  {\color{systemblue}\rule{\textwidth}{0.7pt}}
  \vspace{0.25cm}
}
```

Use it before Section 1:

```tex
\actopener
  {ACT I · THE DENSE-INTELLIGENCE PROBLEM}
  {Research intelligence is sparse in time.}
  {Long-horizon research requires continuity, execution, verification, and durable state—not merely a longer context window.}
```

- [ ] **Step 5: Write `01_executive_thesis.tex`**

The section must contain, in this order:

1. Exact opening:

```tex
\section{Executive Thesis}
\label{sec:thesis}

\begin{quote}
\large\bfseries
Argus sustains dense research intelligence, turns each run's evidence into
runtime capability, and carries that capability into the next unknown task.
Every run expands the frontier.
\end{quote}
```

2. A four-paragraph argument: sparse intelligence; dense role loop; evidence-gated runtime state; OOD consequence.
3. `master_spine.pdf` at `0.98\textwidth`.
4. A compact `What this report claims / does not claim` two-column table.
5. A one-paragraph public evidence summary preserving all six arena and 41-paper caveats.
6. Exact qualification:

```tex
``Every run leaves the system more capable'' is the design objective: a run may
fail, produce only a negative result, or add no verified capability. The report
does not claim monotone empirical capability growth.
```

- [ ] **Step 6: Write `02_dense_intelligence.tex`**

Required subsections:

- `Definition`.
- `Continuity is not token volume`.
- `A conceptual density model`.
- `What is and is not measured`.

Include `dense_intelligence.pdf`, then the exact notation:

```tex
\begin{equation}
\rho_{\mathrm{DI}}(T)
= \frac{1}{T}\int_0^T
\lambda(t)\,\eta_d(t)\,\eta_x(t)\,\eta_v(t)\,dt .
\end{equation}
```

Define all five symbols in a table. Include both sentences:

```tex
\rho_{\mathrm{DI}} is an explanatory construct, not a reported benchmark metric.
It does not establish universal superiority over human researchers or other systems.
```

- [ ] **Step 7: Write `03_episodic_agents.tex`**

Required subsections:

- `Model capability is not system capability`.
- `Episodic work pays context-recovery cost`.
- `Unbounded context is not institutional memory`.
- `Process data disappears when only the final artifact survives`.
- `Design requirements for compounding research`.

End with five requirements that transition directly into Act II: persistent objective, role separation, bounded context, independent evidence, reusable runtime state.

- [ ] **Step 8: Wire Act I while preserving a buildable transitional report**

Replace the first two inputs in `main.tex`:

```tex
\input{sections/01_executive_thesis}
\input{sections/02_dense_intelligence}
\input{sections/03_episodic_agents}
```

Keep the existing implementation and evidence sections after Act I temporarily. Delete only:

```text
technical_report/sections/01_executive_summary.tex
technical_report/sections/02_problem.tex
```

- [ ] **Step 9: Run tests and build**

```bash
pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
make -C technical_report clean all
grep -Ec 'Overfull \\[hv]box' technical_report/main.log
grep -Eic 'undefined references|undefined citations|citation.*undefined|reference.*undefined' technical_report/main.log
```

Expected: tests PASS; build exits 0; both grep counts are `0`.

- [ ] **Step 10: Commit Act I**

```bash
git add tests/test_technical_report_narrative.py \
  technical_report/main.tex \
  technical_report/sections/01_executive_thesis.tex \
  technical_report/sections/02_dense_intelligence.tex \
  technical_report/sections/03_episodic_agents.tex \
  technical_report/sections/01_executive_summary.tex \
  technical_report/sections/02_problem.tex \
  technical_report/argus-technical-report.pdf
git commit -m "docs(report): establish dense-intelligence thesis" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 3: Rewrite Act II — The Machine That Turns Work into Capability

**Files:**
- Modify: `tests/test_technical_report_narrative.py`
- Modify: `technical_report/main.tex`
- Create: `technical_report/sections/04_argus_life.tex`
- Create: `technical_report/sections/05_roles_planes.tex`
- Create: `technical_report/sections/06_lifecycle_state.tex`
- Create: `technical_report/sections/07_evidence_review.tex`
- Create: `technical_report/sections/08_reliability_resources.tex`
- Delete after migration: `03_architecture.tex`, `04_runtime_lifecycle.tex`, `05_state_memory.tex`, `06_roles.tex`, `07_execution_resources.tex`, `08_reliability.tex`, `09_evaluation_evidence.tex`, `12_workbench.tex`, `13_deployment_security.tex`

**Interfaces:**
- Consumes: Act I requirements, committed Argus code at the implementation base, `system_planes.pdf`, `mission_lifecycle.pdf`.
- Produces: labels `sec:life`, `sec:rolesplanes`, `sec:lifecycle`, `sec:evidence`, `sec:operations`; complete source-grounded implementation story.

- [ ] **Step 1: Add failing Act-II source contracts**

Append:

```python
def test_act_two_sections_are_wired() -> None:
    main = _read("technical_report/main.tex")
    for section in (
        "04_argus_life",
        "05_roles_planes",
        "06_lifecycle_state",
        "07_evidence_review",
        "08_reliability_resources",
    ):
        assert rf"\input{{sections/{section}}}" in main


def test_act_two_preserves_committed_runtime_facts() -> None:
    source = "\n".join(
        _read(f"technical_report/sections/{name}.tex")
        for name in (
            "04_argus_life",
            "05_roles_planes",
            "06_lifecycle_state",
            "07_evidence_review",
            "08_reliability_resources",
        )
    )
    for required in (
        "Manager",
        "Planner",
        "Engineer",
        "Reviewer",
        "control plane",
        "execution plane",
        "evidence plane",
        "checkpoint.json",
        "1.5 million",
        "1,800",
        "112",
        "75",
        "artifact-digest",
    ):
        assert required in source
    assert "CHECKPOINT.md" not in source
    assert "fresh session every round" not in source
```

- [ ] **Step 2: Run the Act-II tests and confirm failure**

```bash
pytest -q \
  tests/test_technical_report_narrative.py::test_act_two_sections_are_wired \
  tests/test_technical_report_narrative.py::test_act_two_preserves_committed_runtime_facts
```

Expected: FAIL because the five files and inputs do not exist.

- [ ] **Step 3: Write `04_argus_life.tex`**

Use `\section{Argus Life and the Technical Spine}` and:

- Map each master-spine stage to `LifeSupervisor`, `SkillLoop`, the Reviewer, evidence persistence, and skill/wiki update paths.
- State that Life keeps the lifetime objective moving while missions remain bounded units.
- Include a table `Master-spine concept | Implemented owner | Durable output`.
- Explain that runtime evolution can be automatic, model-proposed, Reviewer-authored, or operator-invoked; do not flatten those ownership differences.

- [ ] **Step 4: Write `05_roles_planes.tex`**

Use `\section{Four Roles, Three Planes, One Research Runtime}` and retain:

- Manager front door and stage authority.
- Planner task DAG and project-level verdict.
- Engineer real work and backend contract.
- Reviewer sole completion authority and `progress_class`.
- Control/execution/evidence plane table.
- Existing role-interface tables, abridged to fields that matter for the narrative.
- `system_planes.pdf` as the secondary implementation figure.

- [ ] **Step 5: Write `06_lifecycle_state.tex`**

Required content:

- Backlog claim and mission outcome flow.
- `mission_lifecycle.pdf`.
- Persisted identity and safe resume.
- `checkpoint.json` location, Reviewer authorship, hard caps, and atomic rewrite.
- Separate resumable Engineer and Reviewer sessions.
- Default three-round / prior 1.5-million-input-token rollover.
- `replan_requested` as control transfer, not successful completion.
- State table with canonical event tape, backlog, continuous state, inbox, checkpoint, daemon PID/status.

- [ ] **Step 6: Write `07_evidence_review.tex`**

Required content:

- 112 event types / 11 categories / 75 payload schemas, computed from current committed code during implementation.
- Reviewer execution-log audit.
- `call_id` correlation.
- Benchmark-specific measurement integrity and randomized inputs where appropriate.
- Credential redaction before downstream persistence.
- `website_snapshot` versus committed external-project artifact-digest metadata.
- No cryptographic result-signing claim.
- Reviewer completion authority and its single-authority limitation.

- [ ] **Step 7: Write `08_reliability_resources.tex`**

Consolidate without dropping:

- Decision-progress classes and two-nondecision-round termination.
- 1,800-second safe-boundary decision budget.
- 3,600-second in-call liveness limits.
- Backend retry/backoff and paused statuses.
- Background-job supervision and cadence wait.
- Budget reservation/reconciliation.
- GPU lease as explicit operator/agent-invoked tooling, not automatic scheduling.
- TUI/Web cockpit, authenticated command surface, deployment, and drain-to-boundary replacement.

- [ ] **Step 8: Add the Act-II opener and replace old inputs**

Before Section 4:

```tex
\actopener
  {ACT II · THE MACHINE THAT TURNS WORK INTO CAPABILITY}
  {Continuity requires an institution, not a longer prompt.}
  {Argus organizes persistent objectives, bounded role contexts, real execution, independent review, and durable evidence into one research runtime.}
```

Replace old implementation-section inputs with the five new files. Delete the nine superseded files listed above only after the new files contain their required facts.

- [ ] **Step 9: Run source contracts and build**

```bash
pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
make -C technical_report clean all
grep -Ec 'Overfull \\[hv]box' technical_report/main.log
grep -Eic 'undefined references|undefined citations|citation.*undefined|reference.*undefined' technical_report/main.log
```

Expected: tests PASS, build exits 0, both counts `0`.

- [ ] **Step 10: Commit Act II**

```bash
git add tests/test_technical_report_narrative.py technical_report
git commit -m "docs(report): explain the capability-building runtime" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 4: Write Act III — Online Runtime Evolution

**Files:**
- Modify: `tests/test_technical_report_narrative.py`
- Modify: `technical_report/main.tex`
- Create: `technical_report/sections/09_runtime_evolution.tex`
- Create: `technical_report/sections/10_process_data.tex`
- Create: `technical_report/sections/11_ood_expansion.tex`

**Interfaces:**
- Consumes: checkpoint, skills, wiki, tools, verifiers, routing, evaluation facts established in Act II.
- Produces: definitions of `H_t`, `tau_t`, `E_t`, fixed `theta`, process-data tuple, and qualified capability set `C_t`.

- [ ] **Step 1: Add failing formula and claims-discipline tests**

Append:

```python
def test_runtime_evolution_formula_fixes_model_parameters() -> None:
    source = _read("technical_report/sections/09_runtime_evolution.tex")

    assert r"H_{t+1}" in source
    assert r"U(H_t,\tau_t,E_t)" in source
    assert r"\theta_{t+1}=\theta_t" in source
    for symbol in ("M_t", "S_t", "A_t", "V_t", "R_t", "Q_t"):
        assert symbol in source
    assert "online parameter training" in source


def test_process_data_strictly_contains_final_artifact() -> None:
    source = _read("technical_report/sections/10_process_data.tex")

    assert r"D_{\mathrm{process}}" in source
    assert r"D_{\mathrm{final}}" in source
    assert "states, actions, evidence, feedback" in source


def test_ood_expansion_has_non_monotone_caveat() -> None:
    source = _read("technical_report/sections/11_ood_expansion.tex")

    assert r"C_{t+1}" in source
    assert r"\operatorname{Verify}(c,E_t)" in source
    assert "does not guarantee" in source
    assert "monotone" in source
    assert "negative result" in source
```

- [ ] **Step 2: Run the new tests and confirm failure**

```bash
pytest -q \
  tests/test_technical_report_narrative.py::test_runtime_evolution_formula_fixes_model_parameters \
  tests/test_technical_report_narrative.py::test_process_data_strictly_contains_final_artifact \
  tests/test_technical_report_narrative.py::test_ood_expansion_has_non_monotone_caveat
```

Expected: FAIL because Sections 9–11 do not exist.

- [ ] **Step 3: Write `09_runtime_evolution.tex`**

Include:

```tex
\begin{equation}
H_t = \{M_t,S_t,A_t,V_t,R_t,Q_t\},
\qquad
H_{t+1}=U(H_t,\tau_t,E_t),
\qquad
\theta_{t+1}=\theta_t .
\end{equation}
```

Define:

- `M`: memory.
- `S`: skills.
- `A`: available tools/procedures.
- `V`: verifiers/evidence procedures.
- `R`: routing and role configuration.
- `Q`: evaluations/task definitions.
- `tau`: process trajectory.
- `E`: accepted evidence.

Add an ownership table with `state component | proposing role | accepting owner | persistence surface`. State explicitly that not every component is automatically rewritten every run and that runtime evolution is not online parameter training.

- [ ] **Step 4: Write `10_process_data.tex`**

Include:

```tex
\begin{equation}
D_{\mathrm{process}}
= \{(s_k,a_k,e_k,r_k,\Delta H_k)\}_{k=1}^{N}
\supsetneq
D_{\mathrm{final}}=\{y^\star\}.
\end{equation}
```

Explain states, actions, evidence, review feedback, runtime-state deltas, failed branches, and counterexamples. Ground each storage mechanism in events, checkpoint, skills, and wiki. Do not imply that raw private reasoning traces are published.

- [ ] **Step 5: Write `11_ood_expansion.tex`**

Include:

```tex
\begin{equation}
C_{t+1}
= C_t \cup
\{c:\operatorname{Verify}(c,E_t)\ge\epsilon\}.
\end{equation}
```

State:

- The equation models retained verified additions.
- It does not guarantee success, practical monotonicity, or permanent retention.
- Skills/pages can be revised, archived, or retired.
- Negative results and NO-GO decisions may reduce repeated search cost.
- Breadth across domains and depth within domains are distinct expansion modes.

- [ ] **Step 6: Add the Act-III opener and inputs**

```tex
\actopener
  {ACT III · ONLINE RUNTIME EVOLUTION}
  {Every run becomes capability—or evidence about what not to repeat.}
  {Argus can update the operating state around a fixed model: memory, skills, tools, verifiers, routing, and evaluations.}

\input{sections/09_runtime_evolution}
\input{sections/10_process_data}
\input{sections/11_ood_expansion}
```

- [ ] **Step 7: Run tests and build**

```bash
pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
make -C technical_report clean all
grep -Ec 'Overfull \\[hv]box' technical_report/main.log
grep -Eic 'undefined references|undefined citations|citation.*undefined|reference.*undefined' technical_report/main.log
```

Expected: all tests PASS, build exits 0, both counts `0`.

- [ ] **Step 8: Commit Act III**

```bash
git add tests/test_technical_report_narrative.py technical_report
git commit -m "docs(report): formalize online runtime evolution" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 5: Complete Act IV, Appendices, and Final Thirteen-Chapter Structure

**Files:**
- Modify: `tests/test_technical_report_narrative.py`
- Modify: `technical_report/main.tex`
- Create: `technical_report/sections/12_frontier_evidence.tex`
- Create: `technical_report/sections/13_limitations_roadmap.tex`
- Modify: `technical_report/sections/90_appendix.tex`
- Delete after migration: `10_results.tex`, `11_portfolio.tex`, `14_limitations.tex`, `15_roadmap.tex`

**Interfaces:**
- Consumes: website-results evidence, paper inventory, `public_results.pdf`, `paper_portfolio.pdf`, all prior act labels.
- Produces: final exact thirteen-section order, result/portfolio proof points, notation appendix, 28–30 page candidate.

- [ ] **Step 1: Add failing final-structure and evidence contracts**

Append:

```python
FINAL_SECTION_INPUTS = (
    "01_executive_thesis",
    "02_dense_intelligence",
    "03_episodic_agents",
    "04_argus_life",
    "05_roles_planes",
    "06_lifecycle_state",
    "07_evidence_review",
    "08_reliability_resources",
    "09_runtime_evolution",
    "10_process_data",
    "11_ood_expansion",
    "12_frontier_evidence",
    "13_limitations_roadmap",
)


def test_report_has_exactly_thirteen_main_inputs() -> None:
    main = _read("technical_report/main.tex")
    inputs = re.findall(r"\\input\{sections/([0-9]{2}_[^}]+)\}", main)
    assert tuple(inputs[:13]) == FINAL_SECTION_INPUTS
    assert len([item for item in inputs if not item.startswith("90_")]) == 13


def test_frontier_evidence_preserves_public_values() -> None:
    source = _read("technical_report/sections/12_frontier_evidence.tex")
    for value in (
        "Global \\#6",
        "0.9636",
        "0.9855",
        "79.77",
        "63/82",
        "76.8\\%",
        "28.0",
        "41",
        "35 manuscripts",
        "6 drafts",
    ):
        assert value in source
    assert "accepted papers" in source
    assert "does not claim" in source


def test_appendix_defines_all_formal_symbols() -> None:
    source = _read("technical_report/sections/90_appendix.tex")
    for symbol in (
        r"\rho_{\mathrm{DI}}",
        r"\lambda",
        r"\eta_d",
        r"\eta_x",
        r"\eta_v",
        "H_t",
        r"\tau_t",
        "E_t",
        r"\theta_t",
        "C_t",
        r"\epsilon",
    ):
        assert symbol in source
```

- [ ] **Step 2: Run final-structure tests and confirm failure**

```bash
pytest -q \
  tests/test_technical_report_narrative.py::test_report_has_exactly_thirteen_main_inputs \
  tests/test_technical_report_narrative.py::test_frontier_evidence_preserves_public_values \
  tests/test_technical_report_narrative.py::test_appendix_defines_all_formal_symbols
```

Expected: FAIL because Sections 12–13 and the final input order do not exist.

- [ ] **Step 3: Write `12_frontier_evidence.tex`**

Structure:

1. `What the evidence is for`: proof points for breadth/depth, not universal capability.
2. `Six benchmark arenas`: preserve current table and `public_results.pdf`.
3. `Reading evidence status`: two artifact-digest records, four website snapshots.
4. `Six research programs`: preserve current program table and `paper_portfolio.pdf`.
5. `What 41 means`: 35 manuscripts + 6 drafts, duplicate versions removed, no acceptance claim.
6. `Connection to the frontier thesis`: show which results indicate depth within a domain and which indicate breadth across task classes, without inventing a scalar frontier score.

Do not alter `website_results.json` or `paper_inventory.json`.

- [ ] **Step 4: Write `13_limitations_roadmap.tex`**

Required limitations:

- Underlying model quality ceiling.
- Single fallible Reviewer authority.
- Two digest-correlated results versus four snapshots.
- External artifact bytes not committed here.
- Benchmark-specific integrity.
- Cost and compute.
- GPU lease explicit, not automatic.
- Runtime-state updates can be wrong, stale, revised, or retired.
- No guarantee of monotone capability expansion.

Roadmap must connect each limitation to a checkable objective and conclude:

```tex
Argus is designed to make research intelligence denser in time, research
experience more reusable, and the frontier of tractable work wider. The claim
remains conditional on evidence: no run, capability, or number outruns the
protocol that supports it.
```

- [ ] **Step 5: Add the Act-IV opener and final inputs**

```tex
\actopener
  {ACT IV · EVIDENCE FROM THE FRONTIER}
  {A grand thesis is only as strong as its proof points.}
  {Six public arenas and six research programs show where Argus has produced scoped evidence—and where the evidence remains incomplete.}

\input{sections/12_frontier_evidence}
\input{sections/13_limitations_roadmap}
\appendix
\input{sections/90_appendix}
```

Remove every superseded old input and delete the four migrated files.

- [ ] **Step 6: Add the notation appendix**

At the start of `90_appendix.tex`, add a notation table:

```tex
\section{Formal Notation}
\begin{tabularx}{\linewidth}{@{}p{2.4cm} X@{}}
\toprule
\textbf{Symbol} & \textbf{Meaning} \\
\midrule
$\rho_{\mathrm{DI}}(T)$ & explanatory dense-intelligence density over wall-clock horizon $T$ \\
$\lambda(t)$ & rate of relevant reasoning and tool-mediated research work \\
$\eta_d,\eta_x,\eta_v$ & decision, execution, and verification effectiveness factors \\
$H_t$ & runtime capability state $\{M_t,S_t,A_t,V_t,R_t,Q_t\}$ \\
$\tau_t,E_t$ & process trajectory and accepted evidence from run $t$ \\
$\theta_t$ & underlying model parameters \\
$C_t$ & retained verified capability set \\
$\epsilon$ & evidence threshold in the explanatory capability-set model \\
\bottomrule
\end{tabularx}
```

Keep the complete interfaces, statuses including `replan_requested`, persisted state, 112/11/75 event taxonomy, source map, results, and portfolio summaries.

- [ ] **Step 7: Run the full narrative and figure tests**

```bash
pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
```

Expected: all tests PASS.

- [ ] **Step 8: Build and enforce the report envelope**

```bash
make -C technical_report clean all
pages=$(pdfinfo technical_report/argus-technical-report.pdf | awk '/^Pages:/ {print $2}')
overfull=$(grep -Ec 'Overfull \\[hv]box' technical_report/main.log || true)
undefined=$(grep -Eic 'undefined references|undefined citations|citation.*undefined|reference.*undefined' technical_report/main.log || true)
test "$pages" -ge 28
test "$pages" -le 30
test "$overfull" = 0
test "$undefined" = 0
```

Expected: every test exits 0.

- [ ] **Step 9: Commit the completed report structure**

```bash
git add tests/test_technical_report_narrative.py technical_report
git commit -m "docs(report): complete the expanding-frontier whitepaper" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 6: Rewrite the English and Chinese GitHub Landing Pages

**Files:**
- Modify: `tests/test_technical_report_narrative.py`
- Modify: `README.md`
- Modify: `README.zh-CN.md`

**Interfaces:**
- Consumes: master-spine PNG, final report identity, public evidence JSON, final report link.
- Produces: six-layer English and Chinese landing pages with the same causal spine and unchanged proof points.

- [ ] **Step 1: Add failing README narrative tests**

Append:

```python
def _prose_word_count(text: str) -> int:
    text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", "", text)
    text = "\n".join(line for line in text.splitlines() if not line.startswith("|"))
    return len(re.findall(r"\b[\w×·#.–]+\b", text))


def test_english_readme_uses_expanding_frontier_spine() -> None:
    readme = _read("README.md")

    assert "Every run expands the frontier." in readme
    assert "Dense Intelligence" in readme
    assert "Runtime Evolution" in readme
    assert "master_spine.png" in readme
    assert "Technical Report 0.3" in readme
    assert 1200 <= _prose_word_count(readme) <= 1600


def test_readmes_preserve_public_proof_points() -> None:
    english = _read("README.md")
    chinese = _read("README.zh-CN.md")
    for value in (
        "Global #6",
        "0.9636",
        "0.9855",
        "79.77",
        "63/82",
        "76.8%",
        "28.0",
        "41",
    ):
        assert value in english
        assert value in chinese
    assert "35 manuscripts" in english
    assert "6 drafts" in english
    assert "35 篇 manuscript" in chinese
    assert "6 篇 draft" in chinese


def test_readmes_bound_runtime_evolution_claim() -> None:
    english = _read("README.md")
    chinese = _read("README.zh-CN.md")

    assert "does not require online parameter training" in english
    assert "does not guarantee that every run adds capability" in english
    assert "不依赖在线参数训练" in chinese
    assert "不保证每次 run 都增加能力" in chinese
```

- [ ] **Step 2: Run README tests and confirm failure**

```bash
pytest -q \
  tests/test_technical_report_narrative.py::test_english_readme_uses_expanding_frontier_spine \
  tests/test_technical_report_narrative.py::test_readmes_preserve_public_proof_points \
  tests/test_technical_report_narrative.py::test_readmes_bound_runtime_evolution_claim
```

Expected: FAIL on the new hero, master figure, report version, and claims-boundary sentences.

- [ ] **Step 3: Rewrite the English README in six layers**

Use this exact section order:

```markdown
# Argus: Autonomous Research Generation and Understanding System

> **Every run expands the frontier.**

[master_spine.png]

## Dense Intelligence for Long-Horizon Research
## From Work to Evidence to Runtime Evolution
## Evidence from the Frontier
## How Argus Makes the Loop Real
## Quick Start
## Technical Report, Limitations, and Provenance
```

Requirements:

- Hero paragraph states the complete causal spine in no more than 90 words.
- Aim for 1,350–1,450 English prose words so both the pytest tokenizer and the
  shell release check remain comfortably inside the 1,200–1,600 acceptance band.
- Explain `rho_DI` conceptually without making the README equation-heavy.
- State that runtime evolution does not require online parameter training.
- State that the design does not guarantee every run adds capability.
- Preserve the six-row result table and evidence status.
- Preserve 41 = 35 + 6 and no-acceptance wording.
- Compress four roles and three planes to one table.
- Keep installation, machine-policy prompt, daemon launch, supported backends, limitations, and report link.
- Remove extended API/event/status detail now covered by the report.

- [ ] **Step 4: Rewrite the Chinese README with matching structure**

Use the same six sections and the exact hero:

```markdown
> **每一次 run，都在拓展下一次研究的边界。**
```

Use `持续密集智能` as the primary translation of Dense Intelligence. Preserve all numeric values and evidence qualifications. Include:

```text
运行时能力演化不依赖在线参数训练。
这一设计不保证每次 run 都增加能力；失败、负结果和 NO-GO 也可能只减少未来重复搜索。
```

- [ ] **Step 5: Run README and full documentation tests**

```bash
pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
```

Expected: all tests PASS.

- [ ] **Step 6: Verify links and word envelope**

```bash
for path in \
  technical_report/figures/master_spine.png \
  technical_report/evidence/website_results.json \
  technical_report/evidence/paper_inventory.json \
  technical_report/argus-technical-report.pdf \
  docs/API_CONFIG.md; do
  test -e "$path"
done

words=$(sed '/^```/,/^```/d; /<[^>]*>/d; /^|/d' README.md | wc -w)
test "$words" -ge 1200
test "$words" -le 1600
git diff --check -- README.md README.zh-CN.md
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the bilingual landing pages**

```bash
git add README.md README.zh-CN.md tests/test_technical_report_narrative.py
git commit -m "docs: tell the expanding-frontier Argus story" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

---

### Task 7: Run the Complete Build, Reproducibility, and Visual Audit

**Files:**
- Modify only if verification finds defects: report/README source and generated figures.
- Regenerate: `technical_report/argus-technical-report.pdf`
- Create no committed audit scratch files.

**Interfaces:**
- Consumes: complete report and README candidate.
- Produces: clean verified commit with deterministic figures and page-by-page visual acceptance.

- [ ] **Step 1: Run every focused documentation test**

```bash
pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
```

Expected: all tests PASS with no warnings or errors.

- [ ] **Step 2: Regenerate figures twice and compare all hashes**

```bash
audit=.superpowers/audit/grand-narrative
mkdir -p "$audit"
python technical_report/figures/build_report_figures.py
sha256sum technical_report/figures/{master_spine,dense_intelligence,system_planes,mission_lifecycle,public_results,paper_portfolio}.{pdf,png} technical_report/figures/REPORT_FIGURES.json > "$audit/full-hash-1"
python technical_report/figures/build_report_figures.py
sha256sum technical_report/figures/{master_spine,dense_intelligence,system_planes,mission_lifecycle,public_results,paper_portfolio}.{pdf,png} technical_report/figures/REPORT_FIGURES.json > "$audit/full-hash-2"
diff -u "$audit/full-hash-1" "$audit/full-hash-2"
```

Expected: no diff.

- [ ] **Step 3: Build the report from a clean LaTeX state**

```bash
make -C technical_report clean all
pages=$(pdfinfo technical_report/argus-technical-report.pdf | awk '/^Pages:/ {print $2}')
overfull=$(grep -Ec 'Overfull \\[hv]box' technical_report/main.log || true)
undefined=$(grep -Eic 'undefined references|undefined citations|citation.*undefined|reference.*undefined' technical_report/main.log || true)
test "$pages" -ge 28
test "$pages" -le 30
test "$overfull" = 0
test "$undefined" = 0
```

Expected: all checks exit 0.

- [ ] **Step 4: Verify identity, formulas, results, and terminology in extracted PDF text**

```bash
audit=.superpowers/audit/grand-narrative
mkdir -p "$audit"
pdftotext technical_report/argus-technical-report.pdf "$audit/report.txt"
grep -q 'Technical Report 0.3' "$audit/report.txt"
grep -q 'Dense Intelligence for an Expanding Research Frontier' "$audit/report.txt"
grep -q 'Every run expands the frontier' "$audit/report.txt"
grep -q 'model parameters remain fixed' "$audit/report.txt"
grep -q 'Global #6' "$audit/report.txt"
grep -q '0.9636' "$audit/report.txt"
grep -q '0.9855' "$audit/report.txt"
grep -q '79.77' "$audit/report.txt"
grep -q '63/82' "$audit/report.txt"
grep -q '76.8%' "$audit/report.txt"
grep -q '28.0' "$audit/report.txt"
grep -q '41 papers' "$audit/report.txt"
! grep -Eiq 'dumb pipe|plumbing|not smarter than' "$audit/report.txt"
```

Expected: every positive grep succeeds and the negative grep finds nothing.

- [ ] **Step 5: Render and inspect every page**

```bash
audit=.superpowers/audit/grand-narrative
rm -rf "$audit/pages"
mkdir -p "$audit/pages"
pdftoppm -r 120 -png technical_report/argus-technical-report.pdf "$audit/pages/page"
ls "$audit"/pages/page-*.png
```

Inspect all pages. Reject:

- clipped or overlapping text;
- unreadable formulas;
- low-contrast act titles;
- dark cover;
- figures with labels smaller than surrounding body text at normal zoom;
- tables split without readable headers;
- inconsistent blue/gold semantics;
- accidental marketing claims not supported in source.

For every defect, edit the source, rerun Steps 1–5, and do not hand-edit generated PDF files.

- [ ] **Step 6: Validate manifest hashes against committed files**

```bash
python - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path("technical_report/figures")
manifest = json.loads((root / "REPORT_FIGURES.json").read_text(encoding="utf-8"))
for name, info in manifest["figures"].items():
    for kind in ("pdf", "png"):
        actual = hashlib.sha256((root / info[kind]).read_bytes()).hexdigest()
        assert actual == info[f"{kind}_sha256"], (name, kind)
print("all deterministic figure hashes match")
PY
```

Expected: `all deterministic figure hashes match`.

- [ ] **Step 7: Commit build/audit fixes if any**

If the previous steps changed files:

```bash
git add README.md README.zh-CN.md technical_report tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
git commit -m "docs(report): close build and visual audit" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

If the worktree is clean, do not create an empty commit.

---

### Task 8: Run Independent Systems and Claims Reviews

**Files:**
- Read-only review first.
- Modify only the exact source/generated files implicated by Critical or Important findings.

**Interfaces:**
- Consumes: final candidate commit and the approved spec.
- Produces: two independent APPROVED verdicts with zero unresolved Critical/Important findings.

- [ ] **Step 1: Generate a review package**

Locate the first implementation commit by its Task 1 commit message, then use
its parent as base and current `HEAD` as head:

```bash
first=$(git log --format=%H --reverse --grep='^docs(report): add expanding-frontier figure system$' | head -1)
test -n "$first"
base=$(git rev-parse "${first}^")
head=$(git rev-parse HEAD)
/home/argustest/.copilot/installed-plugins/superpowers-marketplace/superpowers/skills/subagent-driven-development/scripts/review-package "$base" "$head"
```

Record the emitted package path.

- [ ] **Step 2: Dispatch the ML-systems reviewer**

Give a fresh high-capability reviewer:

- the approved spec path;
- review package path;
- committed head;
- instruction to validate against committed source, never dirty working-tree state;
- the dense-intelligence and runtime-evolution formulas;
- every architecture, state, session, checkpoint, budget, event, auth, deployment, GPU, skill/wiki, and capability-expansion claim.

Required output: `APPROVED` or `CHANGES_REQUIRED`, with only Critical/Important findings and exact sources.

- [ ] **Step 3: Dispatch the claims/citations/visual reviewer independently**

Require validation of:

- six result values/protocols/evidence statuses;
- 41/35/6 and six program counts;
- no paper-acceptance claim;
- formula definitions and caveats;
- every figure hash and visible label;
- light cover and every PDF page;
- 28–30 pages;
- no overfull/undefined;
- README links and word envelope;
- banned-term constraints.

Required output: `APPROVED` or `CHANGES_REQUIRED`.

- [ ] **Step 4: Fix every Critical/Important finding with regression coverage**

For each reproducible defect:

1. Add a focused failing assertion to `tests/test_technical_report_narrative.py` or `tests/test_technical_report_figures.py`.
2. Run that assertion and observe the intended failure.
3. Edit the source.
4. Regenerate affected figures/PDF.
5. Run both focused test files and the full build checks.
6. Commit:

```bash
git add README.md README.zh-CN.md technical_report tests
git commit -m "docs(report): close independent review findings" \
  -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>" \
  -m "Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b"
```

- [ ] **Step 5: Re-review fixes**

Regenerate the review package and require both reviewers to return `APPROVED`. Do not proceed with a reviewer still reporting a Critical or Important issue.

---

### Task 9: Verify, Integrate, and Push `origin/main`

**Files:**
- No new source files expected.
- Do not touch unrelated changes in the shared main checkout.

**Interfaces:**
- Consumes: approved clean implementation branch.
- Produces: fast-forward `origin/main` at the verified report commit.

- [ ] **Step 1: Invoke verification-before-completion**

Invoke `superpowers:verification-before-completion` before any completion claim or push.

- [ ] **Step 2: Run the complete release gate from the clean worktree**

```bash
set -euo pipefail
test -z "$(git status --porcelain)"
git fetch origin --quiet
git merge-base --is-ancestor origin/main HEAD
pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
python technical_report/figures/build_report_figures.py
make -C technical_report clean all
pages=$(pdfinfo technical_report/argus-technical-report.pdf | awk '/^Pages:/ {print $2}')
overfull=$(grep -Ec 'Overfull \\[hv]box' technical_report/main.log || true)
undefined=$(grep -Eic 'undefined references|undefined citations|citation.*undefined|reference.*undefined' technical_report/main.log || true)
test "$pages" -ge 28
test "$pages" -le 30
test "$overfull" = 0
test "$undefined" = 0
```

If the LaTeX rebuild changes only PDF binary metadata, verify its semantic output, then restore the committed PDF before checking cleanliness. Any source, figure, or manifest drift is a failure.

- [ ] **Step 3: Recheck evidence values directly**

```bash
python - <<'PY'
import json
from pathlib import Path

results = json.loads(Path("technical_report/evidence/website_results.json").read_text())
assert results["results_count"] == 6
assert [row["result"] for row in results["results"]] == [
    "Global #6 · 2× #1 · 7 top-3",
    "0.9636 BPB",
    "0.9855 BPB",
    "79.77 seconds",
    "63/82 · 76.8%",
    "28.0 gap",
]
papers = json.loads(Path("technical_report/evidence/paper_inventory.json").read_text())
assert papers["totals"] == {
    "papers": 41,
    "manuscript": 35,
    "draft": 6,
    "programs": 6,
}
assert sum(papers["program_counts"].values()) == 41
print("public evidence values match")
PY
```

Expected: `public evidence values match`.

- [ ] **Step 4: Push the clean branch directly as a fast-forward**

The shared `main` checkout may contain unrelated staged or unstaged work. Do not reset, stash, unstage, or commit it. From the isolated worktree:

```bash
git push origin HEAD:main
remote=$(git ls-remote origin refs/heads/main | awk '{print $1}')
test "$remote" = "$(git rev-parse HEAD)"
```

Expected: fast-forward push succeeds; remote SHA equals local `HEAD`.

- [ ] **Step 5: Report the exact release**

Report:

- final commit SHA;
- Technical Report 0.3 path;
- page count;
- README word count;
- four-act/thirteen-chapter structure;
- master-spine and dense-intelligence figures;
- both independent reviewer verdicts;
- unchanged six-result and 41-paper evidence.
