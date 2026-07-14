# Public Brand Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the public Argus brand language to the existing React workbench without changing its three-column architecture, behavior, accessibility, or performance profile.

**Architecture:** Extend the existing CSS-variable/Tailwind design system rather than styling components independently. Shared React primitives own button and panel semantics; existing `useGsapMotion` remains the only runtime motion layer. High-density streaming surfaces receive static glass styling, while bounded UI transitions receive short GSAP animations.

**Tech Stack:** React 18, TypeScript, Tailwind CSS 3, GSAP 3.15, Vitest, Vite

## Global Constraints

- Preserve the current three-column layout, resizing, collapse behavior, routes, API contracts, and session behavior.
- Keep Geist Variable, Noto Sans SC Variable, and Geist Mono Variable.
- Do not add an animation dependency or eagerly load GSAP.
- Do not add continuous per-event animation to streaming logs.
- Preserve keyboard navigation, focus trapping, ARIA semantics, mobile operation, and reduced-motion behavior.
- Keep primary button magnetic displacement below 6 px and all bounded transitions between 180 and 320 ms.
- Do not introduce global smooth scrolling or horizontal overflow at 320 px.

---

### Task 1: Brand Tokens and Ambient Shell

**Files:**
- Modify: `frontend/web/src/index.css:5-66,190-197`
- Modify: `frontend/web/src/lib/theme.ts:1-37`
- Modify: `frontend/web/src/App.tsx` at the root shell container
- Test: `frontend/web/src/test/core.test.ts`

**Interfaces:**
- Consumes: existing `rgb(var(--token))` Tailwind color convention.
- Produces: `--spectral-blue`, `--spectral-violet`, `--spectral-rose`, `--spectral-gold`, `--glass`, `--glass-raised`, `--glass-edge`, `.workbench-shell`, `.glass-panel`, and `.glass-card`.

- [ ] **Step 1: Add a failing token contract test**

Add to `frontend/web/src/test/core.test.ts`:

```ts
import fs from 'node:fs';
import path from 'node:path';

it('defines the public-brand workbench surface contract', () => {
  const css = fs.readFileSync(path.resolve('src/index.css'), 'utf8');
  for (const token of [
    '--spectral-blue',
    '--spectral-violet',
    '--spectral-rose',
    '--spectral-gold',
    '--glass',
    '--glass-raised',
    '--glass-edge',
  ]) {
    expect(css).toContain(token);
  }
  expect(css).toContain('.workbench-shell');
  expect(css).toContain('.glass-panel');
  expect(css).toContain('.glass-card');
});
```

- [ ] **Step 2: Run the test and confirm the missing-token failure**

Run:

```bash
cd frontend/web
npm test -- --run src/test/core.test.ts
```

Expected: FAIL because `--spectral-blue` and the glass classes are absent.

- [ ] **Step 3: Implement the light and dark token mapping**

In `frontend/web/src/index.css`, add RGB-channel tokens to both theme blocks and define the shell classes:

```css
:root {
  --spectral-blue: 44 116 235;
  --spectral-violet: 105 73 205;
  --spectral-rose: 190 67 119;
  --spectral-gold: 207 153 49;
  --glass: 255 255 255;
  --glass-raised: 255 255 255;
  --glass-edge: 179 192 214;
}

:root[data-theme='dark'] {
  --spectral-blue: 110 168 255;
  --spectral-violet: 170 132 255;
  --spectral-rose: 255 126 177;
  --spectral-gold: 237 201 111;
  --glass: 15 27 45;
  --glass-raised: 20 35 57;
  --glass-edge: 65 88 122;
}

.workbench-shell {
  isolation: isolate;
  background:
    radial-gradient(circle at 12% 0%, rgb(var(--spectral-blue) / 0.10), transparent 32rem),
    radial-gradient(circle at 88% 100%, rgb(var(--spectral-gold) / 0.08), transparent 30rem),
    rgb(var(--bg));
}

.glass-panel {
  border-color: rgb(var(--glass-edge) / 0.55);
  background: rgb(var(--glass) / 0.82);
  backdrop-filter: blur(18px) saturate(1.15);
}

.glass-card {
  border: 1px solid rgb(var(--glass-edge) / 0.52);
  background: rgb(var(--glass-raised) / 0.72);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.36);
}
```

Apply `workbench-shell` only to the full application shell in `App.tsx`. Map `theme.accent`, role hues, and semantic colors to the new tokens without changing exported names.

- [ ] **Step 4: Verify tests and type checking**

Run:

```bash
cd frontend/web
npm test -- --run src/test/core.test.ts
npm run typecheck
```

Expected: both commands PASS.

