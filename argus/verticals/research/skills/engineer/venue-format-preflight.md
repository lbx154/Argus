---
name: "Preparing the draft for the venue"
description: "Compile a complete draft against the selected venue's official author kit before Review, and read it once for what must not appear: private paths, internal role or stage names, development narration, or descriptions of the system that contradict each other."
---

# Preparing the draft for the venue

Use this in Paper, once the draft is complete, for compilation, official venue
structure and the reader-facing surface of the manuscript. Resolve the selected
venue from the project state and verify its current official author kit; rules
inferred from another conference are wrong often enough (page counting,
anonymity, bibliography style) that the guess is not worth making. The
scientific, visual and language judgements are made in Review, not here.

## What the draft must include and respect

- The official document class, style files, review mode, paper size, columns,
  fonts, bibliography behaviour and anonymity rules, exactly as the kit ships
  them.
- The venue's body limit as a compliance ceiling, not a quota imposed by the
  venue: reflow content that exceeds the current limit, and separately compare
  the rendered, officially counted body extent with the writing target from
  `research-paper-playbook.md`. Total PDF pages including excluded end matter
  do not establish body length. Record the actual extent and the target in the
  existing research notes, and use the playbook's principle-led expansion
  guidance to develop a short body toward its writing target rather than
  padding it.
- Every required section, disclosure and statement in the venue's required
  order, including the venue's own submission checklist where it has one, and
  the end matter it expects.
- Every citation and reference resolved, with no placeholders and no
  compilation warnings left, because each one is a sentence a reviewer cannot
  trust.
- No material overflow and no layout override the author kit forbids.
- A caption, a label and a reader-facing reference for every included figure
  and table. This is a completeness check; the final visual inspection happens
  in Review.

## What must not appear

Read the rendered paper once as its eventual reader, someone with no access to
the project. The paper does not hold yet if that reader can see:

- private paths, credentials, endpoints, device assignments or caches;
- internal role names, task identifiers, stage names, validator names or
  daemon details;
- development logs, debugging narration or local-only commands that are not
  part of a reproducible scientific description;
- descriptions of the actual model, evaluator, data or method that contradict
  each other between sections.

Necessary method and reproduction details belong in the paper; how the agents
worked does not. In the final Review the Reviewer makes this same read without
editing the paper, returns exact locations and replacement wording through the
normal Reviewer response, and the Engineer applies the fix. The integrated
judgement stays in `paper/REVIEW.md`; no separate leak-review file is written.

## Compile

Compile from the project root with the official toolchain. For LaTeX venues,
prefer:

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error \
  -output-directory=paper paper/main.tex
```

Fix compilation and venue-structure errors until the rendered paper and the
build log are current. Do not write a separate report about these
preparations.
Proceed to Review for the parallel scientific, visual, and language inspections.
