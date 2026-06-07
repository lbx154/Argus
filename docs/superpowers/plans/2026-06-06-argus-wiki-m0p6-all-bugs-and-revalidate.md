# Argus Wiki M0.6 — fix all 17 audit bugs + revalidate on unify_RL_argus

> **Executor:** Codex via tmux + `codex --yolo` + `/goal <this file>`.
> The architect (Claude) hands you this; he verifies. Do NOT write code
> for the architect — you (Codex) implement every task here yourself,
> run every test, and run the final live revalidation.
>
> **Scope is large (17 bugs).** Work the tasks in order. Each task is an
> independent TDD red→green→commit unit. Commit after each task so the
> architect can bisect. Do not batch commits.

## Why this exists

Two audits found 18 distinct bugs in the wiki subsystem + surrounding
argus daemon glue:

- **Architect (4):** BUG-1 wiki-collector starvation, BUG-2 sources/runs
  pollution, BUG-3 orphan sources at `sources/` root, BUG-4
  conflicts/patterns永远为0 (**deferred — out of scope this milestone**).
- **Codex gpt-5.5-xhigh audit (14):** B-1 … B-14 with cited file:line.

This milestone fixes **all 17 actionable** bugs (everything except
BUG-4, which is a design问题 deferred to M1). The work is grouped into
10 tasks by code locality and dependency, not one-task-per-bug.

## Bug → Task map

| Task | Bugs | Theme |
|---|---|---|
| 1 | B-10, B-1 | store.py: ID validator + atomic/concurrency-safe writes |
| 2 | B-9, B-11 | ingest.py: BibTeX resync + canonical paper identity (dedup) |
| 3 | B-2 | bot_state.py: atomic save + tolerate corruption |
| 4 | B-3, B-4 | supervisor.py: all-targets-present + malformed-blocker waiting |
| 5 | B-6, B-7, B-14 | validate/index/reviewer/cli: structural validation + atomic index + CLI guard |
| 6 | B-8, B-5 | curator recovery policy + final-submission cert persistence |
| 7 | BUG-1, B-12 | wiki-collector starvation short-circuit + cooldown failure semantics |
| 8 | BUG-2, BUG-3 | RunCard gating + `sources/notes/` bucket + orphan migration |
| 9 | B-13 | behavioral (not string-grep) bounded-mode tests |
| 10 | — | full sweep + restart argus on unify_RL_argus + revalidate |

**Deferred (do NOT do):** BUG-4 (pages/conflicts + pages/patterns stay
0). It needs LLM-driven conflict detection + cross-run pattern mining;
revisit in M1 after these fixes bake.

---

## Ground rules for every task

1. **TDD.** Write the failing test FIRST, run it, confirm it fails for
   the expected reason, then implement, then confirm green.
2. **No shimming/skipping tests.** If an existing test encodes the old
   (buggy) behavior, update it deliberately and note it in the commit body.
3. **ASCII-safe** in source/prompt files (use `--`, `->`, not unicode
   punctuation) to match the existing codebase convention.
4. After each task: `git add <files> && git commit -m "<msg>"`.
5. Keep the unrelated dirty file
   `paper/artifacts/slm_llm_human_hierarchy.json` untouched.

---

## Task 1 — store.py: central ID validator + atomic, concurrency-safe writes (B-10, B-1)

**Files:** `argus_skill/wiki/store.py`, new
`tests/test_wiki_store_safety.py`.

### B-10 — path traversal / nested-path IDs

`write_source` / `write_page` derive a filename from `src.id` /
`card.id` by stripping the `papers/` prefix, then join under
`sources/<subdir>/`. A BibTeX key or arxiv id like `../../x` or
`2406/12345` escapes the bucket or creates accidental nesting.

