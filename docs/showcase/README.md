# Argus Physics Vertical — Showcase

A single-file, self-contained showcase page for the **Argus Physics Vertical**
and its first physics research case study, **SVEPη₀** (Case B, project
`s-764e2ed6`).

## File

- [`physics_vertical_showcase.html`](physics_vertical_showcase.html) — the whole
  page in one file: hand-written HTML + CSS + vanilla JS + inline SVG. All six
  paper figures are embedded as base64, so there is **no CDN and no network
  dependency**. Just open it in any browser.

```bash
xdg-open docs/showcase/physics_vertical_showcase.html   # Linux
# or open the file directly in a browser
```

## What it presents

**A. The Physics Vertical mechanism**

- Generic research vertical (`Plan → Run → Draft → Review → Submit`) vs. the
  physics vertical (`Scope → Model → Execute → Review → Manuscript`).
- Stage-entry contracts — hard requirements pushed into the agent prompt before
  each stage starts.
- The V3 capability library (221 capabilities, exposed and audited across 5
  gates).
- The novelty-seeking loop for original-research-required mode.
- Six gates (Literature, Theory, Numerical, Novelty, Paper-Type, and the
  terminal Manuscript hard gate).

**B. The SVEPη₀ case study**

- A 2D non-Hermitian Floquet lattice with one-directional quasiperiodic
  modulation, studied on rational approximants in the η₀ quasienergy sector.
- Finding: the reproduced analytic-radius GBZ baseline stays gap-resolved but
  systematically misses the positive η₀ open-boundary labels; the proposed
  **SVEPη₀** diagnostic (edge polarization of the smallest right singular vector
  of `A₀ = U − I`) restores open-label prediction on held-out and reduced
  scaling grids.
- Evidence dashboard: four evidence sets (16 / 42 / 66 / 40 rows) and six
  figures, each mapped to a bounded claim (C1–C6).

## Scientific boundary

The result is a **finite-volume diagnostic result** for the sampled model family
(η₀ sector; sampled phasons, q = 3/5/8, L/q = 2/3). It is **not** a proof of an
irrational-limit bulk invariant, an η₋π statement, or a bulk topological
invariant. SVEPη₀ is open-boundary based and should be read as a predictive
finite-volume diagnostic.