- [ ] **Step 5: Commit the token layer**

```bash
git add frontend/web/src/index.css frontend/web/src/lib/theme.ts frontend/web/src/App.tsx frontend/web/src/test/core.test.ts
git commit -m "feat(web): add public-brand workbench tokens"
```

---

### Task 2: Shared Buttons, Cards, and Modals

**Files:**
- Modify: `frontend/web/src/components/primitives.tsx:1-86`
- Modify: `frontend/web/src/components/Modal.tsx:28-125`
- Modify: `frontend/web/src/index.css`
- Test: `frontend/web/src/test/core.test.ts`

**Interfaces:**
- Consumes: `.glass-card`, spectral tokens, and existing `Button` variants.
- Produces: `.brand-button`, `.brand-button-primary`, `.brand-button-ghost`, `.brand-button-danger`, and `.brand-modal`.

- [ ] **Step 1: Add failing markup tests for primitive variants**

Add imports for `Button`, `PanelHeader`, `Modal`, and assertions:

```ts
it('renders branded shared primitives without changing semantics', () => {
  const primary = renderToStaticMarkup(createElement(Button, { variant: 'primary' }, 'Run'));
  const ghost = renderToStaticMarkup(createElement(Button, { variant: 'ghost' }, 'Cancel'));
  const danger = renderToStaticMarkup(createElement(Button, { variant: 'danger' }, 'Delete'));
  expect(primary).toContain('brand-button-primary');
  expect(ghost).toContain('brand-button-ghost');
  expect(danger).toContain('brand-button-danger');
  expect(primary).toContain('type="button"');
});
```

- [ ] **Step 2: Run the test and confirm branded classes are missing**

Run:

```bash
cd frontend/web
npm test -- --run src/test/core.test.ts
```

Expected: FAIL on `brand-button-primary`.

- [ ] **Step 3: Centralize button visuals in CSS**

Replace variant-specific utility strings in `Button` with stable branded class names:

```tsx
const styles = {
  ghost: 'brand-button-ghost',
  primary: 'brand-button-primary',
  danger: 'brand-button-danger',
};

className={`brand-button ${styles[variant]} ${className}`}
```

Define `.brand-button` as a 180 ms transition with a 2 px transparent border. Use pseudo-elements for the primary white core and spectral border, a quiet translucent fill for ghost, and semantic red for danger. Add `@media (hover: hover) and (pointer: fine)` hover styles, `:active` mobile feedback, disabled behavior, and reduced-motion overrides.

- [ ] **Step 4: Restyle shared cards, headers, and modal shell**

Apply `glass-card` to `.card`, refine `.chip` and `PanelHeader`, and replace the opaque modal scrim with:

```tsx
<div
  ref={backdropRef}
  className="absolute inset-0 bg-[rgb(4_11_24_/_0.58)] backdrop-blur-md"
/>
```

Apply `brand-modal glass-panel` to the dialog. Keep the existing focus trap, Escape handling, and click-to-dismiss behavior unchanged. Keep the GSAP duration at 280 ms.

- [ ] **Step 5: Verify primitive and modal behavior**

Run:

```bash
cd frontend/web
npm test -- --run src/test/core.test.ts
npm run typecheck
```

Expected: PASS with no accessibility behavior changes.

- [ ] **Step 6: Commit shared components**

```bash
git add frontend/web/src/components/primitives.tsx frontend/web/src/components/Modal.tsx frontend/web/src/index.css frontend/web/src/test/core.test.ts
git commit -m "feat(web): brand shared controls and modals"
```

---

### Task 3: Three-Column Surface Integration and Bounded Motion

**Files:**
- Modify: `frontend/web/src/components/Sidebar.tsx`
- Modify: `frontend/web/src/components/TopBar.tsx`
- Modify: `frontend/web/src/components/EventStream.tsx`
- Modify: `frontend/web/src/components/ChatBox.tsx`
- Modify: `frontend/web/src/components/ResearchCanvas.tsx`
- Modify: `frontend/web/src/components/RolesPanel.tsx`
- Modify: `frontend/web/src/components/BackendHandshake.tsx`
- Modify: `frontend/web/src/lib/motion.ts`
- Test: `frontend/web/src/test/core.test.ts`

**Interfaces:**
- Consumes: shared glass and button classes from Tasks 1 and 2.
- Produces: consistent surface classes and motion constants `motionDuration.fast`, `motionDuration.normal`, and `motionDistance.magnetic`.

- [ ] **Step 1: Add failing bounded-motion tests**

Export constants from `lib/motion.ts` and add the intended assertions first:

