---
name: Chemistry Skill and Capability Router
description: Route chemistry tasks to shared foundations, one or more of eight domain workflow families, and only then to a narrow tool profile; use as a catalog, not an installation script or scientific workflow.
category: chemistry-routing
version: 4
---

Choose from the scientific question, not software familiarity.

## Domain workflow families

| Need | Skill directory |
|---|---|
| Small-molecule reaction records, retrosynthesis, route validation | `engineer/organic_synthesis/` |
| Broad materials data, discovery, processing-structure-property | `engineer/materials_science/` |
| Diffraction, crystal solution/refinement, CIF validation | `engineer/crystallography/` |
| Framework nodes/linkers/nets, MOF synthesis, porosity, generation | `engineer/mof_reticular_chemistry/` |
| Electronic structure, atomistic simulation, method validation | `engineer/computational_chemistry/` |
| Cell/protocol identity, cycling, degradation, battery models | `engineer/batteries/` |
| Raw analytical data, modality-specific interpretation, integration | `engineer/characterization/` |
| Constructs, assays, kinetics, docking/MD evidence boundaries | `engineer/biochemistry/` |
| Explicit bounded speculative hypothesis probing | `engineer/workflows/chemistry-playground.md` |

Materials, crystallography, and MOF are parallel. Load multiple Skills only when
the task truly crosses their evidence boundaries.

## Shared foundations

Use `engineer/foundations/` for chemical identity, units and conditions,
evidence provenance and claim levels, uncertainty and applicability, dataset
curation and leakage, computational reproducibility, chemical risk and
authorization, failure diagnosis, and the common workflow/output contract.

## Existing narrow tool profiles

| Capability | Matchable Skill |
|---|---|
| Molecular parsing, canonicalization, fingerprints, descriptors | `tools/rdkit.md` |
| Chemical file conversion or second-parser checks | `tools/openbabel.md` |
| Public compound records | `tools/pubchem.md` |
| Curated target, assay, and bioactivity records | `tools/chembl.md` |
| Structured public reaction records | `tools/ord.md` |
| Local retrosynthesis search | `tools/aizynthfinder.md` |
| Authorized ASKCOS deployment | `tools/askcos.md` |
| Python electronic-structure workflow | `tools/pyscf.md` |
| Psi4 electronic-structure workflow | `tools/psi4.md` |
| Operator-provided licensed ORCA | `tools/orca.md` |
| Molecular ML datasets, models, and splits | `tools/deepchem.md` |
| TDC datasets or predictive oracles | `tools/tdc.md` |
| GuacaMol molecular-design benchmarks | `tools/guacamol.md` |
| Olympus reaction-optimization surfaces | `tools/olympus.md` |
| ChemCrow public integration patterns | `tools/chemcrow.md` |
| Coscientist supporting implementation | `tools/coscientist.md` |
| ChemOS laboratory orchestration reference | `tools/chemos.md` |

First load the domain workflow; then select the narrowest capability it requires.
Inspect the project environment, current official documentation, release,
license, data/model provenance, API terms, and a representative capability probe.
Do not install into or modify the Argus Harness for a project dependency.

The named tools are optional capabilities. They do not replace identity,
controls, calibration, convergence, uncertainty, or domain validation. Add a new
tool profile only when recurring capability-specific behavior cannot be stated
cleanly in the domain workflow.

Physical commands require authenticated, pre-authorized instrument capabilities
with facility limits and interlocks. Without that boundary, stop at analysis,
computation, simulation, or planning.

Do not route ordinary uncertainty into the Playground. It is selected only by an
explicit Playground request and never grants formal Research status or physical
authorization.
