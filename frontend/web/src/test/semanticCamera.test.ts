import { describe, expect, it } from "vitest";
import {
  FOCUS_FILL,
  FOCUS_FILL_MAX,
  READER_HEADROOM,
  focusZoom,
  motionDuration,
  overviewViewport,
  readerViewport,
} from "../map/useSemanticCamera";

// Geometry measured from the live 1600x950 cockpit: the chrome-reduced viewing
// area (toolbar above, composer dock below) and a long-task card frame.
const canvas = { width: 1600, height: 950 };
const card = { width: 1440, height: 1000 };

describe("focusZoom", () => {
  it("fills FOCUS_FILL of the canvas short side when the area is squeezed", () => {
    // Tall composer: the contain-fit alone left the card at ~40% of the width.
    const area = { width: 1500, height: 541 };
    const zoom = focusZoom(canvas, area, card);
    const fit = Math.min(area.width / card.width, area.height / card.height);
    expect(zoom).toBeCloseTo((FOCUS_FILL * 950) / 1000, 6);
    expect(zoom).toBeGreaterThan(fit);
    expect(
      (Math.min(card.width, card.height) * zoom) /
        Math.min(canvas.width, canvas.height),
    ).toBeCloseTo(FOCUS_FILL, 6);
  });

  it("keeps the plain contain-fit when it is already at least as readable", () => {
    const area = { width: 1500, height: 760 };
    const wide = { width: 1440, height: 700 };
    const fit = Math.min(area.width / wide.width, area.height / wide.height);
    expect(focusZoom(canvas, area, wide)).toBeCloseTo(fit, 6);
  });

  it("never exceeds a FOCUS_FILL_MAX fill of the canvas short side", () => {
    // Shallow window with thin chrome: the contain-fit alone would fill 87.5%.
    const shallow = { width: 3000, height: 800 };
    const area = { width: 2900, height: 700 };
    const wide = { width: 1440, height: 700 };
    const zoom = focusZoom(shallow, area, wide);
    expect(zoom).toBeCloseTo((FOCUS_FILL_MAX * 800) / 700, 6);
    expect(zoom).toBeLessThan(
      Math.min(area.width / wide.width, area.height / wide.height),
    );
  });

  it("never drops below the contain-fit within the fill cap", () => {
    const screens = [
      { canvas: { width: 360, height: 640 }, area: { width: 320, height: 455 } },
      { canvas: { width: 1600, height: 950 }, area: { width: 1500, height: 541 } },
      { canvas: { width: 2560, height: 1400 }, area: { width: 2460, height: 1150 } },
    ];
    for (const s of screens) {
      const fit = Math.min(s.area.width / card.width, s.area.height / card.height);
      const cap =
        (FOCUS_FILL_MAX * Math.min(s.canvas.width, s.canvas.height)) /
        Math.min(card.width, card.height);
      expect(focusZoom(s.canvas, s.area, card)).toBeGreaterThanOrEqual(
        Math.min(fit, cap) - 1e-9,
      );
    }
  });
});

describe("overviewViewport", () => {
  const area = { x: 50, y: 85, width: 1500, height: 665 };

  it("centers the graph on the visible canvas, not on the chrome-reduced band", () => {
    const bounds = { x: 0, y: 0, width: 4000, height: 1500 };
    const view = overviewViewport(canvas, area, bounds);
    expect(view.zoom).toBe(0.27);
    // Content center matches the canvas center on both axes: no bottom band.
    expect(view.x + (bounds.x + bounds.width / 2) * view.zoom).toBeCloseTo(
      canvas.width / 2,
      6,
    );
    expect(view.y + (bounds.y + bounds.height / 2) * view.zoom).toBeCloseTo(
      canvas.height / 2,
      6,
    );
  });

  it("slides back inside the unobstructed area when the graph is tall", () => {
    const bounds = { x: 0, y: 0, width: 3000, height: 2200 };
    const view = overviewViewport(canvas, area, bounds);
    const top = view.y + bounds.y * view.zoom;
    const bottom = top + bounds.height * view.zoom;
    expect(top).toBeGreaterThanOrEqual(area.y);
    expect(bottom).toBeLessThanOrEqual(area.y + area.height + 1e-6);
  });

  it("keeps the padded fit-to-area zoom for large graphs", () => {
    const bounds = { x: -200, y: 100, width: 5200, height: 6000 };
    const view = overviewViewport(canvas, area, bounds);
    expect(view.zoom).toBeCloseTo(
      Math.min(
        area.width / (bounds.width + 160),
        area.height / (bounds.height + 160),
      ),
      6,
    );
  });
});

describe("readerViewport", () => {
  const area = { x: 50, y: 85, width: 1500, height: 665 };
  const origin = { x: 1000, y: 2000 };

  it("keeps READER_HEADROOM of card context above a tall reader rect", () => {
    const rect = { x: 100, y: 50, width: 800, height: 700, scale: 1 };
    const view = readerViewport(area, origin, rect);
    const top = view.y + (origin.y + rect.y * rect.scale) * view.zoom;
    expect(top).toBeCloseTo(area.y + READER_HEADROOM, 6);
    // The rect itself still ends above the composer band.
    expect(top + rect.height * rect.scale * view.zoom).toBeLessThanOrEqual(
      area.y + area.height + 1e-6,
    );
  });

  it("keeps the 1.05 readable zoom cap for small rects, headroom intact", () => {
    const rect = { x: 40, y: 400, width: 300, height: 200, scale: 1.2 };
    const view = readerViewport(area, origin, rect);
    expect(view.zoom).toBeCloseTo(1.05 / 1.2, 6);
    const top = view.y + (origin.y + rect.y * rect.scale) * view.zoom;
    expect(top).toBeGreaterThanOrEqual(area.y + READER_HEADROOM - 1e-6);
  });

  it("scales headroom down on very short areas instead of starving the reader", () => {
    const short = { x: 20, y: 60, width: 320, height: 100 };
    const rect = { x: 0, y: 0, width: 300, height: 300, scale: 1 };
    const view = readerViewport(short, origin, rect);
    expect(view.zoom).toBeCloseTo((100 - 100 * 0.35) / 300, 6);
  });
});

describe("motionDuration", () => {
  it("zeroes every camera move under prefers-reduced-motion", () => {
    for (const ms of [180, 320, 340, 380]) {
      expect(motionDuration(true, ms)).toBe(0);
      expect(motionDuration(false, ms)).toBe(ms);
    }
  });
});