```ts
import { motionDistance, motionDuration, motionQueries } from '../lib/motion';

it('keeps workbench motion bounded and accessible', () => {
  expect(motionDuration.fast).toBeGreaterThanOrEqual(0.18);
  expect(motionDuration.normal).toBeLessThanOrEqual(0.32);
  expect(motionDistance.magnetic).toBeLessThanOrEqual(6);
  expect(motionQueries.reduceMotion).toBe('(prefers-reduced-motion: reduce)');
});
```

- [ ] **Step 2: Run the test and confirm constants are absent**

Run:

```bash
cd frontend/web
npm test -- --run src/test/core.test.ts
```

Expected: FAIL because `motionDistance` and `motionDuration` are not exported.

- [ ] **Step 3: Add shared motion constants and button magnetic hook**

In `lib/motion.ts`:

```ts
export const motionDuration = { fast: 0.18, normal: 0.28 } as const;
export const motionDistance = { magnetic: 5 } as const;
```

Add a `useMagneticMotion` hook in the same module that uses `useGsapMotion`, activates only for `(hover: hover) and (pointer: fine)`, clamps x/y displacement to 5 px, returns to zero on pointer leave, and is inert under reduced motion.

- [ ] **Step 4: Apply the density-adjusted brand surfaces**

Use `glass-panel` on Sidebar, TopBar, ChatBox composer, ResearchCanvas, and RolesPanel boundaries. Use `glass-card` on session rows, role cards, bounded notices, and artifact controls. Preserve current DOM order, width calculations, scroll containers, and event keys.

Do not apply per-row decorative animation to the generic EventStream. Retain the existing one-time conversation-row entry and reduce it to `motionDuration.normal`.

- [ ] **Step 5: Upgrade bounded state transitions**

Use `useMagneticMotion` only on shared primary actions and the header start action. Keep mobile `:active` behavior CSS-only. Enhance `BackendHandshake` with spectral line colors and the existing lazy GSAP timeline; do not increase its number of infinite animations.

- [ ] **Step 6: Run focused tests and production build**

Run:

```bash
cd frontend/web
npm test -- --run src/test/core.test.ts src/test/sessionRename.test.tsx
npm run build
```

Expected: tests PASS; Vite build completes; GSAP remains a separate lazy chunk.

- [ ] **Step 7: Commit integrated workbench surfaces**

```bash
git add frontend/web/src/components frontend/web/src/lib/motion.ts frontend/web/src/test/core.test.ts
git commit -m "feat(web): unify workbench surfaces and motion"
```

---

### Task 4: Visual, Accessibility, and Performance Verification

**Files:**
- Modify only if verification exposes a defect in files changed by Tasks 1–3.
- Test: `frontend/web/src/test/core.test.ts`

**Interfaces:**
- Consumes: complete branded workbench.
- Produces: release-ready frontend bundle and measured regression evidence.

- [ ] **Step 1: Run all existing frontend checks**

```bash
cd frontend/web
npm test
npm run build
```

Expected: all Vitest tests PASS; type checking and Vite build PASS.

- [ ] **Step 2: Record bundle sizes**

```bash
cd frontend/web
find dist/assets -type f -printf '%f %s\n' | sort
```

Expected: GSAP remains in a separate asset; the main application chunk does not absorb the GSAP payload.

- [ ] **Step 3: Verify the production UI in Playwright**

Start the production preview on an unused port and check:

```bash
cd frontend/web
npm run preview -- --host 127.0.0.1 --port 4180
```

At 320, 390, 768, and 1440 px, verify light and dark themes, sidebar collapse, preview collapse, composer focus, modal focus trap, command palette, artifact/PDF transitions, disconnected landing, long event logs, and mobile controls. Assert `document.documentElement.scrollWidth === document.documentElement.clientWidth`.

- [ ] **Step 4: Verify reduced motion**

Open the same states with `reducedMotion: 'reduce'`. Confirm there is no magnetic displacement, looping handshake pulse, modal scale, or decorative ambient movement; content remains immediately visible and usable.

- [ ] **Step 5: Verify long-log responsiveness**

Open a real session with a large event history. Measure role expansion and scrolling. Accept only if role expansion remains at or below the previous approximately 265 ms baseline and scrolling does not trigger long tasks over 50 ms.

- [ ] **Step 6: Commit any verification fixes**

If fixes were required:

```bash
git add frontend/web
git commit -m "fix(web): close branded workbench regressions"
```

If no fixes were required, do not create an empty commit.

- [ ] **Step 7: Publish the release-matched web bundle**

Run the repository's existing release-manifest and web publication workflow, then restart only the 8798 WebAPI behind the persistent 8799 proxy. Verify 8799 returns the new release ID and the frontend reports no incompatible API banner.
