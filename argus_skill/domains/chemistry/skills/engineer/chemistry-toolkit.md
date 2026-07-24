---
name: Chemistry Tool Router
description: Select the narrowest chemistry capability and then load its tool-specific Skill; do not treat this catalog as an installation script.
category: chemistry-tool-selection
version: 2
---

Choose from the scientific requirement, not brand familiarity:

| Need | Matchable Skill |
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

Load only the selected Skill and current official documentation. Verify the
release, license, model weights, API terms, and one real smoke test in the project
environment before a long run.

For agent experiments, define online, periodic, frozen, or conventional control
before implementation. Route each budgeted decision through the live agent when
online control is the tested capability. A same-user subprocess is interface
separation, not adversarial sealing. Physical commands require authenticated,
pre-authorized instrument capabilities with instrument-side limits and interlocks.
