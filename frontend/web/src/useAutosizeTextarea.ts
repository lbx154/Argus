import { useCallback, useLayoutEffect, useRef, type RefObject } from 'react';

/**
 * Grow and shrink a textarea with its content, between `min` and `max` pixels.
 *
 * Measured before paint on every value change, and again on the DOM's own
 * input event (an IME commit, undo or autofill can reach the element before
 * React state does), on width changes and when the textarea becomes visible.
 * The scrollbar appears only when the content really exceeds the box, so a
 * stale scrollbar can never keep the box tall after text is deleted.
 */
export function useAutosizeTextarea(
  ref: RefObject<HTMLTextAreaElement>,
  value: string,
  min: number,
  max: number,
  onHeight?: (height: number) => void,
) {
  const report = useRef(onHeight);
  report.current = onHeight;
  const latest = useRef<() => void>();
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const resize = () => {
      if (!element.getClientRects().length) return;
      element.style.overflowY = 'hidden';
      element.style.height = '0px';
      const height = Math.min(max, Math.max(min, element.scrollHeight));
      element.style.height = `${height}px`;
      // A CSS max-height may clamp below `max`; scroll only when content is cut off.
      element.style.overflowY = element.scrollHeight > element.clientHeight + 1 ? 'auto' : 'hidden';
      report.current?.(height);
    };
    latest.current = resize;
    resize();
    let width = element.clientWidth;
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
      if (element.clientWidth === width) return;
      width = element.clientWidth;
      resize();
    });
    observer?.observe(element);
    element.addEventListener('input', resize);
    window.addEventListener('resize', resize);
    return () => {
      observer?.disconnect();
      element.removeEventListener('input', resize);
      window.removeEventListener('resize', resize);
    };
  }, [ref, value, min, max]);
  /** Measure again now, e.g. after a container animation settles. */
  return useCallback(() => latest.current?.(), []);
}
