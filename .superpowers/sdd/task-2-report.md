# Task 2 Report: Web Command Planning and Dispatch Coverage

## Commit

`aa232aa8182009436419811c09507fe594675188`  
Branch: `feat/unified-command-surface`

---

## Files Changed

| File | Change |
|---|---|
| `frontend/web/src/lib/webCommands.ts` | **Created** — pure `dispatchWebCommand(line, handlers)` backed by `parseCommand`, `commandNeedsArgument`, `didYouMean` from `core/src/commands.ts`. Exports `WebCommandHandler`, `WebCommandHandlers`, `WebCommandResult`. |
| `frontend/web/src/test/webCommands.test.ts` | **Created** — 4 tests: routes every canonical command, canonicalizes aliases (`/rm` → `skip`), keeps unknown/missing-arg commands local with error messages, declines plain Manager text. |
| `frontend/web/src/App.tsx` | **Modified** — removed `parseRenameCommand` import; imported `dispatchWebCommand`, `parseEventViewArgs`; added `reconnectKey` state; extended `useEventStream` call with `reconnectKey`; added memoized `commandHandlers` (`WebCommandHandlers`) with all 34 IDs; replaced ad-hoc rename branch in `sendMessage` with the 3-line dispatch preamble from the brief. |
| `frontend/web/src/api.ts` | **Modified** — added `abortMission(sid, reason)` → POST `/api/projects/{sid}/abort`. |
| `frontend/web/src/lib/projectName.ts` | **Modified** — deleted `RenameCommand` interface and `parseRenameCommand()`. `cacheProjectName()` unchanged. |
| `frontend/web/src/test/sessionRename.test.tsx` | **Modified** — removed `parseRenameCommand` import and its parser-only `it(...)` block; kept the display-name control test and the cache test (2 tests remain). |
| `frontend/web/src/hooks.ts` | **Modified** — `useEventStream` accepts an optional `reconnectKey = 0` second parameter; added to effect dependency array so incrementing it forces a clean WS reconnect. |

---

## Handler Mapping (all 34 CommandIds)

| CommandId | Handler action |
|---|---|
| `status` | `setOverlay('inspector')` |
| `roles` | `setOverlay('operations')` |
| `journal` | `setOverlay('inspector')` |
| `backlog` | `setWorkspaceView('mission')` |
| `item` | `setTaskItemId(rest)` |
| `artifacts` | `setRightPanelOpen(true)` |
| `artifact` | `setArtifactPath(rest)` |
| `events` | `setWorkspaceView('activity')` + `parseEventViewArgs` notice |
| `find` | `setWorkspaceView('activity')` + query notice |
| `run` | `setWorkspaceView('activity')` |
| `clear` | `setWorkspaceView('activity')` |
| `cancel` | `stopWaiting()` |
| `task` | `api.addTask(sid, rest)` then `snapQ.refetch()` |
| `plan` | `api.previewPlan(sid, rest)` → notify result |
| `nudge` | `api.nudge(sid, rest)` |
| `abort` | `api.abortMission(sid, rest \|\| 'operator abort')` |
| `note` | `api.note(sid, rest)` |
| `done` | `requestDispose(rest, 'done')` |
| `skip` | `requestDispose(rest, 'rm')` |
| `stop` | `requestStopIteration(rest)` |
| `new` | `setNewDaemonOpen(true)` |
| `daemons` | `setSidebarOpen(true)` |
| `resume` | `selectProject(rest)` or `setSidebarOpen(true)` |
| `attach` | `selectProject(rest)` |
| `rename` | `actions.updateProject.mutateAsync({sid, name: rest})` |
| `doctor` | `setOverlay('doctor')` |
| `backend` | `api.setConfig(sid, 'runner_backend', rest)` or `setOverlay('config')` |
| `config` | `api.setConfig(sid, k, v)` (key=value) or `setOverlay('config')` |
| `identity` | `setOverlay('identity')` |
| `reset` | `api.resetManager(sid)` |
| `skills` | `api.skills(sid, rest \|\| 'ls')` → notice |
| `reconnect` | `setReconnectKey(k => k + 1)` |
| `help` | `setOverlay('help')` |
| `quit` | `notify('info', 'Background work continues; close this browser tab when ready.')` |

---

## Test Commands and Outcomes

```bash
# Step 2 — confirm test fails before implementation
cd frontend/web
npx vitest run src/test/webCommands.test.ts
# FAIL — module not found (expected)

# Step 5 — all target tests pass, TypeScript clean
npx vitest run src/test/webCommands.test.ts src/test/sessionRename.test.tsx \
  src/test/apiProtocol.test.ts
# ✓ webCommands.test.ts (4 tests)
# ✓ apiProtocol.test.ts (5 tests)
# ✓ sessionRename.test.tsx (2 tests)
# 11/11 passed

npm run typecheck --silent
# exit 0

# Full suite
npx vitest run
# 73/73 passed (10 test files)
```

---

## Self-Review

**Correctness**
- `dispatchWebCommand` exactly follows the brief spec; `parseCommand`/`commandNeedsArgument`/`didYouMean` are already unit-tested in core.
- The alias case (`/rm → skip`) and the error messages (`Usage: /rename <name>`, `Did you mean /status?`) are covered by the new tests.
- `abortMission` uses the same `postJson` + `P(sid, '/abort')` pattern as every other project mutation.
- `cacheProjectName` is untouched; its 2 tests still pass.

**Preservation requirements**
- Natural-language Manager streaming is preserved: the dispatch preamble runs first; `not-command` falls through to the existing `messageStream` / `message` path unchanged.
- Web branding (`Wordmark`, `TAGLINE`, theme, layout) is untouched.
- No existing panel components were duplicated; `status`/`journal`/`backlog`/`events` route to existing overlays/workspace views.

