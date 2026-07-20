# Argus Technical Report

This directory contains the public technical report for **Argus: A General-Purpose
Agentic Runtime for Long-Horizon Reasoning**.

- Compiled paper: `argus-technical-report.pdf`
- LaTeX entry point: `main.tex`
- Section sources: `sections/`
- Figure sources and exports: `figures/`
- Reproducibility data: `evidence/`

## Build

The report uses a standard TeX Live toolchain:

```bash
make clean
make all
```

The build uses the committed paper-facing figure PDFs, runs BibTeX, and writes
`argus-technical-report.pdf`. Component-editable PowerPoint sources are retained
under `figures/` and `PPT_SOURCES/`; the HTML/Python figure generators remain as
provenance and deterministic comparison sources rather than automatic build steps.

## Public release scope

The paper reports aggregate benchmark results, task-level intervention statistics,
and one representative mathematical trajectory. Supplementary files contain only
the public or aggregate fields needed to reproduce the paper's tables and figures;
credentials, private model reasoning, and raw runtime event streams are excluded.
