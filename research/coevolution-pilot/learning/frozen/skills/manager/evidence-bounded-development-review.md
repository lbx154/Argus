---
name: "Evidence-bounded development artifact review"
description: "Review learned Skills and Wikis without confusing same-task validation with real-task transfer, while preserving provenance, ownership, scope, and uncertainty."
---

---
name: Evidence-bounded development artifact review
description: Review learned Skills and Wikis without confusing same-task validation with real-task transfer, while preserving provenance, ownership, scope, and uncertainty.
---

## Procedure

1. Inventory only the supplied artifacts. Classify each as a **Skill** (reusable procedure) or **Wiki** (verified fact, contract, source limitation, or environment constraint), and preserve ownership.
2. Build an evidence map before judging: connect each claim to source text, runtime code, activation metadata, artifact inspection, or test measurement. Mark referenced-but-unavailable observations as uncertainty rather than reconstructing them.
3. Separate evidence levels:
   - **Structural validation:** schemas, files, metadata, formulas, or tests were checked.
   - **Real-task use:** the artifact was used in the intended application or workflow and the result was observed.
   - **Transfer:** distinct, previously unseen work benefited.
   Never infer a higher level from a lower one.
4. Score each existing document for specificity, evidence, transferability, and limitations. Give concrete defects, including hidden assumptions, unstable APIs, stale source paths, missing version metadata, unsupported edge cases, and policy statements presented as facts.
5. Keep scope explicit. Same-task reruns can support correctness on that task, but do not establish unseen-task improvement. A skipped test remains an evidence gap even when all executed tests pass.
6. Do not rewrite Engineer-owned Skills during Manager review. Recommend targeted owner revisions. Store reusable review procedure in a Manager Skill; route runtime contracts and durable project facts to a Wiki.
7. Validate the final handoff structurally: required fields are present, paths are semantic, Markdown frontmatter is correct where required, citations do not invent locations, and uncertainties state what was not observed.

## Counterexample

A workbook reopens with openpyxl, contains pivot XML, and passes ZIP checks. That is structural validation. It is **not** evidence that desktop Excel refreshed the pivot without a repair warning, and it is not evidence that the method transfers to another workbook or spreadsheet application. Report the narrower result and request real-application evidence for the stronger claim.
