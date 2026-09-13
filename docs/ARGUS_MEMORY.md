# Argus Memory Design

Argus memory is a layered, file-backed system that turns execution into resumable state and reusable knowledge.

## Memory Layout

```text
Operator memory   — what the operator wants and permits
Curated memory    — what later work should reuse
Working memory    — where current work stands
Execution memory  — what happened
```

**Execution memory** is the project’s observable event history: lifecycle events, selected progress, role outputs, verdicts, costs, and provider I/O when enabled (`life/event_log.py`). Hidden provider reasoning is unavailable. In the default `signal` mode, Argus retains selected high-value events rather than every progress message.

**Working memory** is the resumable state of projects and missions: backlog, frontier, checkpoint, reviewed handoffs, and role-session capsules (`life/context_packet.py`, `core/role_session.py`). It lets a later role continue from the current frontier without replaying the full trajectory.

**Curated memory** is knowledge selected for reuse: Wiki pages hold declarative project knowledge, Skills hold procedures, and experience capsules preserve bounded observations from settled missions (`wiki/store.py`, `skills/layered.py`, `life/failure_experience.py`). Its scope may be project, vertical, or shared profile. A successful runtime verdict records its original verdict source and scope; it is not automatically a verified causal lesson or a shared fact.

**Operator memory** stores directives, preferences, capabilities, and revocations in OperatorContext (`core/operator_context.py`). Role prompts and acknowledgements use the project state root, including when their memory facade is a `MemoryBundle`. New explicit `global` preferences go to that user's shared root; other projects under that root read them as defaults, with project preferences taking precedence. Capability grants and one-shot instructions are never shared by preference projection. Custom state layouts must supply `global_root`; the resolver never guesses a user namespace from the process's ambient home. Project state symlinks cannot silently change the resolved project or user.

An old project-ledger preference labelled `global` remains local compatibility data. It is never silently copied into the shared profile. A new explicit global preference writes the shared ledger, and an explicit project revocation can remove an old local override. Project and shared ledgers retain separate revision numbers. Updating a preference replaces its previous value for the same kind, scope, and role set; withdrawing the replacement does not resurrect that obsolete value.

OperatorContext compacts into an atomic, checksummed checkpoint in its existing JSONL file. Original revisions, the closed history prefix, role acknowledgements, one-shot consumption and mission bindings survive cache deletion and restart. Old tails cannot reintroduce discarded revisions. A fixed ownership marker prevents a missing established source from being treated as a fresh legacy import or revision zero. Revoked, consumed and superseded preference history can be removed; active operator constraints are preserved. The default limits are 256 retained records and 1 MB. If still-active authority cannot fit after compaction, a new write fails explicitly rather than silently dropping an instruction. Legacy one-shot input whose consumption cache is already missing is withheld because replay cannot be justified. Processes must use the checkpoint-aware runtime before writing or reading migrated state.

Manager supervision is separate project advice within operator constraints. The current Manager direction reaches subsequent Planner/Engineer/teammate prompts without becoming a new operator authorization or an append-only preference.

## How Memory Is Produced

```text
execution
  → observable events
  → frontier, checkpoint, and handoffs
  → post-mission curation
  → retrieval by later roles
```

While roles work, Argus records observable events. At execution boundaries, it compresses current state into the frontier, checkpoint, handoffs, and role capsules. After settlement, stable facts can enter the Wiki, reusable procedures can enter Skills, and bounded success or failure observations can become project experience. Later roles retrieve each layer according to project, mission, role, and authority.

Experiences retain stable identities, revisions, evidence references and bounded revision history. Manager, Planner and Engineer can inspect, correct or retract their own project's capsules through call-bound tools. Reviewer can inspect but cannot mutate them. A correction requires the current revision, new evidence and a reason. The tool accepts up to 16 existing relative `workspace:` or `state:` text files, at most 32 KiB combined; nonexistent, out-of-scope, credential/private and truncated files are rejected. It reuses the Advisor's bounded evidence reader and records source-byte hashes plus redacted-text hashes in the receipt. Revision references retain the source hashes without copying the evidence body into memory. This verifies which bytes were read, not whether the agent's interpretation is true; arbitrary receipt-like strings cannot stand in for files. Concurrent stale writes fail; retracting an interpretation removes it from future recall. The host binds the project and role; tool callers cannot supply a different root or role. These tools do not change OperatorContext or grant workspace write permission. Pi exposes native experience tools; backends with an existing shell use `python -m argus_skill.tools.experience` during a live role call. No shell is added to a read-only backend.

The canonical experience file retains at most 256 active records / 1 MB by default, with a combined 64 retired or historical records / 256 KB. IDs carry immutable admission timestamps, and a durable watermark prevents old discarded identities from being replayed as new. SQLite indexes contain derived lexical terms and vectors; source checks and revision/digest reconciliation prevent superseded or withdrawn content from being recalled.

Wiki and Skill recall uses the current semantic Markdown paths, with at most 256 documents, 32 KiB per file and 1 MB total per snapshot. Every recall and post-mission evolution reconciles direct edits, archive moves and deletion. Hidden, retired and archived files and symlink escapes are excluded. Missing or corrupt indexes are rebuilt from current files. A Markdown digest is a content-version token, not an invented monotonic source revision. Retrieved pointers direct the agent to open current pages and assess their full evidence and scope; the cache is not an authority for prose. Planner and Engineer consume these pointers through the same project memory path as experiences, within a combined 6,000-character recall budget.


The original layout reference is `lbx154/Argus` commit `ae2daa1fbc2c918b4e7126151fe55eb68fd0cb98`; the OperatorContext scope and checkpoint rules above describe the September 12, 2026 implementation.
