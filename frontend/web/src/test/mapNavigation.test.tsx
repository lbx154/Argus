import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TaskTrace } from '../components/TaskTrace';
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { Edge } from "@xyflow/react";
import type { ComponentProps } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { Modal } from "../components/Modal";
import { MapCanvas } from "../map/MapPanel";
import { MapReaderContent } from "../map/MapReaderContent";
import type { MacroNode } from "../map/MacroTaskNode";
import type { Dataset } from "../map/model";
import type { CameraMemory } from "../map/useSemanticCamera";

const fitCamera = vi.hoisted(() => vi.fn<(ids?: ReadonlySet<string>) => void>());
const initialization = vi.hoisted(() => ({ ready: false }));
const memory = vi.hoisted(() => ({ camera: undefined as CameraMemory | undefined }));
const restoreCamera = vi.hoisted(() => vi.fn());

vi.mock("@xyflow/react", async (original) => {
  const react = await import("react");
  return {
    ...await original<typeof import("@xyflow/react")>(),
    ReactFlow: ({ nodes, edges, onlyRenderVisibleElements, children }: { nodes: MacroNode[]; edges: Edge[]; onlyRenderVisibleElements: boolean; children: React.ReactNode }) =>
      <div data-testid="flow" data-nodes={nodes} data-edges={edges} data-culling={onlyRenderVisibleElements}>{children}</div>,
    Background: () => null, Controls: () => null, MiniMap: () => <aside data-testid="minimap" />,
    useNodesState: () => [...react.useState([]), () => {}],
    useNodesInitialized: () => initialization.ready,
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
        enter, fit, back: fit, readStep: noop, restore: restoreCamera, capture: noop,
        fitUpdatedScene: noop, navigate: noop, onMove: noop, reducedMotion: true,
      };
    },
  };
});
vi.mock("../components/Modal", () => ({
  Modal: ({ open, children, label }: ComponentProps<typeof Modal>) => open ? <div role="dialog" aria-label={label}>{children}</div> : null,
  ModalHeader: ({ title, sub }: { title: string; sub?: string }) => <header><h2>{title}</h2><p>{sub}</p></header>,
}));
vi.mock("../map/viewMemory", () => ({ recalledView: () => memory, rememberView: () => {} }));

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
  data, zh: false, sessionId: "navigation", viewKey: "navigation", readOnly: true,
  snapshot: {
    session: { id: "navigation", display_name: "Navigation", objective: "", last_active: 0, workdir: "" },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null},
    roles: [], backlog: [],
  },
  pending: false,
  actions: {
    conversationEvents: [], connected: true, artifacts: [], deliveryCount: 0,
    onOpenDelivery: () => {}, onOpenReceipt: () => {}, onOpenArtifact: () => {},
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
  initialization.ready = false;
  memory.camera = undefined;
  restoreCamera.mockClear();
  vi.stubGlobal("window", { location: { search: '' }, addEventListener: vi.fn(), removeEventListener: vi.fn() });
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  act(() => { renderer = create(<QueryClientProvider client={client}><MapCanvas {...props} /></QueryClientProvider>, {
    createNodeMock: (element) => element.props.className?.split(" ").includes("map-canvas-wrap")
      ? { clientWidth: 1440 } : null,
  }); });
});
afterEach(() => {
  act(() => renderer?.unmount());
  client.clear();
  fitCamera.mockClear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("omits the empty status row and ambient decoration without hiding active work", () => {
  const empty = { ...data, tasks: [], events: [] };
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} data={empty} /></QueryClientProvider>));
  expect(renderer.root.findAllByProps({ className: "map-status-row" })).toHaveLength(0);
  expect(renderer.root.findAllByProps({ className: "map-ambient" })).toHaveLength(0);
  expect(renderer.root.findAllByProps({ className: "map-canvas-wrap" })).toHaveLength(1);

  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} data={empty}
    pending={true} snapshot={{ ...props.snapshot, work_status: { state: "running", reason: "", role: "manager", roles: ["manager"], taskId: "", title: "", activityAt: null, foreground: true, label: { "zh-CN": "正在处理你的请求", en: "Working on your request" } } }} /></QueryClientProvider>));
  expect(renderer.root.findAllByProps({ className: "map-status-text" })).toHaveLength(0);
  expect(renderer.root.findAllByProps({ className: "map-progress" })).toHaveLength(0);
});

