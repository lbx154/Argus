# Public Brand Workbench Design

## Goal

Unify the Argus Web workbench with the public site's visual language while preserving the existing three-column layout, information density, functionality, accessibility, and measured performance.

## Visual System

The workbench will use a density-adjusted version of the public brand:

- Map the public blue, gold, violet, rose, paper, and ink colors into the existing Tailwind CSS variables.
- Retain Geist Variable for Latin UI text, Noto Sans SC Variable for Chinese UI text, and Geist Mono Variable for code.
- Add restrained blue-gold ambient light to the application background.
- Render panels as translucent paper or deep-blue glass with crisp low-contrast borders.
- Use localized spectral highlights rather than full rainbow fills.
- Keep role colors distinguishable but close in chroma so event logs remain calm.

The layout geometry, panel widths, resizing behavior, and content hierarchy remain unchanged.

## Components

### Primitives

`frontend/web/src/components/primitives.tsx` remains the shared source for buttons, chips, status indicators, panel headers, spinners, and empty hints.

- Primary buttons use a white core with a thin spectral border and soft outer glow.
- Ghost buttons use quiet translucent surfaces and reveal spectral edge light on hover or focus.
- Danger buttons retain semantic red and never use decorative rainbow treatment.
- All buttons share the same focus, hover, press, disabled, and reduced-motion behavior.

### Panels and Cards

Sidebar, activity, preview, cards, role panels, composer, and artifact surfaces use the same glass surface tokens. Hover light is limited to interactive items. Event rows and long log surfaces remain visually stable and do not animate continuously.

### Modals

Modal backdrops use tinted blur rather than opaque black. Dialogs use the raised glass surface, spectral top-edge highlight, and the existing focus trap and Escape behavior.

### Empty and Connection States

The landing and backend handshake may use stronger public-brand animation and a restrained Argus word-field motif. This decoration must not run behind active high-density work.

## Motion

Continue using the existing lazy `useGsapMotion` helper. Do not add another animation runtime.

- Modal entry: 220–320 ms fade, translate, and slight scale.
- Message and panel state changes: 180–260 ms.
- Artifact/PDF transitions: 220–320 ms.
- Buttons: desktop magnetic displacement under 6 px; mobile press feedback under 180 ms.
- Ambient background motion must be compositor-only and paused when the document is hidden.
- No per-event entrance animation for streaming logs.
- No global smooth scrolling; only explicit “jump to latest” behavior may remain smooth.
- `prefers-reduced-motion` removes decorative movement while retaining immediate state changes.

## Accessibility and Performance

- Preserve keyboard navigation, focus restoration, focus trapping, and current ARIA semantics.
- Keep text contrast at WCAG AA or better in both themes.
- Do not encode role or status using color alone.
- Do not increase initial JavaScript by eagerly loading GSAP.
- Maintain the current fast first paint and avoid layout animation in virtualized or streaming surfaces.
- Mobile interactions must work without hover or cursor input.

## Verification

- Existing frontend tests remain green.
- Add focused tests for primitive variants, theme persistence, reduced-motion behavior, and modal semantics.
- Build the production bundle and compare chunk sizes.
- Exercise light, dark, mobile, reduced-motion, long-log, modal, artifact, and disconnected states in Playwright.
- Confirm no horizontal overflow at 320, 390, 768, and 1440 px.
- Confirm event streaming and scrolling remain responsive with a large real session.

## Scope Exclusions

- No backend or API changes.
- No change to the three-column architecture.
- No public-site hero animation inside active workbench views.
- No new animation dependency.
- No redesign of task, daemon, session, or artifact behavior.
