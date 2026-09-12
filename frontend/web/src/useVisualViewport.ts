import { useEffect, useState } from 'react';

export function workspaceViewport(width: number, height: number, viewport?: Pick<VisualViewport, 'height' | 'offsetTop'> | null) {
  const visibleHeight = viewport?.height ?? height;
  const covered = height - visibleHeight - (viewport?.offsetTop ?? 0);
  return {
    keyboardInset: covered > 24 ? Math.round(covered) : 0,
    compact: width < 1024 && visibleHeight <= 600,
  };
}

/** Publish how much of the layout viewport the software keyboard covers.
 *
 * iOS Safari does not shrink the layout viewport when the keyboard opens — it
 * scrolls the page instead — so a composer pinned to the bottom of a `100dvh`
 * shell ends up behind the keyboard. `visualViewport` is the only thing that
 * reports the real visible area, so the overlap is measured from it and
 * written to the `--keyboard-inset` custom property, which `.keyboard-aware`
 * consumes.
 *
 * Short visible viewports also use compact reading/composer chrome. Browsers
 * without this API use the window height and have no keyboard inset. */
export function useVisualViewport(): boolean {
  const [compact, setCompact] = useState(() => typeof window !== 'undefined'
    && workspaceViewport(window.innerWidth, window.innerHeight, window.visualViewport).compact);
  useEffect(() => {
    const viewport = window.visualViewport;
    const root = document.documentElement;

    let frame: number | null = null;

    const sync = () => {
      if (frame != null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        // Distance between the bottom of the visible area and the bottom of
        // the layout viewport. `offsetTop` covers the case where the page has
        // been scrolled up to keep the focused field in view.
        const state = workspaceViewport(window.innerWidth, window.innerHeight, viewport);
        root.style.setProperty('--keyboard-inset', `${state.keyboardInset}px`);
        root.dataset.compactViewport = String(state.compact);
        setCompact(state.compact);
      });
    };

    sync();
    viewport?.addEventListener('resize', sync);
    viewport?.addEventListener('scroll', sync);
    window.addEventListener('resize', sync);
    return () => {
      if (frame != null) window.cancelAnimationFrame(frame);
      viewport?.removeEventListener('resize', sync);
      viewport?.removeEventListener('scroll', sync);
      window.removeEventListener('resize', sync);
      root.style.removeProperty('--keyboard-inset');
      delete root.dataset.compactViewport;
    };
  }, []);
  return compact;
}
