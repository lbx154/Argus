# Argus roadmap: from 0.1.8 to 1.0

Chinese version: [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md)

## In one sentence

**Give Argus a real research or engineering goal, and it finishes the job on its own. When it says "done", the work
is actually done. It can tell you what the work cost in money and time, you can watch the process at any moment, and it
gets better the more you use it.** When that is true, we have 1.0.

This document covers plans and status on `dev` only; it does not mean anything has been released. Milestones are in
order, with no dates. Each task has one status line; the history of how it got there is in its commits and PRs, not
here. Old task IDs (HO-1, CO-2, …) are kept in the "Absorbs" column so earlier links and branch names still lead
somewhere.

## The 1.0 standards

1.0 is these eight things being true at the same time. Targets marked "initial" are recalibrated once line 1 has
measured today's baseline.

| # | Standard | How we measure it | 1.0 target | Lines |
| --- | --- | --- | --- | --- |
| 1 | **Dependable**: finishes unattended; a crash or restart loses nothing and repeats nothing; failures are visible | Unattended completion rate on the standard task set; fault injection; swallowed errors on the main path | Completion initial ≥ 90%; all fault-injection cases pass; zero swallowed errors on the main path | L1, L3, L4, L6 |
| 2 | **"Done" means done** | Injected-defect set: defects the Reviewer lets through, sound work it rejects; every "done" traceable | Each initial ≤ 10%; every "done" traces to the reviewed change, who judged it, and the evidence | L1, L2, P4 |
| 3 | **No waste** | Same task through Argus and through the same coding Agent used directly | Simple tasks ≤ 1.2× the time; success rate no lower; every task's cost visible | L1, L4, L6 |
| 4 | **Gets better with use** | Unseen tasks run with accumulated experience and with it cleared | In at least one domain, success or cost improves, and holds up statistically | L1, P5 |
| 5 | **Anyone can get started** | A new machine, from install to the first result | ≤ 10 minutes on Linux, macOS and Windows without reading docs; signed Mac installer; Windows native | P1 |
| 6 | **Safe for many users** | Isolation tests and a security review | Accounts and projects isolated by construction; one user's Agent cannot see others' files; no high-severity findings | P2 |
| 7 | **Stable interfaces, upgrades don't break** | Domain plugin interface; upgrading old data | Public, versioned domain SDK v1; community domains pass contract tests; 0.x data upgrades directly | P3, L3, L7 |
| 8 | **Every change can be shown not to make things worse** | Replay and standard tasks before every merge; CI on three platforms | Replay suite blocks merges; standard set runs before every merge to `dev`; three-platform core tests; type debt only goes down | L1, L6 |

## Where we are now

Measured on `chore/merge-1023` (`190d9b7f`, 2026-10-22):

- **Size**: 198,000 lines of Python in `argus/` plus 202,000 in `tests/`; 75,000 lines of TypeScript; 766 test files;
  3,039 commits.
- **Errors still get swallowed**: 2,679 `try:` and 694 `except Exception` in `argus/`; 1,433 type-check issues
  (2026-09-12); each commit runs backend tests on Linux only.
- **Roles hand off by writing text the host parses**: about 3,400 lines turn free text into decisions. In five
  dogfood runs (286 model calls), questions lost their options, internal file names reached the operator, and the
  Manager settled three ambiguous requirements itself without recording an assumption.
- **There is no ruler**: nothing replays real runs before a merge; there is no standard task set, no injected-defect set,
  and no comparison with using an Agent directly. Simple tasks took 53 s against 28 s for Copilot used directly.
- **"Gets better with use" has no evidence**: two real measurements showed no improvement.
- **Getting started and multi-user use are hard**: a coding Agent CLI must be installed and logged in first; the Mac
  installer is unsigned; self-hosted web has no accounts.

## Why the roadmap is organized by root cause

The earlier version listed 134 symptoms. About 40 measured problems trace back to four roots, and fixing one root
removes many symptoms at once:

