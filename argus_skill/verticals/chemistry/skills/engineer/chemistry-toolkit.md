---
name: Chemistry Open Tool Selection
description: Select and verify open chemistry tools, agent examples, molecular and reaction databases, computational engines, and closed-loop benchmarks without treating paper demos as deployable systems.
category: chemistry-tool-selection
version: 1
---

# Purpose

Use this as a current starting map, not an installation script or an authority on
chemical quality. Verify the official documentation, release, license, model
weights, API terms, and a real smoke test at execution time. Keep every tool in
the project environment or a dedicated container.

## Reusable chemistry-agent and closed-loop examples

- **ChemCrow**: <https://github.com/ur-whitelab/chemcrow-public>. The public
  LangChain package (`pip install chemcrow`) combines RDKit, paper search,
  PubChem, and reaction tools. Its own README states that API restrictions omit
  tools used in the paper, so the public package does not reproduce the published
  system. Reuse tool ideas, not reported performance.
- **Coscientist supporting information**:
  <https://github.com/gomesgroup/coscientist>. The repository contains supporting
  data and a simple implementation, not a portable robot-laboratory product.
  Hardware adapters and the execution environment must be rebuilt locally.
- **ChemOS 2.0**: <https://github.com/malcolmsimgithub/ChemOS2.0>. This is an
  orchestration reference for self-driving laboratories, but it is infrastructure
  heavy and tied to device services. Treat it as an architecture source unless
  the target facility already supports it.
- **Olympus**: <https://github.com/aspuru-guzik-group/olympus>. It provides
  experimentally derived optimization surfaces and baselines. The official
  package command is `pip install olymp`, not `pip install olympus`; its age and
  compatibility make a local smoke test mandatory.
- **Therapeutics Data Commons**: <https://github.com/mims-harvard/TDC>. TDC
  provides public molecular datasets, generation tasks, and predictive oracles.
  Read the exact dataset and oracle documentation, licenses, split semantics, and
  model provenance before constructing a benchmark; a predictive oracle is not a
  biochemical experiment.
- **GuacaMol**: <https://github.com/BenevolentAI/guacamol_baselines>. It provides
  standardized distribution-learning and goal-directed molecular-generation
  benchmarks plus public baselines. Check package age and environment compatibility
  before using it as a current baseline.

## Open chemistry engines and data

Choose only what the task needs:

- Molecular structures and descriptors: **RDKit**
  (<https://www.rdkit.org/>) or **Open Babel**
  (<https://openbabel.org/>). Use these for parsing and structural checks rather
  than asking an LLM to emulate them.
- Public chemical records: **PubChem PUG REST**
  (<https://pubchem.ncbi.nlm.nih.gov/docs/pug-rest>) and the **ChEMBL webresource
  client** (<https://github.com/chembl/chembl_webresource_client>). Record stable
  identifiers and query dates; database hits are not experimental validation.
- Reaction data: **Open Reaction Database**
  (<https://github.com/open-reaction-database/ord-schema>). `ord-schema` supplies
  schema and I/O; the records live separately in `ord-data`.
- Retrosynthesis: **AiZynthFinder**
  (<https://github.com/MolecularAI/aizynthfinder>) or **ASKCOS**
  (<https://gitlab.com/mlpds_mit/askcosv2/askcos2_core>). AiZynthFinder requires
  stock and policy files and currently documents Python 3.10-3.12; ASKCOS is a
  larger deployment with separate weights and terms. A returned route is a model
  proposal, not proof that a chemist can execute it.
- Quantum chemistry: **PySCF** (<https://github.com/pyscf/pyscf>) or **Psi4**
  (<https://psicode.org/>). Method, basis, charge, spin, geometry, convergence,
  and validation choices remain scientific decisions. ORCA is useful but
  proprietary; never assume automated download or redistribution is permitted.
- Molecular machine learning: **DeepChem**
  (<https://github.com/deepchem/deepchem>). MoleculeNet-style static datasets are
  useful for model checks but are widely exposed and weak evidence for an
  uncontaminated agent benchmark.

## Selection and evaluation discipline

1. Decide whether the chemistry task needs retrieval, deterministic molecular or
   reaction handling, prediction, quantum calculation, simulation, optimization,
   or physical measurement.
2. Probe the narrowest credible tool and retain its real input, output, version,
   and failure logs.
3. For an agent benchmark, prefer a sequential hidden oracle over static
   chemistry QA. Seal test answers from proposal logic, randomize or regenerate
   systems when possible, and compare against random plus a strong domain
   optimizer under the same query budget.
4. Define the capability being evaluated and record whether actions come from an online agent, an agent-designed policy
   frozen before outcomes, or a conventional optimizer. These evaluate different
   capabilities and must not share one "agent-guided" label. When the operator asks
   to evaluate online Argus decisions, route each budgeted decision through the live
   agent; do not compile a fixed heuristic and retain the online-agent claim.
5. A same-user subprocess is interface separation, not adversarial sealing. Use
   an external evaluator or OS-enforced capability boundary for an anti-cheat
   claim, or state that the benchmark assumes cooperative protocol compliance.
6. Treat literature, surrogate models, DFT, retrospective datasets, and wet-lab
   measurements as different evidence regimes. Escalation between them requires
   new evidence, not stronger prose.
7. Do not send physical commands unless the facility exposes an authenticated,
   pre-authorized capability with instrument-side limits and interlocks. The
   language model is not the laboratory safety controller.
