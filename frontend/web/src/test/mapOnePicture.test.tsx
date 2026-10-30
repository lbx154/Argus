import { renderToStaticMarkup } from "react-dom/server";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import { MacroTaskNode, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { MAP_CARD_LEGEND } from "../map/MapPanel";
import { plainTitle, type MapTask } from "../map/model";
import { layoutSubmap } from "../map/submap";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const task: MapTask = {
  id: "recheck", title: "完成必经步骤① hc-35885033b637", status: "running", deps: [], ts: 5,
};
const card = (patch: Partial<MacroData> = {}): NodeProps<MacroNode> => ({
  id: "recheck-card", type: "task", zIndex: 0, draggable: false, selected: false, dragging: false, selectable: true, deletable: false, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0,
  data: {
    id: "recheck-card", task, ordinal: 2, part: 1, partCount: 1, start: 1, end: 0, totalSteps: 0,
    layout: layoutSubmap(task, [], true, []), frame: { width: 1440, height: 900, scale: 1 },
    canvasSize: { width: 1440, height: 960 }, zh: true, focused: false, detailed: false, open: vi.fn(), readStep: vi.fn(),
    ...patch,
  },
}) as NodeProps<MacroNode>;

describe("one picture of the work", () => {

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
