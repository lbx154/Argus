---
name: "EMNLP Paper Drafting"
description: "Draft an ACL-style paper around one defensible insight, using authentic local evidence and the selected venue's official format."
---

# EMNLP Paper Drafting

## Purpose

Write a paper, not an experiment log. The manuscript must make one useful,
defensible argument to the target community. Evidence constrains the argument;
artifact completeness does not create publication value.

Use this skill only after the run and analysis stages have authentic,
claim-relevant evidence. Draft from the strongest honest conclusion available.
Negative, null, mixed, and baseline-losing results should be developed into a
boundary, mechanism, scaling, benchmark, or decision paper rather than routed
back merely because the original headline failed.

## 1. Pass the thesis gate before LaTeX

Read the research brief, experiment plan, canonical results, claim graph,
nearest prior work, and current venue profile. State in `research/NARRATIVE_REPORT.md`:

- the one-sentence thesis;
- why a reviewer should care;
- the non-trivial insight or capability;
- the strongest evidence;
- the strongest likely rejection argument and the evidence-based answer.

When the method does not win, do not present a chronological failure report.
First complete the positive-recovery loop over implementation, optimization, data,
scale, evaluator, and method repairs under the approved budget. Prefer a genuine
recovered positive result supported at a scale proportional to its claim; it need
not win on every seed, benchmark, or strongest baseline. If recovery remains
impossible after independent engineering-adequacy review, use the completed diagnosis to state what failed,
where, why, how reliably, and what design or deployment decision changes. The
paper need not prove the original optimistic hypothesis; it must explain a
truthful reusable finding.

## 2. Separate the audit trail from the paper story

Preserve all valid runs in canonical experiment artifacts. The manuscript is a
selective scientific argument:

- include every comparison needed to evaluate the thesis;
- disclose genuine contrary evidence that materially bears on the headline claim;
- keep misconfigured runs out of scientific results;
- place secondary diagnostics, dead ends, and exhaustive failure inventories in
  internal reports or the appendix when useful;
- remove unsupported claims rather than narrating every unsuccessful attempt.

Do not hide a failed claim-critical comparison. Do not foreground unrelated
negative results merely because they exist.

## 3. Build the narrative

Use the selected venue profile and official author kit. Let the thesis determine
section order; do not copy an exemplar skeleton or force a `Failure Cases`
section.

- **Title:** name the insight/capability, not the project's disappointment.
- **Abstract:** problem, gap, thesis, method, decisive evidence, implication.
- **Introduction:** establish importance and nearest gap, explain the key idea,
  preview the strongest evidence, and state contributions as reader value.
- **Related work:** compare against the nearest intellectual alternatives and
  end each group with the exact distinction.
- **Method/system/theory:** explain the mechanism deeply enough to reproduce and
  evaluate it.
- **Experiments:** test the thesis with strong baselines, controls, uncertainty,
  and fair budgets. Avoid protocol narration that does not help interpretation.
- **Results/analysis:** lead with the evidence that resolves the research
  question. Use ablations and failure analysis to explain why, not to dump rows.
- **Limitations:** bound external validity without undoing the central claim.
- **Conclusion:** state what the community can now understand or do.

Tables and figures must each carry a claim. Use the Research Visualization Router
and inspect rendered output at final size. Prefer a few high-information visuals
over an inventory of every metric.

## 4. ACL/EMNLP essentials

- Use the current official ACL style and anonymous review mode.
- Follow the selected track's actual page limit; do not pad to fill pages.
- Put references before appendices and include required Limitations/Ethics
  material for the selected cycle.
- Use verified, claim-complete citations; there is no universal bibliography
  count.
- Compile cleanly with resolved citations/references and no material overflow.
- Keep local paths, devices, routes, agent names, secrets, and authoring
  infrastructure out of manuscript prose.

Style exemplars are optional calibration aids. Study them when useful, but do not
create conformance JSON or lock the paper to another paper's section sequence.

## 5. Minimal deliverables

Maintain only artifacts that carry scientific or submission value:

- `paper/main.tex`, bibliography, figures/tables, and compiled PDF;
- canonical claim-to-evidence mapping and result sources;
- a short draft report naming the thesis, evidence gaps, compile state, and next
  substantive revision;
- venue-required submission material.

Do not create duplicate schema files, prose mirrors, suitability scores, or
checklist-shaped reports when an existing canonical artifact already answers the
question.

## 6. Final self-review

Before handoff, read the paper as a skeptical venue reviewer:

1. Can the thesis be stated in one sentence?
2. Is the strongest accept argument obvious by the end of page one?
3. Did the engineering earn the scientific conclusion?
4. Do the experiments test the insight rather than merely document activity?
5. Does any section weaken the core claim without adding explanatory value?
6. Would the paper remain useful if workflow/provenance details were removed?

If the strongest honest answer is still "the proposed idea did not work," return
to implementation, experiments, or analysis framing within the selected research
direction. Do not reopen idea selection or polish the experiment report into a
paper.

## Response shape

Report the thesis, manuscript/PDF paths, decisive evidence, and the single most
important remaining scientific weakness.
