# Complete Rounded 02 Logo Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every remaining legacy Web and TUI Argus logo with the Rounded 02 identity.

**Architecture:** Web reuses its existing SVG `ArgusMark` and `Wordmark` primitives, including during boot, and ships a matching standalone favicon. TUI uses one shared compact Rounded 02 text mark plus full/compact terminal art derived from the same silhouette.

**Tech Stack:** React 18, Ink 5, SVG, TypeScript, Vitest, Node test runner.

## Global Constraints

- No runtime source may retain the legacy `ARGUS-SKILL` banner.
- Web uses actual Rounded 02 SVG geometry; TUI uses terminal-safe derived block art.
- Preserve blue-gold colors, splash timing, skip controls, responsive breakpoints, and reduced motion.
- Favicon uses fixed user-space blue-to-gold SVG gradient.
- Header, Splash, ResumePicker, FirstRun, connecting, and error states share the new identity.
- No dependency changes.

---

### Task 1: Web boot and favicon identity

**Files:**
- Modify: `frontend/web/src/components/BootSplash.tsx`
- Modify: `frontend/web/src/components/Wordmark.tsx`
- Modify: `frontend/web/src/test/core.test.ts`
- Replace: `frontend/web/public/favicon.svg`

**Interfaces:**
- Consumes: existing `Wordmark` and `ArgusMark`.
- Produces: boot-specific wide/compact logo rendering without ASCII constants.

- [ ] **Step 1: Write failing Web identity tests**

Update the BootSplash test in `frontend/web/src/test/core.test.ts`:

```ts
it('uses Rounded 02 SVGs for both boot splash widths', () => {
  const html = renderToStaticMarkup(
    createElement(BootSplash, { onDone: () => undefined }),
  );
  expect(html).toContain('data-logo="rounded-horizontal"');
  expect(html).toContain('data-logo="rounded-mark"');
  expect(html).not.toContain('<pre');
  expect(html).not.toContain('ARGUS-SKILL');
});
```

Add a Node read of `frontend/web/public/favicon.svg` and assert
`gradientUnits="userSpaceOnUse"`, blue and gold stops, circular eye paths, and
absence of the old vertical `<rect class="mark"`.

- [ ] **Step 2: Run focused tests and verify red**

```bash
cd frontend/web
npx vitest run src/test/core.test.ts
```

Expected: FAIL because BootSplash still renders two `<pre>` elements.

- [ ] **Step 3: Reuse SVG primitives in BootSplash**

Export `RoundedLockup` from `Wordmark.tsx`. Replace both `<pre>` elements with:

```tsx
<div className="argus-web-splash-logo-full">
  <Wordmark size={72} />
</div>
<div className="argus-web-splash-logo-compact">
  <ArgusMark size={112} />
</div>
```

Remove imports from `frontend/core/src/splash`. Keep `WEB_SPLASH_DURATION_MS`,
event handlers, and outer splash classes unchanged.

- [ ] **Step 4: Replace favicon geometry**

Use the two Rounded 02 mark paths from `ArgusMark`, a
`gradientUnits="userSpaceOnUse"` gradient from x=66 to x=440, and fixed stops
`#075fe4` to `#d99a16`. Keep viewBox `0 0 512 512`.

- [ ] **Step 5: Run Web tests and typecheck**

```bash
cd frontend/web
npx vitest run src/test/core.test.ts
npm run typecheck --silent
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/web/src/components/BootSplash.tsx \
  frontend/web/src/components/Wordmark.tsx frontend/web/src/test/core.test.ts \
  frontend/web/public/favicon.svg
git commit -m "fix(web): complete Rounded 02 identity"
```

### Task 2: TUI Rounded 02 identity

**Files:**
- Modify: `frontend/core/src/splash.ts`
- Modify: `frontend/tui/src/components/Wordmark.tsx`
- Modify: `frontend/tui/src/components/Header.tsx`
- Modify: `frontend/tui/src/components/Splash.tsx`
- Modify: `frontend/tui/test/header.test.ts`
- Modify: `frontend/tui/test/splash.test.ts`

**Interfaces:**
- Produces: `ARGUS_ROUNDED_ART_FULL`, `ARGUS_ROUNDED_ART_COMPACT`, and shared TUI `Wordmark`.

- [ ] **Step 1: Write failing TUI identity tests**

Change splash tests to import the renamed constants and assert:

```ts
assert.doesNotMatch(full.join('\n'), /ARGUS-SKILL/);
assert.match(full.join('\n'), /[●◉]/);
assert.match(compact.join('\n'), /[●◉]/);
```

Change Header test to assert lowercase `argus`, the shared mark glyph, and
`Autonomous Research Lab`, while rejecting uppercase `ARGUS`.

- [ ] **Step 2: Run focused tests and verify red**

```bash
cd frontend/tui
node --import tsx --test test/splash.test.ts test/header.test.ts
```

Expected: FAIL on the legacy banner and uppercase Header.

- [ ] **Step 3: Replace terminal art and shared wordmark**

Define six-line full and four-line compact block art in
`frontend/core/src/splash.ts`. Both depict a rounded lower-case `a` enclosure
with a central `◉` eye and right stem. Rename width exports consistently.

In TUI `Wordmark.tsx`, replace the diamond with compact mark glyph `◉` and keep
the existing five-letter lowercase blue-to-gold ramp. In `Header.tsx`, import
and render `<Wordmark />` before the existing descriptor.

Update `Splash.tsx` imports to the renamed Rounded constants without changing
animation logic.

- [ ] **Step 4: Run TUI suite and typecheck**

```bash
cd frontend/tui
node --import tsx --test test/splash.test.ts test/header.test.ts
npm test
npm run typecheck --silent
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/core/src/splash.ts frontend/tui/src/components/Wordmark.tsx \
  frontend/tui/src/components/Header.tsx frontend/tui/src/components/Splash.tsx \
  frontend/tui/test/header.test.ts frontend/tui/test/splash.test.ts
git commit -m "fix(tui): complete Rounded 02 identity"
```

