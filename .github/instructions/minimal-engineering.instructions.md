---
description: Keep Argus implementation and orchestration minimal without weakening real boundaries
applyTo: '**/*.{py,ts,tsx,js,mjs}'
---

# Minimal engineering

- Use the shortest control path that satisfies the current requirement. Add a
  role, guard, retry, wrapper, or artifact only when it resolves a reachable
  uncertainty and its failure changes the next action.
- Validate real input, authority, persistence, security, and irreversible
  boundaries. Trust established internal invariants instead of repeating the
  same check in every layer.
- Keep one owner for one coherent deliverable. Split work only for a hard
  dependency, independent information source, or independent authority such as
  Reviewer acceptance.
- Pass one canonical contract plus role-specific deltas. Do not restate the
  operator request, plan, checkpoint, and review policy in every role prompt.
- Treat an accepted decisive check as settled until its inputs change or a
  contradiction appears. Do not add another validation-only task or rerun an
  unchanged check for ceremony.
- Repair routine local failures locally. Escalate only when scope, semantics,
  integrity, authority, or irreversible effects change.
- Do not introduce hashes, including SHA-256, for identifiers, migrations,
  freshness, or correctness. Prefer direct names and explicit persisted state;
  do not add defensive fallbacks or guards for states the current contract
  cannot reach.
- Test the changed surface and reachable consumers. Run an end-to-end smoke
  test for a newly exercised path; broaden only when the blast radius is broad.

For example, coupled files produced by one Engineer and checked by one Reviewer
are one task, not an implementation task followed by a second review task.
Conversely, credential handling and publication remain explicit authority
boundaries even when bypassing them would be shorter.
