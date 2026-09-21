import { describe, expect, it } from "vitest";
import { createZoomStepStore, heldZoomStep, zoomStep } from "../map/zoomStep";

describe("the zoom that type is sized against", () => {
  it("steps by a quarter octave and never rounds up past the zoom", () => {
    expect(zoomStep(1)).toBe(1);
    expect(zoomStep(0.5)).toBe(0.5);
    expect(zoomStep(0.25)).toBe(0.25);
    expect(zoomStep(0.24)).toBeCloseTo(2 ** -2.25, 4);
    for (const zoom of [0.07, 0.24, 0.31, 0.5, 0.77, 1.3, 2]) {
      const step = zoomStep(zoom);
      expect(step).toBeLessThanOrEqual(zoom + 1e-9);
      expect(zoom / step).toBeLessThan(2 ** 0.25 + 1e-3);
    }
  });

  it("is the same for every zoom inside a step, so a gesture inside one re-sets nothing", () => {
    const inside = [0.5, 0.52, 0.55, 0.58, 0.59];
    expect(new Set(inside.map(zoomStep)).size).toBe(1);
    expect(zoomStep(0.6)).not.toBe(zoomStep(0.59));
  });

  it("leaves a degenerate zoom alone", () => {
    expect(zoomStep(0)).toBe(0);
    expect(Number.isNaN(zoomStep(Number.NaN))).toBe(true);
  });
});

describe("the step a moving map holds", () => {
  it("takes the exact step when nothing is held yet", () => {
    expect(heldZoomStep(null, 0.3)).toBe(zoomStep(0.3));
  });

  it("keeps its step through a gesture of ordinary length", () => {
    const held = zoomStep(0.25);
    for (const zoom of [0.18, 0.2, 0.25, 0.3, 0.36, 0.41]) expect(heldZoomStep(held, zoom)).toBe(held);
  });

  it("gives the step up once the picture has drifted too far to look right", () => {
    const held = zoomStep(0.25);
    expect(heldZoomStep(held, 0.45)).toBe(zoomStep(0.45));
    expect(heldZoomStep(held, 0.16)).toBe(zoomStep(0.16));
  });
});

describe("the held step's store", () => {
  it("tells its listeners only when the step changes", () => {
    const store = createZoomStepStore();
    let calls = 0;
    const stop = store.subscribe(() => calls++);
    expect(store.get()).toBeNull();
    store.set(0.25);
    store.set(0.25);
    store.set(0.5);
    expect(calls).toBe(2);
    expect(store.get()).toBe(0.5);
    stop();
    store.set(1);
    expect(calls).toBe(2);
  });
});