**Fix:** add a single module-level `_validate_stem(stem: str) -> str`
helper used by BOTH `write_source` and `write_page`. Reject any stem
that, after stripping the known bucket prefix, contains a path
separator (`/`, `\`), `..`, a leading dot, or is empty. Old arxiv ids
that legitimately contain a slash (e.g. `cs/0112017`) must be
**normalized** (replace `/` with `__`) rather than rejected — but the
normalization must be deterministic and reversible-enough for dedup
(coordinate with Task 2's canonical id). For M0.6 keep it simple:
replace internal `/` with `__`, then reject if `..` or leading dot
remains.

### B-1 — non-atomic, racy writes

`write_source` does `if path.exists(): raise` then `write_text` (TOCTOU
race; two processes both pass the check). `write_page` does a plain
`write_text` (a concurrent reader can see a truncated file).

**Fix:**
- Add `_atomic_write_text(path, text)`: write to
  `path.with_suffix(path.suffix + ".tmp-<pid>-<rand>")` then
  `os.replace(tmp, path)` (atomic on POSIX). Use this for `write_page`.
- For immutable sources, use exclusive create: open the tmp, then
  `os.link`/`os.replace` semantics won't give EEXIST, so instead test
  existence by attempting `os.open(path, O_CREAT|O_EXCL|O_WRONLY)` as a
  0-byte sentinel BEFORE writing, OR (simpler + portable) wrap the
  exists-check + write in a per-wiki lock (see below) and keep the
  `FileExistsError` raise. Prefer the lock approach for clarity.
- Add a per-wiki advisory lock: a `data/.wiki.lock` file guarded with
  `fcntl.flock` (POSIX) in a small context manager
  `_wiki_lock(self)`. `write_source` and `write_page` acquire it around
  the check+write. Readers (`iter_pages`, `read_source`) do NOT need the
  lock because atomic replace guarantees they see either old or new
  whole file. Keep lock hold-time minimal.

> Note: `fcntl` is POSIX-only; that's fine, argus runs on Linux. Guard
> the import so unit tests on the file lock can run without it if
> needed, but do not add a Windows path.

### Tests (`tests/test_wiki_store_safety.py`)

- `test_write_source_rejects_path_traversal`: `SourcePaper(id="papers/../../x", ...)`
  → `write_source` raises `ValueError` (or a dedicated
  `WikiIdError`) and creates **no** file outside `sources/papers/`.
- `test_write_page_rejects_path_traversal`: same for a `PageCard` with
  `id="../../evil"`.
- `test_write_source_normalizes_legacy_arxiv_slash`: id
  `papers/cs/0112017` writes to `sources/papers/cs__0112017.md` and
  round-trips via `read_source`.
- `test_concurrent_same_source_write_is_safe`: spawn 2 processes (use
  `multiprocessing` with a module-level worker fn, or
  `concurrent.futures.ProcessPoolExecutor`) that both write the SAME
  source id; exactly one succeeds, the other gets `FileExistsError`;
  the resulting file parses cleanly.
- `test_concurrent_page_write_and_read`: one writer loops rewriting a
  page; a reader loops `iter_pages()`; assert the reader never raises a
  parse error (atomic replace). Keep iteration counts small (e.g. 50)
  so the test is fast.

Red → implement → green → commit:
```
git add argus_skill/wiki/store.py tests/test_wiki_store_safety.py
git commit -m "wiki/store: central id validator + atomic, lock-guarded writes (B-10, B-1)"
```

---

## Task 2 — ingest.py: BibTeX resync + canonical paper identity (B-9, B-11)

**Files:** `argus_skill/wiki/ingest.py`,
`argus_skill/builtin_skills/engineer/wiki-collector.md`, new
`tests/test_wiki_canonical_id.py`, extend `tests/test_wiki_ingest.py`.

### B-9 — one malformed BibTeX entry aborts the rest

`_iter_bib_entries` breaks the whole scan at the first unclosed entry
(`if end is None: break`). The M0.1 plan claimed "malformed entries are
ignored." Make it true.

**Fix:** when an entry has no matching close brace (`end is None`),
**resync**: advance `idx` to the next `@` after the current `at`
(`idx = text.find("@", at + 1)`) and continue, instead of `break`.
Skip the malformed span. If no further `@`, stop.

Test (extend `tests/test_wiki_ingest.py`):
- `test_parse_bib_resyncs_after_unclosed_entry`: input = one entry
  missing its closing `}` followed by a valid entry; assert the valid
  entry IS parsed.

### B-11 — duplicate-paper detection is path-only (data pollution)

Same paper ingested as `papers/schulman2017ppo`, `papers/1707.06347`,
and a DOI-only result becomes 3 sources → 3 scratch cards. This
directly pollutes the wiki and is the highest-value fix in this task.

**Fix — canonical identity layer:**
- Add `canonical_paper_id(*, url: str | None, doi: str | None, key: str) -> str`
  to `ingest.py`. Resolution priority:
  1. arXiv id parsed from `url` (`arxiv.org/abs/<id>` or
     `arxiv.org/pdf/<id>`), normalized (strip version suffix `v\d+`,
     replace legacy `/` with `__`) → `arxiv-<id>`.
  2. DOI (from `doi` field or a `doi.org/<doi>` url), lowercased →
     `doi-<doi-with-slashes->__>`.
  3. fallback to the cleaned bibtex `key`.
- Maintain an **alias index** at `data/paper_aliases.json`:
  `{ "<alias-stem>": "<canonical-stem>" }`. On every `ingest_refs_bib`
  / collector write, compute the canonical id; if a source already
  exists for the canonical id (directly OR via alias index), **skip**
  and record the new alias → canonical mapping in the json. Only when
  the canonical id is new do you write `sources/papers/<canonical>.md`.
- `ingest_refs_bib` writes under the **canonical** stem, not the raw
  bibtex key. Record the original key inside the source body provenance
  stanza (already does via `_reconstruct_stanza`) so nothing is lost.
- Update `wiki-collector.md` Step 2 (the `SourcePaper(id=f"papers/{arxiv_id}")`
  example) to instruct: compute canonical id via
  `argus_skill.wiki.ingest.canonical_paper_id(...)` and check the alias
  index before writing. Keep the code example minimal but correct.

> Keep the alias index updates atomic (reuse Task 3's atomic-json
> helper once it exists, or a local tempfile+replace). Guard against a
> missing/corrupt alias json the same way (tolerate, start empty).

Tests (`tests/test_wiki_canonical_id.py`):
- `test_canonical_id_prefers_arxiv_over_key`: url
  `https://arxiv.org/abs/1707.06347v2`, key `schulman2017ppo` →
  `arxiv-1707.06347` (version stripped).
