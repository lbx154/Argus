---
name: Argus Research Workbench
description: A vigilant live workspace for autonomous research, conversation, and artifacts.
colors:
  primary-indigo: "#4F46E5"
  primary-indigo-dark: "#818CF8"
  canvas-light: "#F9FAFB"
  panel-light: "#FCFCFD"
  line-light: "#D6D8DC"
  ink-light: "#111827"
  muted-light: "#6B7280"
  canvas-dark: "#0D0E12"
  panel-dark: "#14161C"
  raised-dark: "#1A1C23"
  line-dark: "#475264"
  ink-dark: "#F9FAFB"
  muted-dark: "#9CA3AF"
  success: "#15A05A"
  warning: "#C28A2C"
  error: "#C45F5A"
typography:
  badge:
    fontFamily: "Geist Mono Variable, monospace"
    fontSize: "8px"
    fontWeight: 700
    lineHeight: 1
  micro:
    fontFamily: "Geist Mono Variable, monospace"
    fontSize: "9px"
    fontWeight: 400
    lineHeight: 1.25
  caption:
    fontFamily: "Geist Variable, Noto Sans SC Variable, sans-serif"
    fontSize: "10px"
    fontWeight: 500
    lineHeight: 1.35
  metadata:
    fontFamily: "Geist Variable, Noto Sans SC Variable, sans-serif"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.4
  compact:
    fontFamily: "Geist Variable, Noto Sans SC Variable, sans-serif"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.5
  title:
    fontFamily: "Geist Variable, Noto Sans SC Variable, sans-serif"
    fontSize: "14px"
    fontWeight: 600
    lineHeight: 1.4
  body:
    fontFamily: "Geist Variable, Noto Sans SC Variable, sans-serif"
    fontSize: "15px"
    fontWeight: 400
    lineHeight: 1.625
  label:
    fontFamily: "Geist Variable, Noto Sans SC Variable, sans-serif"
    fontSize: "12px"
    fontWeight: 600
    lineHeight: 1.4
  mono:
    fontFamily: "Geist Mono Variable, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.5
rounded:
  control: "6px"
  panel: "8px"
  composer: "16px"
  message: "18px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary:
    backgroundColor: "{colors.primary-indigo}"
    textColor: "{colors.ink-dark}"
    rounded: "{rounded.control}"
    padding: "8px 12px"
  composer:
    backgroundColor: "{colors.panel-light}"
    textColor: "{colors.ink-light}"
    rounded: "{rounded.composer}"
    padding: "12px 16px"
  user-message:
    backgroundColor: "{colors.canvas-light}"
    textColor: "{colors.ink-light}"
    rounded: "{rounded.message}"
    padding: "10px 16px"
---

# Design System: Argus Research Workbench

