# Choosing a venue and preparing the paper for it

Use this when the venue is not yet selected, and again in the paper step once
the draft is complete: compile it against the venue's official author kit and
read it once for what must not appear.

## Choosing the venue

Compare current venues from their official calls for papers, deadlines, scope
and author kits; choose only when the operator left the venue open. Write the
choice into `RESEARCH_NOTES.md`:

- venue and track;
- why the contribution fits;
- current deadline;
- body limit or word limit for the exact track and submission type, including
  which parts of references, appendices, limitations, and ethics count;
- anonymity model;
- required sections, disclosures, and end matter;
- official author-kit URL.

Do not write a separate venue report or a summary of the formatting rules; the
author kit is the source.

## What the draft must include and respect

Verify the selected venue's current official author kit before relying on it;
rules inferred from another conference are wrong often enough (page counting,
anonymity, bibliography style) that the guess is not worth making. The
scientific, visual and language judgements are made in the review step, not
here.

- The official document class, style files, review mode, paper size, columns,
  fonts, bibliography behaviour and anonymity rules, exactly as the kit ships
  them.
- The venue's body limit as a compliance ceiling, not a quota imposed by the
  venue: reflow content that exceeds the current limit, and separately compare
  the rendered, officially counted body extent with the writing target in
  `paper.md`. Total PDF pages including excluded end matter do not establish
  body length. Record the actual extent and the target in `RESEARCH_NOTES.md`,
  and develop a short body toward its target with principle-level explanation
  rather than padding it.
- Every required section, disclosure and statement in the venue's required
  order, including the venue's own submission checklist where it has one, and
  the end matter it expects.
- Every citation and reference resolved, with no placeholders and no
  compilation warnings left, because each one is a sentence a reviewer cannot
  trust.
- No material overflow and no layout override the author kit forbids.
- A caption, a label and a reader-facing reference for every included figure
  and table. This is a completeness check; the final visual inspection happens
  in review.

## What must not appear

Read the rendered paper once as its eventual reader, someone with no access to
the project. The paper does not hold yet if that reader can see:

- private paths, credentials, endpoints, device assignments or caches;
- internal role names, task identifiers, step names, checker names or tool
  details;
- development logs, debugging narration or local-only commands that are not
  part of a reproducible scientific description;
- descriptions of the actual model, evaluator, data or method that contradict
  each other between sections.

Necessary method and reproduction details belong in the paper; how the agents
worked does not. In review the Reviewer makes this same read and names exact
locations and replacement wording in its letter; the Engineer applies the fix.

## Compile

Compile with the official toolchain. For LaTeX venues:

```bash
latexmk -cd -pdf -interaction=nonstopmode -halt-on-error paper/main.tex
```

`-cd` builds inside `paper/`, so figure and bibliography paths in the source are
relative to `paper/`, and the build writes `paper/main.pdf` and `paper/main.log`.
Fix compilation and venue-structure errors until the rendered paper and the
build log are current; `checks/paper.mjs` reports a PDF older than its sources
and undefined citations or references in the log. Do not write a separate
report about these preparations.