- `test_canonical_id_doi_when_no_arxiv`.
- `test_canonical_id_falls_back_to_key`.
- `test_ingest_same_paper_two_keys_makes_one_source`: ingest a bib with
  two entries that resolve to the same arxiv id under different keys;
  assert exactly ONE file under `sources/papers/`, and the alias json
  has both keys → canonical.
- `test_ingest_idempotent_with_alias_index`: re-running ingest writes
  nothing new and does not corrupt the alias index.

```
git add argus_skill/wiki/ingest.py argus_skill/builtin_skills/engineer/wiki-collector.md tests/test_wiki_canonical_id.py tests/test_wiki_ingest.py
git commit -m "wiki/ingest: bibtex resync + canonical paper identity with alias index (B-9, B-11)"
```

---

## Task 3 — bot_state.py: atomic save + tolerate corruption (B-2)

**Files:** `argus_skill/wiki/bot_state.py`, extend
`tests/test_wiki_bot_state.py`.

`load_bot_state` does `json.loads(path.read_text())` with no guard. A
partial write or hand-edit raises inside the planner prompt build
(`planner.py:656` calls `load_bot_state`), wedging the cycle before any
decision.

**Fix:**
- `load_bot_state`: wrap parse in try/except `(json.JSONDecodeError,
  ValueError, OSError)`. On failure, **rename the bad file aside**
  (`<path>.corrupt-<utc-timestamp>`) and return a default `BotState()`.
  Add a `notes` marker like `"recovered from corrupt state"` so it's
  visible. Never raise out of `load_bot_state`.
