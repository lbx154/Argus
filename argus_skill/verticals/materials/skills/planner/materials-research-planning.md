---
name: "Materials Research Planning"
description: "Plan materials research by selecting the physical scale, evidence source, toolchain, baseline, and validation route that resolve the current scientific uncertainty."
---

# Materials Research Planning

Plan from the material system and unresolved scientific question, not from a
fixed software menu. Own the scale and route decision: first determine which
scales and observables control the claim. Choose only the methods needed:

- primary literature and public materials data;
- electronic-structure or atomistic calculation;
- ML interatomic potential, molecular dynamics, or mesoscale simulation;
- constitutive modeling, CAD, meshing, FEM, or process simulation;
- statistical/data-driven analysis;
- physical experiment design or execution when real instruments are available.

Inspect the actual repository, installed software, licenses, data access,
compute, and instrument access before depending on them. Prefer public,
reproducible inputs and a real evaluation protocol. A commercial solver is a
valid route only when its license and executable/API are genuinely available;
otherwise choose a scientifically defensible open route or report the missing
capability. Before planning physical synthesis, processing, testing, or
instrument operation, confirm the authorized personnel, approved procedure,
hazards, equipment interlocks, required controls, and waste-handling path.

For every planned claim, identify a like-for-like baseline or independent
validation source and the checks that could falsify it. Separate calibration
from validation. Include convergence, sensitivity, uncertainty, and physical
sanity work in proportion to the conclusion, not as generic paperwork.

Use the strongest existing tool layer rather than building another agent
harness. AtomisticSkills, MatClaw, CAE-Agent-Hub, sim-cli, and CAD MCP projects
are capability references or adapters; Argus remains the Planner. Schedule
bounded tasks that produce inspectable scientific evidence, retain failed
attempts, and change route when evidence invalidates the plan.
