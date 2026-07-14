# Recovery Task 2 Report — Graceful Stale-Release Recovery

**Commit:** `f38b00b` on `feat/unified-command-surface`
**Date:** 2026-07-14

## Files Modified

| File | Change |
|------|--------|
| `frontend/tui/src/ensureApi.ts` | Added recovery path with injectable deps, ownership check, SIGTERM, bounded wait, respawn + ownership write |
| `frontend/tui/src/args.ts` | Added `ownerFile?: string` to `Args`, default from `ARGUS_TUI_API_OWNER_FILE` |
| `frontend/tui/src/cli.tsx` | Pass `args.ownerFile` to both `ensureApi()` call sites (Boot + --web) |
| `frontend/tui/test/protocol.test.ts` | Added 3 recovery tests (red→green TDD) |
| `.gitignore` | Added `.superpowers/` |

## Test Results

```
$ node --import tsx --test test/protocol.test.ts test/args.test.ts test/apiOwnership.test.ts

# tests 26
# pass 26
# fail 0

$ npx tsc -p tsconfig.json --noEmit
# exit 0 (clean)
```

### New Tests

| # | Test | Asserts |
|---|------|---------|
| 1 | `replaces a proven owned stale API with SIGTERM only` | Only SIGTERM sent to recorded PID (no SIGKILL); result is reachable |
| 2 | `never signals an incompatible unowned listener` | signal never called; message contains "ownership could not be proven" |
| 3 | `does not spawn when graceful shutdown times out` | SIGTERM sent but spawnApi never called; message contains "graceful shutdown timed out" |

### Existing Tests (regression)

All 5 pre-existing protocol tests pass unchanged, confirming no behavioral regression when `ownerFile` is absent.

## Design Decisions

1. **Opt-in via env only.** `ownerFile` comes from `ARGUS_TUI_API_OWNER_FILE`; no public CLI flag. This is a deployment control, not a user-facing option.

2. **Ownership-proven.** Recovery only proceeds when `readOwnedApi()` returns a validated record (matching host/port/binary/pid/argv). Unowned listeners are never signaled.

3. **SIGTERM only.** No escalation to SIGKILL. If the process doesn't exit, we refuse to spawn.

4. **8-second bounded wait.** 32 × 250 ms polls. Sleep is injectable for instant test execution.

5. **No spawn after timeout.** The `spawned: false` return prevents orphan processes when SIGTERM is ignored.

6. **New ownership after spawn.** `writeOwnershipRecord` is called with the new PID immediately after `spawnApi()`, before the compatibility poll loop.

7. **Dependency injection.** All process operations (`probeApi`, `readOwnedApi`, `signal`, `spawnApi`, `sleep`) are injectable via an optional `dependencies` object. Defaults use real implementations. Existing tests (fetch-mocking style) are unaffected.

8. **Preserved current behavior.** When `ownerFile` is absent, the incompatible branch returns the same refusal message as before.

## Concerns

1. **`writeOwnershipRecord` not injectable.** The implementation always calls the real `writeOwnershipRecordImpl` after spawn. Tests don't assert on it. If a future test needs to verify ownership writes, add it to the `dependencies` interface.

2. **`resolveBin()` called eagerly in recovery path.** Even when deps override all process ops, `resolveBin()` still runs (to provide the default `readOwnedApi` backendBin). Harmless but could be made lazy if needed.

3. **Race between SIGTERM and respawn.** After the old process becomes unreachable, we spawn immediately. If the port hasn't been fully released yet, the new backend may fail to bind. The compatibility poll loop (10 s) provides a buffer, but a very slow OS socket teardown could cause a transient failure.