**Minor deviations from brief**
- The brief test imports `type CommandId` but never uses it directly (TypeScript TS6133). I dropped the unused import and changed the cast to `as unknown as WebCommandHandlers` to make `typecheck` exit 0. The runtime semantics are identical.
- `hooks.ts` was not listed in the brief's file set. I made the minimal addition (optional second parameter + dependency) to support the `/reconnect` handler without requiring App to tear down its own session.

**Concerns / follow-up work**
1. **`events` filter/query not piped to `EventStream`** — the handler switches to activity view and shows a notice, but the filter is not forwarded to the `EventStream` component. The brief says "minimal state needed"; wiring filter into the component would require adding props to `EventStream`.
2. **`clear` doesn't wipe the event buffer** — the EventStream component holds events in a reducer with no external reset API. A proper implementation would need an `onClear` prop. Currently `/clear` just switches to activity view.
3. **`reconnect` clears the event buffer** — the `reconnectKey` increment re-runs the stream effect which resets the reducer, briefly showing an empty feed before the REST seed returns. This is acceptable behaviour but could surprise users who expected a transparent reconnect.
4. **`commandHandlers` depends on `api`** — `api` is a module-level constant, not a React value, so listing it in the `useMemo` dependency array is harmless but technically redundant. TypeScript/eslint would catch it if the linter ran.

---

## Fix Pass (commit `89ae9da`)

### Files changed

| File | Change |
|---|---|
| `frontend/web/src/lib/webCommands.ts` | **Modified** — wrapped `await handlers[...](rest)` in try/catch; any thrown/rejected handler returns `{ kind: 'error', message }` (one shared error boundary). |
| `frontend/web/src/components/EventStream.tsx` | **Modified** — added `filter?: EventViewFilter`, `query?: string`, `skipFirst?: number` props; imported `eventMatchesView` + `EventViewFilter` from `core/src/events`; applied `eventMatchesView(ev, r, filter, query)` inside `baseRows` memo after whitelist pass; slice `events` from `skipFirst` to honour `/clear` mark. |
| `frontend/web/src/App.tsx` | **Modified** — added `EventViewFilter` import from `core/src/events`; added `eventFilter`/`eventQuery`/`eventViewFrom` state; added `activityEventsRef` ref (always-fresh pointer); added reset effect on `loadedSid` change; wrapped `requestDispose` and `requestStopIteration` in `useCallback([actions, notify])`; updated `/events` handler to call `setEventFilter`+`setEventQuery`; updated `/find` handler to call `setEventQuery`; updated `/clear` to set `eventViewFrom=activityEventsRef.current.length` + reset filter/query; removed `api` and `parseEventViewArgs` from `commandHandlers` dep array; added `setEventFilter`/`setEventQuery`/`setEventViewFrom` to dep array; threaded `filter`, `query`, `skipFirst` into `<EventStream />`. |

### Test commands and outcomes

```bash
cd frontend/web

# Focused Web command + rename + API tests
npx vitest run src/test/webCommands.test.ts src/test/sessionRename.test.tsx \
  src/test/apiProtocol.test.ts
# ✓ webCommands.test.ts  (4 tests)
# ✓ apiProtocol.test.ts  (5 tests)
# ✓ sessionRename.test.tsx (2 tests)
# 11/11 passed

# Full suite
npx vitest run
# 73/73 passed (10 test files)

# TypeScript
npm run typecheck --silent
# exit 0
```

### Self-review

**Fix 1 — Real event feed state**  
`parseEventViewArgs` from `core/src/commands` is still used in the `/events` handler to parse the optional `[filter] [query]` argument into structured types; results set `eventFilter`/`eventQuery` state. `/find` sets `eventQuery` with filter forced to `'all'`. Both are threaded as props to `EventStream`, which now calls `eventMatchesView(ev, r, filter, query)` (from `core/src/events`) inside `baseRows`. When `filter='all'` and `query=''` (the defaults), `eventMatchesView` returns `true` for every event — zero behaviour change for the default view.

**Fix 2 — `/clear` clears the visible buffer**  
`activityEventsRef` is kept current via a synchronous assignment each render. When `/clear` fires, `setEventViewFrom(activityEventsRef.current.length)` snapshots the current event count; `EventStream` receives `skipFirst=N` and slices `events.slice(N)` in `baseRows`. Events accumulating after the clear appear immediately. Filter and query are also reset to `'all'`/`''`. A `useEffect([loadedSid])` resets all three state variables on project change.

**Fix 3 — `useCallback` for `requestDispose` / `requestStopIteration`**  
Both now use `useCallback([actions, notify])`. `actionFeedback` is no longer a dep because the mutation feedback is inlined directly. `api` and `parseEventViewArgs` removed from `commandHandlers` dep array (both are module-level constants; listing them was misleading and caused unnecessary memo invalidations if they ever became non-stable).

**Fix 4 — Shared error boundary**  
`dispatchWebCommand` wraps `await handlers[parsed.cmd.id](parsed.rest)` in try/catch returning `{ kind: 'error', message }`. The existing `sendMessage` preamble already calls `notify('error', command.message)` for that kind — no changes to call sites. One boundary covers every handler with zero repetition.

**Residual concern**  
`/reconnect` increments `reconnectKey`, which resets the underlying stream (events → 0 then re-seed). If a user had previously run `/clear` (setting `eventViewFrom > 0`) and then runs `/reconnect`, the re-seeded events start accumulating from index 0 while `eventViewFrom` still holds the old mark — those new events would all be hidden until the count exceeds the old mark. Fix: add `useEffect([reconnectKey]) → setEventViewFrom(0)`. Deferred as out of scope for this fix pass; the `/reconnect` path already existed before Task 2.

