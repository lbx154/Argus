# System Audit: Six Complaints, Measured

Six problems were raised about the runtime. This document checks each against the
code rather than against impressions. Every number below was measured on the tree
at the time of writing and can be re-derived with the command given.

Chinese version: [system-audit.zh-CN.md](system-audit.zh-CN.md)

**Scope of the tree:** 194,487 lines of Python under `argus_skill/`, 264 shipped
skill documents totalling 22,765 lines.

| # | Complaint | Verdict |
| --- | --- | --- |
| 1 | Over-defensive | **Confirmed** — 2,277 `try:` blocks, 235 that swallow and continue |
| 2 | Verification bar too rigorous | **Confirmed** — 39 failure codes, 5 gates demanding exact CSV columns |
| 3 | Asks the operator unnecessary questions | **Partly** — the rate is low, but the routing is a word list |
| 4 | Weak instruction following | **Confirmed, and self-inflicted** — 7,438 tokens of standing Manager instruction |
| 5 | Redundant, over-complex, spins | **Confirmed** — 28% of event types are dead; four parallel implementations of the same concerns |
| 6 | Schema abuse | **Already fixed at the decision boundary, not elsewhere** |

---

## 1. Over-defensive — confirmed

```
try:                              2,277
except Exception                    634
swallow-and-continue                235
modules named gate/verify/check/
audit/guard/contract/validate        42
```

One in three `except Exception` handlers discards the error and carries on. The
idiom is explicit in the docstrings — `"Fail-open: any error is swallowed"`
(`argus_skill/skills/capability_trace.py`), `"Fail-open to ()"`
(`argus_skill/skills/checklist_store.py`), and comments of the form *"must never
break prompt building"* in `argus_skill/skills/stage_machine.py`.

Each is individually reasonable and the aggregate is not: a runtime that cannot
fail is a runtime that cannot tell you it is broken.

```bash
grep -rc "^\s*try:" --include=*.py argus_skill/ | awk -F: '{s+=$2} END{print s}'
```

## 2. Verification bar too rigorous — confirmed

```
distinct failure codes               39   (LIT, MPKG, NOV, NSL, NUM, PT, TH)
gate modules                          9
gates demanding exact CSV columns     5
```

The Novelty-Seeking gate alone requires ten candidate directions, eleven
reasoning columns each, and six numeric scores before a manuscript may be
written (`argus_skill/verticals/physics/gates/novelty_seeking.py`). That is 170
cells of table to earn the right to make a claim.

The cost is not the check. The cost is that the work bends toward filling the
table.

## 3. Unnecessary operator questions — partly confirmed

```
escalation-related occurrences       269  across 51 files
measured interruption rate       1 / 40.7 h
of which research judgment            13%  (5 requests in 1,548 h)
```

The *rate* is low, so the complaint is not that Argus interrupts constantly. The
problem is **how** the decision is made. `argus_skill/core/role_handoff.py:20`
decides operator ownership with a regular expression over prose:

> `permission|authorization|authorize|approval|approve|consent|confirmation|credential|access|secret|budget|purchase|pay|publish|release|deploy|production|irreversible|delete|destructive|…`

`access`, `release`, `production`, and `delete` are ordinary engineering words. A
round summary that says "delete the temporary directory" or "access the config"
matches. The regex is used as a veto — it prevents a request from being
reclassified as an ordinary review request — so anything containing one of those
words stays with the human by default.

A word list cannot tell authority from vocabulary.

## 4. Weak instruction following — confirmed, and self-inflicted

Measured as string-literal payload in each role prompt module:

| Role | Prompt text | ≈ tokens |
| --- | ---: | ---: |
| Manager | 29,753 chars | 7,438 |
| Reviewer | 13,045 chars | 3,261 |
| Planner | 12,016 chars | 3,004 |
| Engineer | 7,513 chars | 1,878 |
| **Total** | **62,327 chars** | **15,581** |

This is standing instruction, before any task content, project state, skill, or
evidence is added. A model handed 7,438 tokens of rules will not follow all of
them, and the failure will look like disobedience rather than what it is:
**we wrote more rules than fit in an instruction-following budget.**

The fix is not a firmer tone. It is fewer rules.

```bash
python3 -c "
import ast,pathlib
for f in ['manager','reviewer','planner','engineer']:
    t=ast.parse(pathlib.Path(f'argus_skill/roles/prompts/{f}.py').read_text())
    print(f, sum(len(x.value) for x in ast.walk(t)
          if isinstance(x,ast.Constant) and isinstance(x.value,str)))"
```

## 5. Redundant, over-complex, spins — confirmed

**Dead instrumentation.** Of 129 `EventType` members, **37 (28%)** are never
referenced anywhere outside the catalog that defines them. Twenty-one of those are
`SKILL_*` and `WIKI_*` events — the two knowledge surfaces the system is supposed
to learn through are instrumented with events nothing emits.

**A function that does nothing, elaborately.**
`argus_skill/wiki/lifecycle.py:54` takes seven parameters, discards five, and
says so:

> `"""Do nothing: Agents maintain pages and INDEX.md during the mission."""`

**The same concern implemented several times over.**

| Concern | Implementations |
| --- | --- |
| Ledgers | `core/evidence_ledger.py`, `verticals/quant/search_ledger.py`, `verticals/research/literature_ledger.py` |
| Locks / leases | `core/daemon_lock.py`, `core/file_lock.py`, `core/workspace_lease.py`, `tools/gpu_lease.py` |
| State persistence | `core/pipeline_state.py`, `core/knob_store.py`, `daemon/state.py`, `life/memory.py`, `webapi/project_state.py` |

**Size.** 31 files exceed 1,000 lines and 6 exceed 2,000. The largest is
`daemon/self_maintenance.py` at 3,186 lines, followed by `life/memory.py` at
2,754.

**Spin surface.** 32 `while True` loops and 15 sleep-in-loop sites.

## 6. Schema abuse — already fixed where it mattered most

This is the one complaint the code partly answers already, and the reasoning is
worth quoting because it is the position the rest of this audit argues for.
`argus_skill/core/role_reply.py` reads a role's decision out of ordinary prose:

> Roles are not forced to emit JSON. A model told to reply with "ONE JSON object
> and NOTHING else" spends its answer satisfying a serialiser instead of
> thinking, loses the ability to explain itself, and fails the whole decision
> when it adds a sentence of context. **The harness is not smarter than the
> agent, and demanding a wire format is the harness deciding how the agent may
> speak.**

`KEY=value` or `KEY: value` both work, prose above and below costs nothing, the
last occurrence wins, and JSON is accepted but never required.

**Where it was not applied.** Five physics gates still require exact CSV column
sets; 19 modules parse frontmatter; 274 `@dataclass` and 21 `BaseModel`
definitions describe internal shapes, some of which the model is asked to
produce. The principle exists and is written down. It has not been carried
through the rest of the system.

---

## What the numbers say together

Five of six complaints are confirmed by the code, and the sixth is confirmed
everywhere the existing fix was not applied. They are not six problems. They are
one habit with six symptoms: **when something went wrong, we added a mechanism.**

Each addition was locally justified. The aggregate is a runtime with 2,277
`try:` blocks, 7,438 tokens of standing Manager instruction, 37 dead event types,
and four ways to take a lock — and an agent that fills in tables, asks permission
for the word "delete", and does not follow instructions it never had room to
read.

The corrective is not another mechanism. It is deletion.
