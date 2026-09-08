---
name: "Preparing the draft for the venue"
description: "Compile a complete draft against the selected venue's official author kit before Review."
---

# Preparing the draft for the venue

Use this in Paper only for compilation and official venue structure. Resolve the
selected venue from the project state and verify its current official author kit;
do not infer rules from another conference.

## What the draft must include and respect

- Use the official document class, style files, review mode, paper size,
  columns, fonts, bibliography behavior, and anonymity rules.
- Treat the venue's body limit as a compliance ceiling, not a quota imposed by
  the venue; reflow content that exceeds the current limit.
- Separately compare the rendered, officially counted body extent with the
  writing target from `research-paper-playbook.md`. Total PDF pages including
  excluded end matter do not establish body length. Record the actual extent
  and target in existing research notes. Use the playbook's principle-led
  expansion guidance to develop a short body toward its writing target.
- Include all required sections, disclosures, checklists, and end matter in the
  venue's required order.
- Resolve every citation and reference; remove placeholders and compilation
  warnings.
- Avoid material overflow and layout overrides forbidden by the author kit.
- Every included figure and table has a caption, label, and reader-facing
  reference. This is a completeness check, not the final visual inspection.

Compile from the project root with the official toolchain. For LaTeX venues,
prefer:

```bash
latexmk -pdf -interaction=nonstopmode -halt-on-error \
  -output-directory=paper paper/main.tex
```

Fix compilation and venue-structure errors until the rendered paper and build
log are current. Do not create a separate report about these preparations. Proceed to Review for the parallel
scientific, visual, and language inspections.
