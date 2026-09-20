import { createContext, useContext, useSyncExternalStore } from "react";
import { useStore } from "@xyflow/react";

/** The zoom that type and card chrome are sized against.
 *
 * Text held at a constant size on screen has to be sized against the zoom, and
 * a size that follows the zoom continuously re-wraps every card on every frame
 * of a zoom: the whole map is laid out sixty times a second and the gesture
 * stutters. Sized against a stepped zoom, the map scales as one picture inside
 * a step, which costs the compositor a transform and nothing else, and text is
 * re-set only on crossing into another step.
 *
 * Steps are a quarter of an octave (about 19%). The step is the one at or
 * below the zoom, so resting text is never smaller than the size it was given,
 * only up to a fifth larger just before the next step. */
export const ZOOM_STEPS_PER_OCTAVE = 4;

export function zoomStep(zoom: number): number {
  if (!(zoom > 0) || !Number.isFinite(zoom)) return zoom;
  // The epsilon keeps a zoom that is exactly on a step (a fitted 0.25, 0.5, 1)
  // from falling to the step below through floating-point error.
  const step = Math.floor(Math.log2(zoom) * ZOOM_STEPS_PER_OCTAVE + 1e-9) / ZOOM_STEPS_PER_OCTAVE;
  return Math.round(2 ** step * 1e4) / 1e4;
}

/** The step to size against while the map is moving.
 *
 * Re-setting every card is one long frame, and a gesture that crosses several
 * steps would stumble at each. So a moving map keeps the step it had: it is a
 * picture being scaled, as smooth as the compositor can make it. It gives the
 * step up only once the picture has drifted far enough to look wrong, two
 * steps too small or three too large, and the camera settles it on the exact
 * step when the movement rests. */
export function heldZoomStep(held: number | null, zoom: number): number {
  const exact = zoomStep(zoom);
  if (held == null || !(held > 0)) return exact;
  const drift = Math.log2(zoom / held) * ZOOM_STEPS_PER_OCTAVE;
  return drift < -2 || drift >= 3 ? exact : held;
}

export type ZoomStepStore = {
  get(): number | null;
  set(step: number): void;
  subscribe(listener: () => void): () => void;
};

export function createZoomStepStore(): ZoomStepStore {
  let step: number | null = null;
  const listeners = new Set<() => void>();
  return {
    get: () => step,
    set(next) {
      if (next === step) return;
      step = next;
      listeners.forEach((listener) => listener());
    },
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
  };
}

/** The step the camera is holding. Absent (a card or an edge rendered without
 * the camera), the step is read from the flow's own zoom. */
export const ZoomStepContext = createContext<ZoomStepStore | null>(null);

const never = () => () => {};

/** Something derived from the held step, re-rendering only when it changes.
 * `select` must return a primitive. */
export function useSteppedZoom<T extends string | number>(select: (step: number) => T): T {
  const store = useContext(ZoomStepContext);
  const read = () => {
    const step = store?.get();
    return step == null ? null : select(step);
  };
  // The same reading serves a server render (the cards are rendered to a
  // string in tests and exports): the store is plain memory, not the DOM.
  const held = useSyncExternalStore(store ? store.subscribe : never, read, read);
  const live = useStore((state) => (held === null ? select(zoomStep(state.transform[2])) : null));
  return (held ?? live) as T;
}
