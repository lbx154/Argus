# Public Brand Workbench V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Strengthen the deployed Argus Web workbench so its Rounded 02 identity, blue-gold glass surfaces, active-state hierarchy, and bounded motion are visibly consistent with `argusbot.cn`.

**Architecture:** Keep the current React structure and API unchanged. Upgrade the shared CSS token/surface layer, replace old SVG identity geometry in `Wordmark.tsx`, and add only shell-level state classes for tabs, sessions, and role groups. Continue using the existing lazy GSAP helper; persistent ambient movement is CSS-only and visibility controlled.

**Tech Stack:** React 18, TypeScript, Tailwind CSS 3, GSAP 3.15, Vitest, Vite, Playwright

## Global Constraints

- Base implementation is current `origin/main` at or after `80641e4`.
- No backend API, session behavior, or three-column geometry changes.
- No new runtime dependency.
- Main JavaScript growth below 5 KB gzip.
- No per-event, Markdown, PDF, code, or log-line continuous animation.
- Magnetic displacement remains at or below five pixels.
- Reduced motion disables ambient and decorative transitions.
- No horizontal overflow at 320, 390, 768, or 1440 px.

---

### Task 1: Rounded 02 Workbench Identity

**Files:**
- Modify: `frontend/web/src/components/Wordmark.tsx`
- Modify: `frontend/web/src/test/core.test.ts`

**Interfaces:**
- Produces: `RoundedMark`, `RoundedLockup`, and the existing `ArgusMark`/`Wordmark` API.

- [ ] **Step 1: Add failing identity markup tests**

Add to `core.test.ts`:

```ts
import { ArgusMark, Wordmark } from '../components/Wordmark';

it('uses Rounded 02 geometry with one continuous brand gradient', () => {
  const lockup = renderToStaticMarkup(createElement(Wordmark, { size: 24 }));
  const mark = renderToStaticMarkup(createElement(ArgusMark, { size: 32 }));
  expect(lockup).toContain('data-logo="rounded-horizontal"');
  expect(mark).toContain('data-logo="rounded-mark"');
  expect(lockup).toContain('gradientUnits="userSpaceOnUse"');
  expect(lockup).toContain('x1="180"');
  expect(lockup).toContain('x2="1280"');
  expect(lockup).not.toContain('NightPupil');
});
```

- [ ] **Step 2: Run the focused test**

```bash
cd frontend/web
npm test -- --run src/test/core.test.ts
```

Expected: FAIL because current markup lacks Rounded 02 data attributes.

- [ ] **Step 3: Replace geometry while preserving exports**

In `Wordmark.tsx`:

- Replace the old mark paths with Rounded 02 paths from the verified public asset.
- Use one `linearGradient gradientUnits="userSpaceOnUse"`:
  - Horizontal: `x1=180`, `x2=1280`.
  - Mark: `x1=66`, `x2=440`.
- Stops use `var(--spectral-blue)`, `var(--spectral-violet)`, and `var(--spectral-gold)`.
- Keep `ArgusMark({ size, className })` and `Wordmark({ size, tag, compact })` signatures.
- Set `data-logo="rounded-mark"` and `data-logo="rounded-horizontal"`.

- [ ] **Step 4: Verify markup and type checking**

```bash
npm test -- --run src/test/core.test.ts
npm run typecheck
```

- [ ] **Step 5: Commit identity**

```bash
git add frontend/web/src/components/Wordmark.tsx frontend/web/src/test/core.test.ts
git commit -m "feat(web): adopt Rounded 02 workbench identity"
```

---

### Task 2: Stronger Glass Depth and Ambient Canvas

