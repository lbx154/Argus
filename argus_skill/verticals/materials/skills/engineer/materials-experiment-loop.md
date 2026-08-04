---
name: "Materials Experiment Loop"
description: "Design or operate a materials experiment loop with explicit samples, instruments, controls, safety boundaries, provenance, calibration, and simulation-to-experiment comparison."
---

# Materials Experiment Loop

Use this skill for physical synthesis, processing, characterization, or
mechanical testing. A-Lab and Coscientist demonstrate useful closed-loop
patterns, but Argus may claim a physical experiment only when it has real,
authorized instrument or laboratory access and native records.

## Before execution

1. Define sample identity, provenance, batch, geometry, preparation, processing
   history, storage, and environmental controls.
2. Define the observable, instrument, calibration, resolution, detection limit,
   controls, replicates, randomization, and exclusion policy before seeing the
   desired answer.
3. Separate exploration from confirmatory validation. Reserve independent
   samples or conditions for the claim that matters.
4. Respect laboratory safety, equipment interlocks, authorization, and material
   handling rules. Never let a language-model action bypass an existing safety
   control.

## During and after execution

- Record instrument identity, software/version, calibration, operator/agent
  action, timestamps, raw files, sample mapping, environmental conditions, and
  every failed or excluded run.
- Monitor instrument faults and out-of-range states. Do not convert a fault,
  saturation, missing peak, or corrupted file into a scientific observation.
- Analyze raw data with uncertainty and appropriate controls. Preserve the
  transformation from raw signal to reported quantity.
- Compare simulation and experiment under matched geometry, material state,
  loading, temperature, rate, and measurement definition.
- If no instrument was available, deliver an experiment design or simulation
  prediction labeled as such; never imply that a sample was synthesized or
  measured.
