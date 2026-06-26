# argus-skill REPL — UX Overhaul

**Goal:** make the `argus-skill` REPL feel like a real product — at parity with
Codex CLI, Claude Code, Cursor, and Copilot — by fixing the dishonest /
confusing surfaces a real dogfooding session surfaced, not by papering over them.

**Benchmark bar.** The reference products all get four things right that we don't:
(1) you always know *who is executing your request and whether it is alive*;
(2) you see *streaming progress*, not a frozen cursor; (3) you can *preview a plan
and approve it before it runs*; (4) when something is wrong, a *`doctor`* tells you
why. This doc closes those gaps. Every claim below is grounded in code paths in
`argus_skill/manager/repl.py` and `argus_skill/apps/cli/_core.py` and in a real
session driven on 2026-06-26.

---

## 1. Dogfooding session (the real pain)

I launched a bare `argus-skill` to give it a task. Here is what actually happened,
in order:

1. **A live daemon was already working** — pid-bearing project `07197071cf43`
   (it has `daemon.pid`, `daemon.status.json`, a real `backlog.jsonl`, mission
   telemetry). The bare launch ignored it completely.
2. **The bare launch opened a NEW empty session** `s-89fc281b` instead. This is
   Phase-3b *by design* (`_resolve_session_id(..., default_to_new=True)` in
   `apps/cli/_core.py:372` → a bare `argus-skill` always opens a fresh session).
   So the REPL I was typing into and the daemon doing the work were **two
   different projects**. The banner gave no hint the live one existed.
3. **I typed a task. The REPL printed "queued — daemon executing."** It was a
   lie: no daemon owned `s-89fc281b`. The old free-text path printed the
   "executing" line unconditionally, then entered a **600-second event-tail wait**
   on an `events.jsonl` that would never grow — the REPL simply **froze**. (Root
   cause + fix below; this is now `a3e542a`.)
4. **`~/.argus-skill/projects` had grown to 72 dirs — 68 of them husks** (only
   `events.jsonl` + `inbox.jsonl`, zero missions ever run). Every bare launch
   litters one more. Exactly **one** dir (`07197071cf43`) had a live daemon.
5. **No streaming.** While "executing", there was zero token / step output —
   nothing like Claude Code's live tool log or Cursor's streamed diff.
6. **No plan mode.** Codex `--plan`, Claude Code Plan mode, and Cursor all let you
   preview the step plan and approve before anything mutates. We jump straight
   to enqueue.
7. **`/status` and `/help` are cryptic.** `/status` dumps identity + backlog but
   buries the one fact that matters ("is an executor alive for *this* session?").
   `/help` (`_render_help`) is a flat 19-row wall with no grouping and no mention
   of how to find the live daemon I was missing.

Net: I could not tell *who was running my work*, the tool *told me a falsehood*,
and then it *hung*. That is the gap to a real product.

---

## 2. Optimization POINTS (problem → who does it better)

**P1 — Daemon↔session decoupling is invisible.** A bare launch opens a fresh
session whose daemon is dead-on-arrival, while the real worker lives in another
project. *Symptom:* daemon `07197071cf43` working, REPL queuing into
`s-89fc281b`. *Better:* **Claude Code / Codex** bind the REPL to the process that
actually runs your turn; there is never an orphaned front-end.

**P2 — The REPL lied about execution.** "queued — daemon executing" printed
unconditionally. *Symptom:* the line above + the 600 s freeze. *Better:*
**Copilot / Cursor** never claim work is running when no backend is attached;
state is derived from the live connection, not hard-coded.

**P3 — The live daemon is not surfaced at startup.** The banner shows *this*
session's daemon cell only; a healthy daemon in a sibling project is invisible.
*Symptom:* I never saw `07197071cf43`. *Better:* **Cursor**'s background-agents
panel lists every running agent across the workspace.

**P4 — `--continue` does not prefer the live worker.** `_session_mode` maps
`--continue` to the most-recent session by mtime, which can be a husk, not the
one with a beating daemon. *Better:* **Codex `--continue`** resumes the session
you were *actually* working in.

**P5 — Sessions are created eagerly, before any work exists.** `_resolve_session_id`
materializes `projects/<id>/` on launch, so even `argus-skill` → immediate exit
leaves a husk. *Symptom:* 68/72 husk dirs. *Better:* **Claude Code** only
persists a session once it has content.

**P6 — The resume picker is flat.** `_pick_session` lists by recency with no
"● live" marker and no named-first ordering, so a husk outranks the working
session. *Better:* **Cursor / Claude Code** mark running sessions and float named
/ active ones to the top.

**P7 — Executor state is never stated in one honest line.** You must run `/status`
and parse it. *Better:* **Codex** keeps a persistent one-line status (model,
connection, tokens) in view at all times.

**P8 — No streaming progress.** "Executing" is a black box until completion or
freeze. *Better:* **Claude Code** streams the tool log; **Cursor** streams the
diff; **Copilot** streams tokens. Progress visible within ~1 s.