- `save_bot_state`: write atomically (tempfile in same dir +
  `os.replace`). Factor a small `_atomic_write_text` (or reuse a shared
  util — if you add one to a `argus_skill/wiki/_fsutil.py`, have Task 1
  use it too; otherwise duplicate the 3 lines, that's fine).

Tests:
- `test_load_bot_state_tolerates_corrupt_json`: write `"{"` to the
  path; `load_bot_state` returns a default `BotState`, does NOT raise,
  and the corrupt file is renamed aside (assert a `*.corrupt-*` sibling
  exists).
- `test_save_bot_state_is_atomic`: monkeypatch or simulate by asserting
  no `.tmp*` residue remains after save and the json round-trips.
- `test_planner_prompt_build_survives_corrupt_bot_state` (integration,
  put in `tests/test_wiki_planner_context.py` if that's where planner
  prompt tests live): corrupt `data/bot_state.json`, build the planner
  wiki block, assert no exception.

```
git add argus_skill/wiki/bot_state.py tests/test_wiki_bot_state.py tests/test_wiki_planner_context.py
git commit -m "wiki/bot_state: atomic save + tolerate corruption, never wedge planner (B-2)"
```

---

## Task 4 — supervisor.py: all-targets-present + malformed-blocker waiting (B-3, B-4)

**Files:** `argus_skill/life/supervisor.py`, extend
`tests/life/test_external_blocker_short_circuit.py`.

Both bugs live in
`_operator_only_external_blocker_wait_reason_for_project`
(supervisor.py ~91-123).

### B-3 — "any target present" wrongly resolves the blocker

Current logic computes `present = [t for t in required if (root/t).exists()]`
and `if present: continue` (treats blocker as resolved). A blocker with
targets `[a.csv, b.csv]` where only `a.csv` arrived stops waiting while
`b.csv` is still missing.

**Fix:** compute `missing = [t for t in required if isinstance(t, str)
and not (project_root / t).exists()]`. Resolve (`continue`) ONLY when
`missing` is empty. Otherwise return the wait reason and name the
missing targets (use `len(missing)` and list the first few names).

### B-4 — malformed blocker JSON silently falls through

Current `except (json.JSONDecodeError, OSError): continue` means a
half-written or hand-broken lock file is ignored → supervisor doesn't
wait → planner queues impossible repair work.

**Fix:** distinguish transient vs real:
- If the unreadable file's name ends in `.tmp`, skip it (operator mid-write).
- Otherwise, if a file MATCHING the blocker glob fails to parse, return
  an operator-visible waiting reason
  (`f"operator-only external blocker {name} is present but unreadable
  (malformed JSON); treating as active blocker pending operator fix"`)
  rather than `continue`. This fails *closed* (wait) instead of *open*
  (run planner).

Tests (extend `tests/life/test_external_blocker_short_circuit.py`):
- `test_blocker_waits_until_all_targets_present`: blocker with two
  required targets; create only one; assert the wait reason is
  non-empty and names the missing one.
- `test_blocker_resolves_when_all_targets_present`: create both; assert
  wait reason is empty (blocker resolved).
- `test_malformed_blocker_json_is_treated_as_waiting`: write invalid
  json to `operator_only_external_blocker_x.json`; assert a non-empty
  wait reason (fail-closed), NOT a fall-through to planner.
- `test_blocker_tmp_file_is_ignored`: a `*.json.tmp` malformed file is
  skipped (no wait reason from it alone).

```
git add argus_skill/life/supervisor.py tests/life/test_external_blocker_short_circuit.py
git commit -m "supervisor: blocker resolves only when ALL targets present; malformed lock fails closed (B-3, B-4)"
```

---

## Task 5 — structural validation + atomic index + CLI guard (B-6, B-7, B-14)

**Files:** `argus_skill/wiki/validate.py`, `argus_skill/wiki/index.py`,
`argus_skill/engineer/reviewer.py`, `argus_skill/apps/cli.py`, extend
`tests/test_wiki_validate.py`, `tests/test_wiki_index.py`,
`tests/test_wiki_curator_fixed_loading.py`, `tests/apps/test_cli_parser.py`.

### B-6 — partial wiki dirs activate curator but aren't validated

`_load_wiki_curator_skill_if_present` (reviewer.py ~84-96) only checks
`(p / "wiki").is_dir()`. A wiki missing `data/schema.yaml` or
`query_pack.md` still activates the curator; `validate_wiki` only checks
dangling source refs.

**Fix:**
- Add `is_initialized_wiki(root: Path) -> bool` to `bootstrap.py` (or
  `validate.py`): returns True only when the required tree exists —
  `data/schema.yaml`, `query_pack.md`, and the `sources/` + `pages/`
  subdirs. 
- `_load_wiki_curator_skill_if_present`: require `is_initialized_wiki`,
  not just a `wiki/` dir.
- Add `validate_wiki_structure(store)` (called by `validate_wiki` first)
  that raises `ValidationError` listing missing required files.

### B-7 — index rebuild can leave mixed old/new query layer

`rebuild_indexes` writes the four query files in place sequentially; a
failure midway leaves `by-status.md` new but `open-contradictions.md`
stale, and `validate_wiki` never inspects queries.

**Fix:** render ALL four strings first (pure, no I/O), then write them
to a fresh temp dir `queries.new-<pid>/`, then atomically swap:
`os.replace(queries.new, queries)` after moving the old aside, or
write each via atomic temp+replace within the existing `queries/` so a
crash leaves every individual file either fully-old or fully-new.
Simplest robust approach: render all → write each with atomic
temp+replace. If any render raises, nothing is written.

### B-14 — CLI ingest silently creates a wrong wiki path

`_cmd_wiki_ingest` (cli.py ~1440) does `WikiStore(args.wiki)` and
ingests; a typo'd `--wiki` path gets `sources/papers/` created under it
with no schema/templates.

**Fix:** before ingesting, require `is_initialized_wiki(args.wiki)`.
If not initialized, print an error to stderr and return nonzero,
creating NO directories — UNLESS an explicit `--init` flag is passed
(add it; when set, bootstrap the wiki first via the existing
`init_wiki`).

Tests:
- `tests/test_wiki_validate.py`:
  `test_validate_structure_flags_missing_schema` and
  `test_validate_structure_ok_for_initialized`.
- `tests/test_wiki_curator_fixed_loading.py`:
  `test_curator_not_loaded_for_uninitialized_wiki` (only `wiki/` dir,
  no `data/schema.yaml`) → reviewer does NOT inject curator.
- `tests/test_wiki_index.py`:
  `test_index_rebuild_atomic_on_failure` — monkeypatch one renderer to
  raise, assert pre-existing query files are unchanged (or absent if
  none existed), and no `.tmp`/`.new` residue.
- `tests/apps/test_cli_parser.py` (+ a behavioral cli test if there's a
  home for it): `test_wiki_ingest_rejects_uninitialized_path` →
  nonzero exit, no dirs created; `test_wiki_ingest_init_flag_bootstraps`.

```
git add argus_skill/wiki/validate.py argus_skill/wiki/index.py argus_skill/wiki/bootstrap.py argus_skill/engineer/reviewer.py argus_skill/apps/cli.py tests/test_wiki_validate.py tests/test_wiki_index.py tests/test_wiki_curator_fixed_loading.py tests/apps/test_cli_parser.py
git commit -m "wiki: structural validation gate + atomic index rebuild + CLI ingest path guard (B-6, B-7, B-14)"
```

---

## Task 6 — curator recovery policy + final-submission cert persistence (B-8, B-5)

**Files:** `argus_skill/builtin_skills/reviewer/wiki-curator.md`,
`argus_skill/wiki/ingest.py`, `argus_skill/life/supervisor.py`, extend
`tests/test_wiki_ingest.py`, add `tests/life/test_final_submission_cert_persists.py`.

### B-8 — curator Step 0 backfill has no recovery policy

If LIT_MATRIX enrichment hits one malformed existing source, the whole
backfill aborts and the reviewer may block the mission for wiki
maintenance even when the mission's real objective is unrelated.

**Fix:**
- `ingest_lit_matrix` (and `ingest_refs_bib`): wrap each per-row /
  per-entry body operation in try/except; on a single bad source,
  record a warning and CONTINUE (do not abort the batch). Return a
  small result object or a `(count, warnings)` tuple — keep backward
  compat by returning the count and adding an optional out-param or a
  module-level "last warnings" is ugly; prefer returning a small
  dataclass `IngestResult(written, skipped, warnings)` and updating
  callers (CLI + curator code examples + tests). If you change the
  return type, update ALL callers in this repo.
- `wiki-curator.md` Step 0: add an explicit "Recovery policy" note:
  curator/backfill failures are **isolated warnings** appended to the
  reviewer summary; they MUST NOT block the mission verdict UNLESS the
  mission's objective is explicitly wiki repair/maintenance.

### B-5 — final-submission certification ages out after 50 journal entries

`_journal_has_full_emnlp_gate_success` scans only `journal.tail(50)`.
After 51 later missions, an open-ended daemon forgets the cert and
re-enqueues "Prove final submission readiness."

**Fix:** persist a durable project-level flag. On recording a
`mission_complete` with `final_submission_certified=True`, also write a
sentinel file under the project (e.g.
`.autors/<project>/final_submission_certified.json` or a path next to
the journal). `_journal_has_full_emnlp_gate_success` returns True if
EITHER the sentinel exists OR the tail scan finds it. (Keep the tail
scan as a fast path / backward-compat.)

> This bug did not bite M0.4/M0.5 because those runs were bounded, but
> it WILL bite any long open-ended run, so fix it now.

Tests:
- `tests/test_wiki_ingest.py`:
  `test_ingest_lit_matrix_continues_past_one_bad_source` — corrupt one
  existing source, assert the others still enrich and a warning is
  returned.
- `tests/life/test_final_submission_cert_persists.py`: write a
  certified `mission_complete`, append 51 later non-cert entries, assert
  `_journal_has_full_emnlp_gate_success()` (or the sentinel-aware
  wrapper) still returns True. Use the existing journal/memory test
  fixtures from `tests/life/`.

```
git add argus_skill/builtin_skills/reviewer/wiki-curator.md argus_skill/wiki/ingest.py argus_skill/apps/cli.py argus_skill/life/supervisor.py tests/test_wiki_ingest.py tests/life/test_final_submission_cert_persists.py
git commit -m "curator: isolated backfill warnings; supervisor: durable final-submission cert (B-8, B-5)"
```

---

## Task 7 — wiki-collector starvation + cooldown failure semantics (BUG-1, B-12)

**Files:** `argus_skill/life/supervisor.py`,
`argus_skill/wiki/bot_state.py`, `argus_skill/planner/planner.py`,
`argus_skill/builtin_skills/engineer/wiki-collector.md`, extend
`tests/life/test_external_blocker_short_circuit.py`,
`tests/test_wiki_bot_state.py`.

### BUG-1 — wiki_collect never runs (most important behavioral bug)

The M0.5 pre-planner short-circuit (`_operator_external_blocker_short_circuit_decision`)
skips the WHOLE planner cycle when an external blocker is present.
Real deployments almost always have a persistent blocker (missing data
/ GPU / perms), so `wiki_collect` (which the planner only suggests
mid-cycle) never gets enqueued — `sources/repos` stays 0 and
`bot_state.json` never appears across 5 real runs.

**Fix — blocker short-circuit whitelist for ONE wiki_collect:**
- In the short-circuit decision, BEFORE returning "skip planner",
  check the wiki collect cooldown (reuse `cooldown_elapsed` +
  `load_bot_state`, 12h). If the cooldown has elapsed AND no
  wiki_collect has run today, allow the supervisor to enqueue **exactly
  one** `wiki_collect` mission, then resume short-circuiting on
  subsequent cycles. This is a narrow, budget-bounded escape valve: one
  small train-free mission per 12h window, even while blocked.
- Implementation choice: the cleanest is to have the short-circuit
  decision return a small struct/enum with three outcomes —
  `RUN_PLANNER`, `SKIP` (pure wait), `RUN_WIKI_COLLECT_THEN_SKIP`. The
  supervisor enqueues a single wiki_collect backlog item for the third.
  Mark the item so it bypasses the normal "blocked" gate (it has no
  external dependency). Ensure the per-mission + daily budget caps still
  apply (do NOT bypass budget).
- After the wiki_collect mission completes, `bot_state.last_*` is
  updated by the collector (Step 3), so the next cycle's cooldown check
  returns False → pure short-circuit until the window reopens.

### B-12 — cooldown penalizes total failure for 12h

`wiki-collector.md` Step 3 updates `last_collected_at` even when
`hit_count == 0` (network/rate-limit failure), so the planner suppresses
retries for the full 12h success window.

**Fix:**
- Add `last_attempted_at: datetime | None = None` to `BotState`.
- Step 3 of `wiki-collector.md`: always set `last_attempted_at`; only
  set `last_collected_at` when `hit_count > 0`. On failure increment
  `consecutive_failures` (already does).
- The planner's cooldown gate (planner.py ~658) should use a
  **failure-aware backoff**: if the last attempt was a failure
  (`last_collected_at` older than `last_attempted_at` or
  `consecutive_failures > 0`), use a SHORTER backoff derived from
  `consecutive_failures` (e.g. `min(12, 0.5 * 2**failures)` hours,
  capped at 12) rather than the full 12h. Add a helper
  `collect_backoff_hours(state) -> float` to `bot_state.py` and use it
  in both the planner suggestion gate and the Task-7 short-circuit
  whitelist so they agree.

Tests:
- `tests/test_wiki_bot_state.py`:
  `test_failed_collect_uses_short_backoff` — a state with
  `consecutive_failures=1` and only `last_attempted_at` set yields a
  backoff < 12h; a clean success yields the full 12h.
- `tests/life/test_external_blocker_short_circuit.py`:
  `test_blocker_present_but_cooldown_elapsed_allows_one_wiki_collect` —
  blocker present + cooldown elapsed → decision is
  `RUN_WIKI_COLLECT_THEN_SKIP` and exactly one wiki_collect is
  enqueued; `test_blocker_present_cooldown_not_elapsed_pure_skip` —
  blocker present + recent collect → pure `SKIP`, planner not called,
  nothing enqueued; `test_no_blocker_runs_planner` — unchanged path.

```
git add argus_skill/life/supervisor.py argus_skill/wiki/bot_state.py argus_skill/planner/planner.py argus_skill/builtin_skills/engineer/wiki-collector.md tests/life/test_external_blocker_short_circuit.py tests/test_wiki_bot_state.py
git commit -m "wiki-collect: escape-valve one collect per cooldown even when blocked; failure-aware backoff (BUG-1, B-12)"
```

---

## Task 8 — RunCard gating + sources/notes bucket + orphan migration (BUG-2, BUG-3)

**Files:** `argus_skill/wiki/schema.py`, `argus_skill/wiki/store.py`,
`argus_skill/wiki/validate.py`,
`argus_skill/builtin_skills/engineer/argus-engineer-role.md`, new
`argus_skill/wiki/migrate.py` (one-shot), extend
`tests/test_wiki_schema.py`, `tests/test_wiki_store.py`,
`tests/test_wiki_validate.py`.

### BUG-2 — sources/runs polluted by stage_check artifacts

`argus-engineer-role.md` "Mission-close RunCard" says any mission that
"produced training/eval artifacts" writes a RunCard, but stage_check /
handoff / repair missions wrote 6 non-run cards into `sources/runs/`.

**Fix:** tighten the RunCard prompt condition to be unambiguous: write
a RunCard ONLY when the mission produced **non-empty `metrics`**
(loss/score/eval numbers) OR **non-empty `artifacts`** (checkpoint /
sample grid / curve png). stage_check / handoff / repair / blocker
missions (empty metrics AND empty artifacts) MUST NOT write a RunCard —
their state notes go to `sources/notes/` (BUG-3 below). Make the
condition a literal checklist in the prompt.

### BUG-3 — orphan "operation notes" dumped at sources/ root

The engineer wanted to record operational observations but schema only
had papers/repos/runs, so 6 useful 13KB notes landed at `sources/` root
where `iter_pages` / `validate_wiki` ignore them.

**Fix — add a 4th source type `SourceNote`:**
- `schema.py`: `@dataclass SourceNote(id, title, mission_id, created_at,
  tags: list[str], body)`; add frontmatter parse/serialize support and
  register it.
- `store.py`: add `"notes"` to `_SOURCE_SUBDIR`; support
  `write_source(SourceNote)` and an `iter_note_sources()` (mirror
  `iter_run_sources`). Apply Task 1's id validator + atomic write.
- `argus-engineer-role.md`: add a short "Operational note (wiki
  side-effect)" section — non-run state observations go to
  `sources/notes/<date>-<slug>.md` via `SourceNote`, NEVER to
  `sources/runs/` or `sources/` root.
