---
name: "Authorized Attack Execution"
description: "Demonstrate vulnerabilities against declared targets with canary-first evidence and hash-bound artifacts."
---

# Authorized Attack Execution

Execute offensive work only as far as the declaration authorizes, and record
what you actually demonstrated.

## Method

1. Confirm the engagement before touching anything: the declaration at
   `<project>/SECURITY_ENGAGEMENT.json` names your targets, methods, window,
   and impact flags. Work outside them stops until the operator amends it.
2. Prefer source audit and local reproduction. Read the target's code first; a
   candidate found by reading is then demonstrated by running a minimal
   reproduction against the declared target.
3. Prove access by canary: read a planted marker, list a directory, print a
   constant. Never prove access by deleting, corrupting, or modifying anything
   unless the operator explicitly enabled the matching impact flag — and even
   then prefer the smallest reversible demonstration.
4. For each confirmed vulnerability, save a PoC script and the raw tool output
   under the project's `evidence/` directory, record each artifact's SHA-256
   alongside its path in `FINDINGS.json`, and write the exact reproduction
   command into the finding. The hashes bind the claim to the artifacts.
5. Stop at what you demonstrated. A suspicion you could not reproduce locally
   ships as `unverified` with the reason; it is not a finding.

## Evidence

Per finding: the PoC script, the raw tool output, their SHA-256 bindings, and
the reproduction command — all inside the declared window, targets, methods,
and impact flags.
