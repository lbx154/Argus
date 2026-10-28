# Changing Argus itself: its TypeScript core, prompts and ways of working, kept small and fixed at the root

This is how an Argus team changes Argus. The workspace is Argus's repository, and what it maintains
is the TypeScript core, `packages/core`: `src/` (the CLI, the flow, the store, the agent calls, the
web server), `prompts/` (each role's rules), `ways/` (the ways of working) and `test/`. Read
`docs/typescript-core.md` first: it says what the core does and what it leaves out on purpose. The
Python runtime under `argus/` serves old projects until they end: change it only when the task says
so, and port nothing from it unasked.

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
  or a way's README. The host reads the hand-off form's addressee and never guesses from a
  letter's text.
- **Knowledge lives in ways.** How a kind of work goes belongs in its folder under
  `packages/core/ways/`, not in the core or the role prompts, and the core names no way.

## Fixing a bug

Fix the cause, not the symptom, and not one more symptom each round. Most causes are one of four:
a hand-off made in prose where a form should be filled; one fact stored in two places that drift
apart; the host guessing what a role meant instead of reading the hand-off form; one rule written in
several places. Find which it is and remove it.

Every fix comes with a test that replays the real record that showed the bug. Take the lines that
matter from the project under `$ARGUS_HOME/projects/<id>/` (the backend's raw output in
`calls/<call>.jsonl`, the letters in `events.jsonl`, `project.json`), trim them into
`packages/core/test/fixtures/`, and feed them through the code that misread them. The test fails
before the fix and passes after it. A test written only from someone's reading of the bug
confirms that reading.

## How the work goes

1. **Read.** Follow the real call path from the CLI or the web server to where the behaviour
   happens, and find the closest code that already does something similar. Run `checks/core.mjs`
   and decide which findings bear on the task.
2. **Change.** Make the smallest coherent change, and delete what it leaves unused. Update
   `docs/typescript-core.md` where it describes what changed.
3. **Verify.** Build, run the tests, and show the behaviour at a real entry point.

Run the work in progress from its build, `node packages/core/dist/argus.mjs`, with `ARGUS_HOME` set
to a scratch folder: one process owns a state root, and the operator's belongs to the Argus they
run. Never `npm i -g` or `npm link` the repository, which replaces that Argus. To run the flow
without a model, put `packages/core/test/fixtures/fake-codex.mjs` on the `PATH` as `codex`: it
moves the work from the Manager through the Engineer and the Reviewer back to the operator.

## Done when

- the requested behaviour is shown at a real entry point (`argus run`, `argus serve` or the
  command the change touches, run from the build), not only in unit tests;
- `npm run check` passes at the repository root: the build, the type-check of the tests, and
  every package's tests;
- a bug fix has its replay test, which failed before the fix;
- nothing the change made unused is left, and `checks/core.mjs` reports nothing the change brought
  in, or the letter says why a finding stays;
- a change to what the package ships (`package.json`, `scripts/bundle.ts`, `prompts/`, `ways/`, the
  web cockpit) passes `packages/core/scripts/check-pack.sh`, which needs Docker. CI
  (`.github/workflows/core.yml`) runs the tests and installs the package on Linux, macOS and
  Windows on every push to a `feat/ts-core*` branch.

The Reviewer reads the whole diff and the callers of what changed, checks the types and runs the
tests itself, and judges behaviour and the four rules, not a count of findings. Its workspace is
read-only, so it type-checks without building, `npx tsc --noEmit -p packages/core/tsconfig.json`
(and `-p tsconfig.tests.json` for the tests), and runs the tests on the Engineer's build,
`node --import tsx --test packages/core/test/*.test.ts`. A new wrapper, option, registry or
fallback without a current user, a rename that simplifies nothing, or a wait the backend already
covers is a repair to send back.

## Checks

    node <this folder>/checks/core.mjs <repository>

| Script | Reports | Run |
|---|---|---|
| `core.mjs` | an export of `src/` no other file uses; a way named in `src/` or `prompts/`; a timer in `src/`; a path of one machine, a literal digest or a computed hash in `src/`, `prompts/`, `ways/` or `scripts/` | before handing a change to the Reviewer, and by the Reviewer before accepting |

It exits 0 when it finds nothing, 1 with one line per finding, and 2 when it could not run. A
finding is a candidate, not a verdict: fix it, or say in the letter why it stays.