it('associates the live manager call with one request card and keeps each card’s own action', () => {
  const requests: Dataset = { ...data, tasks: ['q0', 'q4'].map((id, index) => ({
    id, title: `Request ${index}`, objective: '', status: 'in_progress', role: 'manager', turn_kind: 'qa', ts: index,
    latest_action: { role: 'manager', text: `Only for ${id}`, ts: index },
  })), events: [] };
  const call = { role: 'manager', answers: 'raw-request', request_card_id: 'q4', started_ts: 185 };
  const snapshot: ComponentProps<typeof MapCanvas>['snapshot'] = { ...props.snapshot,
    daemon: { ...props.snapshot.daemon, alive: true, calls: [call] },
    work_status: { state: 'running', reason: '', role: 'manager', roles: ['manager'], taskId: '', title: '',
      activityAt: 185, foreground: true, label: { 'zh-CN': '正在回复你的消息', en: 'Replying to your message' } },
  };
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} data={requests} snapshot={snapshot} /></QueryClientProvider>));
  expect(nodes().map(node => [node.id, node.data.task.latest_action?.text, node.data.liveCall])).toEqual([
    ['q0', 'Only for q0', undefined], ['q4', 'Only for q4', call],
  ]);
  const next = { ...snapshot, daemon: { ...snapshot.daemon, calls: [{ ...call, request_card_id: 'q0', started_ts: 195 }] } };
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} data={requests} snapshot={next} /></QueryClientProvider>));
  expect(nodes().filter(node => node.data.liveCall).map(node => [node.id, node.data.liveCall?.started_ts])).toEqual([['q0', 195]]);
  const before = nodes();
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} data={requests}
    snapshot={{ ...next, work_status: { ...next.work_status!, activityAt: 200 } }} /></QueryClientProvider>));
  expect(nodes()).toBe(before);
});

it("keeps the task reader open when background focus changes and reads the real trajectory", () => {
  act(() => nodes().find(node => node.id === 'failed')!.data.open('failed'));
  act(() => nodes().find(node => node.id === 'failed')!.data.readCopy!('failed', 'failed'));
  const reader = () => renderer.root.findByProps({ 'data-testid': 'map-task-reading' });
  expect(reader().props['data-task-id']).toBe('failed');
  act(() => {
    nodes().find(node => node.id === 'failed')!.data.readCopy!('failed', null);
    button('Fit map').props.onClick();
  });
  expect(focused()).toBeUndefined();
  expect(reader().findByType(MapReaderContent).props.taskId).toBe('failed');
  expect(reader().findByType(TaskTrace).props.taskId).toBe('failed');
  act(() => renderer.root.findAllByType(Modal).find(modal => modal.props.label === 'Task explanation')!.props.onClose());
  expect(renderer.root.findAllByProps({ 'data-testid': 'map-task-reading' })).toHaveLength(0);
});

it("does not count a partial research execution as a completed overall goal", () => {
  const partial = { ...data, kind: "live" as const, tasks: [data.tasks[0]], events: [{
    id: "ended", item_id: "parent", type: "life.mission.completed", ts: 5, text: "",
    success: true, overall_complete: false, campaign_continues: true,
  }] };
  act(() => renderer.update(
    <QueryClientProvider client={client}><MapCanvas {...props} data={partial} /></QueryClientProvider>,
  ));
  expect(renderer.root.findAllByProps({ className: "map-progress-strip" })).toHaveLength(0);
  expect(nodes()[0].data.task.status).toBe("done");
  expect(nodes()[0].data.completionScope).toContain("further work remained");
});

it.each([
  ["same desktop canvas", 1440, 960, true],
  ["phone after desktop", 390, 716, false],
  ["shorter desktop window", 1440, 700, false],
] as const)("restores a saved view only on the %s", (_, width, height, restore) => {
  act(() => renderer.unmount());
  memory.camera = {
    canvasSize: { width: 1440, height: 960 },
    viewport: { x: -3002.56, y: -806.481, zoom: 0.746998 },
    overview: { x: 63, y: 280, zoom: 0.16 }, focusId: 'failed', detailed: true,
  };
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  initialization.ready = true;
  act(() => { renderer = create(<QueryClientProvider client={client}><MapCanvas {...props} /></QueryClientProvider>, {
    createNodeMock: (element) => element.props.className?.split(" ").includes("map-canvas-wrap")
      ? { clientWidth: width, clientHeight: height } : null,
  }); });
  act(() => { for (const callback of frames.splice(0)) callback(0); });
  if (restore) {
    expect(restoreCamera).toHaveBeenCalledWith(memory.camera);
    expect(fitCamera).not.toHaveBeenCalled();
  } else {
    expect(restoreCamera).not.toHaveBeenCalled();
    // A phone too opens on the whole map, not on one task's tall card.
    expect(fitCamera).toHaveBeenCalledOnce();
    expect(fitCamera.mock.lastCall?.[0]).toBeUndefined();
  }
});

