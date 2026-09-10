import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { Edge } from "@xyflow/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MapCanvas } from "../map/MapPanel";
import type { MacroNode } from "../map/MacroTaskNode";
import type { Dataset } from "../map/model";

const fitCamera = vi.hoisted(() => vi.fn<(ids?: ReadonlySet<string>) => void>());

vi.mock("@xyflow/react", async (original) => {
  const react = await import("react");
  return {
    ...await original<typeof import("@xyflow/react")>(),
    ReactFlow: ({ nodes, edges }: { nodes: MacroNode[]; edges: Edge[] }) =>
      <div data-testid="flow" data-nodes={nodes} data-edges={edges} />,
    useNodesState: () => [...react.useState([]), () => {}],
    useNodesInitialized: () => false,
  };
});
vi.mock("../map/useSemanticCamera", async () => {
  const { useState, useCallback } = await import("react");
  const noop = () => {};
  return {
    INITIAL_VIEWPORT: { x: 0, y: 0, zoom: 0.24 },
    useSemanticCamera: () => {
      const [focusId, setFocusId] = useState<string | null>(null);
      const enter = useCallback((id: string) => setFocusId(id), []);
      const fit = useCallback((ids?: ReadonlySet<string>) => { fitCamera(ids); setFocusId(null); }, []);
      return {
        focusId, detailed: !!focusId, canvasSize: { width: 1440, height: 960 },
        enter, fit, back: fit, readStep: noop, restore: noop, capture: noop,
        fitUpdatedScene: noop, navigate: noop, onMove: noop, reducedMotion: true,
      };
    },
  };
});
vi.mock("../map/useMapCopy", () => ({ useMapCopy: () => ({ ready: true }) }));
vi.mock("../map/MapComposer", () => ({ MapComposer: () => null }));
vi.mock("../map/viewMemory", () => ({ recalledView: () => ({}), rememberView: () => {} }));

const data: Dataset = {
  id: "live:navigation", title: "Navigation", description: "", kind: "live", read_only: false,
  tasks: [
    { id: "parent", title: "Route data", objective: "", status: "done", deps: [], ts: 1 },
    { id: "failed", title: "Route algorithm", objective: "", status: "failed", deps: ["parent"], ts: 2 },
    { id: "question", title: "Route interface", objective: "", status: "blocked", deps: ["failed"], pending_question: "Which bridge?", ts: 3 },
    { id: "unrelated", title: "Documentation", objective: "", status: "pending", deps: [], ts: 4 },
  ],
  events: [{ id: "failure", item_id: "failed", ts: 1, type: "life.mission.failed", text: "The path is disconnected." }],
};
const props: ComponentProps<typeof MapCanvas> = {
  data, zh: false, sessionId: "navigation", viewKey: "navigation", paused: true, readOnly: true,
  events: [],
  snapshot: {
    session: { id: "navigation", display_name: "Navigation", objective: "", last_active: 0, cwd: "" },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null, global_daily_cap_usd: null },
    roles: [], backlog: [], recent_events: [],
  },
  composer: {
    value: "", onChange: () => {}, onSend: async () => false, pending: false, onCancel: () => {},
    attachments: [], onAttachmentsChange: () => {}, focusSignal: 0, sessionName: "Navigation", historical: false, zh: false,
  },
  actions: {
    conversationEvents: [], connected: true, artifacts: [], deliveryCount: 0,
    onOpenDelivery: () => {}, onOpenReceipt: () => {}, onOpenArtifact: () => {}, onAnswer: () => {},
  },
};
let client: QueryClient;
let renderer: ReactTestRenderer;
const nodes = (): MacroNode[] => renderer.root.findByProps({ "data-testid": "flow" }).props["data-nodes"];
const edges = (): Edge[] => renderer.root.findByProps({ "data-testid": "flow" }).props["data-edges"];
const focused = () => nodes().find((node) => node.data.focused)?.id;
const button = (label: string) => renderer.root.findByProps({ "aria-label": label });
const search = () => button("Search map tasks");
const searchCount = () => renderer.root.findByProps({ className: "map-search-count" }).children.join("");

