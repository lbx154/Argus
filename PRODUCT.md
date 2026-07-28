# Product

> Product/UX contract only. Runtime architecture and state authority are defined
> by [`docs/DESIGN_AUTHORITY.md`](docs/DESIGN_AUTHORITY.md).

## Register

product

## Platform

web

## Users

Argus is used by researchers, engineers, and technical operators supervising long-running autonomous work. They need to understand current progress quickly, inspect intermediate and final artifacts, intervene through conversation, and manage several persistent sessions without losing context.

## Product Purpose

Argus is a live research workbench for an autonomous agent harness. It keeps the Manager conversation, Planner/Engineer/Reviewer execution traces, daemon lifecycle, pending questions, and Manager-selected artifacts in one dependable interface. Success means an operator can answer three questions immediately: what is happening, what has been produced, and where to intervene.

## Positioning

One vigilant workbench connects conversation, autonomous execution, and finished research artifacts without exposing the harness as a black box.

## Brand Personality

Precise, vigilant, authoritative. Argus should feel calm under sustained technical work, confident without being theatrical, and crafted like a first-party developer tool.

## Anti-references

Do not resemble a generic AI SaaS dashboard, nested card grid, neon gradient demo, or template assembled from unrelated panels. Avoid weak typography, ambiguous controls, excessive helper copy, decorative motion, hidden critical state, horizontal overflow, and layouts that only work with perfect short English text.

## Design Principles

1. **Artifact and conversation first.** The operator's current thread and Manager-selected output carry the most visual weight.
2. **Progressive operational detail.** Execution logs remain available inside the conversation that caused them, but collapse when they are not needed.
3. **Earned familiarity.** Navigation, composer, panels, resize handles, and keyboard behavior follow mature developer-tool conventions.
4. **Visible system state.** Loading, active role, daemon state, pending questions, and errors are explicit and recoverable.
5. **Stable at every width.** Side panels may collapse or resize; the center remains usable, text wraps safely, and no page-level horizontal scrolling appears.

## Accessibility & Inclusion

Target WCAG AA contrast for product text and controls. Support keyboard navigation, visible focus, reduced motion, light/dark/system themes, non-color state cues, CJK text, long content, and responsive mobile access.