- `validate.py`: add a check that warns (does NOT hard-fail) if any
  `*.md` exists directly under `sources/` root (not in a known
  subdir) — surface orphans instead of silently ignoring.
- `argus_skill/wiki/migrate.py`: a one-shot `migrate_orphan_sources(store)`
  that moves any `sources/*.md` (root-level) into `sources/notes/` as
  `SourceNote`s (preserve body; derive title from filename). Expose it
  via a CLI subcommand `argus-skill wiki migrate --wiki <path>` OR call
  it in Task 10's revalidation script — your choice, but it must be
  runnable on unify_RL_argus.

Tests:
- `tests/test_wiki_schema.py`: `test_source_note_roundtrip`.
- `tests/test_wiki_store.py`: `test_write_and_iter_note_sources`;
  `test_note_id_validated` (path traversal rejected via Task 1).
- `tests/test_wiki_validate.py`: `test_validate_warns_on_root_orphan`
  (a `sources/x.md` at root produces a warning, not a crash).
- `tests/test_wiki_migrate.py` (new): `test_migrate_moves_root_orphans_to_notes`.

```
git add argus_skill/wiki/schema.py argus_skill/wiki/store.py argus_skill/wiki/validate.py argus_skill/wiki/migrate.py argus_skill/builtin_skills/engineer/argus-engineer-role.md argus_skill/apps/cli.py tests/test_wiki_schema.py tests/test_wiki_store.py tests/test_wiki_validate.py tests/test_wiki_migrate.py
git commit -m "wiki: SourceNote bucket + RunCard gating + orphan migration (BUG-2, BUG-3)"
```

