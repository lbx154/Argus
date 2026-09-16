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
  it("keeps the fixed-height overview card from bleeding", () => {
    expect(rule(".map-macro:not([data-overview-density=full]) .map-card")).toMatch(/overflow:\s*hidden/);
  });
});
