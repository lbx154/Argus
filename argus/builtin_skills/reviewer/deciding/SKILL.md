---
name: "Deciding the review"
description: "When the Reviewer accepts the work, sends it back, hands it to the Planner or Manager, and what changes for direct work, staged work, final paper review and research targets."
---

# Deciding the review

A plan challenge names the failed assumption and an alternative; a
commitment of the Manager or the operator goes to them. Ask only for authority
or facts the operator owns.

## Direct work

Hand off `to=done` when the task meets its requirements and decisive check,
`to=planner` only to change the plan, and `to=engineer` naming the next repair
for a material gap. Leave optional hardening advisory.

## Staged work

- `to=done` needs sufficient evidence, not exhaustive proof or every file. It
  closes the task; a final-submission task may close the project.
- Only claim-essential evidence gaps warrant `to=engineer`; optional evidence
  and minor weaknesses are advice.
- A timeout or failed attempt proves no impossibility; a missed threshold
  describes only that run.
- Integrity is required, not scientific value.

### Upstream defects

Roll back only when a concrete earlier defect makes the current result
unusable; optional outputs, or ones the claim does not need, are advisory. The
Manager owns rollback: hand off `to=manager` with the earliest affected stage
and the evidence. Research moves forward only: name repairs for earlier method,
experiment or paper defects in `note` instead. Never edit
`.argus/PIPELINE_STATE.json`.

## Final paper review

The selected venue's acceptance standard governs completion. Give your actual
current venue recommendation, not a hoped-for future rating. Hand off `to=done`
only when no required repairs remain; otherwise `to=engineer`, even at weak
accept or better. Continue the review and revision loop while acceptance is
unclear or feasible high-impact improvements remain; scientific repairs stay in
this task. Speculative future work stays advisory.

## Research target

The project's research target level defines project completion, not this
round's bar; the round's verification profile is its bar, and the integrity
floor is the same at every profile. Judge directly and explain in the review.

- If the direction cannot reach the target, hand the work to the Planner:
  `to=planner`.
- Fill `research_result` from evidence you inspected, one listed value per
  field. A survey is `literature_review` with `novelty_status` `known` or
  `not_applicable`.
- Ask yourself: what observed result or pattern most changed your belief,
  including a positive surprise, and what is the cheapest observation that
  would tell a new scientific explanation from an artifact? `none` is valid and
  produces no work; if the answer could change the claim or the route, hand it
  to the Planner.
