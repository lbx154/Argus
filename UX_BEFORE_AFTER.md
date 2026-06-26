# Argus REPL UX overhaul — before / after (2026-06-26)

Benchmarked against Codex / Claude Code / Cursor / Copilot. Every item below was
**grounded in a real dogfooding session** (driving the live REPL), shipped to
`main`, and locked by tests. Run `bash scripts/ux_demo.sh` to see the AFTER
behaviour live in an isolated life-dir.

## The dogfooding session that started it

A bare `argus-skill` printed `new session · s-89fc281b` and `no daemon`, yet a
line above said `daemon started … life_dir=…/07197071cf43`. The task was queued
into `s-89fc281b` while the daemon watched `07197071cf43` — **two backlogs**, so
the task never ran. The REPL still said `queued — daemon executing` and then
**froze** (a 600s event-tail on a log that never grew). That single screen
exposed eight problems; this overhaul fixes them and adds three new modes.

## Before → After

| # | Problem (observed) | Before | After | Test / commit |
|---|---|---|---|---|
| T1 | Task never executes ("卡住") | daemon spawned on cwd-legacy project; REPL queued into the session | auto-spawn targets the **session** bundle → same backlog the daemon drains | `test_ux_daemon_coupling` · `a3e542a` |
| T2 | Dishonest + freeze | "queued — daemon executing" (a lie) → 600s hang | truthful "⚠ NO daemon — it will NOT execute yet · `--daemon` · `/doctor`", returns instantly | `test_ux_daemon_coupling` · `a3e542a` |
| T3 | Live daemon invisible | fresh session said "no daemon", real one hidden | banner: "a daemon is already running: <name> — `--continue` to attach, or `/daemons`" | `test_ux_live_daemon` · `4ec1269` |
| T4 | `--continue` reached litter | went to the newest (empty) session | prefers the most-recent session **with a live daemon** | `test_ux_live_daemon` · `4ec1269` |
| T7 | Cryptic executor line | "in-process · no daemon" (contradictory) | "no daemon — tasks queue until `--daemon`" / "⚡ daemon pid N ▸ draining" | `test_ux_live_daemon` · `4ec1269` |
| T5/T6 | Picker unusable | 72 nameless rows | empties hidden, live marked "● live", named/recent first | `test_ux_litter_gc` · `48c6d57` |
| T18 | Empty-session litter | 73 dirs, 69 empty, never reclaimed | GC sweeps content-less, lockless dirs (→ trash, reversible); spares live daemons. Ran live: **73→57** | `test_ux_litter_gc` · `48c6d57` |
| T10 | Chat noise | "🔧 round 1: main agent finished\n ↳ <reply>" | just "argus ↳ <reply>" | `test_ux_chat_clean` · `eab777d` |
| T11 | No Plan mode | type a task → it runs blind | `/plan <objective>` → preview an ordered plan, approve before queuing | `test_plan_mode` · `5206998` |
| T12 | No self-diagnosis | auto-spawn fails (429) → stuck, no path | `/doctor` → ✗/✓ checks + the exact fix per check + top recommendation | `test_doctor` · `5206998` |
| T13 | No cross-project view | a daemon under another project was unreachable | `/daemons` lists all live; `/attach <id>` follows one | `test_ux_live_daemon` · `bd48dbc` |
| T15 | Leaked codex id | "codex: resuming session 019f0505…" | "codex: reusing the previous session (/reset to start fresh)" | `eab777d`(+polish) |
| T19 | Opaque spawn failure | "auto-spawn failed — start one" | "… Run `/doctor` for why + the fix (often a rate-limited backend)" | polish |

T8 streaming and T9 completion-output (artifact path + reviewer verdict) were
already addressed: T8 works once T1 points the daemon at the right project
(`tail_mission_events` renders engineer/reviewer events as they arrive); T9
shipped in Phase 3b (`_format_completion`).

## Why this matters

A research agent that runs 7×24 is only as good as the operator's ability to
*trust what it tells them*. The old REPL lied (a queued task it could not run),
hid running work behind a fresh session, and left no path forward when the
backend rate-limited. Now: tasks actually execute, the running daemon is one
keystroke away, a failure names its own fix, and a plan can be previewed before
it spends a dollar — the difference between a script and a product.
