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
- Without a demonstrated current requirement, do not add UUIDs, random tokens,
  custom idempotency/deduplication keys, retries, backoff, circuit breakers,
  fallback chains, compatibility layers, speculative locks, future fields, or
  placeholder interfaces. Existing framework and upstream invariants are the
  default; validate only real external-input, permission, persistence, security,
  and irreversible-action boundaries.
- Keep errors transparent. Do not turn defects into empty results, default
  values, broad catches, or success-shaped degradation.
- Do not create helpers, wrappers, services, factories, or abstractions unless
  they remove existing semantic duplication, enforce a real boundary, or make
  the current call path materially easier to understand. Prefer direct local
  code and stop when the requested behavior is complete.
- Parallel and Team execution must be real rather than ceremonial: independent
  work, disjoint writable paths, explicit completion evidence, one lead-owned
  synthesis, and normal Reviewer acceptance. DSH native subagents may assist
  flexibly inside that structure; do not confuse them with or forbid the Argus
  Team mechanism the operator requested.
- Test the changed surface and reachable consumers. Run an end-to-end smoke
  test for a newly exercised path; broaden only when the blast radius is broad.

For example, coupled files produced by one Engineer and checked by one Reviewer
are one task, not an implementation task followed by a second review task.
Conversely, credential handling and publication remain explicit authority
boundaries even when bypassing them would be shorter.