**P9 — Completion output is thin.** `tail_mission_events` returns, but the
operator gets no crisp "here is the artifact + the reviewer's verdict." *Better:*
**Cursor** ends an agent run with a reviewable summary + diff; **Codex** prints
the patch and the test result.

**P10 — The chat fast-path is noisy.** Greetings/questions go through the same
plumbing as real tasks and emit queue/daemon chatter. *Better:* **Copilot Chat**
keeps conversational turns instantly local; no executor noise.

**P11 — No Plan mode.** We cannot preview a step plan and approve before mutating.
*Better:* **Codex `--plan`**, **Claude Code Plan mode**, **Cursor** all gate
execution behind an approved plan. (User explicitly asked for this.)

**P12 — No self-diagnosis.** When auto-spawn fails (gpt-5.5 backend 429 / vault
preflight), the operator gets a one-liner and no way to introspect. *Symptom:*
banner "daemon auto-spawn failed — backlog will NOT be executed"
(`repl.py:1266`) with no follow-up. *Better:* **`gh`/Cursor** ship a `doctor`
that checks backend, ports, locks, and vault and tells you the fix.

**P13 — No cross-project visibility / attach.** You cannot list running daemons or
attach to one from the REPL. *Better:* **Cursor** background-agents list +
click-to-attach.

---

## 3. Optimization TASKS

