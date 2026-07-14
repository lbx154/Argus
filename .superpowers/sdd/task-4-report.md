# Task 4 Report — Shared Web help and command palette

## Status: ✅ COMPLETE

**Commits:**
- `4607c70 feat(web): expose shared commands in palette and help`
- `31168f8 fix(web): restore transcript palette entry — no slash command counterpart`

---

## What was done

### Files modified
| File | Change |
|------|--------|
| `frontend/web/src/components/CommandPalette.tsx` | Export `commandPaletteRows()` — generates one `PaletteItem` per `SlashCommand`; required-arg commands prefill/focus the composer, others execute directly |
| `frontend/web/src/components/KeybindingHelp.tsx` | Import `helpGroups()` from core; widen to `max-w-2xl`; scroll body `max-h-[70dvh]`; render grouped command reference below keyboard bindings |
| `frontend/web/src/App.tsx` | Add `COMMANDS` + `commandPaletteRows` imports; replace hand-maintained `/doctor`, `/config`, `/identity`, `/transcript` duplicates with generated command rows; preserve project switching and non-command UI toggles; `sendMessageRef` keeps rows stable across renders |
| `frontend/web/src/test/core.test.ts` | Added `vi`, `COMMANDS`, `commandPaletteRows` imports; new test asserting 34 rows with `/status` and `/quit` hints |
| `frontend/web/src/test/commandHelp.test.tsx` | **Created** — renders `KeybindingHelp` with `open={true}`; asserts `/status`, `/task &lt;text&gt;`, `/skills [ls|promote`, `/quit` present |

---

## Test commands and results

### Step 1+2 — Failing tests (before implementation)
```
cd frontend/web
npx vitest run src/test/core.test.ts src/test/commandHelp.test.tsx
```
**Result:** FAIL — `commandPaletteRows is not a function`, `/status` not in help HTML.

### Step 5 — Focused tests (after implementation)
```
cd frontend/web
npx vitest run src/test/core.test.ts src/test/commandHelp.test.tsx
```
**Result:**
```
✓ src/test/commandHelp.test.tsx (1 test) 7ms
✓ src/test/core.test.ts (28 tests) 33ms
Test Files  2 passed (2)
     Tests  29 passed (29)
```

### Full suite
```
cd frontend/web && npm test -- --run
```
**Result:** `Test Files 13 passed (13)` / `Tests 80 passed (80)`

### TypeScript
```
cd frontend/web && npm run typecheck --silent
```
**Result:** exit 0, no errors.

---

## Design decisions

- **Restored `/transcript` in nav** (self-review catch): `/transcript` has no COMMANDS entry so removing it made `TranscriptModal` permanently unreachable. Only `/doctor`, `/config`, and `/identity` were true duplicates (they exist in COMMANDS and are now covered by `commandPaletteRows`). `/transcript` is web-only and kept in the nav.
- **`sendMessageRef`** in App.tsx keeps the 34 generated `run` closures stable — `commandRows` is computed once inside `useMemo` and the ref always points at the latest `sendMessage` when a button is clicked.
- **Removed exactly 4 duplicates** (`/doctor`, `/config`, `/identity`, `/transcript`) as specified. `inspector`, `operations`, `help` (keyboard shortcut, `?`), `reasoning`, and `kiosk` toggles are preserved as Web-only UI items.
- **`KeybindingHelp` uses the same `helpGroups()` as the TUI** — labels, aliases, and grouping are identical; no duplication of text.

---

## Concerns / caveats

None blocking. Minor notes:
- The `/help` command row (`hint: /help`, group "Other") appears alongside the "Keyboard shortcuts" `?` palette item — both open the help overlay. This is minor UX duplication but is correct per spec ("non-command UI toggles are preserved").
- `commandPaletteRows` is exported from `CommandPalette.tsx` rather than a separate utility file; this keeps it co-located with `PaletteItem` and `filterPaletteItems` without a new dependency boundary.
