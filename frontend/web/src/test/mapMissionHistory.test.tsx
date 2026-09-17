import { expect, it, vi } from "vitest";
import { act, create } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { buildMap, type MapEvent, type MapTask } from "../map/model";
import { layoutScene } from "../map/submap";
import { MacroTaskNode, type MacroNode } from "../map/MacroTaskNode";

vi.mock("@xyflow/react", async (original) => ({
  ...await original<typeof import("@xyflow/react")>(),
  Handle: () => null,
  useStore: (select: (state: { transform: number[] }) => unknown) => select({ transform: [0, 0, 1] }),
}));

const task: MapTask = { id: "mission", title: "Study", objective: "", status: "running", deps: [] };
const events: MapEvent[] = Array.from({ length: 36 }, (_, i) => ({
  id: `review-${i}`, item_id: task.id, ts: i + 1, round_index: i + 1,
  type: "round.review.completed", text: `Review ${i + 1}`, status: "done",
}));

it("shows one current card while retaining all four pages for explicit history", () => {
  const graph = buildMap([task]);
  const collapsed = layoutScene(graph, events, false, undefined, [], new Set());
  expect(collapsed.allCards).toHaveLength(4);
  expect(collapsed.cards).toHaveLength(1);
  expect(collapsed.cards[0]).toMatchObject({ id: task.id, part: 4, historyCount: 3, historyExpanded: false });
  expect(collapsed.links).toEqual([]);
  const expanded = layoutScene(graph, events, false, collapsed, [], new Set([task.id]));
  expect(expanded.cards).toHaveLength(4);
  expect(expanded.links).toHaveLength(3);
  expect(expanded.cards.flatMap((card) => expanded.layouts[card.id].steps).length).toBe(collapsed.cards[0].totalSteps);
  const progressed = layoutScene(graph, [...events, { ...events[35], id: "later", ts: 40, round_index: 40 }],
    false, expanded, [], new Set([task.id]));
  expect(progressed.cards.every((card) => card.historyExpanded)).toBe(true);
  const nextPage = layoutScene(graph, [...events, ...events.slice(0, 12).map((event, i) => ({
    ...event, id: `new-${i}`, ts: 50 + i, round_index: 50 + i,
  }))], false, collapsed, [], new Set());
  expect(nextPage.cards).toHaveLength(1);
  expect(nextPage.cards[0]).toMatchObject({ id: task.id, part: 5, historyCount: 4 });
});

it("never merges separate tasks by title and keeps dependency endpoints visible", () => {
  const other = { ...task, id: "other", deps: [task.id] };
  const graph = buildMap([task, other]);
  const scene = layoutScene(graph, events, false, undefined, undefined, new Set());
  expect(scene.cards.map((card) => card.id)).toEqual([task.id, other.id]);
  expect(scene.links).toContainEqual(expect.objectContaining({ source: task.id, target: other.id }));
  for (const link of scene.links) {
    expect(scene.cards.some((card) => card.id === link.source)).toBe(true);
    expect(scene.cards.some((card) => card.id === link.target)).toBe(true);
  }
  const expanded = layoutScene(graph, events, false, scene, undefined, new Set([task.id]));
  expect(expanded.links).toContainEqual(expect.objectContaining({
    source: expanded.cards.filter((card) => card.task.id === task.id).at(-1)!.id,
    target: other.id,
  }));
});

it("offers a keyboard-accessible history control without repeating Continued titles", () => {
  const scene = layoutScene(buildMap([task]), events, false, undefined, [], new Set());
  const card = scene.cards[0], toggleHistory = vi.fn();
  const props: NodeProps<MacroNode> = {
    id: card.id,
    type: "task", zIndex: 0, draggable: false, selected: false, dragging: false,
    selectable: true, deletable: false, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0,
    data: { ...card, layout: scene.layouts[card.id], frame: scene.frames[card.id],
      canvasSize: { width: 1440, height: 960 },
      zh: false, focused: false, detailed: false, live: true, readOnly: false, source: "live:test",
      open: vi.fn(), quote: vi.fn(), menu: vi.fn(), readStep: vi.fn(), toggleHistory },
  };
  let renderer: ReturnType<typeof create>;
  act(() => { renderer = create(<MacroTaskNode {...props} />); });
  const button = renderer!.root.findByProps({ "aria-label": "Mission history: Study" });
  expect(button.props["aria-expanded"]).toBe(false);
  act(() => button.props.onClick());
  expect(toggleHistory).toHaveBeenCalledWith(task.id);
  expect(JSON.stringify(renderer!.toJSON())).not.toContain("Continued");
  expect(renderer!.root.findAllByProps({ className: "macro-detail " })).toHaveLength(0);
  act(() => renderer!.unmount());
});
