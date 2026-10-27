import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// The overview card sits centred in a fixed frame (MacroTaskNode
// `--summary-height`), so unbounded title or summary text is clipped
// mid-glyph at both edges and the body runs into the footer. Titles and
// summaries must clamp by line count instead (stable web trial, Q&A 02/05/06,
// 2026-09-16).
describe("atlas overview card text is line-clamped", () => {
  const atlas = readFileSync(new URL("../map/atlas.css", import.meta.url), "utf8");
  const design = readFileSync(new URL("../map/design.css", import.meta.url), "utf8");
  const rule = (selector: string, css = atlas) => {
    const match = css.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`));
    return match ? match[1] : "";
  };
  it("clamps the title to two lines and wraps long tokens", () => {
    const title = rule(".macro-summary .map-card h3");
    expect(title).toMatch(/-webkit-line-clamp:\s*2/);
    expect(title).toMatch(/overflow:\s*hidden/);
    expect(title).toMatch(/overflow-wrap:\s*anywhere/);
  });
  it("clamps the summary to three lines", () => {
    const copy = rule(".macro-summary .map-card-copy");
    expect(copy).toMatch(/-webkit-line-clamp:\s*3/);
    expect(copy).toMatch(/overflow:\s*hidden/);
  });
  it("keeps the final title layout fixed and never squeezes its two lines", () => {
    const title = rule(".argus-map .macro-summary .map-card h3", design);
    expect(title).toMatch(/-webkit-line-clamp:\s*2/);
    expect(title).toMatch(/font-size:\s*16px/);
    expect(title).toMatch(/flex-shrink:\s*0/);
    expect(rule(".argus-map .macro-summary .map-card-copy", design)).toMatch(/mask-image/);
  });
  it("keeps the quoted-ask line at two lines despite the generic paragraph rule", () => {
    const objective = rule(".macro-summary .map-card > p.map-card-objective");
    expect(objective).toMatch(/-webkit-line-clamp:\s*2/);
    expect(objective).toMatch(/min-height:\s*0/);
  });
  it("keeps the fixed-height overview card from bleeding", () => {
    expect(rule(".argus-map .macro-summary .map-card", design)).toMatch(/overflow:\s*hidden/);
  });
  it.each(["map", "atlas", "design", "alive"])("has no zoom-dependent text rules in %s.css", (name) => {
    const css = readFileSync(new URL(`../map/${name}.css`, import.meta.url), "utf8");
    expect(css).not.toMatch(/data-overview-density|data-copy-lines|data-course|--overview-type|--title-lines|--copy-lines/);
  });
  it("lets only the far view follow the zoom: the title and state grow and the rest fades, from CSS alone", () => {
    // The person asked for a readable title at overview zoom (2026-10-26).
    // It is one rule set on --z and --summary-scale; the markup stays the
    // same at every zoom (macroCardCopy.test.tsx).
    const scope = rule(".argus-map .macro-summary", design);
    expect(scope).toMatch(/--on-screen:\s*calc\(var\(--z/);
    expect(design).toMatch(/font-size:\s*max\(19px,\s*min\(56px,\s*calc\(15px \/ var\(--on-screen\)\),/);
    // Three whole lines always fit the card's height, so the clamp ends on an
    // ellipsis and never cuts through glyphs (2026-10-26).
    expect(design).toMatch(/calc\(\(var\(--summary-height, 300px\) - 55px - 1\.5 \* var\(--status-size\)\) \/ 4\.2\)\)\)/);
    // A failed card, or one waiting on the person, says so from afar: the
    // state dot and word stay and grow like the title; only the task number
    // fades with the rest (overview zoom made every card read as done).
    expect(design).toMatch(/\.map-card > :not\(h3\):not\(\.map-card-top\),\s*\.argus-map \.macro-summary \.map-card \.map-card-number \{ opacity: var\(--near\); \}/);
    expect(scope).toMatch(/--status-size:\s*clamp\(11\.5px, calc\(11px \/ var\(--on-screen\)\)/);
    expect(design).toMatch(/\.macro-summary \.map-status \{[^}]*font-size: var\(--status-size/);
  });
});
