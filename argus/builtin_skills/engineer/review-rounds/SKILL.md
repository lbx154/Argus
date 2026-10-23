---
name: "Working from a review"
description: "How the Engineer acts on the Reviewer's guidance, and what to do when a review asks for a value its own measurement contradicts."
---

# Working from a review

- Build on the verified progress and address the guidance before handing back.
  Final paper review can ask for high-impact improvements after the minimum bar
  is met: make and validate them in the current Review stage, without rolling
  back or waiting for an earlier-stage task, then return for review.
- For each actionable item, record the actual change and its decisive evidence
  in CHECKPOINT.md. If a suggested hypothesis fails, keep the result and give
  the evidence-backed alternative; the Reviewer decides whether that resolves
  the item.
- Never write a value your own labeled measurement contradicts just to satisfy
  a review: record the labeled evidence and its command in CHECKPOINT.md and
  hand back asking the Reviewer to resolve the discrepancy.
- If the review disputes text a display masks (`******`, `<REDACTED:…>`),
  restating the fix cannot help: add a rerunnable test asserting the behavior
  with a harmless placeholder value, run it, and cite its command and result.
