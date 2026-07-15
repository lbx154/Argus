# Task 2 Report — Rewrite the Cover and Act I (The Dense-Intelligence Problem)

**Status:** ✅ Complete. Technical Report 0.3 identity, light cover, `\actopener`, and
the full Act I (Executive Thesis, Dense Intelligence, Why Episodic Agents Stall) are
implemented and committed. The transitional report builds cleanly with zero
overfull/undefined boxes and all narrative + figure contracts pass.

**Baseline:** `a35192a5`  **Commit:** `9beca49d`  **Branch:** `copilot/pro-readme-sync`

---

## TDD: RED → GREEN

### Step 1–2: RED (before any production edit)
Created `tests/test_technical_report_narrative.py` verbatim from the brief, then ran:

```
$ pytest -q tests/test_technical_report_narrative.py
4 failed, 1 passed in 0.06s
FAILED ...::test_report_identity_is_dense_intelligence_03      # version + subtitle
FAILED ...::test_cover_is_light_blue_gold                      # palette colors
FAILED ...::test_act_one_sections_and_master_spine_are_wired   # new section inputs + master figure
FAILED ...::test_dense_intelligence_not_presented_as_measured_score  # rho_DI notation
```
Failures matched the brief's expectation exactly (version, subtitle, section inputs,
master figure, dense-intelligence notation). The one pass was the banned-rhetoric
check against the still-clean pre-existing sections.

### Step 9: GREEN (after implementation)
```
$ pytest tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
16 passed in 0.86s     # 5 narrative + 11 figure
```

Exact-match prose contracts independently reverified: opening thesis quote = MATCH,
qualification block = MATCH, `\rho_{\mathrm{DI}}(T)` equation = MATCH,
"explanatory construct, not a reported benchmark metric" present, five R-requirements
present, all four §2 and five §3 subsection headings present.

---

## LaTeX build / page / visual evidence

Final clean build **from the committed state**:
```
$ make -C technical_report clean all        → exit 0
$ grep -Ec 'Overfull \\[hv]box' main.log    → 0
$ grep -Eic 'undefined ...' main.log        → 0
Pages: 34   Title: Argus...   Subject: Technical Report 0.3 – Dense Intelligence...
```

Both hard gates (overfull = 0, undefined refs/citations = 0) pass. Remaining log
notes are informational only: 74 `Underfull` (whitespace from `[H]`-pinned floats),
7 benign `h`→`ht` float adjustments, and `T1/cmtt` bold-typewriter font-shape
substitutions — a direct, cosmetic consequence of the brief-mandated
`\bfseries\ttfamily` kicker (bold typewriter gracefully falls back to regular
typewriter in base Computer Modern). No reference/citation is undefined.

**Visual inspection** (rendered pages 1–9 at 100 dpi, reviewed, temp files removed):
- **Page 1 — Light cover:** bone-white page, gold `ARGUS` kicker, deep-blue
  left-aligned title, graphite subtitle "Dense Intelligence for an Expanding
  Research Frontier", centered `master_spine.pdf` (0.62), blue rule +
  "TECHNICAL REPORT 0.3 / JULY 2026" footer. No dark background.
- **Pages 2–3 — Abstract + TOC:** header reads "Technical Report 0.3"; TOC lists the
  new §1 Executive Thesis, §2 Dense Intelligence, §3 Why Episodic Agents Stall,
  then the kept sections renumbered §4…§16 + appendices.
- **Page 4 — Act I opener + §1:** gold kicker "ACT I · THE DENSE-INTELLIGENCE
  PROBLEM", deep-blue "Research intelligence is sparse in time.", graphite standfirst,
  blue rule; §1 bold opening quote; four-paragraph argument with the
  `H(t+1)=U(H(t),trajectory,evidence)` law.
- **Page 5 — §1 in exact brief order:** `master_spine.pdf` at 0.98 (Fig 1) →
  claims/does-not-claim table (Table 1) → evidence-summary paragraph.
- **Page 6 — §1 close + §2 open:** exact qualification callout; §2 designbox, §2.1
  Definition, `dense_intelligence.pdf` (Fig 2) placed right after its reference.
- **Page 7 — §2:** §2.2, §2.3 with equation (1) + five-symbol table (Table 2), §2.4
  with the explanatory-construct + "universally superior" disclaimer.
- **Pages 8–9 — §3:** all five subsections, R1–R5 requirements, and the
  "Act II turns these five requirements into a running system…" transition straight
  into §4 System Architecture.

---

## Files

**Created**
- `tests/test_technical_report_narrative.py` — report-identity + Act-I contracts (verbatim from brief).
- `technical_report/sections/01_executive_thesis.tex` — §1: exact opening quote, four-paragraph
  argument, `master_spine.pdf@0.98`, claims/does-not-claim table, evidence summary preserving all
  six-arena + 41-paper caveats, exact qualification callout.
