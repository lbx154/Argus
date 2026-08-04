---
name: "Paper Review Revision Loop"
description: "Review and revise a paper from the reader's perspective, prioritizing scientific argument, natural prose, and the rendered paper over review paperwork."
---

# Paper Review Revision Loop

## When to use

Use when `paper/main.tex` exists and the paper needs scientific review, prose
revision, or layout repair.

## Review like a real reader

1. Read the title, abstract, introduction, main result, limitations, and conclusion
   straight through before opening process artifacts.
2. Identify the strongest accept argument and the few issues most likely to cause
   rejection: weak insight, unclear mechanism, unfair comparison, unsupported
   claim, missing uncertainty, poor positioning, or unreadable presentation.
3. Check disputed claims against raw results and primary literature. Do not rerun
   settled checks or create a reviewer-question inventory by default.
4. Rewrite paragraphs as prose, not as validator responses:
   - lead with the concrete problem and insight;
   - name the method, setting, comparator, and result where useful;
   - vary sentence structure naturally;
   - remove generic openings, repeated "not X but Y" constructions, compliance
     language, local paths, role names, and defensive caveat chains;
   - keep honest limitations without making process failure the paper's identity.
5. Fix figures and tables from the rendered PDF. One targeted aesthetic repair is
   enough unless a visible defect remains.
6. Compile and inspect the current PDF after meaningful source changes.

Model-backed language, infrastructure, and layout tools are optional second
opinions. Run one when the Reviewer has a concrete doubt; fix the manuscript rather
than editing generated review JSON.

## Stop or route back

- If the argument lacks evidence, return to experiments or analysis.
- If the evidence does not support a worthwhile thesis, return to research/plan.
- If the paper is sound, stop polishing minor preferences.

## Handoff

Describe the meaningful revision and any remaining reject-level issue naturally.
Do not generate `REVIEWER_QUESTIONS.json`, `PAPER_REVISION_LOG.md`, or an assurance
packet solely to prove that review occurred.