---

## Task 9 — behavioral bounded-mode tests (B-13)

**Files:** `tests/test_bounded_disables_emnlp_gate.py` (rewrite the
string-grep assertions).

The M0.4/M0.5 tests assert that the SOURCE STRING
`full_emnlp_gate=...` appears in `life_worker.py` / `_life_repl.py`. A
refactor that moves config construction passes the test while bounded
mode silently regresses.

**Fix:** replace the `inspect.getsource(...)` string asserts with
**behavioral** assertions that actually construct the config path with
`continuous_open_ended=False` (bounded) and `=True` (open-ended) and
assert the resulting `LifeSupervisorConfig.full_emnlp_gate` is `False`
and `True` respectively. If constructing the full daemon is too heavy,
extract the config-construction into a small testable helper
(`_build_supervisor_config(cfg) -> LifeSupervisorConfig`) in
`life_worker.py` and have both the daemon and the test call it. Do the
same for `_life_repl.py` (or assert via its bounded arg). Keep the two
existing test NAMES (`test_bounded_disables_full_emnlp_gate`,
`test_unbounded_keeps_full_emnlp_gate`) so history is traceable, but
make their bodies behavioral.

Red (rewrite asserts so they fail against current string-only code if
no helper exists) → extract helper → green.

```
git add tests/test_bounded_disables_emnlp_gate.py argus_skill/daemon/life_worker.py argus_skill/apps/_life_repl.py
git commit -m "tests: bounded-mode asserts on config behavior, not source strings (B-13)"
```

