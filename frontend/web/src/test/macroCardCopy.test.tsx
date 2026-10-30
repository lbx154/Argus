import { renderToStaticMarkup } from "react-dom/server";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTaskNode, type MacroData, type MacroNode } from "../map/MacroTaskNode";
import { layoutSubmap, type SubmapStep } from "../map/submap";
import type { MapTask } from "../map/model";
import { createZoomStepStore, ZoomStepContext } from "../map/zoomStep";

vi.mock("@xyflow/react", async (original) => ({
  ...(await original<typeof import("@xyflow/react")>()),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) =>
    select({ transform: [0, 0, 1] }),
}));

const task: MapTask = {
  id: "parent",
  title: "Research portfolio",
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
    type: "task", zIndex: 0, draggable: false, selected: false, dragging: false, selectable: true, deletable: false, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0,
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
      open: vi.fn(),
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
  it("shows an explicitly cancelled task as ended", () => {
    const markup = renderToStaticMarkup(<MacroTaskNode {...propsFor([], {
      task: { ...task, status: "cancelled" }, live: false,
    })} />);
    expect(markup).toContain("Ended");
    expect(markup).not.toContain("Unknown status");
    expect(markup).not.toContain('class="macro-state">Paused');
  });
  it("shows a blocked task as waiting for the operator", () => {
    const markup = renderToStaticMarkup(<MacroTaskNode {...propsFor([], {
      task: { ...task, status: "blocked" }, live: false,
    })} />);
    expect(markup).toContain("Waiting for your answer");
  });
  it("keeps closing letters off ended summaries, while open tasks show the latest action", () => {
    const completed = { ...task, status: "done", objective: "Build a reusable date workflow",
      latest_action: { role: "reviewer", text: "Accepted the workflow.", ts: 1 } };
    const markup = renderToStaticMarkup(<MacroTaskNode {...propsFor([
      step("last-edit", { summary: "Removed a fingerprint from one footnote" }),
    ], { task: completed, part: 2, partCount: 2, live: false })} />);
    const summary = markup.slice(0, markup.indexOf('class="macro-detail'));
    expect(summary).toContain("Research portfolio");
    expect(summary).toContain("Ended");
    expect(summary).not.toContain("Accepted the workflow.");
    expect(summary).not.toContain("Build a reusable date workflow");
    expect(summary).not.toContain("Removed a fingerprint from one footnote");
    expect(markup).toContain("Removed a fingerprint from one footnote"); // retained inside execution history
    const ongoing = renderToStaticMarkup(<MacroTaskNode {...propsFor([], {
      task: { ...completed, status: "running" }, live: false,
    })} />);
    expect(ongoing).toContain("Accepted the workflow.");
  });
  it("keeps an explanation that may claim too much off an ended card, and says the state once", () => {
    const ended = { ...task, status: "done", objective: "Implement and execute downstream long-context evaluation" };
    const scope = "This execution ended; the overall goal is not complete yet.";
    const written = renderToStaticMarkup(<MacroTaskNode {...propsFor([step("s1")], {
      task: ended, live: false, completionScope: scope,
      // An explanation written before the execution ended may claim more than happened.
      copy: { cards: { parent: { title: "Accepted evaluation", summary: "All goals accepted.", detail: "d", generated_at: 1, task_status: "done" } } },
    })} />);
    expect(written).not.toContain("Accepted evaluation");
    expect(written).not.toContain("All goals accepted.");
    expect(written.match(/class="map-status"/g)).toHaveLength(1);
  });
  it("keeps a reader's own turn in the reader's words", () => {
    const turn = { ...task, kind: "turn" as const, status: "done", title: "How many lines is the README?" };
    const markup = renderToStaticMarkup(<MacroTaskNode {...propsFor([step("s1")], {
      task: turn, live: false,
    })} />);
    expect(markup).toContain("How many lines is the README?");
  });
  it("leaves a written summary off the card, whose way in reads the same whatever the summary's age", () => {
    const markup = renderToStaticMarkup(
      <MacroTaskNode
        {...propsFor([step("s1")], { copy: copyFor("Yesterday we ruled out the leak.", "done") })}
      />,
    );
    expect(markup).not.toContain("Yesterday we ruled out the leak.");
    expect(markup).toContain("View progress");
  });
  it("uses the unified fallback for the step card and the reader", () => {
    const props = propsFor([step("empty", { detail: "" })], { detailed: true });
    act(() => {
      renderer = create(<MacroTaskNode {...props} />);
    });
    act(() => renderer!.root.findByProps({ "data-step-id": "empty" }).props.onClick());
    const rendered = JSON.stringify(renderer!.toJSON());
    expect(rendered).toContain("Nothing has been written down for this step yet.");
    expect(rendered).not.toContain("Details are not available yet");
    expect(rendered).not.toContain("这一步还没有留下记录。");
  });
  it("keeps detail-page text inside the details instead of adding a history-card summary", () => {
    const markup = renderToStaticMarkup(
      <MacroTaskNode
        {...propsFor(
          [
            step("real", { detail: "Real measured progress." }),
            step("empty", { detail: "Nothing has been written down for this step yet." }),
          ],
          { partCount: 2 },
        )}
      />,
    );
    const body = markup.slice(markup.indexOf("map-card-state"), markup.indexOf("map-card-course"));
    expect(body).not.toContain("Real measured progress.");
    expect(markup).toContain("Real measured progress.");
    expect(body).not.toContain("Nothing has been written down for this step yet.");
  });
});

describe("zoom-independent card content", () => {
  it.each([false, true])("keeps the same layout and words at every zoom (stacked: %s)", (stacked) => {
    const props = propsFor([step("s1"), step("review", { kind: "review" })], {
      task: { ...task, status: "done" },
      live: false,
      completionScope: "The goal was incomplete on this attempt.",
      copy: copyFor("A superseded conclusion must not return when zooming.", "done"),
    });
    props.data.layout.stacked = stacked;
    const zoom = createZoomStepStore();
    const markups = [0.03, 0.08, 0.16, 0.24, 0.5, 1, 1.4].map((value) => {
      zoom.set(value);
      return renderToStaticMarkup(
        <ZoomStepContext.Provider value={zoom}><MacroTaskNode {...props} /></ZoomStepContext.Provider>,
      );
    });
    expect(new Set(markups).size).toBe(1);
    expect(markups[0]).not.toContain("A superseded conclusion");
    expect(markups[0]).toContain('class="map-card-course"');
    expect(markups[0]).not.toContain("Mission history: Research portfolio");
    expect(markups[0]).not.toMatch(/data-overview-density|data-copy-lines|--title-lines/);
  });
});

it('opens a saved question answer directly from its Atlas card', () => {
  const readCopy = vi.fn();
  const open = vi.fn();
  let qaRenderer: ReactTestRenderer;
  act(() => {
    qaRenderer = create(<MacroTaskNode {...propsFor([], {
      task: { ...task, turn_kind: 'qa', status: 'done', title: 'Explain SFT.', latest_action: { role: 'manager', text: 'A saved answer.', ts: 1 } },
      readCopy, open,
    })} />);
  });
  act(() => qaRenderer!.root.findByProps({ 'data-testid': 'map-card' }).props.onClick());
  expect(readCopy).toHaveBeenCalledWith('parent-card', 'parent');
  expect(open).not.toHaveBeenCalled();
  act(() => qaRenderer!.unmount());
});