**Files:**
- Modify: `frontend/web/src/index.css`
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/components/Sidebar.tsx`
- Modify: `frontend/web/src/components/ResearchCanvas.tsx`
- Modify: `frontend/web/src/components/ChatBox.tsx`
- Modify: `frontend/web/src/components/Modal.tsx`
- Modify: `frontend/web/src/test/core.test.ts`

**Interfaces:**
- Produces: `.glass-panel--side`, `.glass-panel--main`, `.glass-panel--raised`, `.ambient-canvas`, and visibility state `data-page-visible`.

- [ ] **Step 1: Extend the failing surface contract**

In the existing CSS contract test, require:

```ts
for (const selector of [
  '.ambient-canvas',
  '.glass-panel--side',
  '.glass-panel--main',
  '.glass-panel--raised',
]) {
  expect(css).toContain(selector);
}
expect(css).toContain('@keyframes ambient-drift');
expect(css).toContain('[data-page-visible=\"false\"]');
```

- [ ] **Step 2: Confirm the surface test fails**

```bash
npm test -- --run src/test/core.test.ts
```

- [ ] **Step 3: Implement visible brand depth**

In `index.css`:

- Raise light ambient opacities to 18%, 12%, and 16% for blue/violet/gold.
- Raise dark ambient opacities to 22%, 16%, and 20%.
- Add `ambient-drift` at 60 seconds using only pseudo-element opacity and transform.
- Add the three glass depths:
  - Side: 68–78% surface opacity, 22 px blur.
  - Main: 88–94% opacity for reading.
  - Raised: 92–97% opacity, stronger spectral edge and shadow.
- Use pseudo-elements for a continuous cool-to-warm edge.
- Disable ambient animation under reduced motion and when `data-page-visible="false"`.

- [ ] **Step 4: Add visibility lifecycle**

In `App.tsx`, add one effect:

```ts
useEffect(() => {
  const sync = () => {
    document.documentElement.dataset.pageVisible = String(!document.hidden);
  };
  sync();
  document.addEventListener('visibilitychange', sync);
  return () => document.removeEventListener('visibilitychange', sync);
}, []);
```

Apply classes:

- Root: `ambient-canvas`.
- Main activity section: `glass-panel--main`.
- Sidebar and ResearchCanvas: `glass-panel--side`.
- ChatBox and Modal: `glass-panel--raised`.

- [ ] **Step 5: Verify**

```bash
npm test -- --run src/test/core.test.ts
npm run typecheck
```

- [ ] **Step 6: Commit glass depth**

```bash
git add frontend/web/src
git commit -m "feat(web): strengthen branded glass depth"
```

---

### Task 3: Active Sessions, Tabs, Roles, and Bounded Transitions

**Files:**
- Modify: `frontend/web/src/App.tsx`
- Modify: `frontend/web/src/components/Sidebar.tsx`
- Modify: `frontend/web/src/components/EventStream.tsx`
- Modify: `frontend/web/src/index.css`
- Modify: `frontend/web/src/test/core.test.ts`

**Interfaces:**
- Produces: `.workspace-tabs`, `.workspace-tab`, `.workspace-tab-indicator`, `.session-card--active`, `.role-log-group`, and state data attributes.

- [ ] **Step 1: Add failing state markup tests**

Extend static markup tests:

```ts
expect(sidebarHtml).toContain('session-card');
expect(sidebarHtml).toContain('data-active=');
```

Add CSS contract assertions for `.workspace-tab-indicator` and `.role-log-group[data-open="true"]`.

- [ ] **Step 2: Run and confirm failure**

```bash
npm test -- --run src/test/core.test.ts src/test/sidebar.test.ts
```

- [ ] **Step 3: Implement active session edge**

In `Sidebar.tsx`:

- Add `data-active={active ? 'true' : 'false'}` to session cards.
- Remove flat `bg-blue/10`.
- Use CSS spectral border, inner glow, and a two-pixel active rail.
- Hover affects only edge/glow.

- [ ] **Step 4: Implement sliding workspace indicator**

Replace the two loose tab classes in `App.tsx` with:

```tsx
<div className="workspace-tabs" data-active={workspaceView}>
  <span className="workspace-tab-indicator" aria-hidden="true" />
  <button className="workspace-tab" data-selected={workspaceView === 'mission'}>Mission</button>
  <button className="workspace-tab" data-selected={workspaceView === 'activity'}>Activity</button>
</div>
```

CSS translates the indicator 0%/100% in 200 ms.

- [ ] **Step 5: Implement role edge hierarchy**

In `RoleLogGroup`:

```tsx
<section
  className="role-log-group"
  data-role={role}
  data-open={open ? 'true' : 'false'}
  data-active={active ? 'true' : 'false'}
>
```

CSS adds a spectral left edge for open groups and limits breathing animation to the active dot and edge.

- [ ] **Step 6: Verify**

```bash
npm test -- --run src/test/core.test.ts src/test/sidebar.test.ts
npm run build
```

Regenerate the release manifest before build if fencing reports staleness.

- [ ] **Step 7: Commit interaction hierarchy**

```bash
git add frontend/web argus_skill/release_manifest.json frontend/core/src/release.generated.ts
git commit -m "feat(web): add branded active-state hierarchy"
```

---

### Task 4: Visual, Performance, Review, and Deployment

**Files:**
- Modify only files that fail verification.

- [ ] **Step 1: Run full checks**

```bash
cd frontend/web
npm test
npm run build
```

- [ ] **Step 2: Compare bundle size**

```bash
find dist/assets -type f -printf '%f %s\n' | sort
```

Confirm GSAP remains in `motion-*` and the main gzip increase is below 5 KB.

- [ ] **Step 3: Verify real states**

Against a release-matched temporary API, capture:

- 1440×900 light and dark real session.
- 390×844 mobile.
- Sidebar collapsed/expanded.
- Preview collapsed/expanded.
- Mission and Activity.
- Expanded active role.
- Modal and Artifact/PDF.
- Reduced motion.

Assert no horizontal overflow and no release compatibility banner.

- [ ] **Step 4: Review**

Run a read-only code review over the complete diff. Fix all critical and important findings, then rerun full checks.

- [ ] **Step 5: Rebase latest remote**

```bash
git fetch origin --prune
git rebase origin/main
python scripts/generate_release_manifest.py
cd frontend/web && npm test && npm run build
```

- [ ] **Step 6: Publish**

Push to `origin/main`, restart only 8798, keep the 8799 proxy running, and verify:

- `/api/meta` release matches source.
- No incompatibility banner.
- New Rounded 02 logo and glass depth are present.
- Light/dark/mobile screenshots match the approved design.