---

## Task 10 — full sweep + restart argus on unify_RL_argus + revalidate

This is the architect's "重新测试argus skills的状态" requirement. Do NOT
skip it.

### Step 1 — full test sweep

```bash
cd /data/yijia/argus-skill
pytest tests/test_wiki_*.py tests/life/ tests/planner/ tests/apps/ \
  tests/tools/test_stage_check_fail_closed.py \
  tests/test_bounded_disables_emnlp_gate.py -q 2>&1 | tail -30
```
Expected: all green. Fix any regression before proceeding. Report the
final pass count.

### Step 2 — migrate existing wiki orphans + pollution on the target

```bash
W=/data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki
# snapshot BEFORE
echo "BEFORE:"; for d in sources/papers sources/repos sources/runs sources/notes pages/techniques pages/conflicts pages/patterns; do printf '%s: ' "$d"; find $W/$d -maxdepth 1 -type f 2>/dev/null | wc -l; done
ls $W/sources/*.md 2>/dev/null   # root orphans

# run the BUG-3 migration (move root orphans -> notes) and rebuild indexes
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts \
  argus-skill wiki migrate --wiki "$W"   # or python -m argus_skill.wiki.migrate
```
The 6 stage_check root orphans should move into `sources/notes/`. The 6
polluted `sources/runs/` stage_check cards: leave them (immutable) but
the migration may optionally relabel — do NOT delete data; if you move
them, move to `sources/notes/`. Record what you did.

