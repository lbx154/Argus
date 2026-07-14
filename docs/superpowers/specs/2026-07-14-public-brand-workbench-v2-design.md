# Public Brand Workbench V2 Design

## Goal

Make the Argus Web workbench visibly and immediately recognizable as the same product as `argusbot.cn`, while preserving the existing three-column information architecture, dense research workflow, accessibility, and long-session performance.

## Why V2

The first brand pass added shared spectral tokens, glass classes, bounded magnetic controls, and branded modals. In the deployed UI these effects are too restrained:

- Ambient light is only 8–10% opacity.
- Most panels remain visually close to flat white or flat charcoal.
- The workbench still uses the retired black Night Pupil lockup.
- Active sessions, tabs, and role rows retain old dashboard styling.
- Only shared primitive buttons clearly expose the public-site spectral treatment.

V2 strengthens perception, not decoration count.

## Brand Identity

- Replace the old lockup and mark with the selected Rounded 02 geometry.
- Use one continuous user-space blue-to-gold gradient across the horizontal logo.
- Use the Rounded mark on compact/collapsed surfaces and connection states.
- Preserve Geist, Noto Sans SC, and Geist Mono typography.

## Ambient Canvas

The root canvas uses three fixed light domains:

- Blue in the upper-left.
- Violet through the center.
- Gold in the lower-right.

Light theme uses 16–22% spectral opacity over an off-white canvas. Dark theme uses 18–26% over deep navy.

One optional 60-second compositor-only ambient drift animates opacity and background position. It pauses when the document is hidden. Reduced motion disables it.

## Glass Depth System

Define three surface depths:

1. `glass-panel--side`: translucent side rails with stronger blur and subtle color transmission.
2. `glass-panel--main`: the clearest reading surface with an opaque-enough text substrate.
3. `glass-panel--raised`: modal, composer, selected session, and artifact controls.

Each depth includes:

- A one-pixel cool-to-warm spectral edge.
- A top inner highlight.
- A low, wide shadow.
- Theme-specific opacity.

Streaming text, Markdown, PDF, and code areas retain stable solid substrates.

## Interactive Hierarchy

### Sessions

- Active session receives a continuous blue-violet-gold edge and a quiet inner glow.
- Hover reveals a localized edge highlight, not a filled rainbow background.
- Running status remains semantic and readable without motion.

### Workspace Tabs

- Mission and Activity share one sliding spectral indicator.
- The active label remains high contrast.
- Switching takes 180–220 ms and never reanimates the event list.

### Roles

- Expanded role rows use a spectral left edge plus the semantic role color.
- Active role status may breathe only on its dot/edge.
- Role body text never pulses or fades continuously.

### Buttons

- Primary controls: white core, spectral border, dark text.
- Ghost/icon controls: translucent glass core, edge highlight on hover/focus.
- Danger controls: semantic red; no decorative spectral fill.
- Fine pointers may use bounded magnetic motion up to five pixels.
- Touch uses brief press feedback only.

## Motion

- Side panel open/close: 220–280 ms, translate/opacity with slight depth.
- Mission/Activity indicator: 180–220 ms.
- Session content replacement: 160 ms fade-out and 240 ms fade-in at the shell level.
- Modal and artifact transitions: existing lazy GSAP path, 220–320 ms.
- Ambient canvas: optional 60-second compositor-only loop.
- No per-event, per-log-line, Markdown, PDF, or code animation.
- Reduced motion converts every decorative transition into an immediate state change.

## Component Boundaries

- `Wordmark.tsx`: Rounded 02 horizontal and mark geometry.
- `index.css`: tokens, glass depths, ambient canvas, spectral edges, tab indicator, and motion keyframes.
- `motion.ts`: bounded shared durations and visibility-aware ambient lifecycle.
- `Sidebar.tsx`: side glass and active-session treatment.
- `TopBar.tsx`: raised control layer.
- `App.tsx`: main glass depth and workspace tab indicator.
- `RolesPanel.tsx` / event role groups: active/expanded edge treatment.
- `ResearchCanvas.tsx`: side glass with stable artifact substrate.
- `Modal.tsx` / `ChatBox.tsx`: raised glass depth.

Do not change backend APIs, session behavior, panel geometry, or event semantics.

## Performance and Accessibility

- No new runtime dependency.
- GSAP remains lazy and in a separate chunk.
- Main bundle growth must remain below 5 KB gzip.
- No layout reads in pointermove handlers.
- WCAG AA text contrast in both themes.
- Focus remains visible on every control.
- Mobile does not require hover.
- No horizontal overflow at 320, 390, 768, or 1440 px.

## Verification

- Existing frontend test suite passes.
- Production build and release manifest match.
- Real sessions are checked in light and dark themes.
- Validate desktop, tablet, mobile, reduced motion, long logs, modal, artifact/PDF, side-panel collapse, and workspace tabs.
- Confirm no long tasks above 50 ms during steady scrolling.
- Confirm 8799 serves the new release with no compatibility banner.
