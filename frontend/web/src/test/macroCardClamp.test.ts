import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

// The overview card sits centred in a fixed frame (MacroTaskNode
// `--summary-height`), so unbounded title or summary text is clipped
// mid-glyph at both edges and the body runs into the footer. Titles and
// summaries must clamp by line count instead (stable web trial, Q&A 02/05/06,
// 2026-09-16).
describe("atlas overview card text is line-clamped", () => {
  const atlas = readFileSync(new URL("../map/atlas.css", import.meta.url), "utf8");
  const rule = (selector: string) => {
    const match = atlas.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\s*\\{([^}]*)\\}`));
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
  it("never squeezes the compact title below two full lines", () => {
    const map = readFileSync(new URL("../map/map.css", import.meta.url), "utf8");
    const compact = map.match(/\.map-macro\[data-overview-density="compact"\] \.map-card h3 \{([^}]*)\}/)?.[1] ?? "";
    expect(compact).toMatch(/-webkit-line-clamp:\s*2/);
    expect(rule(".map-macro:not([data-overview-density=full]) .map-card h3")).toMatch(/flex-shrink:\s*0/);
    expect(rule(".map-macro[data-overview-density=compact] .map-card-copy")).toMatch(/mask-image/);
  });
  it("keeps the quoted-ask line at two lines despite the generic paragraph rule", () => {
    const objective = rule(".macro-summary .map-card > p.map-card-objective");
    expect(objective).toMatch(/-webkit-line-clamp:\s*2/);
    expect(objective).toMatch(/min-height:\s*0/);
  });
  it("keeps the fixed-height overview card from bleeding", () => {
    expect(rule(".map-macro:not([data-overview-density=full]) .map-card")).toMatch(/overflow:\s*hidden/);
  });
});
