# Argus M0.7 — thread `--bounded` into the reviewer acceptance gate

> **Executor:** Codex via tmux + `codex --yolo` + `/goal <this file>`.
> The architect (Claude) hands you this; he verifies. You (Codex)
> implement, test, and run the live re-validation yourself.
>
> **Operator requirement (explicit):** at every code change in this plan,
> add a short `why` comment explaining the reason for the change. The
> operator asked for this specifically. Keep comments to 1-3 lines, lead
> with the WHY (not the WHAT), and reference this milestone (M0.7) + the
> root cause so a future reader understands the intent.

## Why this exists

M0.6 fixed 17 wiki/daemon bugs and we then ran a real end-to-end test:
a **bounded, train-free** diagnostic objective ("diagnose whether Bagel
RL is bottlenecked at SFT / data / RL"). The engineer produced an
excellent 18 KB report in 270 s; the engineer self-check passed; the
reviewer itself certified the report. **But the mission then looped 7
times and failed**, burning ~$1.93, with this verbatim reason:

> "The bounded diagnostic report itself is **certified** by concrete
> self-check output, but the injected acceptance check for the broader
> benchmark stage still fails fail-closed because official
> CompBench/WISE/OneIG scored artifacts/access are missing. Under the
> reviewer contract, a failed acceptance check prevents a clean done
> verdict unless the blocker is external; here it is external
> data/access under a no-training/no-GPU/no-download mission."

### Root cause (this is the second head of the M0.4 bug)

`--bounded` (i.e. `LifeWorkerConfig.continuous_open_ended = False`)
was only threaded into the **supervisor** project-done path
(M0.4/M0.5 fixed `full_emnlp_gate`). It was **never** threaded into the
**reviewer per-round acceptance gate**, which is a different code path:

- `argus_skill/daemon/life_worker.py:108-110` — `check_commands`
  defaults to `'{argus_python} -m argus_skill.tools.stage_check
  --project-root .'`. This command is run after every engineer round as
  an automated acceptance gate (consumed via
  `argus_skill/mission/engine.py:197` and
  `argus_skill/engineer/runner.py:976`).
- `argus_skill/tools/stage_check.py` has **zero** bounded awareness:
  its argparse (`stage_check.py:291-292`) only accepts `--project-root`
  and `--stage`.
- `stage_check.py:327-333` collects `_blocked_pipeline_findings(...)`
  (project-level paper-pipeline state: `research/PIPELINE_STATE.json`
  blocked, `experiments/BENCHMARK_*` gates not passed, fewer than 3
  authentic scored benchmark families). `stage_check.py:390` folds
  `blocked_state_fail = len(blocked_findings)` straight into
  `total_failed`, so `stage_check.py:400` returns exit code 1.
- `automated_gates.py` (header comment) confirms the round is gated by
  `stage_check` exit code and **"the reviewer cannot overrule"** a
  structural failure. So even a reviewer that has certified the report
  cannot sign off — the automated gate hard-blocks it.

So a bounded diagnostic mission has **no clean path to done**: with the
operator blocker present the supervisor short-circuits the planner (the
mission never runs); with the blocker removed the mission runs and
produces a certified deliverable, but the reviewer's `stage_check`
acceptance gate fail-closes on paper-pipeline benchmark state that is
irrelevant to a train-free diagnosis.

## What this plan does

Thread `--bounded` into the acceptance gate so that, in bounded mode,
**project-level paper-pipeline "blocked" findings are surfaced as
advisory (printed, never block)** instead of counting into the exit
code. Crucially, **anti-fraud / provenance `structural` gates STILL
block** in bounded mode — bounded means "don't gate me on paper-pipeline
benchmark readiness", NOT "skip all checks".

This is the exact analogue of M0.4: M0.4 threaded the bounded flag into
the supervisor's `full_emnlp_gate`; M0.7 threads the same flag into the
reviewer's `stage_check` acceptance gate.

### The precise blast radius

In `stage_check.py:388-400`, `total_failed` has three addends:

```
total_failed = failed + structural_block + blocked_state_fail
               ^^^^^^   ^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^
               shell    anti-fraud gates    paper-pipeline state
```

M0.7 changes ONLY how `blocked_state_fail` is treated in bounded mode
(reclassify as advisory). `failed` (shell checks) and `structural_block`
(anti-fraud/provenance) are **untouched** — they still block in bounded
mode. This is the whole safety argument: bounded relaxes the
paper-pipeline benchmark gate and nothing else.

---

## File structure

**Modified:**
| Path | Change |
|---|---|
| `argus_skill/tools/stage_check.py` | add `--bounded`; in bounded mode reclassify `blocked_state_fail` as advisory (don't count into exit code); `why` comments |
| `argus_skill/daemon/life_worker.py` | thread `--bounded` into the `stage_check` check command when `continuous_open_ended is False`; `why` comment |
| `argus_skill/apps/_life_repl.py` | mirror the same threading for the REPL path; `why` comment |

**New:**
| Path | Purpose |
|---|---|
| `tests/tools/test_stage_check_bounded_advisory.py` | bounded reclassifies pipeline-blocked as advisory; anti-fraud still blocks |
| `tests/test_bounded_threads_into_check_commands.py` | bounded → check command carries `--bounded`; unbounded → it does not |

---

## Task 1 — stage_check: `--bounded` reclassifies paper-pipeline blocks as advisory

**Files:** `argus_skill/tools/stage_check.py`, new
`tests/tools/test_stage_check_bounded_advisory.py`.

### Step 1 — write the failing tests first (TDD)

Create `tests/tools/test_stage_check_bounded_advisory.py`. Use a tmp
project root and write the minimal JSON files that
`_blocked_pipeline_findings` / `_benchmark_external_findings` read, so
you can drive stage_check's exit code deterministically. Invoke
`stage_check.main()` in-process (monkeypatch `sys.argv`) or via
`subprocess` — either is fine; prefer in-process so you can assert on
the printed advisory marker.

Tests:
- `test_pipeline_blocked_fails_closed_when_not_bounded`: write
  `research/PIPELINE_STATE.json` with `{"status":"blocked",...}`; run
  stage_check WITHOUT `--bounded`; assert exit code == 1 and the finding
  is printed under the fail-closed (❌) section. (This pins current
  behavior so we don't silently regress non-bounded projects.)
- `test_pipeline_blocked_is_advisory_when_bounded`: same project, run
  WITH `--bounded`; assert exit code == 0 and the same finding is now
  surfaced as advisory (📋), and the summary line reports it under
  advisory, not under "fail-closed state finding(s)".
- `test_benchmark_external_block_is_advisory_when_bounded`: write
  `experiments/BENCHMARK_ARTIFACT_BUNDLE_STATUS.json` with
  `{"passed": false, ...}`; bounded → exit 0 advisory; unbounded →
  exit 1.
- `test_structural_antifraud_gate_still_blocks_when_bounded`: this is
  the safety test. Construct a project state where an automated
  **structural** gate fails (e.g. a broken evidence chain — see how
  `tests/tools/test_stage_check_fail_closed.py` sets up structural gate
  failures and reuse that fixture/approach). Run WITH `--bounded`;
  assert exit code is STILL 1. Bounded must NOT pardon anti-fraud gates.

Run them; confirm the bounded ones fail (because `--bounded` doesn't
exist yet) and the structural one's expectation is well-formed.

### Step 2 — implement

In `argus_skill/tools/stage_check.py`:

1. argparse (near line 291): add
   ```python
   # M0.7: bounded one-shot diagnostic/survey missions must not be gated
   # on project-level paper-pipeline benchmark readiness (official scores,
   # >=3 scored families). Those are irrelevant to a train-free deliverable.
   # We still block on anti-fraud structural gates below; --bounded only
   # downgrades paper-pipeline "blocked" *state* findings to advisory.
   parser.add_argument("--bounded", action="store_true")
   ```

2. Where `blocked_findings` are printed (line 327-333) and counted
   (line 328 `blocked_state_fail = len(blocked_findings)`): when
   `args.bounded`, print them with the advisory marker (📋) and DO NOT
   put them into `blocked_state_fail`. Add them to `advisory_count`
   instead so the summary line stays accurate. Add a `why` comment:
   ```python
   # M0.7: in bounded mode these are project-level paper-pipeline state
   # findings (missing official benchmark scores, <3 scored families).
   # A bounded train-free diagnosis cannot and should not produce them,
   # so surface them as advisory facts for the reviewer instead of
   # hard-blocking the round. Anti-fraud structural gates are unaffected.
   ```

3. Exit-code line (390): ensure `blocked_state_fail` is 0 in bounded
   mode (because the findings were reclassified), so `total_failed`
   excludes them while still including `failed` + `structural_block`.

> Do NOT change `structural_block` handling. The whole point is that
> anti-fraud gates keep blocking in bounded mode.

### Step 3 — green + commit

```
pytest tests/tools/test_stage_check_bounded_advisory.py tests/tools/test_stage_check_fail_closed.py -q
git add argus_skill/tools/stage_check.py tests/tools/test_stage_check_bounded_advisory.py
git commit -m "stage_check: --bounded downgrades paper-pipeline blocks to advisory; anti-fraud still blocks (M0.7)"
```

---

## Task 2 — thread `--bounded` into the daemon + REPL check command

**Files:** `argus_skill/daemon/life_worker.py`,
`argus_skill/apps/_life_repl.py`, new
`tests/test_bounded_threads_into_check_commands.py`.

The default `check_commands` entry
(`life_worker.py:108-110`) hardcodes
`stage_check --project-root .` with no bounded flag. When the daemon is
bounded (`continuous_open_ended is False`) the stage_check invocation
must carry `--bounded`.

### Step 1 — failing test (TDD)

Create `tests/test_bounded_threads_into_check_commands.py`:
- `test_bounded_appends_bounded_flag_to_stage_check`: build a
  `LifeWorkerConfig(..., continuous_open_ended=False)` and assert the
  effective check command(s) that reach the mission contain
  `stage_check` AND `--bounded`.
- `test_unbounded_check_command_has_no_bounded_flag`:
  `continuous_open_ended=True` → the stage_check command does NOT
  contain `--bounded` (open-ended paper projects keep the strict gate).
- Mirror the assertion for the REPL config path in `_life_repl.py`
  (`continuous_open_ended` / bounded arg there — match the local name).

Pick the assertion surface that matches how you implement Step 2 (a
small pure helper is easiest to test — see below).

### Step 2 — implement

Add a tiny pure helper (e.g. in `life_worker.py`, importable by both
sites) and a `why` comment:
```python
def _apply_bounded_to_check_commands(commands: list[str], *, bounded: bool) -> list[str]:
    # M0.7: thread --bounded into the stage_check acceptance gate. Without
    # this, a bounded one-shot mission is hard-blocked by the reviewer's
    # automated stage_check on paper-pipeline benchmark state it can never
    # satisfy (the M0.4 bug's second head: bounded was only wired into the
    # supervisor's full_emnlp_gate, never into the reviewer acceptance gate).
    if not bounded:
        return list(commands)
    out: list[str] = []
    for cmd in commands:
        if "stage_check" in cmd and "--bounded" not in cmd:
            cmd = cmd + " --bounded"
        out.append(cmd)
    return out
```
Apply it where `check_commands` is materialized for the mission:
- In `life_worker.py` at the point the config's `check_commands` is
  passed onward (around line 1110 `ns.check_commands = list(cfg.check_commands)`),
  wrap with `_apply_bounded_to_check_commands(..., bounded=not cfg.continuous_open_ended)`.
- In `_life_repl.py` (around line 861 where `check_commands` is built),
  apply the same helper using the REPL's bounded/open-ended flag.

> Only stage_check commands are touched; any custom check command passes
> through unchanged. This keeps the change surgical.

### Step 3 — green + commit

```
pytest tests/test_bounded_threads_into_check_commands.py -q
git add argus_skill/daemon/life_worker.py argus_skill/apps/_life_repl.py tests/test_bounded_threads_into_check_commands.py
git commit -m "daemon+repl: thread --bounded into stage_check acceptance gate (M0.7, M0.4 second head)"
```

---

## Task 3 — full sweep + live re-validation on unify_RL_argus

This is the proof: the SAME bounded diagnostic objective that looped 7×
in M0.6 must now run, get accepted, and reach a clean done WITHOUT a
review loop.

### Step 1 — full sweep

```bash
cd /data/yijia/argus-skill
pytest tests/tools/ tests/test_bounded_*.py tests/life/ tests/planner/ tests/test_wiki_*.py -q 2>&1 | tail -20
```
Expected: green. Report the pass count.

### Step 2 — restart argus with the diagnostic objective

The diagnostic objective file already exists at
`/data/yijia/unify_RL_argus/.argus_diag_objective.txt` (the architect
wrote it; it is bounded, train-free, requires reading the wiki and
citing technique cards). The prior v2 report already exists at
`reports/bagel_rl_root_cause_diagnosis_v2_20260607.md`.

```bash
cd /data/yijia/unify_RL_argus
ARGUS_SKILL_SPECIAL_PROMPTS_DIR=$PWD/.argus_special_prompts argus-skill --daemon-stop 2>/dev/null || true
# make sure no operator blocker is in the glob path (the architect already moved it):
find diagnosis -maxdepth 1 -name 'operator_only_external_blocker*.json'   # expect empty

tmux send-keys -t argus-unify-rl 'cd /data/yijia/unify_RL_argus' Enter
tmux send-keys -t argus-unify-rl "ARGUS_SKILL_SPECIAL_PROMPTS_DIR=/data/yijia/unify_RL_argus/.argus_special_prompts argus-skill --daemon-fg --continuous --bounded --objective \"\$(cat /data/yijia/unify_RL_argus/.argus_diag_objective.txt)\"" Enter
sleep 45
tmux capture-pane -t argus-unify-rl -p | tail -15
```

### Step 3 — confirm clean done, NO review loop

Watch the events for the diagnostic mission. The key contrast vs M0.6:
- M0.6: `round.review.completed reason="Acceptance checks failed ..."`
  repeated 7× → `mission_failed status=blocked`.
- M0.7 expected: the engineer round's `stage_check` runs with
  `--bounded`, the benchmark findings print as advisory (📋), the
  automated gate passes, the reviewer signs off, and the mission
  reaches `life.mission.completed` with a done/accept verdict — at most
  1-2 review rounds, no loop.

```bash
EV=/home/yifanyang/.argus-skill/projects/59ec632ebc50/events.jsonl
sleep 240
# how many review rounds for this run, and did it complete cleanly?
tail -120 "$EV" | python3 -c "
import sys,json
for l in sys.stdin:
    try: o=json.loads(l)
    except: continue
    k=o.get('kind') or o.get('type') or ''
    if any(x in k for x in ('review.completed','mission.completed','mission.failed','planner.verdict')):
        print(k, str(o.get('reason') or o.get('status') or o.get('enqueued_titles') or '')[:90])
"
# verify the stage_check inside the mission actually ran with --bounded:
grep -o 'stage_check --project-root . --bounded' "$EV" | head -1 && echo 'BOUNDED FLAG REACHED THE GATE'
```

### Step 4 — stop daemon, report

Stop the daemon to cap spend, then report:
1. Sweep pass count + the 2 implementation commit SHAs.
2. Did the diagnostic mission reach a clean done? How many review rounds
   (must be far fewer than M0.6's 7; ideally 1)?
3. Did `stage_check ... --bounded` actually appear in the mission's
   gate invocation (evidence the flag threaded through)?
4. Confirm anti-fraud is intact: note that only paper-pipeline benchmark
   findings were downgraded (they show as 📋 advisory in the mission's
   stage_check stdout), not any structural gate.
5. Cost of the re-validation run.

Do NOT babysit beyond Step 4. The architect monitors.

---

## Definition of done

- `stage_check --bounded` reclassifies paper-pipeline `blocked` findings
  as advisory (exit 0) while anti-fraud structural gates still block
  (exit 1). Tests prove both.
- The daemon and REPL thread `--bounded` into the stage_check check
  command when `continuous_open_ended is False`. Tests prove both.
- Every code change carries a short `why` comment (operator requirement).
- Live re-validation: the bounded diagnostic objective reaches a clean
  done with no review loop; `--bounded` is observably present in the
  gate invocation.

## Non-goals

- Do NOT re-architect the reviewer acceptance scope (moving stage_check
  out of per-mission and into project-level only). That is a larger
  design change for M1; M0.7 is the surgical flag-threading fix.
- Do NOT weaken or skip `structural` anti-fraud / provenance gates in
  bounded mode. Bounded only downgrades paper-pipeline benchmark *state*.
- Do NOT touch `paper/artifacts/slm_llm_human_hierarchy.json` (unrelated
  pre-existing dirty file).
