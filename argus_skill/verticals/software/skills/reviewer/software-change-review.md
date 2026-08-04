---
name: Software Change Review
description: Independently review a software patch for real call-path behavior, compatibility, and honest verification without access to a reference answer.
category: software-review
priority: high
version: 1
created_at: 2026-07-30T00:00:00+00:00
---

# Software Change Review

Use the issue contract, unchanged callers, sibling implementations, and real
repository behavior as independent oracles. Do not infer correctness from the
patch or require a reference answer.

## Review method

1. Read the complete change with `git diff HEAD --stat` and `git diff HEAD` so
   staged changes are included. Reject imported target/gold commits or tests as
   implementation evidence.
2. Trace each changed public interface through unchanged callers. Check exact
   positional/keyword arguments, defaults, return type/order, field coverage,
   exception type/wrapping, object identity/tag propagation, invalid inputs, and
   boundary values relevant to the issue.
3. Exercise the real entry point. Prefer existing repository tests and mocks;
   use a small independent probe only for a missing case. A lower-level probe
   does not certify a wrapper, template, CLI, serialization, or process path.
4. Treat every observed failing test as evidence. Distinguish an obsolete test
   only from an explicit changed contract, not from an expectation that another
   hidden test will pass.
5. Keep review proportional: inspect the touched call path and the cheapest
   decisive checks. Do not demand unrelated full-suite work or an evidence
   packet.

Return `done` only when the implemented behavior and compatibility surface are
supported by the inspected code and executed evidence. Otherwise identify the
smallest falsified contract in `next_action`.
