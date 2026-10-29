import { renderToStaticMarkup } from "react-dom/server";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import { MacroTaskNode, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { MAP_CARD_LEGEND } from "../map/MapPanel";
import { plainTitle, replacedNote, requestChip, type MapTask } from "../map/model";
import { layoutSubmap } from "../map/submap";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const task: MapTask = {
  id: "recheck", title: "完成必经步骤① hc-35885033b637", objective: "", status: "running", deps: [], ts: 5,
  requests: [
    { id: "turn:web-1", text: "试试 A", full: "试试 A", ts: 1 },
    { id: "turn:web-2", text: "更正：换成 B", full: "更正：换成 B，并且先给我看结果", ts: 3 },
  ],
  replaced: [{ id: "check", title: "方案 A", reason: "太慢", ts: 2 }],
  replaced_count: 1,
};
const card = (patch: Partial<MacroData> = {}): NodeProps<MacroNode> => ({
  id: "recheck-card",
  data: {
    id: "recheck-card", task, ordinal: 2, part: 1, partCount: 1, start: 1, end: 0, totalSteps: 0,
    layout: layoutSubmap(task, [], true, []), frame: { width: 1440, height: 900, scale: 1 },
    canvasSize: { width: 1440, height: 960 }, zh: true, focused: false, detailed: false,
    source: "live:project", readOnly: false, open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn(),
    ...patch,
  },
}) as NodeProps<MacroNode>;

describe("one picture of the work", () => {
  it("puts what the person said on the task as a chip, with every request in full on hover", () => {
    const chip = requestChip(task.requests, true)!;
    expect(chip).toEqual({ label: "你说：更正：换成 B", more: 1, title: "你说：试试 A\n\n你说：更正：换成 B，并且先给我看结果" });
    expect(requestChip([], true)).toBeNull();
    const markup = renderToStaticMarkup(<MacroTaskNode {...card()} />);
    expect(markup).toContain('data-testid="map-request-chip"');
    expect(markup).toContain("你说：更正：换成 B");
    expect(markup).toContain("+1");
  });

  it("folds a replaced plan into its replacement: one card, how many, and each one's title and reason", () => {
    expect(replacedNote(task, true)).toEqual({ label: "替代了 1 个旧方案", lines: ["方案 A：太慢"] });
    expect(replacedNote({ replaced_count: 0 }, true)).toBeNull();
    const markup = renderToStaticMarkup(<MacroTaskNode {...card()} />);
    expect(markup).toContain("替代了 1 个旧方案");
    expect(markup).toContain('title="替代了 1 个旧方案\n方案 A：太慢"');
    const opened = renderToStaticMarkup(<MacroTaskNode {...card({ focused: true, detailed: true })} />);
    expect(opened).toContain('data-testid="macro-replaced"');
    expect(opened).toContain("<li>方案 A：太慢</li>");
  });

  it("never shows a canvas card id in a title", () => {
    expect(plainTitle("完成必经步骤① hc-35885033b637")).toBe("完成必经步骤①");
    expect(plainTitle("清理hc-deadbeef0000留下的")).toBe("清理留下的");
    expect(plainTitle("thc-123456 stays")).toBe("thc-123456 stays");
    const markup = renderToStaticMarkup(<MacroTaskNode {...card()} />);
    expect(markup).not.toContain("hc-35885033b637");
  });

  it("explains how the cards are split in the map's key", () => {
    expect(MAP_CARD_LEGEND.map(([name, text]) => `${name[0]} = ${text}`)).toEqual([
      "任务卡 = 团队拆出来、能单独完成和验收的一件事",
    ]);
  });
});
