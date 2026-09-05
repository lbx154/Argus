---
name: "Venue Format Preflight"
description: "Compile a complete draft against the selected venue's official author kit before Review."
---

# Venue Format Preflight

Use this in Paper only for compilation and official venue structure. Resolve the
selected venue from pipeline state and verify its current official author kit;
do not infer rules from another conference.

## Checks

- Use the official document class, style files, review mode, paper size,
  columns, fonts, bibliography behavior, and anonymity rules.
- Treat the venue's body limit as a ceiling, not a quota. Never pad to reach a page number or word target; reflow content that exceeds the current limit.
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
log are current. Create no preflight report. Proceed to Review for the parallel
scientific, visual, and language inspections.