### Step 3 — restart the daemon, blocker PRESENT, watch wiki_collect fire

The whole point of BUG-1 is that wiki_collect now runs **even while a
blocker is present**. So restart WITH the existing blocker in place
(don't clear it), and confirm a single wiki_collect mission gets
enqueued within the cooldown window.

```bash
cd /data/yijia/unify_RL_argus
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=$PWD/.argus_special_prompts argus-skill --status | grep -i daemon
# if running, stop first:
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=$PWD/.argus_special_prompts argus-skill --daemon-stop || true

# ensure bot_state cooldown is open (delete or age it so a collect is allowed now):
rm -f "$PWD/.autors/unify_RL_argus/wiki/data/bot_state.json"

OBJECTIVE='Survey + diagnose: continue building the process+terminal reward wiki for image-editing RL. Consult the wiki query_pack first. Train-free: no GPU. If blocked on external artifacts, the wiki-collector escape valve should still ingest new arxiv/github sources.'

tmux send-keys -t argus-unify-rl 'cd /data/yijia/unify_RL_argus' Enter
tmux send-keys -t argus-unify-rl "ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts argus-skill --daemon-fg --continuous --bounded --objective \"$OBJECTIVE\"" Enter
sleep 45
tmux capture-pane -t argus-unify-rl -p | tail -15
```

### Step 4 — wait one collect window, snapshot AFTER

```bash
sleep 240
W=/data/yijia/unify_RL_argus/.autors/unify_RL_argus/wiki
echo "AFTER:"; for d in sources/papers sources/repos sources/runs sources/notes pages/techniques; do printf '%s: ' "$d"; find $W/$d -maxdepth 1 -type f 2>/dev/null | wc -l; done
test -f "$W/data/bot_state.json" && echo "bot_state: PRESENT" || echo "bot_state: ABSENT"
# event scan: did a wiki_collect mission get enqueued? any forbidden loop titles?
PROJ=/home/yifanyang/.argus-skill/projects
grep -hE 'wiki_collect|enqueued_titles' $PROJ/*/events.jsonl 2>/dev/null | tail -10
grep -hcE 'Prove final submission readiness|Repair benchmark external-artifact' $PROJ/*/events.jsonl 2>/dev/null | tail -1
```

### Step 5 — stop daemon, report

Stop the daemon to cap spend, then report to the architect:

1. **Test sweep**: final pass count, any tests changed and why.
2. **Per-task commits**: list the 9 implementation commit SHAs +
   subjects (Tasks 1-9) and this revalidation note.
3. **Wiki BEFORE→AFTER**: the per-bucket counts. The key signals:
   - `sources/notes/` went from 0 → ≥6 (BUG-3 migration worked)
   - `sources/` root orphans went to 0 (BUG-3)
   - `bot_state.json` is PRESENT (BUG-1 — collector fired)
   - `sources/repos` and/or `sources/papers` increased (BUG-1 —
     collect actually ingested) OR, if network was unavailable,
     `consecutive_failures` incremented and the short backoff applied
     (B-12) — state that explicitly.
   - `sources/runs/` did NOT gain new stage_check pollution (BUG-2)
4. **No loop**: zero new "Prove final submission readiness" / "Repair
   benchmark external-artifact" enqueues (B-3/B-4/B-5 + M0.5 still hold).
5. **Cost** for the revalidation run.

Do NOT babysit beyond Step 5. The architect monitors.

---

## Definition of done

- 9 implementation commits (Tasks 1-9), each green in isolation.
- Full sweep green (Task 10 Step 1).
- On unify_RL_argus: `sources/notes/` populated, root orphans gone,
  `bot_state.json` present, no new stage_check pollution in
  `sources/runs/`, no submission/repair loop, collect fired (or failed
  with correct short backoff) while a blocker was present.
- All 17 actionable bugs (B-1…B-14 except none; BUG-1/2/3) addressed.

## Non-goals (deferred)

- **BUG-4**: pages/conflicts + pages/patterns auto-population. Needs
  LLM conflict detection + cross-run pattern mining. M1.
- Windows file-locking support (Linux-only `fcntl` is fine).
- Backfilling canonical ids for the 25 already-ingested papers
  (B-11 applies to NEW ingests; a retro-dedup pass is optional and can
  be a follow-up — if you do it, do it as a separate clearly-labeled
  commit and never delete source data, only add alias mappings).
