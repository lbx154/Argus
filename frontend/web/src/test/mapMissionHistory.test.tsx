import { expect, it, vi } from "vitest";
import { act, create } from "react-test-renderer";
import type { NodeProps } from "@xyflow/react";
import { buildMap, type MapEvent, type MapTask } from "../map/model";
import { layoutScene } from "../map/submap";
import { MacroTaskNode, MapArtifactContext, type MacroNode } from "../map/MacroTaskNode";

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

it("keeps one overview card while paging every recorded step inside its details", () => {
  const graph = buildMap([task]);
  const latest = layoutScene(graph, events, false);
  expect(latest.allCards).toHaveLength(4);
  expect(latest.cards).toHaveLength(1);
  expect(latest.cards[0]).toMatchObject({ id: task.id, part: 4, partCount: 4 });
  expect(latest.links).toEqual([]);
  const first = layoutScene(graph, events, false, latest, [], new Map([[task.id, 1]]));
  expect(first.cards).toHaveLength(1);
  expect(first.cards[0]).toMatchObject({ id: task.id, part: 1, nextId: first.allCards[1].id });
  expect(first.layouts[task.id].steps).toEqual(first.layouts[first.allCards[0].id].steps);
  expect(first.frames[task.id]).toMatchObject({ width: latest.frames[task.id].width, height: latest.frames[task.id].height });
  expect(first.positions).toEqual(latest.positions);
});

it("never merges separate tasks by title and keeps dependency endpoints visible", () => {
  const other = { ...task, id: "other", deps: [task.id] };
  const graph = buildMap([task, other]);
  const scene = layoutScene(graph, events, false, undefined, undefined, new Map());
  expect(scene.cards.map((card) => card.id)).toEqual([task.id, other.id]);
  expect(scene.links).toContainEqual(expect.objectContaining({ source: task.id, target: other.id }));
  for (const link of scene.links) {
    expect(scene.cards.some((card) => card.id === link.source)).toBe(true);
    expect(scene.cards.some((card) => card.id === link.target)).toBe(true);
  }
  const expanded = layoutScene(graph, events, false, scene, undefined, new Map([[task.id, 1]]));
  expect(expanded.links).toContainEqual(expect.objectContaining({
    source: expanded.cards.filter((card) => card.task.id === task.id).at(-1)!.id,
    target: other.id,
  }));
});

it("keeps history navigation in the details without an overview history control", () => {
  const scene = layoutScene(buildMap([task]), events, false, undefined, [], new Map());
  const card = scene.cards[0];
  const props: NodeProps<MacroNode> = {
    id: card.id,
    type: "task", zIndex: 0, draggable: false, selected: false, dragging: false,
    selectable: true, deletable: false, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0,
    data: { ...card, layout: scene.layouts[card.id], frame: scene.frames[card.id],
      canvasSize: { width: 1440, height: 960 },
      zh: false, focused: false, detailed: false,
      open: vi.fn(), readStep: vi.fn() },
  };
  let renderer: ReturnType<typeof create>;
  act(() => { renderer = create(<MacroTaskNode {...props} />); });
  expect(renderer!.root.findAllByProps({ className: 'map-history-toggle nodrag nopan' })).toHaveLength(0);
  expect(JSON.stringify(renderer!.toJSON())).not.toContain("Continued");
  expect(renderer!.root.findAllByProps({ className: "macro-detail " })).toHaveLength(0);
  act(() => renderer!.unmount());
});

it.each([
  ['results/solo.md'],
  ['results/first.html', 'results/说明.md'],
])('names every delivered file button: %s', (...paths) => {
  const delivered = { ...task, status: 'done', result_paths: paths };
  const scene = layoutScene(buildMap([delivered]), [], false);
  const card = scene.cards[0];
  const opened = vi.fn();
  let renderer!: ReturnType<typeof create>;
  act(() => { renderer = create(<MapArtifactContext.Provider value={{ onOpenArtifact: opened }}><MacroTaskNode {...{
    id: card.id, type: "task", zIndex: 0, draggable: false, selected: false, dragging: false, selectable: true, deletable: false, isConnectable: false, positionAbsoluteX: 0, positionAbsoluteY: 0, data: { ...card, layout: scene.layouts[card.id], frame: scene.frames[card.id],
      canvasSize: { width: 1440, height: 960 }, zh: false, focused: false, detailed: false, open: vi.fn(), readStep: vi.fn() },
  } as NodeProps<MacroNode>} /></MapArtifactContext.Provider>); });
  const buttons = renderer.root.findByProps({ className: 'map-card-results nodrag nopan' }).findAllByType('button');
  expect(buttons.map(button => button.children.join(''))).toEqual(paths.map(path => path.split('/').pop()));
  act(() => buttons.forEach(button => button.props.onClick()));
  expect(opened.mock.calls).toEqual(paths.map(path => [path]));
  act(() => renderer.unmount());
});