1. **Roles hand off by writing text, and the host takes it apart.** Questions lose their options, internal fields leak
   to the screen, one paragraph serves as both data and a message to the operator.
2. **The same thing is stored in several places**, and each place derives its own version: summary files, the task
   brief and backlog fields; the frontend recomputing state the backend already has.
3. **The host guesses the next step for the roles**: default routes when text is unreadable, retries that re-ask the
   model, guards and timers that decide "stuck".
4. **Rules are scattered and contradict each other**: "when to ask the operator" exists in seven versions across
   prompts.

Two things sit under all four. Without a **ruler** (replay tests and a standard task set), no fix can be shown to
help, and four of the eight standards cannot even be judged. And **the amount of code** is itself a root: every copy
and every rescue path is a place for the four roots to hide.

So the core is six lines, one per root plus the ruler and subtraction, and a seventh line moves the kernel to
TypeScript on top of them. The product lines keep their goals and depend on the core.

## Milestones

| Version | Theme | Done means | Tasks |
| --- | --- | --- | --- |
| **0.2** | A ruler, and handoff as a form | The replay suite runs on every commit and blocks merges; about 10 standard tasks run before every merge to `dev`; form batch 1 is in `dev`; the form schema is in the shared contracts; open PRs for old items are merged or closed | L1.1–L1.4, L2.1, L2.6, L6.1 |
| **0.3** | One place per fact; the host stops guessing | Every role ends with the form; summaries rebuild from records; the frontend only displays; the host follows the form's `to` and nothing else; the injected-defect set has a baseline | L1.5, L2.2–L2.5, L3, L4, L7.1–L7.2 |
| **0.4** | Rules written once; subtraction shows | One rule table that prompts cite; production lines, swallowed errors and type issues all lower than at 0.2; the first public comparison table; the TS kernel finishes one full task | L1.6, L5, L6, L7.3 |
| **0.5** | Anyone can get started | A first result in 10 minutes on Linux and macOS; signed installer; the TS kernel passes replay and the standard set in shadow | P1, L7.4–L7.5 |
| **0.6** | Kernel switch; safe for many users | Production runs on the TS kernel and the Python kernel is deleted; Windows native; isolation by construction; security review passed | L7.6, P2, P1 (Windows) |
| **0.7** | An ecosystem you can rely on | Domain SDK v1 is the plugin-worker interface; version matrix; old data upgrades | P3 |
| **0.8** | Gets better with use; research you can trust | Experience → outcome is traceable; first public "gets better" curve; host checks research numbers | P4, P5, L1.7 |
| **0.9** | Release candidate | Long unattended run passes; metric targets met; release rehearsed | L1.8, L6.3–L6.4, P6 |
| **1.0** | 1.0 | All eight standards met | — |

## Core lines

Status words: **done** (in `dev`, with commit); **PR open** (not merged); **in progress**; **todo**; **partly**
(what is left is named).

### L1 A ruler first