it("measures offscreen cards before fitting and after graph growth", () => {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal("cancelAnimationFrame", vi.fn());
  expect(renderer.root.findByProps({ "data-testid": "flow" }).props["data-culling"]).toBe(false);

  initialization.ready = true;
  act(() => renderer.update(
    <QueryClientProvider client={client}><MapCanvas {...props} /></QueryClientProvider>,
  ));
  act(() => { for (const callback of frames.splice(0)) callback(0); });

  expect(fitCamera).toHaveBeenCalledOnce();
  expect(renderer.root.findByProps({ "data-testid": "flow" }).props["data-culling"]).toBe(true);

  const expanded = { ...props, data: { ...data, tasks: [
    ...data.tasks, { ...data.tasks[0], id: "later", title: "Later work", status: "pending", ts: 9 },
  ] } };
  initialization.ready = false;
  act(() => renderer.update(
    <QueryClientProvider client={client}><MapCanvas {...expanded} /></QueryClientProvider>,
  ));
  expect(nodes()).toHaveLength(5);
  expect(renderer.root.findByProps({ "data-testid": "flow" }).props["data-culling"]).toBe(false);
  initialization.ready = true;
  act(() => renderer.update(
    <QueryClientProvider client={client}><MapCanvas {...expanded} /></QueryClientProvider>,
  ));
  act(() => { for (const callback of frames.splice(0)) callback(0); });
  expect(fitCamera).toHaveBeenCalledOnce();
  expect(renderer.root.findByProps({ "data-testid": "flow" }).props["data-culling"]).toBe(true);
});

it("keeps attention counts and question details out of the map", () => {
  expect(renderer.root.findAll(node => typeof node.type === 'string').flatMap(node => node.children.filter(child => typeof child === 'string')).join('')).not.toContain('Which bridge?');
  expect(renderer.root.findAllByProps({ className: 'map-attention-detail' })).toHaveLength(0);
  expect(renderer.root.findAllByProps({ className: 'map-attention-link' })).toHaveLength(0);
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
      <MapCanvas {...props} data={{ ...data, kind: "live", tasks: data.tasks.filter((task) => task.id !== "question") }} />
    </QueryClientProvider>,
  ));
  expect(button("Trace dependencies").props["aria-pressed"]).toBe(false);
  act(() => nodes()[0].data.open(nodes()[0].id));
  expect(focused()).toBe("parent");
});

it('waits for the initial fit before locating the task of a team-letter search result', () => {
  const frames: FrameRequestCallback[] = [];
  const located = vi.fn();
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback));
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} requestedTaskId="question" onTaskLocated={located} /></QueryClientProvider>));
  expect(focused()).toBeUndefined();
  initialization.ready = true;
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} requestedTaskId="question" onTaskLocated={located} /></QueryClientProvider>));
  act(() => { for (const callback of frames.splice(0)) callback(0); });
  expect(focused()).toBe('question');
  expect(located).toHaveBeenCalledOnce();
  // Ordinary map updates keep the user's subsequent navigation.
  act(() => button('Fit map').props.onClick());
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} data={{ ...data }} requestedTaskId="question" onTaskLocated={located} /></QueryClientProvider>));
  expect(focused()).toBeUndefined();
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} /></QueryClientProvider>));
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...props} requestedTaskId="question" onTaskLocated={located} /></QueryClientProvider>));
  expect(focused()).toBe('question');
  expect(located).toHaveBeenCalledTimes(2);
});

it("opens the minimap only on request and offers no map input path", () => {
  expect(renderer.root.findAllByProps({ "data-testid": "minimap" })).toHaveLength(0);
  expect(button("Minimap").props["aria-expanded"]).toBe(false);
  act(() => button("Minimap").props.onClick());
  expect(renderer.root.findAllByProps({ "data-testid": "minimap" })).toHaveLength(1);
  act(() => button("Minimap").props.onClick());
  expect(renderer.root.findAllByProps({ "data-testid": "minimap" })).toHaveLength(0);
  expect(renderer.root.findAllByType("textarea")).toHaveLength(0);
  expect(nodes().every(node => !('quote' in node.data) && !('menu' in node.data))).toBe(true);
});