> **Scope:** this file is the UI visual design system only. Runtime architecture,
> role authority, state machines, and protocol ownership live under
> [`docs/DESIGN_AUTHORITY.md`](docs/DESIGN_AUTHORITY.md) and
> [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Overview

**Creative North Star: "The Vigilant Workbench"**

Argus should feel like a first-party research instrument: composed, precise, and continuously aware. The interface combines the familiarity of a mature conversation product with the density and control of a developer workbench. Conversation is centered and readable; operational detail is progressively disclosed; artifacts remain inspectable without leaving the current task.

The system rejects generic AI dashboards, card mosaics, decorative gradients, weak gray typography, ambiguous iconography, and animation that does not communicate state.

**Key Characteristics:**
- Three stable columns: scoped sessions, conversation/activity, and artifact preview.
- Restrained neutral surfaces with one indigo action color.
- Geist/Noto typography with full CJK coverage and Geist Mono for operational data.
- Clear collapse, resize, loading, error, and pending-question states.
- Safe wrapping and no page-level horizontal overflow.

## Colors

The palette is restrained and state-driven. Indigo marks selection and primary actions; role and semantic colors carry operational meaning only.

### Primary
- **Argus Indigo** (#4F46E5): selection, active borders, focus, primary controls.
- **Argus Indigo Light** (#818CF8): dark-theme active states and emphasis.

### Neutral
- **Light Canvas** (#F9FAFB) and **Light Panel** (#FCFCFD): quiet daylight work surfaces.
- **Dark Canvas** (#0D0E12), **Dark Panel** (#14161C), and **Raised Dark** (#1A1C23): warm charcoal layers rather than blue-black.
- **Light Ink** (#111827) and **Dark Ink** (#F9FAFB): primary text.
- **Muted Light** (#6B7280) and **Muted Dark** (#9CA3AF): secondary metadata only.

**The Sparse Accent Rule.** Indigo is used for action, focus, and selection—not decoration.

## Typography

**Display Font:** Geist Variable with Noto Sans SC Variable fallback

**Body Font:** Geist Variable with Noto Sans SC Variable fallback

**Label/Mono Font:** Geist Mono Variable

**Character:** compact and modern in English, stable and highly legible in Chinese. Product copy uses one sans family across headings, controls, and body; mono is reserved for paths, timestamps, IDs, and code.

### Hierarchy
- **Title** (600, 14px, 1.4): session, panel, and artifact titles.
- **Body** (400, 15px, 1.625): conversation Markdown and primary content, capped near 760px.
- **Compact** (400, 13px, 1.5): dense status and secondary product copy.
- **Label** (600, 12px, 1.4): role names, controls, and short metadata.
- **Metadata** (400–500, 8–11px): badges, timestamps, terse counters, and low-priority diagnostics only.
- **Mono** (400, 12px, 1.5): logs, paths, time, code, and IDs.

**The Reading Width Rule.** Conversation prose stays within 65–75 characters per line. Sizes below 12px are reserved for non-essential metadata and never carry primary actions or state alone.

## Elevation

The shell is flat by default. Depth is communicated with surface tone and translucent 1px separators. Shadows are reserved for floating composer, modal, toast, and detached controls.

### Shadow Vocabulary
- **Composer Lift** (`0 4px 24px rgba(0,0,0,0.18)`): floating message composer.
- **Overlay Lift** (`0 16px 44px rgba(0,0,0,0.34)`): modal and command palette.
- **Control Lift** (`0 2px 8px rgba(0,0,0,0.16)`): detached edge controls only.

**The Flat-by-Default Rule.** Resting work surfaces do not use decorative shadows.

## Components

### Buttons
- **Shape:** compact rounded rectangle (6px), with circular send/stop controls where the action is icon-only.
- **Primary:** indigo with high-contrast text; reserved for creation and submission.
- **Hover / Focus:** 100–150ms color response and visible focus ring.
- **Ghost:** neutral text on transparent background; gains a subtle tonal fill on hover.

### Chips
- **Style:** 1px translucent border, 12px label, no decorative fill at rest.
- **State:** selection uses an indigo tint and never relies on color alone.

### Cards / Containers
- **Corner Style:** 8px only where a bounded object is semantically useful.
- **Background:** panel tokens; columns themselves remain flush rather than card-wrapped.
- **Shadow Strategy:** none at rest.
- **Border:** translucent 1px hairline.
- **Internal Padding:** 12–16px.

### Inputs / Fields
- **Style:** tonal background, 1px border, 6–16px radius according to context.
- **Focus:** indigo border/focus ring.
- **Error / Disabled:** explicit text or icon plus semantic color.

### Navigation
- Expanded Sessions supports Local and All scopes; All groups by full launch path.
- Collapsed Sessions shows the NIGHT PUPIL `a` mark and only exposes the square expand control; expanded Sessions shows the complete `argus` lockup.
- Left and right sidebars collapse and resize independently while the center remains usable.
- Activity always fills the available main column. On desktop, conversation
  content (messages, role logs, system lines, and composer) is centered with
  `width: min(61.8vw, 100% of the main column)`; mobile remains full width.

### Conversation Thread
- User messages are right-aligned neutral bubbles.
- Argus Markdown is open, left-aligned content with a brand marker.
- Manager, Planner, Engineer, and Reviewer logs fold beneath the operator turn that caused them.

## Do's and Don'ts

### Do:
- **Do** keep conversation content within 760px and use 15px body text.
- **Do** preserve a minimum 360px center column when side panels resize.
- **Do** show real Manager phase, elapsed time, and stop-waiting controls during loading.
- **Do** wrap CJK, code, tables, paths, and long names without page-level horizontal scrolling.
- **Do** respect reduced motion and keep state transitions between 100–250ms.

### Don't:
- **Don't** resemble a generic AI SaaS dashboard or nested card grid.
- **Don't** use neon gradients, gradient text, glassmorphism as decoration, or bounce/elastic easing.
- **Don't** hide critical pending, error, daemon, or loading state.
- **Don't** use ambiguous controls, tiny unreadable metadata, or excessive helper copy.
- **Don't** allow text, tables, code, or side panels to overlap or create a page-level horizontal scrollbar.