- `technical_report/sections/02_dense_intelligence.tex` — §2: Definition / Continuity is not token
  volume / A conceptual density model / What is and is not measured; `dense_intelligence.pdf`,
  `\rho_{\mathrm{DI}}(T)` equation, five-symbol table, both explanatory sentences.
- `technical_report/sections/03_episodic_agents.tex` — §3: five subsections + R1–R5 requirements
  (persistent objective, role separation, bounded context, independent evidence, reusable runtime state)
  transitioning into Act II.

**Modified**
- `technical_report/main.tex` — Report 0.3 identity (subject/header/version), light blue+gold palette
  (`systemblue`, `deepblue`, `frontiergold`, `goldsoft`), light left-aligned cover, `\actopener` macro
  + ACT I opener, wired the three Act I inputs, `\usepackage{float}` (to pin Act I floats with `[H]`).
- `technical_report/argus-technical-report.pdf` — rebuilt (34 pp).

**Deleted (replaced by Act I)**
- `technical_report/sections/01_executive_summary.tex`
- `technical_report/sections/02_problem.tex`

**Modified — required reference-integrity fixes (see Concerns)**
- `technical_report/sections/03_architecture.tex`, `06_roles.tex`, `09_evaluation_evidence.tex` —
  repointed three now-orphaned `\ref{sec:problem}` targets to `\ref{sec:episodic}` (or dropped a
  locally-redundant parenthetical). Mandatory to satisfy the zero-undefined-references gate after
  deleting `02_problem.tex`.
- `technical_report/sections/05_state_memory.tex`, `06_roles.tex` — neutralized two visible
  `(O1)` tokens that referenced the deleted numbered-objective list (honesty: no dangling label in
  the rendered PDF). No build impact; preserves all facts.

---

## Commit
```
9beca49d docs(report): establish dense-intelligence thesis
         Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
         Copilot-Session: c1e64701-d91e-47f2-95f6-47da7176cf0b
```
12 files: 4 created (3 sections + 1 test), 2 deleted, 6 modified (main.tex + PDF + 4 kept sections).
Working tree clean; committed tree independently verified to build with 0/0 gates.

---

## Self-review

- **Contracts:** all 16 narrative+figure tests pass; every exact-string requirement
  (opening quote, qualification, ρ-equation, both dense sentences, four §2 + five §3
  subsections, R1–R5) verified present character-for-character.
- **Claims boundary preserved:** the six arena results carry exact protocol/hardware
  and the two-of-six artifact-digest vs. four-of-six website-snapshot distinction;
  the 41-paper portfolio is "35 manuscripts + 6 drafts across six programs, compared
  only to human-authored literature, a de-duplicated inventory, not accepted papers."
  Nothing is upgraded to a universal-SOTA or accepted-publication claim.
- **Honesty guards:** dense intelligence is stated as an *explanatory construct, not a
  reported benchmark metric*; the qualification explicitly denies monotone capability
  growth; no banned rhetoric (0 "guardrail", 0 "dumb pipe/plumbing/not smarter than").
- **Scope:** figure source (`build_report_figures.py`), `REPORT_FIGURES.json`, the two
  Task-1 figure PDFs, and all `evidence/*.json` are byte-identical to baseline
  (`git diff a35192a5` empty). No README, runtime, or website code touched.
- **Reading order:** Act I floats pinned with `[H]` so §1 renders figure → table →
  evidence → qualification exactly as the brief specifies (before pinning, the claims
  table floated into §2).

---

## Concerns

1. **Test vs. brief wording conflict (resolved in favor of the test).** The figure
   test asserts `"universal superiority" not in dense`, but the brief's Step 6 sample
   sentence contains that literal phrase. Tests are the hard contract, so the disclaimer
   is phrased "It does not, in itself, establish that Argus is **universally superior**
   to human researchers or other systems." — same calibrated meaning (scoped wins exist;
   no universal claim), test passes.

2. **Four kept sections edited beyond the brief's Step 10 add-list (necessary).**
   Deleting `02_problem.tex` orphaned three `\ref{sec:problem}` references in kept
   sections; the zero-undefined gate (Step 9) cannot pass without repointing them, so
   they were included in the commit. Two additional cosmetic `(O1)` tokens (no `\ref`,
   no build impact) were neutralized for honesty. These are minimal, surgical, and
   preserve all implementation facts — but they touch sections outside Act I. The
   repointed targets (`sec:episodic`) are the closest heirs of the removed content;
   the integrity/anti-cheat objective (old O5) is expected to be re-homed in a later
   Act, at which point these references can be re-aimed.

