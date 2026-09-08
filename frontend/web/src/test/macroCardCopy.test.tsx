import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTaskNode, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { layoutSubmap, type SubmapStep } from "../map/submap";
import type { MapTask } from "../map/model";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) =>
    select({ transform: [0, 0, 1] }),
}));

const task: MapTask = {
  id: "parent",
  title: "Research portfolio",
  objective: "",
  status: "running",
  deps: [],
};
const step = (id: string, patch: Partial<SubmapStep> = {}): SubmapStep => ({
  id,
  kind: "execution",
  title: id,
  detail: `Work for ${id}`,
  status: "done",
  source: "event",
  eventIds: [id],
  ...patch,
});
const propsFor = (
  steps: SubmapStep[],
  patch: Partial<MacroData> = {},
): NodeProps<MacroNode> =>
  ({
    id: "parent-card",
    data: {
      id: "parent-card",
      task,
      ordinal: 1,
      part: 1,
      partCount: 1,
      start: 1,
      end: steps.length,
      totalSteps: steps.length,
      layout: layoutSubmap(task, [], false, steps),
      frame: { width: 900, height: 650, scale: 1 },
      canvasSize: { width: 1440, height: 960 },
      zh: false,
      focused: true,
      detailed: false,
      live: true,
      source: "live:project",
      readOnly: false,
      open: vi.fn(),
      quote: vi.fn(),
      menu: vi.fn(),
      readStep: vi.fn(),
      ...patch,
    },
  }) as NodeProps<MacroNode>;
const copyFor = (summary: string, task_status: string) => ({
  cards: {
    parent: {
      title: "Research portfolio",
      summary,
      detail: "d",
      generated_at: 1,
      task_status,
    },
  },
});

let renderer: ReactTestRenderer | undefined;
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
});

describe("macro card copy", () => {
  it("keeps yesterday's summary when the status drifted and hints at the refresh", () => {
    const markup = renderToStaticMarkup(
      <MacroTaskNode
        {...propsFor([step("s1")], { copy: copyFor("Yesterday we ruled out the leak.", "done") })}
      />,
    );
    expect(markup).toContain("Yesterday we ruled out the leak.");
    expect(markup).not.toContain("Zoom to explore");
    expect(markup).toContain("Summary updating");
  });
  it("shows the plain progress hint when the copy matches the live status", () => {
    const markup = renderToStaticMarkup(
      <MacroTaskNode
        {...propsFor([step("s1")], { copy: copyFor("Fresh summary of the round.", "running") })}
      />,
    );
    expect(markup).toContain("Fresh summary of the round.");
    expect(markup).not.toContain("Summary updating");
    expect(markup).toContain("View progress");
  });
  it("uses the unified fallback for the step card and the reader", () => {
    const props = propsFor([step("empty", { detail: "" })], { detailed: true });
    act(() => {
      renderer = create(<MacroTaskNode {...props} />);
    });
    act(() => renderer!.root.findByProps({ "data-step-id": "empty" }).props.onClick());
    const rendered = JSON.stringify(renderer!.toJSON());
    expect(rendered).toContain("No details available yet.");
    expect(rendered).not.toContain("Details are not available yet");
    expect(rendered).not.toContain("暂无详细记录。");
  });
  it("skips the unified placeholder when choosing a multi-part summary", () => {
    const markup = renderToStaticMarkup(
      <MacroTaskNode
        {...propsFor(
          [
            step("real", { detail: "Real measured progress." }),
            step("empty", { detail: "No details available yet." }),
          ],
          { partCount: 2 },
        )}
      />,
    );
    const body = markup.slice(markup.indexOf("map-card-copy"), markup.indexOf("map-card-stages"));
    expect(body).toContain("Real measured progress.");
    expect(body).not.toContain("No details available yet.");
  });
});

describe("compact density summary", () => {
  const atlas = readFileSync(new URL("../map/atlas.css", import.meta.url), "utf8");
  it("keeps a clamped two-line summary visible at compact density", () => {
    expect(atlas).toMatch(
      /\[data-overview-density=compact\][^{]*\.map-card-copy[^}]*-webkit-line-clamp:\s*2/,
    );
    expect(atlas).toMatch(/\[data-overview-density=micro\][^{]*\.map-card-copy[^}]*display:\s*none/);
    expect(atlas).not.toMatch(
      /:not\(\[data-overview-density=full\]\)\s*\.map-card-copy\s*\{\s*display:\s*none/,
    );
  });
});
