---
name: argus-maintenance
description: "Changing Argus itself: its TypeScript core, prompts and skills, kept small and fixed at the root"
---

In a source checkout, this skill maintains the TypeScript core, `packages/core`:
`src/` (the CLI, the flow, the store, the agent calls, the web server), `prompts/` (each role's
rules), `skills/` (Argus's own skills) and `test/`. `docs/typescript-core.md` says what the core
does and what it leaves out on purpose. The Python runtime under `argus/` serves old projects until
they end: change it only when the task says so, and port nothing from it unasked.

## What a change may add

Argus exists to keep agents doing research stably and simply. Its core hands work between roles,
delivers letters, records what happened and keeps the permission boundary, and nothing else. Four
rules follow, and each is a reason to turn a change away.

- **Lean.** Prefer deleting to adding, and reuse a mechanism that exists before writing one. Leave
  no dead code: no function, export, option, alias, fallback or compatibility path without a
  current caller. An abstraction needs more than one real user. A rename or a move that does not
  make the code simpler is not worth making.
- **Argus is not a parent.** What the backend already does, Argus does not build in: waiting,
  running work in the background, polling, timeouts, deciding a turn is stuck, retrying. Add no
  wait, poll, watchdog or retry, and delete one you find. A failure is recorded as it happened.
- **One fact, one place.** A fact lives in one place, with no copy or digest of it beside it: a
  project's state is in `project.json`, and `events.jsonl` records changes without ever being
  folded back into state. A rule is written once, in a role's prompt, the hand-off form's schema
  or a skill's SKILL.md. The host reads the hand-off form's addressee and never guesses from a
  letter's text.
- **Knowledge lives in skills.** How a kind of work goes belongs in its folder under
  `packages/core/skills/`, not in the core or the role prompts, and the core names no skill.

## Fixing a bug

Fix the cause, not the symptom, and not one more symptom each round. Most causes are one of three:
one fact stored in two places that drift apart; the host guessing from a letter's text instead of
reading only its addressee; one rule written in several places. Find which it is and remove it.

Every fix comes with a test that replays the real record that showed the bug. Take the lines that
matter from the project under `$ARGUS_HOME/projects/<id>/` (the backend's raw output in
`calls/<call>.jsonl`, the letters in `events.jsonl`, `project.json`), trim them into
`packages/core/test/fixtures/`, and feed them through the code that misread them. The test fails
before the fix and passes after it. A test written only from someone's reading of the bug confirms
that reading.

## Running it

A desktop's `argus-core/` resource directory may contain only an installed bundle, not this
repository. Identify the running process and its entry file before editing, and locate a checkout
for the fix and regression test. A disk edit does not reload an already running Node ESM bundle:
activate through the desktop's supported **Restart backend** command at a task boundary, or ask the
operator to do so. Never start a second owner of its state root. An explicitly authorized local
bundle patch needs a backup and rollback, and is not an upstream release or a live recovery until
the actual blocked role starts. Keep project-specific recovery evidence in that project's workspace.

Run the work in progress from its build, `node packages/core/dist/argus.mjs`, with `ARGUS_HOME` and
`ARGUS_SHARED` set to the same scratch folder: one process owns a state root, and the operator's belongs to the Argus they
run. Never `npm i -g` or `npm link` the repository, which replaces that Argus. To run the flow
without a model, put `packages/core/test/fixtures/fake-codex.mjs` on the `PATH` as `codex`.
On Windows use an npm-style `.cmd` shim naming that Node file; a POSIX link or extensionless
shebang alone is not a Windows CLI installation. In a shared checkout, coordinate builds so
one task does not replace the `dist/` another is testing. A
reviewer, whose workspace is read-only, type-checks with
`npx tsc --noEmit -p packages/core/tsconfig.json` and runs the tests on the Engineer's build,
`node --import tsx --test packages/core/test/*.test.ts`.

For Windows filesystem fixtures and a built/installed CLI smoke, see
`docs/windows-filesystem-tests.md` in the source checkout. It names the shared link helper and
keeps capability-gated file-link assertions separate from ordinary I/O tests.

## Handing it over

The change is its commits: the letter that delivers it names them, and opens first on the source
files they change, so the operator reads the change itself. What only checked it once (a report,
screenshots, a one-off script) stays out of the repository.

## Done when

The requested behaviour is shown at a real entry point (the web cockpit, served by the build's
`argus`), not only in unit tests, and `npm run check` passes at the repository root. A change to
what the package ships is also installed from its pack by `packages/core/scripts/check-pack.sh`,
which needs Docker, missing from a role's sandbox: the operator runs it when merging, and CI runs
the tests and that install on Linux, macOS and Windows on every push to a `feat/ts-core*` branch.