3. **Added `\usepackage{float}`.** One standard TeX Live package, added solely to pin
   the four Act I floats with `[H]` for the brief's required reading order. Low-risk,
   no effect on the rest of the document.

4. **Cosmetic-only log notes.** `T1/cmtt` bold-typewriter font-shape substitutions
   (bold kicker falls back to regular typewriter) and `[H]`-induced `Underfull`
   whitespace. Neither affects the two hard gates; both are inherent to the brief's
   mandated cover/actopener design. Not fixed to avoid a global font-package change.

---

## Review Fix (post-review corrections)

**Commit:** `750364bd`  **Base:** `9beca49d`  **Branch:** `copilot/pro-readme-sync`

### Findings addressed

1. **Important — stale 0.2 identity strings survived in committed sections.**
   `technical_report/sections/14_limitations.tex` still said "Technical Report 0.2"
   and `technical_report/sections/15_roadmap.tex` still said "version 0.2",
   contradicting the 0.3 identity established in Task 2.
2. **Minor, coupled to (1) — wrong integrity cross-reference.**
   `technical_report/sections/03_architecture.tex`'s "Evidence plane" paragraph
   pointed the run's integrity invariants at `Section~\ref{sec:episodic}` ("Why
   Episodic Agents Stall" — a problem-statement section about model vs. system
   capability), when the actual event-tape / credential-redaction / provenance
   implementation is discussed in `Section~\ref{sec:evidence}`
   ("Evaluation and Evidence Architecture", `09_evaluation_evidence.tex`).
3. **Test gap — the identity test only scanned `main.tex`.**
   `test_report_identity_is_dense_intelligence_03` never scanned
   `technical_report/sections/*.tex`, so the two 0.2 stragglers above were
   invisible to the test suite.

### TDD: RED → GREEN

**Step 1 — strengthen the test first.** Added `_all_report_source()` to
`tests/test_technical_report_narrative.py`, concatenating `main.tex` with every
`technical_report/sections/*.tex` (sorted glob), and rewrote the identity test to
assert both `"Technical Report 0.2" not in source` and `"version 0.2" not in
source` against that concatenation (kept the existing `main.tex`-only positive
assertions for 0.3 + subtitle unchanged).

**Step 2 — RED, confirmed against current (pre-fix) sources:**
```
$ python -m pytest -q tests/test_technical_report_narrative.py::test_report_identity_is_dense_intelligence_03
F
AssertionError: assert 'Technical Report 0.2' not in '...'
  'Technical Report 0.2' is contained here:
    opment at Technical Report 0.2. The architecture and
```
Failure matched the review finding exactly (14_limitations.tex's stale string),
confirming the strengthened test now catches what the old `main.tex`-only test missed.

**Step 3 — production fixes:**
- `14_limitations.tex`: "Technical Report 0.2" → "Technical Report 0.3".
- `15_roadmap.tex`: "version 0.2" → "version 0.3".
- `03_architecture.tex`: `Section~\ref{sec:episodic}` → `Section~\ref{sec:evidence}`
  in the evidence-plane integrity-invariants sentence — same wording preserved,
  only the cross-reference target changed; no facts altered.

**Step 4 — GREEN:**
```
$ python -m pytest -q tests/test_technical_report_narrative.py tests/test_technical_report_figures.py
................                                                         [100%]
16 passed
```

### Build evidence

```
$ make -C technical_report clean all     → exit 0, "Output written on main.pdf (34 pages, ...)"
$ grep -Ec 'Overfull \[hv]box' technical_report/main.log            → 0
$ grep -Eic 'undefined references|undefined citations|citation.*undefined|reference.*undefined' technical_report/main.log → 0 (grep exit 1 = no matches = count 0)
```
Both hard gates remain 0/0 after the fix; page count unchanged at 34.

### Files

**Modified**
- `tests/test_technical_report_narrative.py` — identity test now scans `main.tex`
  + all `technical_report/sections/*.tex` for both `Technical Report 0.2` and
  `version 0.2`, not just `main.tex`.
- `technical_report/sections/14_limitations.tex` — "Technical Report 0.2" → "0.3".
- `technical_report/sections/15_roadmap.tex` — "version 0.2" → "version 0.3".
- `technical_report/sections/03_architecture.tex` — integrity-invariants
  cross-reference repointed `sec:episodic` → `sec:evidence`.
- `technical_report/argus-technical-report.pdf` — rebuilt (34 pp, 0 overfull, 0 undefined).

### Commit

```
750364bd fix(report): remove stale 0.2 identity strings and fix integrity cross-ref
         Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>
```

### Scope note

No other files touched. Did not re-open or re-litigate the other Task 2 self-review
concerns (e.g., `sec:problem` repointing, `(O1)` token neutralization, `float`
package) — those were already resolved and are out of scope for this review-fix pass.
