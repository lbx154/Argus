---
name: MOF Synthesis Activation and Postsynthetic Evidence
description: Extract and evaluate MOF synthesis, activation, solvent exchange, defect modulation, and postsynthetic modification evidence with sample linkage and characterization requirements; excludes generic organic route planning.
category: chemistry-mof-synthesis
version: 1
---

## When to use

Use for MOF literature research, synthesis-condition extraction, reproducibility
assessment, activation planning, defect-engineering comparison, or
postsynthetic modification analysis.

## Do not use when

Do not convert a scheme or database record into an execution-ready procedure.
Do not treat ligand synthesis as framework synthesis, or a proposed
postsynthetic reaction as demonstrated framework modification.

## Scientific question

Which precursor, composition, process, workup, activation, and modification
conditions produced the identified MOF sample, and what evidence links that
sample to the claimed framework and property?

## Required inputs

Primary article/patent and supplementary information; precursor identity and
purity; metal/linker/modulator/solvent amounts; concentration and vessel;
temperature-time/pressure profile; atmosphere; washing, solvent exchange,
activation, storage, and yield basis; sample labels; characterization; and
reported deviations or failed conditions.

## Identity and normalization

Preserve source text and sample names. Normalize quantities and conditions in
separate fields. Link every structure, powder pattern, adsorption isotherm,
composition, spectrum, microscopy result, and property to the exact batch and
activation/modification state. Distinguish as-synthesized, exchanged, activated,
hydrated, guest-loaded, defect-modulated, and postsynthetically modified samples.

## Decision procedure

1. Reconstruct the full sequence from precursor preparation through storage.
2. Separate framework formation, washing/exchange, activation, and
   postsynthetic steps.
3. Record order/rate of addition, concentration, pH or acid/base/modulator
   information, vessel fill, heating mode, cooling, aging, and atmosphere when
   reported.
4. Check whether phase identity, composition, porosity, and modification were
   measured on the same batch/state.
5. Evaluate whether evidence distinguishes framework incorporation, pore
   adsorption, surface deposition, linker exchange, defect capping, and
   decomposition.
6. Compare conditions only after normalizing scale and process history.
7. Preserve failed syntheses and activation losses when reported.

## Minimum capability probe

Trace one reported sample from its recipe to its phase, composition, and porosity
evidence. Recalculate reagent ratios and identify all inferred or missing fields.

## Validation gates

- Powder diffraction similarity alone does not prove composition, defect state,
  activation, or postsynthetic conversion.
- Digestion NMR, elemental analysis, spectroscopy, microscopy, gas sorption, and
  other measurements answer different parts of the identity question.
- Porosity after activation cannot be assigned to an as-synthesized sample.
- Yield states whether based on metal, linker, dry activated mass, or another basis.
- Reproducibility claims require independent preparations and comparable activation.
- Scale-up requires explicit mixing, heat/mass transfer, pressure, solvent,
  washing, activation, and safety review.

## Safety and authorization

Flag toxic metals/linkers/solvents, corrosive modulators, sealed heating,
pressure, flammable solvent exchange, vacuum/temperature activation,
air/moisture sensitivity, and waste. Planning does not authorize physical work.

## Output contract

Return a source-linked process record, sample-state graph, missing/conflicting
fields, characterization linkage, reproducibility evidence, safety flags, and
whether the source supports retrieval, reproduction planning, or a validated
sample claim.

## Stop, block, or replan conditions

Block when sample labels cannot connect process to characterization, activation
state is unknown for a property claim, or critical operational/safety fields
needed for the intended use are absent.

## Official references

- ACS chemical safety: https://www.acs.org/chemical-safety.html
- PubChem Laboratory Chemical Safety Summaries: https://pubchem.ncbi.nlm.nih.gov/
- IUCr data resources: https://www.iucr.org/resources/data
