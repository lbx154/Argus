# Learning Vertical — Design & Implementation

> Give Argus a piece of learning material; Argus autonomously does gated CRUD on
> its **own** skill library and wiki library, distilling the material into new or
> revised skills and wiki cards. This document is the durable design record — what
> it is, how it's wired, what's LIVE vs PENDING, and the self-modification safety
> model. It reflects the design *after* an adversarial code review, so the
> safety story here is the corrected one (protected floor, not a blanket
> active-set guard).

## 1. Philosophy (the split that governs everything)

Two halves, mirroring the project's core rule (harness is dumb plumbing; research
judgment belongs to the agent/reviewer):

| | What | Owner | Where |
|---|---|---|---|
| **Method** | *how to learn* — read material, inventory the libraries, decide create/update/archive, cite evidence | agent (a hand-written skill) | `builtin_skills/engineer/study-and-curate.md`, `verticals/learning/skills/reviewer/curation-review.md` |
| **Orchestration + guardrails** | trigger, stages, gates, provenance, audit | harness | `verticals/learning/`, `SkillRouter`, `WikiRouter`, CLI |

Neither half is the system alone. A skill with no vertical is advice with no
trigger and no teeth; a vertical with no skill is the harness trying to encode
judgment it isn't smart enough for. The seed skills are hand-written to
bootstrap, then become editable by the learning loop itself (dogfooding) — but
only through the protected-update path (§5), so improving "how I learn" is a
gated, reversible, git-committed change, never a silent runtime mutation.

