# EXISTING_CAPABILITY_INVENTORY

Inventory of the capability / skill libraries that already existed before the
research-capability gates were added, so a future maintainer knows what is reused
and what is new. Compiled by read-only inspection; nothing here was modified.

## 1. In-repo skill registry (mature, reused)

The repo already has a full skill/capability system — the research gates do NOT
replace it; the new `CapabilityRegistry` sits alongside it.

| Component | Path | What it is |
|---|---|---|
| `SkillStore` | `argus_skill/skills/store.py` | markdown+YAML skills; `list_summaries` / `load` / `find_relevant` (LLM matcher) / `save`; versioning; `protected` governing skills |
| `LayeredSkillStore` | `argus_skill/skills/layered.py` | project+global composition, `promote_to_global` |
| role matcher | `argus_skill/skills/role_match.py`, `missions.py` | `match_role_skills` → prompt block per role |
| skill mutations | `argus_skill/skills/skill_router.py`, `evolution.py`, `lifecycle.py` | validated create/update/archive; protected-category guard |
| bundled skills | `argus_skill/builtin_skills/` (engineer/reviewer/planner/manager/curator) | version-controlled role playbooks |
| global skills dir | `~/.argus-skill/skills` (`core/paths.py::skills_global_root`) | learned skills |

Skill schema (frontmatter): `name, description, category, version, created_at,
skill_id, successful_reuses, failed_reuses, task_history, protected`.

## 2. Distilled physics capability library (the ~200-paper distillation, reused read-only)

Found OUTSIDE this worktree, in the workspace (READ-ONLY; never modified):

| Artifact | Path (relative to workspace `/data/zimo/zimo6`) | Content |
|---|---|---|
| Canonical synthesis | `worktrees/argus-skill-physics-v1/PHYSICS_CAPABILITY_SYNTHESIS_FROM_223.json` | 55 capabilities + 3 candidates, distilled from 223 papers (190 full-text) |
| Richest trace | `worktrees/argus-skill-physics-v1/PHYSICS_CAPABILITY_TRACE_V2.json` | 55 capabilities, families A–L, per-capability `metric` / `pass_threshold` / `hard_fail_conditions` / `extracted_research_actions` / `source_domains` / `source_refs` |
| Literature trace | `worktrees/argus-skill-physics-v1/PHYSICS_CAPABILITY_LITERATURE_TRACE.md` | 223 verified refs across 12 domains |
| Rebuild registry | `_argus_review/physics_vertical_rebuild_v1/03_CAPABILITY_REGISTRY.json`, `06_CAPABILITY_EXTRACTION_SCHEMA.json` | capability taxonomy + extraction schema |
| Corpus | `data/physics_literature/` (`extractions/`, `metadata/`, `pdfs/`, `capability_notes/`) | per-paper extractions + PDFs |

Capability families (A–L) and their gate mapping:

| Family | Theme | Consuming gate |
|---|---|---|
| A/B/C | mission understanding / problem formulation / reasoning | (cross-cutting) |
| D | equation & model construction | theory (Phase 2) |
| E | dimensional & scale reasoning | theory (Phase 2) |
| F | numerical research design | numerical (Phase 3) |
| G | data & experimental analysis | numerical (Phase 3) |
| **I** | **literature synthesis & positioning** | **literature (Phase 1, shipped)** |
| J | reproduction & replication | (manuscript/reproducibility) |
| K | report/paper construction | (manuscript) |
| L | reviewer hard gates | novelty (Phase 4) |

TRACE_V2 record schema (per capability): `capability_id, capability_name,
priority, capability_family, capability_group, source_domains, metric,
pass_threshold, hard_fail_conditions, extracted_research_actions, source_refs /
full_text_supporting_refs, related_benchmark_tasks, implementation_target`.

## 3. Reusable items pulled into Phase 1

- The **literature-synthesis family (I)** shape informs the in-source base library
  `argus_skill/verticals/physics/capabilities/literature.json` (5 curated,
  gate-tuned capabilities `CAP-LITBASE-01..05`).
- The full TRACE_V2 library is **merged read-only** at runtime by the
  `CapabilityRegistry` when discoverable (env var / relative discovery), giving 55+
  additional capabilities with provenance — see `CAPABILITY_REGISTRY_DESIGN.md`.

## 4. What is genuinely new (this change)

`argus_skill/skills/research_gates.py` (generic gate contract),
`argus_skill/skills/capability_registry.py` (the registry), the base literature
library, and `argus_skill/verticals/physics/gates/literature.py` (the first gate).
Theory / numerical / novelty / paper-type gates are future phases behind the same
interface.