beforeEach(() => {
  vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn() });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  act(() => { renderer = create(<QueryClientProvider client={client}><MapCanvas {...props} /></QueryClientProvider>); });
});
afterEach(() => {
  act(() => renderer?.unmount());
  client.clear();
  fitCamera.mockClear();
  vi.unstubAllGlobals();
});

it("cycles all attention tasks and displays their actual question or failure reason", () => {
  const jump = () => act(() => button("Cycle through 2 tasks needing attention").props.onClick());
  jump();
  expect(focused()).toBe("question");
  expect(renderer.root.findByProps({ className: "map-attention-detail" }).findByType("p").children).toEqual(["Which bridge?"]);
  jump();
  expect(focused()).toBe("failed");
  expect(renderer.root.findByProps({ className: "map-attention-detail" }).findByType("p").children).toEqual(["The path is disconnected."]);
  jump();
  expect(focused()).toBe("question");
});

it("shows the result that Enter actually selected, including wraparound and changed searches", () => {
  act(() => search().props.onChange({ target: { value: "Route" } }));
  expect(searchCount()).toBe("3 found");
  for (const [id, count] of [["parent", "1/3"], ["failed", "2/3"], ["question", "3/3"], ["parent", "1/3"]]) {
    act(() => search().props.onKeyDown({ key: "Enter" }));
    expect(focused()).toBe(id);
    expect(searchCount()).toBe(count);
  }
  act(() => search().props.onChange({ target: { value: "interface" } }));
  expect(searchCount()).toBe("1 found");
  act(() => search().props.onKeyDown({ key: "Enter" }));
  expect(focused()).toBe("question");
  expect(searchCount()).toBe("1/1");
});

it("highlights direct dependencies without changing geometry and can return to normal navigation", () => {
  act(() => nodes().find((node) => node.id === "failed")!.data.open("failed"));
  const positions = nodes().map(({ id, position }) => ({ id, position }));
  act(() => button("Trace dependencies").props.onClick());
  expect(button("Trace dependencies").props["aria-pressed"]).toBe(true);
  expect(fitCamera).toHaveBeenLastCalledWith(new Set(["parent", "failed", "question"]));
  expect(nodes().map(({ id, position }) => ({ id, position }))).toEqual(positions);
  expect(nodes().filter((node) => node.style?.opacity === 1).map((node) => node.id)).toEqual(["parent", "failed", "question"]);
  expect(edges().filter((edge) => edge.style?.opacity === 1).map((edge) => [edge.source, edge.target])).toEqual([
    ["parent", "failed"], ["failed", "question"],
  ]);
  act(() => nodes().find((node) => node.id === "question")!.data.open("question"));
  expect(nodes().filter((node) => node.style?.opacity === 1).map((node) => node.id)).toEqual(["failed", "question"]);
  expect(nodes().map(({ id, position }) => ({ id, position }))).toEqual(positions);
  act(() => button("Fit map").props.onClick());
  expect(nodes().every((node) => node.style?.opacity === 1)).toBe(true);
  expect(button("Trace dependencies").props["aria-pressed"]).toBe(false);
  expect(fitCamera).toHaveBeenLastCalledWith(undefined);
  act(() => nodes()[0].data.open(nodes()[0].id));
  expect(focused()).toBe("parent");
});

it("exits dependency focus when the selected card is clicked again", () => {
  act(() => button("Trace dependencies").props.onClick());
  act(() => nodes().find((node) => node.id === "question")!.data.open("question"));
  expect(button("Trace dependencies").props["aria-pressed"]).toBe(false);
  expect(nodes().every((node) => node.style?.opacity === 1)).toBe(true);
});

it("keeps normal card navigation when the traced task disappears from the live map", () => {
  act(() => button("Trace dependencies").props.onClick());
  act(() => renderer.update(
    <QueryClientProvider client={client}>
      <MapCanvas {...props} data={{ ...data, tasks: data.tasks.filter((task) => task.id !== "question") }} />
    </QueryClientProvider>,
  ));
  expect(button("Trace dependencies").props["aria-pressed"]).toBe(false);
  act(() => nodes()[0].data.open(nodes()[0].id));
  expect(focused()).toBe("parent");
});