The learning vertical is close to the existing **curator / skill-flywheel**,
which already distills experience into skills+wiki. Learning swaps the *trigger*
(a completed engineering mission → operator-supplied material) and the *input*
(the reviewer's own verdict → that material), and reuses the same writers, gates,
lifecycle, and wiki promotion.

## 2. Pipeline (4 stages)

`ingest → study → curate → review`, `completion_gate = "none"` (no numeric metric,
no paper — the reviewer verdict ends the mission).

```
operator --material X.pdf
   │  learn CLI (input channel)  [PENDING]
   ▼
ingest : material stored immutably as a wiki source + extraction manifest
         (hash, extractor, char_count) — the provenance/fact layer.
   ▼
study  : engineer reads material + INVENTORIES its own skill/wiki libraries,
         emits learning/CHANGE_PLAN.json — create/update/archive/retire ops,
         each with EVIDENCE SPANS {source_id, locator, quote}. Judgment stage;
         reviewer gates the plan for faithfulness / non-redundancy / scope.
   ▼
curate : apply the plan. Skill CRUD → reviewer skill_ops → SkillRouter gates
         (mechanical → dedup → Manager generality, + protected/diff-aware floors).
         Wiki CRUD → structured wiki_ops → WikiRouter (evidence gate + tombstone
         retire).  [WikiRouter loop-wiring PENDING]
   ▼
review : final gate — every committed change evidence-anchored, nothing protected
         removed, no regression, wiki index rebuilt. Reviewer verdict = done.
```

The vertical mostly *reframes* what engineer/reviewer do (via `role_banner` +
`REVIEWER_CHECKLISTS` + `CHECKLIST_ITEMS`); it adds no new commit machinery —
skill_ops commit at mission close through the existing `SkillRouter`, wiki through
`WikiRouter`.

## 3. Reuse map (what's borrowed vs net-new)

**Reused wholesale:** daemon loop, planner/engineer/reviewer/Manager, `SkillRouter`
+ `SkillStore` CRUD, `LayeredSkillStore` (project→global promotion), `WikiStore`
read/write, `index.rebuild_indexes` / `promotion` / `auto_hooks`, the vertical
contract + injection (`verticals/_base.py`), `RunnerOptions.output_schema_path`
structured output, `role_context` read-only skill loading, provisional lifecycle,
`skill_tidy` source promotion.

**Net-new:** the `learning` vertical package, 2 seed skills, `WikiRouter` +
`wiki_ops`, `WikiStore.retire_page` tombstone, `skills/provenance.py`
(evidence-span verify), the `protected` skill field + category floor, the diff-aware
update gate, the `learn` CLI + ingest, and the verification harness.

## 4. The eight refinements (from design review)

1. **wiki gets a structured CRUD channel** symmetric to `skill_ops`: `wiki_ops` +
   `WikiRouter` (propose → gate → apply → audit), replacing free-hand `WikiStore`
   calls. Mechanical `auto_hooks`/`promotion` stay for the deterministic half.
2. **destructive ops are the most-gated, not least**: protected skills are never
   archived/deleted; protected updates must clear a *diff-aware* gate that sees
   old+new and rejects regressions (ordinary create/update/archive keep their
   existing gates).
3. **provenance is span-level**: every learned claim carries
   `{source_id, locator, quote}`; the harness mechanically verifies the quote is
   verbatim in the immutable source (anti-fabrication). Sufficiency is the
   reviewer's judgment.
4. **no default-global pollution**: learned skills land in the project/quarantine
   layer (`LayeredSkillStore`, provisional) and are promoted to global only after a
   real downstream mission proves them effective. [wiring PENDING]
5. **`learn --once` is dry-run by default**: the harness-shortcut path renders the
   plan/diffs and writes nothing until `--apply`. [PENDING]
6. **no-op with reason is success**: an honest "the material added nothing" passes;
   the dead-wire probe watches for *churn* (write-then-discard), not for absence of
   writes — we never reward raw library churn.
7. **material is untrusted input**: treat it as data, never instructions; audit the
   extraction (hash/extractor/char_count); SSRF-guard URL fetch via the existing
   WebFetch capability; disallow executable (fixture-backed) skills from material.
   The structural gates (§5) are the backstop if injection makes the agent propose
   a malicious op. [ingest/fetch PENDING]
8. **end-to-end retrieval must be verified on the real backend**: after a learning
   mission writes skill S / page P, assert the matcher surfaces S to a downstream
   engineer and `unified_query` surfaces P; fix `query_pack.md` staleness so wiki
   reaches the planner. [PENDING]

## 5. Self-modification safety model (corrected)

The hazard: a self-modifying mission editing the rules that govern it (the seed
skills, anti-cheat, role-identity playbooks) — the operator's #1 concern.

**The mechanism is the PROTECTED floor**, enforced mechanically in `SkillRouter`
(`_is_protected`): a skill is protected if its frontmatter carries
`protected: true` **or** its category is one of
`_PROTECTED_CATEGORIES = {anti-cheat, guardrail, role-identity}`.

- **archive/delete of a protected skill → refused** (mechanical, no judge).
- **update of a protected skill → refused by the runtime skill path**. Runtime
  Scientist/Reviewer skill candidates may create/update ordinary provisional
  skills, but protected / governing skills require an explicit source-code review
  rather than an automatic runtime update.
- **a `create` cannot shadow a protected skill** by reusing its name (a top-level
  shadow would win the matcher's last-wins resolution and neutralize the protected
  playbook without touching it) → refused.
- **removals are reversible**: skill archive → `skills/_archive/`; skill update
  snapshots `.<stem>.prev.md`; wiki retire → `pages/_retired/` tombstone (never
  overwrites a prior tombstone). Ultimate rollback: seed skills are git-tracked, so
  the runtime is disposable and re-seedable from the golden master.
- **deferred effect**: skills load at mission *start*; `skill_ops` apply at mission
  *close*. A revised skill only takes effect next mission — a mission cannot rewrite
  its own rules mid-flight.

**What we deliberately do NOT do:** a blanket "a mission can't touch any skill it
used" (active-set) guard. The review showed it breaks a legitimate flywheel
operation — retiring a skill you used and found wrong/harmful. The operator's
requirement is about *governing/seed* skills, which are protected; ordinary used
skills stay retirable. Governance ≠ every matched skill.

Seed skills (`study-and-curate`, `curation-review`) ship `protected: true`; the
kernel measurement-integrity anti-cheat skill was stamped `protected: true`; all
role-identity skills are covered by the category floor.

## 6. LIVE vs PENDING (honest status — no overselling)

**LIVE (implemented + tested, off the daemon hot path except where noted):**
- `Skill.protected` field; `SkillRouter` protected floor (flag + category);
  runtime protected-update refusal; create-shadow refusal.
- `WikiStore.retire_page` tombstone; `iter_pages` skips `_retired`.
- `skills/provenance.py` evidence-span verify (subdir-aware, ambiguity-safe).
- `WikiRouter` structured wiki_ops (create_source/create_page/update_page/
  retire_page) with evidence gate — **constructed only in tests so far**.
- `verticals/learning/` package (stages, banners, checklists) + registration; 2
  seed skills.

**PENDING (the "activation" step — needs review before it touches the running
daemon):**
- `learn` CLI + `ingest` input channel (trigger + material staging + manifest +
  SSRF-guarded URL fetch + `--once` dry-run).
- `wiki_ops` field in `reviewer_schema.json` + `_collect_wiki_ops` + constructing
  `WikiRouter` in the curate stage (so the evidence floor runs on real missions;
  until then wiki writes go through the free-hand curator path).
- `LayeredSkillStore` project-layer isolation for learning + earned global
  promotion trigger.
- `query_pack.md` regeneration fix (so learned wiki reaches the planner) + the
  Copilot end-to-end use-test.

## 7. File map

```
argus_skill/verticals/learning/__init__.py          vertical contract re-export
argus_skill/verticals/learning/stages.py            STAGE_ORDER, checks, checklists, role_banner, completion_gate
argus_skill/verticals/learning/skills/reviewer/curation-review.md   gate skill (protected)
argus_skill/builtin_skills/engineer/study-and-curate.md             method skill (protected, cross-vertical)
argus_skill/skills/store.py                         Skill.protected field
argus_skill/skills/skill_router.py                  _PROTECTED_CATEGORIES, _is_protected, protected floor, create-shadow refusal, protected-update refusal
argus_skill/manager/skill_review.py                 project skill placement/tidy judge
argus_skill/manager/_core.py                        Manager stage / routing / placement authority
argus_skill/skills/provenance.py                    verify_evidence (quote-in-source)
argus_skill/wiki/store.py                           retire_page tombstone; iter_pages _retired skip
argus_skill/wiki/router.py                          WikiRouter (structured wiki_ops)
argus_skill/skills/vertical_select.py               "learning" registered
```

## 8. Phasing

- **P0 scaffold** — data-domain JSON to exercise the 4-stage flow. (skipped; went
  straight to a real package)
- **P1 safety foundation** — protected floor, seed skills, vertical package, diff-aware
  update, create-shadow refusal. **DONE.**
- **P2 wiki first-class** — `WikiRouter`, `retire_page`, provenance. **DONE (standalone).**
- **P3 activation** — `learn` CLI/ingest, `wiki_ops` schema + curate-stage wiring,
  LayeredSkillStore isolation, query_pack fix. **NEXT.**
- **P4 verify & promote** — Copilot end-to-end use-test, earned global promotion,
  churn probe.

## 9. Decisions (resolved)

- **A** learned skills land in the project/quarantine layer, earn global by proven
  downstream use (not default-global).
- **B** learning wiki lives in a fixed learning workdir (per-project wiki reused).
- **C** learned skills stay provisional; no auto-confirm on review.
- **D** first-class vertical is the backbone; `learn --once` is a dry-run-by-default
  shortcut.
- **E** material formats: md/txt/PDF/URL, behind the §4.7 safety guardrails.

## 10. Verification (how to test the foundation)

**Automated:**

```bash
cd <repo>
# the new tests (5 ingest + 15 safety + 12 wiki/provenance)
python -m pytest tests/test_learning_ingest.py \
  tests/skills/test_protected_and_learning.py \
  tests/test_wiki_router_and_provenance.py -v
# regression (CLI parser + skills + manager)
python -m pytest tests/apps tests/skills tests/manager -q
```

**Manual — the `learn` input channel** (use an isolated workdir):

```bash
rm -rf /tmp/learn-test && mkdir -p /tmp/learn-test
printf 'GRPO clips the ratio asymmetrically for stability.\n' > /tmp/learn-test/note.md
python -m argus_skill learn --material /tmp/learn-test/note.md --base /tmp/learn-test
# inspect:
cat /tmp/learn-test/.autors/learning/wiki/sources/notes/note.md   # immutable source
cat /tmp/learn-test/learning/MATERIAL_MANIFEST.json               # sha256/extractor/char_count audit
cat /tmp/learn-test/research/PIPELINE_STATE.json                  # vertical == learning
# edge cases:
python -m argus_skill learn --material /tmp/learn-test/x.bin --base /tmp/learn-test  # -> unsupported format error
python -m argus_skill learn --material /tmp/learn-test/note.md --base /tmp/learn-test # -> already present (immutable)
```

**Manual — the protected floor is live** (a role-identity skill is un-archivable
even without an explicit `protected: true` flag):

```bash
python - <<'PY'
import tempfile, pathlib
from argus_skill.skills.store import Skill, SkillStore
from argus_skill.skills.skill_router import SkillRouter
d = pathlib.Path(tempfile.mkdtemp())/"skills"; d.mkdir()
store = SkillStore(d)
body = "## Title\nGov\n## Description\nx\n## When to use\na\n## How to solve\nb\n"
store.save(Skill(name="Gov", description="x", category="role-identity", content=body))
print(SkillRouter(skill_store=store).apply_ops([{"op":"archive","name":"Gov"}], task="t"))
# expect: {'created': 0, 'updated': 0, 'archived': 0, 'rejected': 1}
PY
```

**Known unrelated failures** in a full `pytest tests/` run (NOT from this change —
proven to fail identically on pristine HEAD via `git stash`):
`tests/test_session_resume.py::...poisoned_resume_thread` (idea-search live-call
sequence, env-dependent) and
`tests/daemon/test_life_worker.py::...stop_event...` (daemon vault-preflight makes
a live gpt-5.5 network probe — TimeoutError/502). To confirm attribution:
`git stash && python -m pytest <test> && git stash pop`.
