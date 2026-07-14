# Task 3 Report: Web Slash Completion

**Branch:** `feat/unified-command-surface`  
**Commits:** `e191dd6` (feature) + `9a01218` (post-review fix)

---

## What Was Built

Slash command completion for the web composer:

| File | Change |
|------|--------|
| `frontend/web/src/components/SlashCompletionMenu.tsx` | Created — accessible `role="listbox"` dropdown, ≤8 rows, null on no match / whitespace |
| `frontend/web/src/test/slashCompletion.test.tsx` | Created — TDD test file (written first, failing, then made green) |
| `frontend/web/src/components/ChatBox.tsx` | Modified — controlled `value`/`onChange`/`onSend` props, keyboard + pointer completion, two-Escape pattern |
| `frontend/web/src/App.tsx` | Modified — `composerDraft` + `slashSelection` state, `sendMessage → Promise<boolean>` |
| `frontend/web/src/index.css` | Modified — `.slash-completion-menu` with `max-height / overflow-y / overscroll-behavior` |

---

## Tests

All run with `npx vitest run` in `frontend/web`.

| Test | File | Result |
|------|------|--------|
| renders shared command names and usage | `slashCompletion.test.tsx` | ✅ PASS |
| renders no menu after argument entry starts | `slashCompletion.test.tsx` | ✅ PASS |
| routes every canonical command to its stable handler id | `webCommands.test.ts` | ✅ PASS |
| canonicalizes aliases | `webCommands.test.ts` | ✅ PASS |
| keeps unknown and missing-argument commands local | `webCommands.test.ts` | ✅ PASS |
| declines plain Manager text | `webCommands.test.ts` | ✅ PASS |
| **Total (full suite)** | 12 test files | **76/76 PASS** |

TypeScript: `npm run typecheck --silent` → exit 0.

---

## TDD Steps Taken

1. Wrote `slashCompletion.test.tsx` with exact boilerplate from brief — confirmed **FAIL** (`SlashCompletionMenu` not found).
2. Created `SlashCompletionMenu.tsx` — confirmed **PASS**.
3. Refactored `ChatBox.tsx` to controlled, integrated menu.
4. Updated `App.tsx` state and `sendMessage` return type.
5. Added CSS.
6. Full 76-test suite green; TypeScript clean.
7. Committed `e191dd6`.

---

## Code Review Findings (self-review via `requesting-code-review` skill)

Reviewer: `code-review` subagent on diff `536b07b..e191dd6`

### Strengths (reviewer quoted)
> "Every checklist item from the spec is addressed: exact test boilerplate matches the brief, `SlashCompletionMenu` hits all the accessibility requirements, keyboard navigation is fully implemented, the CSS is verbatim from spec, and TypeScript is clean."

### Issues Found

#### Important — Fixed in `9a01218`

**Issue 1: Draft stayed visible for entire stream duration**

`sendMessage` was `await`ing the full streaming response before returning `true`, so the `submit()` in `ChatBox` did not clear the draft until the LLM finished — a visible regression from the previous synchronous `setText('')`.

**Fix:** Moved streaming work into a fire-and-forget `void (async () => { ... })()` IIFE; `sendMessage` returns `true` immediately after dispatch. The `pending` spinner still appears; only the draft text clears at once.

**Issue 2: Stale `!isCurrent()` guards returned `true`, could clobber new session's draft**

If a message was in-flight during a session switch, the aborted request's `!isCurrent()` guard paths returned `true`, causing `submit()` to call `onChange('')` and wipe whatever the user had typed in the new session.

**Fix:** Both issues resolved together by the IIFE refactor — `sendMessage` no longer awaits the stream, so there are no stale `return true` paths that reach `submit()`.

---

## Concerns / Known Limitations

- **No test for keyboard navigation behavior**: The brief only specified static rendering tests (`renderToStaticMarkup`). Keyboard interaction tests would require `@testing-library/react` and a DOM environment, which is not currently set up in the web package (no `jsdom` vitest env configured). Added as a future improvement opportunity.
- **menuDismissed is local to ChatBox**: The dismissed flag resets whenever the user types (via the textarea `onChange`). If the draft is programmatically changed from App.tsx by another path (future feature), `menuDismissed` would not reset. Currently there is no such path, so this is not a bug.
- **Tab key default browser action**: `e.preventDefault()` on Tab suppresses focus movement; accessibility linters may flag this in audits. This is standard and intentional for autocomplete menus.
