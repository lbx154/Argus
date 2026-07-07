# Changelog

All notable changes to this project are documented here. The format is
loosely [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
the project adheres to semantic versioning once it leaves 0.x.

## [Unreleased]

### Added
- **Zero-config "who's driving right now" status.** The always-visible
  per-turn prompt status line (previously backend/model only) now also
  appends which of the four roles (Manager/Planner/Engineer/Reviewer) is
  currently active — `cli/roles_status.format_prompt_activity_suffix`,
  reusing the same `role_activity()` source and `●`/colour convention as
  `/roles` and the live cockpit panel. This is the one status question a
  single-agent tool (Codex CLI, Claude Code) structurally never needs to
  answer, since it only ever has one actor; previously it was only visible
  behind the undiscovered `ARGUS_SKILL_COCKPIT_LIVE=1` opt-in or by manually
  typing `/roles`.
- **`/status` lists every unanswered reviewer question, durably.** A
  reviewer's `operator_question` (reviewer_schema.json) used to live only in
  the current REPL/TUI process's in-memory `chat_state` — invisible after a
  restart, and only the single most recent one if several missions blocked
  at once. `BacklogItem` gained a persisted `pending_question` field
  (`life/supervisor/_core.py` writes it whenever a mission's terminal
  status is `"blocked"` with a non-empty question — covering headless 7×24
  daemon runs, not just REPL-attached ones); `/status` now leads with a
  "questions: N awaiting your answer" section before the backlog listing
  every one, and `enqueue_mission` clears the original item's flag once the
  operator's reply is folded into a follow-up item.
- **Telegram now surfaces a blocked mission's question, not just its
  verdict.** `round.review.completed` already carried `operator_question` in
  its payload, but `TelegramStreamReporter._format_review_card_locked` only
  ever rendered `reason`/`next_action`, silently dropping it — meaning a
  headless 7×24 daemon that hit a mission blocked waiting on the operator
  sent no signal anywhere that it needed a human. The card now appends a
  distinct `❓ 需要你回复才能继续：` line whenever the field is non-empty.

### Fixed
- **Manager's SELF replies are grounded in recent history even when nothing
  is running.** `_live_mission_status_block` gave the Manager real
  visibility into a mission the daemon is running *right now*, but returned
  `""` the instant nothing was `running` — so "what just happened?" asked
  right after a mission finished (or blocked) had zero grounding. It now
  falls back to the most recent event derived from `EventJournal`
  (`events.jsonl` — the real, always-populated journal; the legacy `Journal`
  write API over a separate `journal.jsonl` is retired and always empty in
  production) so the Manager still has something concrete to reason from,
  or verify itself, before answering.
- **`pip install -e ".[dev]" && pytest -q` (the README's documented test
  setup) no longer fails 13 tests on a clean checkout.** `cryptography` and
  `jsonschema` were exercised directly by `tests/team/test_result_
  provenance.py`, `tests/team/test_teammate_entry.py`, `tests/
  test_eval_signing.py`, and `tests/planner/test_planner.py` but were never
  listed as `dev` dependencies. Added both; also added a new, separately
  installable `argus-skill[signing]` extra for the Ed25519 result-
  provenance feature itself (inert by default, off the base dependency
  list).

### Removed
- **4 tests asserting engineer-prompt content deleted by `7d20af7`**
  ("refactor(prompts): simplify role context and restore scientist
  skills") — confirmed via full-source grep that none of the asserted
  strings (`"Pipeline stage is Manager-owned"`, `"## Long-horizon paper
  execution contract"`, `"## SETUP STAGE — action control (HARD
  OVERRIDE)"`) remain anywhere in `argus_skill/`. Root-caused all 26
  pre-existing failing tests in the suite before removing anything;
  the other 22 were either a missing optional dependency (see above,
  now fixed) or a small stale-assertion fix with the underlying behavior
  still exercised elsewhere (left as-is — tracked separately, not
  "outdated").

### Added
- **Daemon-resident Curator owns the teammate pool (replaces the detached coordinator).**
  `team/curator.py` is a managed thread in the daemon that keeps N teammates in
  flight, owns each as a retained `Popen` handle, and is the single reaper
  (per-child `killpg` + `task_board.fail` past the hard deadline), wired into
  `daemon/life_worker.py` with `try/finally: curator.stop()`. Campaigns are
  discovered via `.argus/team/<id>.json` markers (`team/registry.py`) so exactly
  one Curator manages every root — a duplicate coordinator is structurally
  impossible and a finished lead mission can never orphan the pool. Makes "the
  daemon can't control the teammate lifecycle" disappear by construction.
- **Deterministic leaderboard + a Curator agent role.** `team/leaderboard.py`
  folds teammate shards (now carrying `{target, metric, mechanism}`) into a
  per-target `{best, attempts}` ledger each tick; fresh teammates inherit a
  "what's already tried — do NOT re-derive" block so the pool builds depth
  instead of re-running exhausted breadth. The Curator is also a first-class LLM
  role (`builtin_skills/curator/argus-curator-role.md`, `ARGUS_SKILL_CURATOR_*`)
  that distills the leaderboard into `strategy.md` (low-frequency, best-effort).

### Changed
- **Retired the detached `nohup` coordinator.** `tools/team.py coordinate`, the
  `/proc` cmdline liveness archaeology, the `pool.json` lead-heartbeat
  orphan-protection, and the teammate self-SIGKILL are removed — superseded by
  the resident Curator owning real process handles. `pool.json` is slimmed to
  `{width, state}` (width 0 = pause). The two "Fixed" items below were the
  best-effort coordinator-era mitigations the Curator now makes obsolete.

### Fixed
- **Rolling-pool coordinator no longer over-spawns a teammate herd.**
  `tools/team.py` `refill_once` sized the pool on
  `task_board.count_in_flight` — a board projection that *lags* reality. A
  freshly spawned teammate needs tens of seconds (import + backend init) to
  register its first heartbeat, and a teammate killed without cleanly failing
  its task stays `claimed`, so the coordinator repeatedly mistook
  already-running teammates for free slots and spawned a thundering herd on
  top of them (observed in production: width 8 → 49 live processes, width 96
  → 256, load 196). Occupancy is now `max(in_flight, live_pids)`, where
  `live_pids` (`_count_live_members`) counts roster members whose process is
  actually alive — making the pool size process-accurate. New
  `ARGUS_TEAM_MAX_SPAWN_PER_REFILL` env caps per-poll spawns to smooth the
  startup load when a large pool cold-fills. Side benefit: process-accurate
  counting also makes accidental duplicate coordinators safe (they observe the
  same live count instead of each double-spawning). The live-PID check
  (`_member_pid_alive`) also verifies, via `/proc/<pid>/cmdline`, that the PID
  is genuinely *this* member's `teammate_entry` (matching `--member-id`):
  a long campaign churns thousands of teammates, the roster never prunes, and
  the OS recycles dead PIDs onto unrelated processes — counting a recycled PID
  as "alive" had the opposite failure, making the pool look full so it stopped
  refilling (96 "alive" PIDs but only 55 real teammates → stuck at ~57/96).
- **Wedged teammates can no longer leak indefinitely.** `team/teammate_entry.py`
  now has a two-tier time-box: a soft `stop_event` the runner polls between
  rounds, plus a HARD backstop that `SIGKILL`s the teammate's own process
  `ARGUS_TEAMMATE_HARD_GRACE_S` after the soft deadline. A teammate wedged in a
  long codex/bash/ssh call (e.g. a hung B200 scoring round) used to blow past
  the soft deadline and never exit, piling up until the box overloaded (300+
  teammates → load 256); the hard kill lets its task heartbeat go stale so the
  coordinator reassigns it.

### Changed
- **Always-verbose, no toggle**: removed `verbose`/`quiet` runtime
  toggles and the `/verbose` `/quiet` slash commands. The 7×24
  lifetime-agent positioning means operators want to see every event
  the engine emits — `LifeStderrSink` no longer filters by
  `_USER_FACING_EVENTS` / `_VERBOSE_EVENTS` and prints everything that
  isn't on the in-life silence list. The slash commands now print a
  one-line note explaining the toggle was removed.
- **Memory placeholder removed from user surfaces**: `--backend
  memory`, `/backend memory`, the banner hint, and the "or run
  `/backend memory`" fallback strings in error messages are gone.
  Codex is the only production backend the REPL exposes. The
  `_MemoryRunner` class is preserved as a programmatic test-only API
  so the existing 500 tests still pass.
- **Single 7×24 entry point**: `argus-skill` is now the only command.
  The `run` and `list-skills` subcommands were removed — they
  fragmented the lifetime-agent positioning. Use the REPL for both
  one-shot tasks (`/add` then `/done`) and the persistent skill
  cache (`/skills ls`).
- **Daemon auto-spawned on REPL launch**: the cockpit now silently
  spawns a detached background worker unless `--no-daemon` is passed
  or one is already alive. The banner reports `daemon auto-spawned
  (pid X)` when this happens.
- **24h-friendly defaults**: `ARGUS_SKILL_DAILY_CAP_USD` raised from
  $5 to $50, `ARGUS_SKILL_PER_MISSION_CAP_USD` from $1 to $1.5. Env
  overrides still win.

### Added
- **Logout-survival probe**: `argus-skill --status` now reports the
  user's `loginctl Linger` state on Linux, and warns operators that
  the daemon may be killed at logout if linger is off (with the
  exact `loginctl enable-linger` command to fix it). README has a
  new "Will the daemon survive when I close the terminal / SSH / sleep?"
  section explaining the detach mechanism (double-fork + setsid +
  SIGHUP ignored) and the one OS-level caveat that remains.
- **Daemon ignores SIGHUP** — belt-and-suspenders alongside the
  existing `setsid`, so an over-eager process supervisor cannot
  bring the 7×24 worker down with a stray SIGHUP.
- **Iteration loop** — after a successful mission the supervisor
  hands the artefact to a new Critic agent. If the critic finds
  concrete operator-visible improvements, the same backlog item is
  re-armed with a polished objective for another cycle, capped by
  ``iteration_max_cycles`` (default 3) and ``iteration_budget_usd``
  (default $2). Anti-vanity prompt rules reject rename/comment-polish
  busywork. New REPL surface: `/add --once` opts a single item out;
  `/stop <id>` disables iteration on an in-flight item; `/add
  --cycles=N --budget=$X` overrides the limits per item. Banner row
  shows `iterate on  default 3 cycles · $2 budget`.
- **Critic agent** (`argus_skill.critic.Critic`) — stateless one-shot
  critic with a tolerant JSON parser; safe-stops on unparseable
  output instead of looping.
- **Journal rotation** — when `journal.jsonl` exceeds 50 MiB the
  next append rotates it to `journal.jsonl.1` so a 24/7 daemon
  cannot fill its disk.
- **Codex auth-failure detection** — known stderr patterns
  (`Unauthorized`, `expired token`, etc.) emit a daemon-level
  warning so an overnight token expiry surfaces instead of looping
  silently on failed missions.

### Fixed
- **Codex backend cost reporting**: `_sum_token_counts` now reads the
  `usage` field on `turn.completed` events (codex-cli ≥0.121 format),
  in addition to the older top-level / nested-content shapes. Mission
  journal entries previously reported `cost_usd=$0.0000` for every
  codex run regardless of real token consumption.
- **Multi-line piped stdin fragmenting into multiple missions**: when
  stdin is not a TTY (heredoc, `< script.txt`, pipe), the REPL now
  coalesces consecutive non-blank lines into a single logical message.
  Blank lines and slash-command lines (e.g. `/exit`) act as boundaries.
  Previously a 12-line task spec produced 4+ separate missions because
  each line was dispatched individually.
- **`argus-skill --status` hid `running` items**: orphan `running`
  backlog items (left over from a killed REPL/daemon) appeared in no
  status bucket. The status line now reports `running ⚠` and
  `skipped` counts, with a hint that orphans will be reaped on the
  next worker startup.

### Changed
- **Reviewer rule 8 (structural spec adherence)**: the reviewer prompt
  now explicitly requires that produced artifacts match the operator's
  structural constraints (file paths, framework choice such as pytest
  vs unittest, package layout, API shape, test count). Unjustified
  deviations must result in `continue`, not `done`, even when the
  work is functionally correct.

## [Earlier in this milestone]
  - `argus-skill --daemon` spawns a detached background worker
    (POSIX double-fork) that drains the backlog forever. Survives
    REPL exit, logout, terminal close.
  - `argus-skill --daemon-fg` runs the same worker in the foreground
    (for systemd / debugging).
  - `argus-skill --daemon-stop` sends SIGTERM and waits for graceful
    exit.
  - `argus-skill --status` prints daemon liveness + backlog summary
    + recent journal without entering the REPL.
  - REPL banner shows `⚡ daemon: pid X · up Yh` when a worker is
    running. The lifetime-agent positioning is now observable, not
    aspirational.
  - Daemon and REPL coexist via separate PID locks (`daemon.pid` vs
    `repl.pid`) and the atomic `Backlog.claim_next()` state machine.
    Re-execution is impossible.
  - systemd unit example shipped in README.
- `argus-skill --version` flag.
- `[project.optional-dependencies] codex` extra: `pip install
  'argus-skill[codex]'` now installs ArgusBot from upstream in one step.
- REPL banner preflight: when `backend=codex`, a launch-time warning is
  printed if ArgusBot is missing or the `codex` binary is not on
  `$PATH`. Avoids mid-mission surprise crashes.
- `CONTRIBUTING.md` and `CHANGELOG.md`.
- Backlog state-machine seal (`IllegalStateTransition`,
  `Backlog.claim_next()`, `Backlog.reap_orphans()`) — terminal items can
  never re-execute, and a process crash leaves them as `failed` instead
  of silently re-running on the next launch.
- Per-life-dir singleton lock (`<state>/repl.pid`). A second
  `argus-skill` invocation against the same state dir exits 2 with a
  clear message instead of corrupting `backlog.jsonl`.
- Free-text input typed at the prompt jumps to head of backlog
  (priority computed from existing items) instead of competing with
  older queued missions.
- Memory-backend lifecycle events now include `round_index`, `status`,
  `reason`, `confidence` so the UI renders `Round 1` / `review ✅ done`
  instead of `?` placeholders.

### Changed
- Default `--max-rounds` for `argus-skill run` aligned with the REPL
  (3 → 20). Single source of truth.
- PyPI classifier: Alpha → Beta.
- README "Status" section rewritten to describe the actual product
  surface (memory + codex backends, REPL-first).

### Removed
- `argus-skill base64` subcommand and `argus_skill.encoding` module.
  Demo cruft that leaked into the public CLI; deleted.
- `argus_skill/core/{supervisor,bus,daemon_client}.py` (~810 LOC) plus
  their dedicated tests (~530 LOC). Phase-1 scaffolding never wired
  into the REPL; resurrectable from git history if needed.
- `apps/{chat,daemon,go,life,mission,up}_app.py` — replaced by the
  single unified REPL.
- README "Base64 helper" section.
- Stale `pip install -e /path/to/ArgusBot` instructions across source
  error messages — replaced with `pip install 'argus-skill[codex]'`.

## [0.1.0]

Initial public release. Supervised skill-driven coding agent: matcher →
distiller → engineer → reviewer loop with a markdown skill cache and
optional codex backend.