| id | task | files touched | before → after (observable) | test |
|----|------|---------------|------------------------------|------|
| **T1** | **Daemon↔session coupling fix (DONE `a3e542a`)** — free-text no longer tails/lies when no daemon owns this session | `manager/repl.py` (`_daemon_alive_for`, free-text path) | before: "queued — daemon executing" then 600 s freeze → after: honest notice, returns immediately | `tests/test_ux_daemon_coupling.py::test_free_text_no_daemon_does_not_tail_or_lie` |
| **T2** | **Honest no-executor message (DONE `a3e542a`)** — `_no_executor_notice` replaces the false "executing" line | `manager/repl.py` (`_no_executor_notice`) | before: false "executing" → after: "⚠ queued — NO daemon running here … `argus-skill --daemon` · `/doctor`" | `tests/test_ux_daemon_coupling.py::test_no_executor_notice_is_honest_and_actionable` |
| **T3** | **Surface live daemon in banner** — if a sibling project has a live daemon, show it ("● daemon live in `<sid>` · `/attach`") | `manager/repl.py` (banner block ~1290), `core/session.py` (`list_sessions` + liveness) | before: live `07197071cf43` invisible → after: banner names it + how to attach | `tests/test_repl_banner.py::test_banner_surfaces_live_sibling_daemon` |
| **T4** | **`--continue` prefers the live daemon** — resolve to the session whose daemon is alive, else most-recent | `apps/cli/_core.py` (`_session_mode`, `_resolve_session_id`), `core/session.py` | before: `--continue` → newest-by-mtime (often a husk) → after: → live-daemon session when one exists | `tests/test_session_resume.py::test_continue_prefers_live_daemon` |
| **T5** | **Lazy session create** — do not materialize `projects/<id>/` until the first mission/note is written | `apps/cli/_core.py` (`_resolve_session_id`), `core/session.py` (`write_session_meta`) | before: bare launch → husk dir → after: launch+exit leaves nothing | `tests/test_session.py::test_no_dir_until_first_write` |
| **T6** | **Picker marks live + named-first** — `● live` marker, named/active sessions sorted ahead of husks | `apps/cli/_core.py` (`_pick_session`), `core/session.py` (`list_sessions` sort) | before: flat by recency, husk on top → after: live & named first, husks demoted | `tests/test_session_resume.py::test_picker_marks_live_named_first` |
| **T7** | **Honest one-line executor state** — persistent status line: executor alive? pid? backend? pending? | `manager/repl.py` (banner + prompt render) | before: must run `/status` → after: always-visible "executor: live pid 1234 · codex · 3 pending" | `tests/test_repl_banner.py::test_executor_state_line` |
| **T8** | **Streaming progress** — tail mission events incrementally with a live spinner/step line instead of a silent block | `manager/repl.py` (`tail_mission_events`, `_follow_events_stream`), `apps/cli/_follow.py` | before: silent until done/freeze → after: step lines stream within ~1 s | `tests/apps/test_follow_stream.py::test_streams_incremental_events` |
| **T9** | **Completion output (artifact + reviewer verdict)** — `_format_completion` prints artifact path + reviewer pass/fail + score | `manager/repl.py` (`_format_completion`, `_record_mission_outcome`) | before: thin tail → after: "✓ done · artifact `<path>` · reviewer: PASS (score …)" | `tests/test_format_completion.py::test_shows_artifact_and_verdict` |
| **T10** | **Chat fast-path noise removal** — greetings/questions answer locally, emit zero queue/daemon chatter | `manager/repl.py` (`_free_text_cmd`, chat classifier) | before: chat turns print daemon lines → after: instant local reply, no executor noise | `tests/apps/test_life_repl_free_text.py::test_chat_fastpath_no_daemon_noise` |
| **T11** | **Plan mode** — `/plan <obj>` previews an ordered step plan; execution gated on explicit approve | `manager/repl.py` (new `_plan_cmd`, dispatch), `manager/stage_decider.py` | before: enqueue immediately → after: preview plan, `approve`/`edit`/`cancel` before any mutation | `tests/test_plan_mode.py::test_plan_previews_and_requires_approval` |
| **T12** | **`/doctor` self-diagnose** — checks backend reachability (gpt-5.5 429), vault preflight, daemon liveness, ports, stale locks; prints fixes | `manager/repl.py` (new `_doctor_cmd`), `core/vault_preflight.py`, `core/daemon_lock.py` | before: opaque "auto-spawn failed" → after: `/doctor` lists each check ✓/✗ + remedy | `tests/test_doctor.py::test_doctor_reports_backend_and_daemon` |
| **T13** | **`/daemons` + `/attach`** — list every live daemon across projects; attach the REPL to one | `manager/repl.py` (new `_daemons_cmd`, `_attach_cmd`), `core/session.py` | before: no cross-project visibility → after: `/daemons` table + `/attach <sid>` binds REPL to it | `tests/test_daemons_attach.py::test_lists_and_attaches_live_daemon` |
| **T14** | **`--plan` CLI flag** — non-interactive plan preview (parity with Codex `--plan`) | `apps/cli/_parser.py`, `apps/cli/_core.py` | before: no flag → after: `argus-skill --plan "<obj>"` prints plan, exits 0 without mutating | `tests/apps/test_parser.py::test_plan_flag_previews_only` |
| **T15** | **`/status` cleanup** — lead with executor liveness; demote identity/backlog dump below the fold | `manager/repl.py` (`_status_cmd`) | before: identity-first wall → after: "executor: live/dead" first, then summary | `tests/test_status.py::test_status_leads_with_executor` |
| **T16** | **`/help` regroup** — group rows (Run · Plan · Sessions · Diagnose · Config) instead of a flat 19-row list | `manager/repl.py` (`_render_help`) | before: flat list → after: grouped sections incl. new `/plan` `/doctor` `/daemons` `/attach` | `tests/test_help.py::test_help_is_grouped` |
| **T17** | **Onboarding** — first-run hint card: how tasks vs chat differ, where the daemon is, the 3 commands that matter | `manager/repl.py` (banner first-run branch), `core/session.py` (first-run flag) | before: no guidance → after: one-time card on first launch | `tests/test_repl_banner.py::test_first_run_onboarding_card` |
| **T18** | **GC empties** — extend `gc_stale_projects` to prune husk dirs (no missions, not live) on startup | `core/project_gc.py` (`gc_stale_projects`, `_project_is_live`), `apps/cli/_core.py` | before: 68/72 husks accumulate → after: husks swept to `projects_trash/` at launch | `tests/test_project_gc.py::test_prunes_husk_sessions` |
| **T19** | **Auto-spawn resilience** — retry/backoff on backend 429; on vault-preflight failure, fall back cleanly and point at `/doctor` | `manager/repl.py` (auto-spawn block ~1250), `daemon/life_worker.py` (`spawn_detached_daemon`) | before: single attempt → "failed, backlog NOT executed" → after: bounded retry, then actionable `/doctor` pointer | `tests/test_autospawn.py::test_retries_on_429_then_points_to_doctor` |
| **T20** | **Before/after harness** — scripted dogfooding replay asserting no-lie + no-freeze + streaming + husk-free | `tests/test_ux_before_after.py` (new), `scripts/ux_replay.py` (new) | before: regressions silent → after: CI replay guards every fixed surface | `tests/test_ux_before_after.py::test_dogfooding_replay_is_honest` |

**Run any test:** `PYTHONPATH=/home/argustest/argus-skill python3 -m pytest <file> -q`

---

## 4. Why this matters

argus is meant to do real science 7×24 with no human in the loop — which makes the
operator's *one* window into it, the REPL, sacred. A front-end that opens an empty
session while the real worker toils invisibly, prints "executing" when nothing is,
and then freezes for ten minutes is not a rough edge; it is the difference between a
tool you trust and a tool you babysit. Codex, Claude Code, Cursor, and Copilot all
clear the same bar: *always tell the truth about who is running the work, stream it
while it runs, let me preview a plan before it mutates, and diagnose itself when it
breaks.* T1 and T2 already killed the lie and the freeze. T3–T20 turn the rest of
the surface honest, legible, and self-explaining — so that the first time someone
types `argus-skill` they see a system that is plainly alive and in control, not a
husk that hides its own daemon. That is what makes argus feel like a product instead
of a script.
