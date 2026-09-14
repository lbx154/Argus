---
name: "Reading the paper's argument and language"
description: "Read the paper's argument and academic language against the selected venue's current conventions, without editing it."
---

# Reading the paper's argument and language

Use this for the reader-facing pass in Review. Under `cold_read`, read only the
rendered PDF supplied in the isolated workspace; do not search for manuscript
source, the research notes, REVIEW, code, or review history. During the later
integrated review, source and venue guidance may be inspected under that
operation's wider terms. Do not edit files and do not create a separate
language-review file.

## Inspect

- State clearly what is studied, what is claimed, under which conditions, and
  why the result matters.
- Make the title, abstract, introduction, contributions, results, and conclusion
  express one consistent thesis.
- Judge the writing as a reviewer at the selected venue would: would this be
  accepted, and what would a careful reader object to? Enforce no abstract
  length, sentence count, number density, or caption format; a longer or
  shorter abstract, more or fewer numbers, and repeated headline figures are
  fine when they serve the argument at that venue.
- Object when a claim outruns its evidence, when a number's meaning is unclear
  from its context, when hedging or limitation lists stand in for a clear
  statement, or when terms for internal task limits, declarations of readiness,
  stage decisions, generated outputs, missions, rounds, transfers between roles,
  checking tools, or inspections appear. Do not ask for more hedging than the
  evidence requires, and do not ask for a number where a
  plain statement is clearer.
- Read as a stranger to the project. Can the argument be recovered from the
  headings, the takeaways and the captions alone? Does the introduction promise
  exactly what the results deliver? Is the central finding recoverable after
  the first page? A results section that lists per-condition numbers without
  saying what they mean, an introduction that opens with the technology rather
  than the problem, or a conclusion that ends on a disclaimer are reader-facing
  defects with a concrete repair; a preference for different wording is not.
- Check whether headline, mechanism, disambiguating-control, scope-changing,
  and completeness evidence are visibly prioritized rather than reported as one
  flat experiment inventory.
- Treat number walls and defensive patterns as findings with a location: a
  prose passage that recites a matrix the table already carries, precision in
  prose beyond what the comparison needs, a hedge or caveat repeated per result
  when one statement of the design's limits would do, apologies for the
  contribution, or assurances of rigor in place of the actual protocol.
  `engineer/references/paper-writing-craft.md` describes the repairs; name the
  passage and the obstacle, not a count.
- Allow a headline number to recur when it serves a different section role.
  Object to repeated matrix recital; do not judge repetition by a mechanical count.
- Require Methods, tables, and appendices to retain complete definitions and
  result coverage while prose explains the comparisons that change the current
  inference.
- Prefer confident, precise academic prose over defensive qualification,
  process narration, repeated caveats, and integrity self-praise.
- Request changes to generic openings, filler, repetitive transitions,
  unexplained acronyms, vague method names, or score restatement only when they
  cause ambiguity, needless repetition, or obstruct the argument. Preserve
  numerical restatement that serves the abstract, caption, or conclusion.
  When the surrounding context already explains a comparison, do not require
  another explanation after each number.
- Keep claims faithful to the actual method and evidence.
- Keep internal paths, role names, workflow language, and development history
  out of the manuscript.
- Apply the selected venue's terminology, anonymity conventions, section
  expectations, and reader-facing style.

For each required repair, return the passage or PDF location, the concrete
obstacle to understanding or inference, and the smallest repair goal. A
report-like tone or a preference for different wording alone is insufficient.
Example wording is optional; the Engineer need not copy it verbatim. Find that
the language holds when no substantive reader-facing defect remains, and return
the corresponding result:

`pass`

Do not manufacture revisions to demonstrate review effort. The single Engineer resolves findings
with the scientific-loss and visual findings. The integrated Reviewer closes
resolved issues and records the final result only in `paper/REVIEW.md`.
