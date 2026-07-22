---
name: Materials Tool and Skill Router
description: Select and connect the appropriate open or licensed materials database, atomistic, CAD, CAE, workflow, and evaluation tools without replacing Argus's four-role control loop.
category: materials-tool-routing
version: 1
---

# Materials Tool and Skill Router

Use this skill after the scientific scale and observable are understood. The
projects below are capability surfaces, not authorities about which research
question to pursue.

## Route by scientific scale

| Need | Preferred capability surface |
| --- | --- |
| structures and computed properties | Materials Project `mp-api`, OPTIMADE, NOMAD, pymatgen |
| structure manipulation and calculators | ASE |
| composable atomistic skills | AtomisticSkills |
| code-first remote/HPC computation | MatClaw patterns, jobflow-remote |
| provenance-aware workflows | atomate2/jobflow or AiiDA |
| DFT | Quantum ESPRESSO, ABACUS, or an installed licensed VASP |
| MD and interatomic models | LAMMPS, ASE, OpenKIM, MACE/MatGL/FAIRChem |
| parametric CAD | build123d-mcp, CadQuery, FreeCAD, or an installed commercial CAD API |
| mesh | Gmsh or the selected solver's native mesher |
| open continuum/FEM | CalculiX, FEniCSx, or MOOSE |
| commercial CAE | Abaqus, PyAnsys, COMSOL API, or DEFORM Python API when licensed |
| solver-neutral execution | sim-cli plugins or a small project-native driver |
| evaluator | public experiments, Matbench Discovery, OpenKIM tests, or a task-specific frozen protocol |

## Integration rules

1. Reuse Argus Manager, Planner, Engineer, and Reviewer. Do not launch an
   external autonomous agent as an unreviewed second planner.
2. Inspect the installed version and local documentation before calling an API.
   Do not guess solver object names or silently fall back to GUI clicking.
3. Prefer typed APIs and solver-native batch commands. For a live session, use
   bounded steps: detect, connect, inspect, execute, inspect, checkpoint, export.
4. Keep data-query tools read-only and separate from code-execution or HPC
   submission tools. Restrict workspaces and credentials.
5. Do not install or copy proprietary SDKs. Open adapters do not grant a solver
   license.
6. Audit third-party MCP servers before enabling arbitrary Python, Java, shell,
   or network execution. Bind local services to a trusted interface only.
7. If no suitable integration is installed, write the smallest project-local
   adapter around the official API rather than screen-scraping the GUI.

## Upstream projects to inspect at the installed revision

- AtomisticSkills: https://github.com/learningmatter-mit/AtomisticSkills
- MatClaw: https://github.com/cz2014/MatClaw
- Materials Project API: https://github.com/materialsproject/api
- ASE: https://gitlab.com/ase/ase
- atomate2: https://github.com/materialsproject/atomate2
- AiiDA: https://github.com/aiidateam/aiida-core
- LAMMPS: https://github.com/lammps/lammps
- Quantum ESPRESSO: https://github.com/QEF/q-e
- OpenKIM: https://openkim.org/
- build123d-mcp: https://github.com/pzfreo/build123d-mcp
- CadQuery MCP: https://github.com/CadQuery/cadquery-contrib/tree/master/mcp-server
- CAE-Agent-Hub: https://github.com/Cai-aa/CAE-Agent-Hub
- sim-cli: https://github.com/svd-ai-lab/sim-cli
- AbaqusAgent: https://github.com/LIRAM-LIN/AbaqusAgent
- PyAnsys: https://docs.pyansys.com/
- DEFORM API announcement: https://www.deform.com/2025/04/11/spring-2025-deform-news/

These links document available interfaces. Their presence here does not certify
a particular commit, scientific result, license, or local installation.
