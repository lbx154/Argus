import { renderToStaticMarkup } from "react-dom/server";
import type { NodeProps } from "@xyflow/react";
import { describe, expect, it, vi } from "vitest";
import { MacroTaskNode, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { farLabelFont, farLabelLines, HumanCardView, type HumanCardNode } from "../map/CanvasNodes";
import { HUMAN_FRAME, humanFrame, type HumanCard } from "../map/canvas";
import { MAP_CARD_LEGEND } from "../map/MapPanel";
import { plainTitle, replacedNote, requestChip, type MapTask } from "../map/model";
import { layoutSubmap } from "../map/submap";
import { createZoomStepStore, ZoomStepContext } from "../map/zoomStep";

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
    canvasSize: { width: 1440, height: 960 }, zh: true, focused: false, detailed: false, live: true,
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
      "你的卡 = 你加的想法和资料",
      "必经步骤 = 你定的必须经过的路",
    ]);
  });
});

describe("required step cards", () => {
  const words = "写 wordstat/core.py：count_words(text) 返回词频字典（不分大小写、去掉标点），配单元测试";
  const step: HumanCard = { id: "hc-a", kind: "step", text: words, attachment: null, created_ts: 1, updated_ts: 1 };
  const render = (zoom: number) => {
    const store = createZoomStepStore();
    store.set(zoom);
    const props = { id: step.id, data: {
      card: step, zh: true, readOnly: false, commit: vi.fn(), cancel: vi.fn(), openFile: vi.fn(), menu: vi.fn(),
      step: { id: "hc-a", number: 1, text: words, checkpoint: false, after: [], status: "running", task_ids: ["t1", "t2"], question_item_id: "" },
      taskTitles: ["写 core", "补测试"],
    } } as unknown as NodeProps<HumanCardNode>;
    return renderToStaticMarkup(<ZoomStepContext.Provider value={store}><HumanCardView {...props} /></ZoomStepContext.Provider>);
  };

  it("are as wide as idea cards and tall enough for the person's words from afar", () => {
    expect(HUMAN_FRAME.step.width).toBe(HUMAN_FRAME.idea.width);
    expect(HUMAN_FRAME.step.width).toBeGreaterThanOrEqual(1200);
    const frame = humanFrame(step);
    expect(frame.width).toBe(HUMAN_FRAME.idea.width);
    // At an overview zoom (~0.125) the words keep at least three whole lines.
    const font = farLabelFont(0.125, "step");
    expect(font * 0.125).toBeCloseTo(14, 0);
    expect(farLabelLines(font, step)).toBeGreaterThanOrEqual(3);
    expect(farLabelLines(font, { kind: "step", text: "短" })).toBeGreaterThanOrEqual(2);
  });

  it("show the number badge, status dot and word in one header row, the words below", () => {
    const near = render(1);
    const header = near.slice(near.indexOf("<header>"), near.indexOf("</header>"));
    expect(header).toContain('class="canvas-step-badge"');
    expect(header).toContain('class="canvas-step-dot"');
    expect(header).toContain("进行中");
    expect(header).toContain("2 个任务");
    expect(near.indexOf("canvas-card-text")).toBeGreaterThan(near.indexOf("</header>"));
    const far = render(0.125);
    expect(far).toContain('class="canvas-step-far-word"');
    expect(far).toMatch(/--far-lines:\s*[3-4]/);
    expect(far).toContain(words);
  });
});
