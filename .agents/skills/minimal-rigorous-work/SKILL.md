---
name: minimal-rigorous-work
description: Use for Argus implementation, research, document, planning, review, and orchestration work that must stay evidence-driven and lean.
---

# Minimal rigorous work

Solve the current task completely with the shortest clear control path.

- Start from the requested behavior, the real entry point, and observable
  evidence. Do not fix a bug that cannot be reproduced on the current commit.
- Reuse existing contracts and helpers. Do not add unrelated features,
  refactors, dependencies, roles, layers, configuration, or artifacts.
- Validate external input, authority, persistence, security, money, citations,
  and irreversible actions. Trust established internal invariants.
- Unless a demonstrated requirement needs them, do not add hashes, UUIDs,
  random identifiers, retries, backoff, fallback chains, duplicate guards,
  speculative locks, compatibility layers, wrappers, factories, or placeholder
  interfaces.
- Keep errors explicit. Do not hide defects behind broad catches, empty
  results, default values, or success-shaped degradation.
- Use tokens by reducing uncertainty and repeated context: carry one canonical
  task contract, inspect unchanged inputs once, act when evidence is sufficient,
  and run one decisive check per claim. Do not hard-code token caps, truncate
  required context, or add token-shortening functions.
- Record a reusable lesson only when reproduced evidence shows it generalizes.
  Scope the lesson narrowly; do not create ceremonial Skills, Wiki entries, or
  validation-only turns.
- Review independently and read-only. Verify the changed user-visible path;
  broaden checks only when the blast radius is broad.
- Report `BLOCKED` or `UNTESTED` honestly. Stop when the requested outcome is
  complete.