Fixes: nothing can show a change helped or hurt. **Done when**: replay tests built from real runs block every merge,
and a standard task set of about 10 tasks runs before every merge to `dev`, with results compared to the last run.

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| L1.1 | Replay tests from real runs: recorded calls become fixtures; the host replays them without models and checks routes and every string the operator sees; Python and TS read the same fixtures | 0.2 | todo; first fixtures come with L2.1 | EV-3, TS-6 |
| L1.2 | Standard task set: about 10 tasks layered by how long a person would take, across software, research, math and kernels | 0.2 | todo | EV-6 |
| L1.3 | Run before every merge: replay blocks on every commit, the standard set runs before merging to `dev`; web and TUI tests on every commit | 0.2 | todo; web/TUI type checks already run per commit | EV-2, CI-2, CI-8 |
| L1.4 | Reports: efficiency report, cost per passed task, weekly report, real work vs overhead, "review too fast" counter-metric | 0.2 | partly: efficiency report done [7a2432b5]; cost per passed task done [b5578571]; weekly report PR open [#295]; overhead split and counter-metric todo | HO-1, CE-1, EV-4, CE-5, SC-9 |
| L1.5 | Injected-defect set for the Reviewer: wrongly passed and wrongly rejected rates | 0.3 | todo | SC-7 |
| L1.6 | First public comparison with using an Agent directly, then experiments that need it: small models as Reviewer or Planner; real-model acceptance of the Manager and the team | 0.4 | todo; small-model test on hold until L1.5 and this | EV-1, CE-7, EC-13 |
| L1.7 | Learning on/off comparison on the standard set | 0.8 | todo | LE-2 |
| L1.8 | Fault injection (kill, restart, network loss, rate limit, full disk) and an unattended long run (initial 72 h) | 0.5 / 0.9 | todo | CO-18, CO-19 |

### L2 Handoff as a form

Fixes root 1. Every role ends its turn with one native action, `hand_off`: five core fields (`to`, `summary`, `note`,
`ask`, `assumptions`) plus a few per call, validated by schema; errors go back to the model in the same turn. The host
reads fields, never text; the screen shows only `summary`, `ask` and `assumptions`. Net about −5,000 lines. **Done
when**: an architecture test finds no text reader left (`read_key_values`, `read_baton_line`, `Decision:`), and replay
gives the same routes as before except the known misreads.

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| L2.0 | Precursor: one handoff line instead of footers; one park status; an unchanged return climbs | — | done [ff151a2c] [9f787bc7] [87cc238a] [b608655c] | HO-16 |
| L2.1 | Batch 1: shared action; Engineer, Reviewer, front door, grounding classify, supervision, display. Includes: a Reviewer that does not submit is asked again; obstacles the Reviewer cannot judge go `to: manager`; `latest_role_decision` and `role_decision.py` go; nothing tells the operator about `CHECKPOINT.md` | 0.2 | in progress (`feat/handoff-forms-1`); HO-4 PR open [#294]; HO-3 PR open [#297]; front door: while the team works every Manager turn files the form, is asked again if it does not, and its reply is not sent if it still does not [23b0edcd] | HO-3, HO-4, HO-10, HO-11 |
| L2.2 | Batch 2: Planner, stage decision, report inputs. Acceptance criteria become form fields judged one by one; follow-ups don't block | 0.3 | todo; `fix/completion-objective-status` never merged, fold in here | HO-9 |
| L2.3 | Batch 3: domain intake, proxy answers, pending answers, Observer, reflection, consolidation, small tools | 0.3 | todo | EC-12 |
| L2.4 | Who said what: verdicts are never rewritten; host interventions sit beside them; no "defaults to reviewer"; one writer for the next instruction | 0.3 | partly: interventions beside verdicts done [d75caf4d]; default author and single writer todo | HO-8 |
| L2.5 | Every backend can submit the form, or is refused before the call; capability matrix generated and tested | 0.3 | todo; cursor, opencode, grok and dsh cannot run the Reviewer today | EC-10, EC-11 |
| L2.6 | Form schema lives in `packages/contracts`, so the TS kernel uses the same form | 0.2 | todo | — |
| L2.7 | Operator decisions through the form: a pass settles before a Manager wait; when the operator cannot be asked, nothing waits for them and the Manager decides in their place | 0.2 | HO-5 PR open [#296]; HO-6 PR open [#301]; earlier part done [6211aaf8] | HO-5, HO-6 |

### L3 Each fact stored once

Fixes root 2. **Done when**: a written state model says which record owns each fact; every summary file and every
status on screen is derived from it; deleting a derived file and rebuilding it gives the same bytes.

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| L3.1 | State model; `latest.json`, the task brief and backlog result fields derived from records | 0.3 | partly done: [state-model.md](state-model.md); the Mission View, the delivered-completion index and the newest supervision and handoff records are computed from their records, not stored [282a3896] [30d33269] [cbbcd2d7]; backlog result fields todo | HO-14 |
| L3.2 | The backend computes status, the frontend only shows it: remove frontend copies of status logic and text stripping | 0.3 | todo | — |
| L3.3 | One meaning per name: `MemoryBundle.root`; one record of what the Planner has seen | 0.3 | todo | CO-5 |
| L3.4 | Storage decision (files, SQLite/WAL or a mix), written as an ADR; the TS kernel is built on it | 0.3 | done: plain files, see [state-model.md](state-model.md) [95d3afd2] | CO-17 |
| L3.5 | A verdict names the snapshot it reviewed; the baseline moves only on a pass | 0.3 | partly: snapshots and real diff done [247b432c] [9c042dda]; binding todo | HO-7 |
| L3.6 | Old-data upgrade test with anonymized 0.1.x projects | 0.3 | done: the three formats tagged releases from v0.1.1 on wrote and the runtime no longer reads (sha1-named project directories, the JSONL inbox, the append-log failure experiences) are converted once by `argus upgrade-state`; `tests/apps/test_state_upgrade.py` builds each in the shape its release wrote, upgrades, and reads it back through the current runtime, and a current project is left byte for byte [6ac48474]. The repository holds no real anonymized 0.1.x project; the fixtures follow the writers in the tags | RL-2 |

### L4 The host executes, it does not guess

Fixes root 3. **Done when**: an architecture test lists the only rules the host may apply on its own (budgets a person
set, permissions, the backend-failure circuit), and every next step comes from a form's `to`.

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| L4.0 | Already removed: timers that killed live work; progress guards; review while a job runs; work that waits forever; Planner idling; an Observer role that watches the flow instead of host rules | — | done [1149681a] [8cbde085] [d75caf4d] [2d2cc0a7] [301fabf4] [c91142d8] [1cf9d467] | HO-2, HO-12 (part), HO-15, HO-17, CE-2 |
| L4.1 | Budgets a person sets (rounds, time, money per task), shown on the web page; on exhaustion the Manager decides | 0.3 | todo; per-task cost already shown; PR [#248] has a budget draft | HO-12, CE-6 |
| L4.2 | Delete the remaining guesses as L2 lands: default routes when unreadable, context/field/tool-loop retries, supervision WAIT | 0.3 | todo | — |
| L4.3 | Review only when work is handed in: confirm with L1 that reviews per passed task fall and wrong "done" does not rise | 0.3 | partly: behaviour in place since the handoff line (`→ engineer` keeps working) [ff151a2c]; not measured | HO-13 |
| L4.4 | Find where simple tasks lose time, and cut host overhead | 0.3 | todo | CE-3 |
| L4.5 | The Observer also watches the Manager's turns | 0.4 | todo | HO-17 (gap) |

### L5 Each rule written once

Fixes root 4. **Done when**: the principles are one rule table (ID, rule, which roles); prompts cite rule IDs; an
architecture test fails if a rule's text appears in two places.

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| L5.1 | Rule table and citations; first rules: when to ask the operator, how to write choices, what the operator reads | 0.4 | todo; the single operator policy is in L2.7 | HO-6 (rule text) |
| L5.2 | The Reviewer weighs understating as heavily as overclaiming, checked on the L1.5 set | 0.4 | todo | RA-1 |
| L5.3 | A send-back names only the 1–3 biggest problems, each with its type | 0.4 | todo | SC-11 |
| L5.4 | Research rules shared by all domains: judge novelty first; replanning may change the question, not only the method | 0.4 | todo | RA-2, RA-5 |

### L6 Subtraction

The amount of code is itself a root. **Metrics that may only go down**, each with a baseline test that fails on growth:
production lines in `argus/` (198,462 at `190d9b7f`), broad `except` handlers, type-check issues. **Rules**: moving
code is allowed only in the same PR as deleting code; Python kernel code that L7 will delete is not polished.

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| L6.1 | Swallowed-error inventory and a per-package guard; type-check gate on every commit; line-count guard | 0.2 | swallowed errors PR open [#293]; type gate and line guard todo | CO-1, CO-3, CI-1 |
| L6.2 | Delete what is waiting to go: the `argus_skill` alias, the sha1 directory migration, 16 copies of atomic write | 0.4 | partly done: atomic writes go through `core.atomic_replace.write_atomic`, about 60 writers converted [17b10d87]; the `argus_skill` alias and the `argus-skill` command are deleted [d2f44629]; the sha1 directory migration moved from every lookup to `argus upgrade-state` [6ac48474]; read-time compatibility for formats only written before v0.1.1 is deleted [c04e733a] | EC-4, CO-20, CO-4 |
| L6.3 | Clear swallowed errors on the main path | 0.9 | started: 27 broad excepts around file reads, JSON parsing and imports narrowed [7d09b0db] | CO-2 |
| L6.4 | Clear type issues file by file: zero in core, none new anywhere | 0.9 | todo | CI-3 |
| L6.5 | Context cut: tools the reading roles never used are no longer sent | — | done [8a74975e] [5be8781d] | CE-4 |
| — | Downgraded: LAYOUT phases (move leaf modules, `pipeline/`, `mission_runner/`, renames, public loader names, tests by package) happen only inside a PR that deletes code | — | downgraded | CO-6–CO-13 |
| — | Downgraded: daemon service objects, short locks, standalone scheduler are not done in Python; the TS kernel is designed with them | — | moved to L7.3 | CO-14, CO-15, CO-16 |

### L7 TypeScript kernel (plan A)

The form lands in Python first and its schema goes into the shared contracts (L2.6). The TS kernel is written in
parallel and uses that form and the single state model (L3) from day one. Once it passes replay and the standard set,
production switches to it and the Python kernel is deleted. Domain plugins that depend on the scientific Python stack
stay in Python behind a plugin worker. Details: [typescript-migration.md](typescript-migration.md).

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| L7.1 | Write commands in the shared contracts; read-only frontend profile | 0.3 | todo; read API, event and snapshot contracts exist | TS-1 |
| L7.2 | Pi adapter on an external provider; training capture; session ownership | 0.3 | todo; local adapter exists | TS-2 |
| L7.3 | Kernel on the form and the state model: storage and budget primitives, one P→E→R→M task with restart and cancel; short locks and a separate scheduler by design | 0.4 | todo | TS-3, CO-15, CO-16 |
| L7.4 | Continuous scheduling, the other backends, write API | 0.5 | todo | TS-4 |
| L7.5 | Shadow run: passes the replay suite and the standard set on copied state | 0.5 | todo | — |
| L7.6 | Switch production; delete the Python kernel; Python plugin worker; one install and desktop path | 0.6 | todo | TS-5, CO-14 |

## Product lines

Goals unchanged; each now names the core line it waits on.

### P1 Getting started — a first result in 10 minutes

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| P1.1 | First-run wizard on the web page from `argus_doctor.py`; login checked without a model call | 0.5 | todo | UX-1, CE-8 |
| P1.2 | Signed and notarized Mac installer | 0.5 | todo (needs a certificate) | UX-2 |
| P1.3 | English invitation page and getting-started guide; quickstart site | 0.5 | todo; quickstart site PR open [#280] (base `main`) | UX-3 |
| P1.4 | A starter template per built-in domain | 0.5 | todo | UX-4 |
| P1.5 | Uploads resume after refresh; archives and Office files; import from a link; one-click export | 0.5–0.6 | todo | UX-6, UX-7, UX-8, UX-9 |
| P1.6 | Nothing lost across devices; read-only share links (need P2 accounts) | 0.6 | todo | UX-5, UX-11 |
| P1.7 | Remaining time as a range from L1 history | 0.8 | todo | UX-10 |
| P1.8 | More notification channels (email, Slack, WeCom, DingTalk) | 0.6 | todo | UX-13 |
| P1.9 | Windows native and three-platform CI: replace `fcntl` (8 files) and `os.fork` (4 calls) only in code that survives L7.6; grow portable tests; PR gate on all platforms | 0.6 | todo | CI-4, CI-5, CI-6, CI-7, CI-9 |
| P1.10 | Cold-start test in a clean container before every release | 0.5 | todo | CI-10 |

### P2 Safe for many users — isolated by construction

Not by checking afterwards: what an Agent cannot see, it cannot leak.

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| P2.1 | The web token never appears in a URL | 0.2 | in `chore/merge-1023` [5acf2408], PR open [#303] | — |
| P2.2 | An Agent sees only its own workspace: tool-layer boundary and isolation tests first, then a sandbox (shell scripts get past tool hooks) | 0.2 / 0.6 | tool layer PR open [#302]; sandbox todo | SE-1, SE-2 |
| P2.3 | Accounts and project permissions | 0.6 | todo | SE-3 |
| P2.4 | Fixed address; self-service export and deletion; operations dashboard | 0.6 | todo | SE-4, SE-5, SE-6 |
| P2.5 | Formal security review; no move to 0.7 with high-severity findings | 0.6 | todo | SE-8 |

### P3 Ecosystem — a stable interface means exposing less

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| P3.1 | Domain SDK v1 = the plugin-worker interface; community domains stop importing about 15 private modules | 0.7 | todo | EC-1 |
| P3.2 | Version and compatibility promise; version matrix with contract tests | 0.7 | todo | RL-1, EC-2 |
| P3.3 | Manage domains on the web page; scaffolding and a 15-minute tutorial | 0.7 | todo | EC-5, EC-6 |
| P3.4 | Every domain declares verification levels and has a 15-minute smoke task | 0.7 | todo | RA-10, RA-11 |

### P4 Research you can trust, and research ambition (needs L1)

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| P4.1 | The host reads numbers from result files: scale vs protocol, truncation rate, wording vs the claim table | 0.8 | todo | SC-1, SC-2, SC-3 |
| P4.2 | Replay "no paper until the premise is reproduced" (via L1.1) | 0.8 | todo | SC-4 |
| P4.3 | The Reviewer reruns one key number; certify-level results ship a one-command rerun package | 0.8 | todo | SC-5, SC-6 |
| P4.4 | Paper regression set; figures reviewed separately; narrative diagnostics may block after calibration; venue standards refreshed yearly | 0.8 | todo | SC-8, SC-10, SC-12, SC-13 |
| P4.5 | Research TODO checkboxes match the code | 0.2 | PR open [#299] | SC-14 |
| P4.6 | Tool registry completed; math: a producer for computational evidence and a small benchmark | 0.8 | todo | RA-6, RA-7, RA-8 |

### P5 Gets better with use (needs L1)

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| P5.1 | Record which experience was used and how the task went; measure page reuse across tasks | 0.8 | todo | LE-1, LE-4 |
| P5.2 | Retire experience that no longer holds, by facts, not age | 0.8 | partly: knowledge pages done [6d37d35b]; Skills todo | LE-3 |
| P5.3 | Why nearly half of learning runs changed nothing | 0.2 | PR open [#298] | LE-5 |
| P5.4 | Try a local semantic search model for recall | 0.8 | todo | LE-6 |
| P5.5 | First public "gets better with use" curve | 0.8 | todo | LE-8 |

### P6 Release and docs

| ID | Task | Ver | Status | Absorbs |
| --- | --- | --- | --- | --- |
| P6.1 | Docs that disagree with the code are fixed or marked | 0.2 | PR open [#300] | RL-4 |
| P6.2 | Release notes generated; Chinese and English docs kept in step; one-command dev setup | 0.9 | todo | RL-3, RL-5, RL-6 |
| P6.3 | 1.0 release rehearsal | 0.9 | todo | RL-7 |
| — | Downgraded: a directory of negative results, kept as a convention in PRs and commits | — | downgraded | EV-5 |

## After 1.0

- **Team research workbench**: comments and annotations, public sign-up and quota (UX-12, SE-7).
- **Big tasks across domains** and a domain marketplace: combined domains, research domain released on its own, more
  workbench backends, catalog with compatibility and downloads (EC-7, EC-3, EC-8, EC-9).
- **Research that holds up independently**: candidates against a shared baseline, discover then build, checking that a
  decomposition implies the goal (RA-3, RA-4, RA-9).
- **A public record of self-improvement**: per-domain curves and a leaderboard kept up to date (L1, P5).

## How to contribute

1. **Pick a task** by its new ID. Good first tasks: L1.4 counter-metric, L3.3, L5.3, L6.2, L6.4 (one file at a time),
   P1.4, P1.8, P3.4 (one domain at a time).
2. **Say so** in an issue or draft PR with the task ID. Branch from `dev` as `fix/<id>-<slug>` or `feat/<id>-<slug>`;
   see [CONTRIBUTING.md](../CONTRIBUTING.md).
3. **Name the root.** Say which root or line the change serves and which of the [principles](PRINCIPLES.md).
   A new mechanism that names none doesn't go into Core.
4. **Delete with every move.** A PR that moves code also deletes some; the L6 guards must not go up.
5. **Failing test first**; run the replay suite and the full Python suite. Old data must still read.
6. **Release manifest.** A Python-only change leaves it stale until `python -m argus.release_tools.build_release`
   runs; say in the PR whether you ran it. Frontend changes ship the rebuilt UI.
7. **One PR, one thing.** Don't change interfaces, storage, stage semantics and deployment in one patch.
8. **Update the status line** when done: one line and the commit or PR link. The story goes in the commit message.

[7a2432b5]: https://github.com/lbx154/Argus/commit/7a2432b5
[b5578571]: https://github.com/lbx154/Argus/commit/b5578571
[c91142d8]: https://github.com/lbx154/Argus/commit/c91142d8
[8a74975e]: https://github.com/lbx154/Argus/commit/8a74975e
[5be8781d]: https://github.com/lbx154/Argus/commit/5be8781d
[247b432c]: https://github.com/lbx154/Argus/commit/247b432c
[9c042dda]: https://github.com/lbx154/Argus/commit/9c042dda
[2d2cc0a7]: https://github.com/lbx154/Argus/commit/2d2cc0a7
[301fabf4]: https://github.com/lbx154/Argus/commit/301fabf4
[6211aaf8]: https://github.com/lbx154/Argus/commit/6211aaf8
[1149681a]: https://github.com/lbx154/Argus/commit/1149681a
[8cbde085]: https://github.com/lbx154/Argus/commit/8cbde085
[d75caf4d]: https://github.com/lbx154/Argus/commit/d75caf4d
[ff151a2c]: https://github.com/lbx154/Argus/commit/ff151a2c
[9f787bc7]: https://github.com/lbx154/Argus/commit/9f787bc7
[87cc238a]: https://github.com/lbx154/Argus/commit/87cc238a
[b608655c]: https://github.com/lbx154/Argus/commit/b608655c
[1cf9d467]: https://github.com/lbx154/Argus/commit/1cf9d467
[6d37d35b]: https://github.com/lbx154/Argus/commit/6d37d35b
[5acf2408]: https://github.com/lbx154/Argus/commit/5acf2408
[#248]: https://github.com/lbx154/Argus/pull/248
[#280]: https://github.com/lbx154/Argus/pull/280
[#293]: https://github.com/lbx154/Argus/pull/293
[#294]: https://github.com/lbx154/Argus/pull/294
[#295]: https://github.com/lbx154/Argus/pull/295
[#296]: https://github.com/lbx154/Argus/pull/296
[#297]: https://github.com/lbx154/Argus/pull/297
[#298]: https://github.com/lbx154/Argus/pull/298
[#299]: https://github.com/lbx154/Argus/pull/299
[#300]: https://github.com/lbx154/Argus/pull/300
[#301]: https://github.com/lbx154/Argus/pull/301
[#302]: https://github.com/lbx154/Argus/pull/302
[#303]: https://github.com/lbx154/Argus/pull/303
