import { readFileSync } from "node:fs";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, create, type ReactTestInstance, type ReactTestRenderer } from "react-test-renderer";
import type { Edge, Node } from "@xyflow/react";
import { isValidElement, type ComponentProps, type ReactElement, type ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../api";
import { MapCanvas } from "../map/MapPanel";
import { EMPTY_CANVAS, placeAroundPeople, type CanvasRecord, type Point } from "../map/canvas";
import { LinkGuide } from "../map/CanvasNodes";
import type { Dataset } from "../map/model";
import type { DispatchObserver } from "../map/submission";

vi.mock("@xyflow/react", async (original) => {
  const react = await import("react");
  return {
    ...await original<typeof import("@xyflow/react")>(),
    ReactFlow: (props: Record<string, unknown>) => <div data-testid="flow" {...{ flow: props }} />,
    useNodesState: () => [...react.useState([]), () => {}],
    useNodesInitialized: () => false,
  };
});
vi.mock("../map/useSemanticCamera", async () => {
  const noop = () => {};
  return {
    INITIAL_VIEWPORT: { x: 0, y: 0, zoom: 0.24 },
    openCanvasArea: () => null,
    useSemanticCamera: () => ({
      focusId: null, detailed: false, canvasSize: { width: 1440, height: 960 },
      enter: noop, fit: noop, back: noop, readStep: noop, restore: noop, capture: noop,
      fitUpdatedScene: noop, hold: noop, navigate: noop, onMove: noop, reducedMotion: true,
    }),
  };
});
vi.mock("../map/MapComposer", () => ({ MapComposer: () => null }));
vi.mock("../map/viewMemory", () => ({ recalledView: () => ({}), rememberView: () => {} }));

const data: Dataset = {
  id: "live:canvas", title: "Canvas", description: "", kind: "live", read_only: false,
  tasks: [
    { id: "parent", title: "Route data", objective: "", status: "done", deps: [], ts: 1 },
    { id: "child", title: "Route algorithm", objective: "", status: "pending", deps: ["parent"], ts: 2 },
  ],
  events: [],
};
const openArtifact = vi.fn();
const props: ComponentProps<typeof MapCanvas> = {
  data, zh: true, sessionId: "canvas", viewKey: "canvas", paused: true, readOnly: false,
  events: [],
  snapshot: {
    session: { id: "canvas", display_name: "Canvas", objective: "", last_active: 0, cwd: "" },
    daemon: { alive: false, pid: null, uptime_seconds: null, backend: null },
    roles: [], backlog: [], recent_events: [],
  },
  composer: {
    value: "", onChange: () => {}, onSend: async () => false, pending: false, onCancel: () => {},
    attachments: [], onAttachmentsChange: () => {}, focusSignal: 0, sessionName: "Canvas", historical: false, zh: true,
  },
  actions: {
    conversationEvents: [], connected: true, artifacts: [], deliveryCount: 0,
    onOpenDelivery: () => {}, onOpenReceipt: () => {}, onOpenArtifact: openArtifact, onAnswer: () => {},
  },
};

/** A small stand-in for the canvas routes, revision checks included. */
let server: CanvasRecord;
let writes: Array<{ method: string; path: string; body?: Record<string, unknown> }>;
let serial = 0;
function fakeWrite(_sid: string, method: string, path: string, body?: Record<string, unknown>) {
  writes.push({ method, path, body });
  const next: CanvasRecord = structuredClone(server);
  let result: Record<string, unknown> = {};
  if (method === "POST" && path === "/cards") {
    const card = { id: `hc-${++serial}`, kind: body!.kind, text: body!.text ?? "", attachment: body!.attachment ?? null, created_ts: 1, updated_ts: 1 };
    next.cards.push(card as CanvasRecord["cards"][number]);
    next.positions[card.id] = body!.position as { x: number; y: number };
    result = { card };
  } else if (method === "POST" && path === "/links") {
    const link = { id: `hl-${++serial}`, source: body!.source, target: body!.target, kind: body!.kind, created_ts: 1 };
    next.links.push(link as CanvasRecord["links"][number]);
    result = { link };
  } else if (method === "PUT" && path === "/positions") {
    Object.assign(next.positions, body!.positions);
  }
  next.revision += 1;
  server = next;
  return Promise.resolve({ canvas: next, ...result });
}

let client: QueryClient;
let renderer: ReactTestRenderer;
const flow = () => renderer.root.findByProps({ "data-testid": "flow" }).props.flow as {
  nodes: Node[]; edges: Edge[];
  onNodeDragStop: (e: unknown, n: unknown, nodes: Array<{ id: string; position: { x: number; y: number } }>) => void;
  onConnectEnd: (e: unknown, state: unknown) => void;
  onConnectStart: () => void;
  onNodesChange: (changes: unknown[]) => void;
  children: ReactNode;
};
const wrap = () => renderer.root.find((n: ReactTestInstance) => n.props.className === "map-canvas-wrap");
const human = () => flow().nodes.filter((n) => n.type === "human");
const target = (...selectors: string[]) => ({ closest: (s: string) => selectors.includes(s) ? {} : null });
const flush = () => act(async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setTimeout(r, 0)); });

beforeEach(() => {
  server = structuredClone(EMPTY_CANVAS);
  writes = [];
  serial = 0;
  vi.stubGlobal("window", { location: { search: "" }, addEventListener: vi.fn(), removeEventListener: vi.fn(), innerWidth: 1440, innerHeight: 960 });
  vi.stubGlobal("document", { addEventListener: vi.fn(), removeEventListener: vi.fn(), elementFromPoint: () => target(".react-flow__renderer"), querySelector: () => null });
  vi.spyOn(api, "writeMapCanvas").mockImplementation(fakeWrite as typeof api.writeMapCanvas);
  vi.spyOn(api, "mapCanvas").mockImplementation(() => Promise.resolve(server));
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(["map-copy", "project", "canvas", "zh-CN", "canvas"], { cards: {}, relations: [], available: false });
  client.setQueryData(["map-canvas", "canvas"], server);
  act(() => { renderer = create(<QueryClientProvider client={client}><MapCanvas {...props} /></QueryClientProvider>, {
    createNodeMock: () => ({ clientWidth: 1440, querySelector: () => null }),
  }); });
});
afterEach(() => {
  act(() => renderer?.unmount());
  client.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("adds an idea card where the empty canvas is double-clicked", async () => {
  act(() => wrap().props.onDoubleClick({ target: target(".react-flow__pane"), clientX: 1000, clientY: 500 }));
  const draft = human()[0];
  expect(draft.data.draft).toBe(true);
  act(() => (draft.data.commit as (id: string, text: string) => void)(draft.id, "先确认口径"));
  await flush();
  expect(writes[0]).toMatchObject({ method: "POST", path: "/cards", body: { revision: 0, kind: "idea", text: "先确认口径" } });
  expect(human().map((n) => [n.id, (n.data.card as { text: string }).text, n.data.draft])).toEqual([["hc-1", "先确认口径", false]]);
  // Double-clicking on a card is not the empty canvas.
  act(() => wrap().props.onDoubleClick({ target: target(".react-flow__node"), clientX: 0, clientY: 0 }));
  expect(human()).toHaveLength(1);
});

it("links two cards with the kind the person chooses and draws it as a human link", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "假设", position: { x: 0, y: -900 } }); });
  writes = [];
  act(() => { client.setQueryData(["map-canvas", "canvas"], server); });
  act(() => flow().onConnectEnd({ clientX: 20, clientY: 30 }, { fromNode: { id: "hc-1" }, toNode: { id: "parent" } }));
  const related = renderer.root.findAll((n) => n.props.role === "menuitemradio" && n.children.includes("检查"));
  act(() => related[0].props.onClick());
  await flush();
  expect(writes).toEqual([{ method: "POST", path: "/links", body: { revision: 1, source: "hc-1", target: "parent", kind: "check" } }]);
  const edge = flow().edges.find((e) => e.type === "human")!;
  expect(edge).toMatchObject({ source: "hc-1", target: "parent", data: { kind: "check" } });
});

it("keeps a person's card measured through a canvas write, so its links stay drawn and startable", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "假设", position: { x: 0, y: -900 } }); });
  writes = [];
  act(() => { client.setQueryData(["map-canvas", "canvas"], server); });
  await flush();
  // React Flow's own bookkeeping: it measures the card, finds its handles and
  // reports the size; then every write hands the nodes over again.
  const { adoptUserNodes } = await import("@xyflow/system");
  const lookup = new Map<string, any>();
  const parents = new Map();
  adoptUserNodes(flow().nodes, lookup, parents);
  const handleBounds = { source: [{ id: "right", type: "source", position: "right", x: 1200, y: 240, width: 1, height: 1 }], target: [] };
  const card = lookup.get("hc-1");
  lookup.set("hc-1", { ...card, measured: { width: 1200, height: 480 }, internals: { ...card.internals, handleBounds } });
  act(() => flow().onNodesChange([{ id: "hc-1", type: "dimensions", dimensions: { width: 1200, height: 480 } }]));
  act(() => flow().onConnectEnd({ clientX: 20, clientY: 30 }, { fromNode: { id: "hc-1" }, toNode: { id: "parent" } }));
  act(() => renderer.root.findAll((n) => n.props.role === "menuitemradio" && n.children.includes("依据"))[0].props.onClick());
  await flush();
  expect(writes).toHaveLength(1);
  adoptUserNodes(flow().nodes, lookup, parents, { checkEquality: true });
  // Without the size React Flow dropped the handles: the new link was not
  // drawn and no line could start from the card until a reload.
  expect(lookup.get("hc-1").internals.handleBounds).toBe(handleBounds);
  expect(flow().edges.find((e) => e.type === "human")).toMatchObject({ source: "hc-1", target: "parent" });
});

it("drops a line on Esc and outlines the same card the drop would link", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "假设", position: { x: 0, y: -900 } }); });
  act(() => { client.setQueryData(["map-canvas", "canvas"], server); });
  const guide = () => [flow().children].flat().find((c): c is ReactElement<ComponentProps<typeof LinkGuide>> =>
    isValidElement(c) && c.type === LinkGuide)!;
  const menu = () => renderer.root.findAll((n) => n.props.role === "menuitemradio");
  const node = { closest: (s: string) => s === ".react-flow__renderer" ? {} : s === ".react-flow__node" ? { getAttribute: () => "parent" } : null };
  (document as unknown as { elementsFromPoint: unknown }).elementsFromPoint = () => [node];
  // The guide's choice is the drop's choice.
  expect(guide().props.targetAt("hc-1", { x: 600, y: 300 } as Point)).toBe("parent");
  act(() => flow().onConnectStart());
  expect(wrap().props["data-linking"]).toBe(true);
  act(() => guide().props.onCancel());
  expect(wrap().props["data-linking"]).toBeUndefined();
  act(() => flow().onConnectEnd({ clientX: 600, clientY: 300 }, { fromNode: { id: "hc-1" }, toNode: null }));
  expect(menu()).toHaveLength(0);
  // The next line is not cancelled.
  act(() => flow().onConnectStart());
  act(() => flow().onConnectEnd({ clientX: 600, clientY: 300 }, { fromNode: { id: "hc-1" }, toNode: null }));
  expect(menu().length).toBeGreaterThan(0);
});

it("offers no link when the drop lands on a panel over the canvas or past its edge", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "假设", position: { x: 0, y: -900 } }); });
  act(() => { client.setQueryData(["map-canvas", "canvas"], server); });
  const node = { closest: (s: string) => s === ".react-flow__renderer" ? {} : s === ".react-flow__node" ? { getAttribute: () => "parent" } : null };
  const menu = () => renderer.root.findAll((n) => n.props.role === "menuitemradio");
  // Over the open conversation panel, with the task card behind it.
  (document as unknown as { elementsFromPoint: unknown }).elementsFromPoint = () => [target(".map-conversation"), node];
  act(() => flow().onConnectEnd({ clientX: 1200, clientY: 300 }, { fromNode: { id: "hc-1" }, toNode: null }));
  expect(menu()).toHaveLength(0);
  // Past the canvas edge, over the sidebar rail.
  (document as unknown as { elementsFromPoint: unknown }).elementsFromPoint = () => [target(".workspace-rail")];
  act(() => flow().onConnectEnd({ clientX: 10, clientY: 300 }, { fromNode: { id: "hc-1" }, toNode: null }));
  expect(menu()).toHaveLength(0);
  // On the canvas, the same card behind the line is linked.
  (document as unknown as { elementsFromPoint: unknown }).elementsFromPoint = () => [target(".react-flow__renderer"), node];
  act(() => flow().onConnectEnd({ clientX: 600, clientY: 300 }, { fromNode: { id: "hc-1" }, toNode: null }));
  expect(menu().length).toBeGreaterThan(0);
});

it("remembers where a task card was dropped and keeps it there", async () => {
  act(() => flow().onNodeDragStop(null, null, [{ id: "parent", position: { x: 4321.4, y: -77 } }]));
  await flush();
  expect(writes).toEqual([{ method: "PUT", path: "/positions", body: { revision: 0, positions: { parent: { x: 4321, y: -77 } } } }]);
  expect(server.positions.parent).toEqual({ x: 4321, y: -77 });
});

it("turns a file dropped on the canvas into a file card that opens the file", async () => {
  const upload = vi.spyOn(api, "uploadAttachments").mockResolvedValue({
    attachments: [{ attachment_id: "att-1", relative_path: ".argus/attachments/att-1/spec.pdf", original_name: "spec.pdf",
      stored_name: "spec.pdf", mime: "application/pdf", size_bytes: 2048 }],
    limits: {} as never,
  });
  const file = { name: "spec.pdf" } as File;
  const event = { dataTransfer: { types: ["Files"], files: [file] }, target: target(".react-flow", ".react-flow__node"),
    clientX: 10, clientY: 10, preventDefault: vi.fn(), stopPropagation: vi.fn() };
  // Dropped onto the "parent" task card: the file card is evidence for it.
  event.target = { closest: (s: string) => s === ".react-flow" ? {} : s === ".react-flow__node" ? { getAttribute: () => "parent" } : null };
  act(() => wrap().props.onDrop(event));
  await flush();
  expect(upload).toHaveBeenCalledWith("canvas", [file]);
  expect(writes[0]).toMatchObject({ method: "POST", path: "/cards", body: { kind: "file", attachment: { attachment_id: "att-1", name: "spec.pdf" } } });
  expect(writes[1]).toMatchObject({ method: "POST", path: "/links", body: { source: "hc-1", target: "parent", kind: "basis" } });
  const card = human()[0];
  expect((card.data.card as { attachment: { name: string } }).attachment.name).toBe("spec.pdf");
  (card.data.openFile as (p: string) => void)(".argus/attachments/att-1/spec.pdf");
  expect(openArtifact).toHaveBeenCalledWith(".argus/attachments/att-1/spec.pdf");
});

it("lays Argus cards out around people's cards and never moves a pinned card", () => {
  const frames = { a: { width: 100, height: 100 }, b: { width: 100, height: 100 } };
  const laid = { a: { x: 0, y: 0 }, b: { x: 1000, y: 0 } };
  const placed = placeAroundPeople(laid, frames, {}, [{ x: 20, y: 20, width: 50, height: 50 }]);
  expect(placed.a).not.toEqual(laid.a);
  expect(placed.b).toEqual(laid.b);
  expect(placeAroundPeople(laid, frames, { a: { x: 30, y: 30 } }, [{ x: 20, y: 20, width: 50, height: 50 }]).a).toEqual({ x: 30, y: 30 });
  expect(placeAroundPeople(laid, frames, {}, [])).toBe(laid);
});

function remount(next: Partial<ComponentProps<typeof MapCanvas>>) {
  Object.assign(window, { setTimeout: vi.fn() });
  vi.stubGlobal("innerHeight", 960);
  act(() => renderer.unmount());
  act(() => { renderer = create(<QueryClientProvider client={client}><MapCanvas {...props} {...next} /></QueryClientProvider>, {
    createNodeMock: () => ({ clientWidth: 1440, querySelector: () => null }),
  }); });
}
const menuItem = (label: string) => renderer.root.find((n) => n.props.role === "menuitem" && n.children.includes(label));
const openCardMenu = (id: string) =>
  act(() => (human().find((n) => n.id === id)!.data.menu as (id: string, p: { x: number; y: number }) => void)(id, { x: 5, y: 5 }));

it("hands an idea card to the team as a message to the Manager that quotes the card", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "写一个导出", position: { x: 0, y: -900 } }); });
  client.setQueryData(["map-canvas", "canvas"], server);
  const onSend = vi.fn(async () => true);
  remount({ composer: { ...props.composer, onSend } });
  openCardMenu("hc-1");
  act(() => menuItem("交给团队").props.onClick());
  await flush();
  expect(onSend).toHaveBeenCalledTimes(1);
  const [text] = onSend.mock.calls[0] as unknown as [string];
  const [marker, ask] = text.split("\n");
  expect(JSON.parse(marker.slice("[[Argus引用 ".length, -2))).toMatchObject({ task_id: "hc-1", task_title: "写一个导出", event_ids: [] });
  expect(ask).toBe("把这张卡做成任务：写一个导出");
});

it.each(["error", "cancelled"] as const)("drops the card notice and opens the conversation when a card message ends %s", async (outcome) => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "写一个导出", position: { x: 0, y: -900 } }); });
  client.setQueryData(["map-canvas", "canvas"], server);
  const onSend = vi.fn(async (_text: string, _files?: File[], observe?: DispatchObserver) => { observe?.({ type: "settled", outcome }); return true; });
  remount({ composer: { ...props.composer, onSend } });
  openCardMenu("hc-1");
  act(() => menuItem("交给团队").props.onClick());
  await flush();
  expect(onSend).toHaveBeenCalledTimes(1);
  expect(renderer.root.findAll((n) => n.type === "span" && n.children.includes("已发给 Argus，回复在对话里"))).toHaveLength(0);
  expect(renderer.root.findAllByProps({ className: "map-conversation nowheel nodrag nopan" })).toHaveLength(1);
});

it("keeps the person's draft: a hand-off then waits in the composer", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "写一个导出", position: { x: 0, y: -900 } }); });
  client.setQueryData(["map-canvas", "canvas"], server);
  const onSend = vi.fn(async () => true);
  const onChange = vi.fn();
  remount({ composer: { ...props.composer, value: "我的草稿", onSend, onChange } });
  openCardMenu("hc-1");
  act(() => menuItem("交给团队").props.onClick());
  await flush();
  expect(onSend).not.toHaveBeenCalled();
  expect(onChange.mock.calls[0][0]).toMatch(/^\[\[Argus引用 .*\]\]\n把这张卡做成任务：写一个导出\n我的草稿$/);
});

it("asks about a card by quoting it in the composer", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "这个假设成立吗", position: { x: 0, y: -900 } }); });
  client.setQueryData(["map-canvas", "canvas"], server);
  const onChange = vi.fn();
  remount({ composer: { ...props.composer, onChange } });
  openCardMenu("hc-1");
  act(() => menuItem("问这张卡").props.onClick());
  expect(onChange.mock.calls[0][0]).toMatch(/^\[\[Argus引用 \{.*"task_id":"hc-1".*\}\]\]\n$/);
});

it("draws the recorded link from an idea card to the task that came from it", async () => {
  await act(async () => { await fakeWrite("canvas", "POST", "/cards", { kind: "idea", text: "写一个导出", position: { x: 0, y: -900 } }); });
  client.setQueryData(["map-canvas", "canvas"], server);
  remount({ data: { ...data, tasks: data.tasks.map((t) => t.id === "child" ? { ...t, relations: [{ kind: "from", target: "hc-1" }] } : t) } });
  const edge = flow().edges.find((e) => e.type === "human" && e.data?.recorded);
  expect(edge).toMatchObject({ source: "hc-1", target: "child", data: { recorded: true, label: "来自" } });
});

it("puts the team line and the held plan's 开始执行 on the canvas overview", async () => {
  const onStartLaunch = vi.fn(() => Promise.resolve());
  const next = {
    ...props,
    snapshot: { ...props.snapshot, launch_hold: { created_at: 1, item_id: "child", title: "Route algorithm", request: "", continuous: false, settings: {} } },
    actions: { ...props.actions, onStartLaunch, teamStatus: <div data-testid="team-line">运行中 <button data-testid="team-pause">暂停团队</button></div> },
  };
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...next} /></QueryClientProvider>));
  const overview = renderer.root.findByProps({ "data-testid": "map-overview" });
  expect(overview.findAllByProps({ "data-testid": "team-pause" })).toHaveLength(1);
  const start = overview.findByProps({ "data-testid": "launch-start" });
  await act(async () => { await start.props.onClick({ stopPropagation: () => {} }); });
  expect(onStartLaunch).toHaveBeenCalledTimes(1);
});

it("lets a link start anywhere on a card's edge, about 14 screen pixels wide at any zoom", async () => {
  const { rimWidth } = await import("../map/CanvasNodes");
  expect(rimWidth(1)).toBe(14);
  expect(rimWidth(0.25)).toBe(56);
  expect(rimWidth(0.0625) * 0.0625).toBeCloseTo(14, 0);
  expect(rimWidth(3)).toBe(14);
});

it("shows a person's card as a large two-line label once its words would be too small to read", async () => {
  const { farLabelFont } = await import("../map/CanvasNodes");
  expect(farLabelFont(0.25, "idea")).toBe(0);
  expect(farLabelFont(0.5, "file")).toBe(0);
  // At an overview zoom the label is sized for the screen (14 px)…
  expect(farLabelFont(0.125, "idea") * 0.125).toBeCloseTo(14, 0);
  // …and never outgrows two lines of the card.
  expect(farLabelFont(0.03, "idea")).toBe(Math.floor((480 - 56) / 2.3));
  expect(farLabelFont(0.03, "file")).toBe(Math.floor((360 - 56) / 2.3));
});

it("refetches the canvas when the live stream announces a newer revision than this window holds", async () => {
  const { canvasRevisionFromEvents } = await import("../hooks");
  const { refreshCanvas } = await import("../map/canvas");
  expect(canvasRevisionFromEvents([])).toBe(0);
  expect(canvasRevisionFromEvents([
    { type: "project.canvas.updated", revision: 3, ts: 1 },
    { type: "ui.argus", ts: 2 },
    { type: "project.canvas.updated", revision: 5, ts: 3 },
  ] as never)).toBe(5);
  const client = new QueryClient();
  const spy = vi.spyOn(client, "invalidateQueries");
  client.setQueryData(["map-canvas", "s1"], { ...EMPTY_CANVAS, revision: 5 });
  refreshCanvas(client, "s1", 5); // the window that wrote it already holds it
  expect(spy).not.toHaveBeenCalled();
  refreshCanvas(client, "s1", 6);
  expect(spy).toHaveBeenCalledWith({ queryKey: ["map-canvas", "s1"], exact: true });
});

it("finds the card a link was dropped on even when no handle takes it", async () => {
  const { resolveLinkTarget } = await import("../map/canvas");
  const nodes = [
    { id: "task-a", kind: "task", x: 0, y: 0, width: 1440, height: 900 },
    { id: "hc-b", kind: "human", x: 100, y: 100, width: 880, height: 480 },
    { id: "hc-src", kind: "human", x: 2000, y: 0, width: 880, height: 480 },
    { id: "draft-1", kind: "human", x: 3000, y: 0, width: 880, height: 480 },
  ];
  // What the browser reports under the pointer wins, skipping the source card.
  expect(resolveLinkTarget("hc-src", [undefined, "hc-src", "task-a"], null, nodes)).toBe("task-a");
  // Nothing under the pointer (a label, a gap in the DOM): the drop point decides,
  // a person's card before the task card it sits on.
  expect(resolveLinkTarget("hc-src", [], { x: 200, y: 200 }, nodes)).toBe("hc-b");
  expect(resolveLinkTarget("hc-src", [null], { x: 1200, y: 800 }, nodes)).toBe("task-a");
  // Never onto itself, a draft, or empty canvas.
  expect(resolveLinkTarget("hc-src", ["hc-src"], { x: 2100, y: 100 }, nodes)).toBeUndefined();
  expect(resolveLinkTarget("hc-src", ["draft-1"], { x: 3100, y: 100 }, nodes)).toBeUndefined();
  expect(resolveLinkTarget("hc-src", [], { x: 5000, y: 5000 }, nodes)).toBeUndefined();
});

it("keeps the link dot about 18 screen pixels at every zoom, above the 7px port rule", async () => {
  const { connectDotSize } = await import("../map/canvas");
  for (const step of [0.0625, 0.125, 0.24, 0.5]) expect(connectDotSize(step) * step).toBeGreaterThanOrEqual(17.5);
  expect(connectDotSize(1)).toBe(40);
  const css = readFileSync(new URL("../map/canvas.css", import.meta.url), "utf8");
  // map.css `.argus-map .react-flow__handle { width: 7px }` shrank the dot to
  // 1.7 screen px at overview zoom; the dot's own rule must outrank it.
  expect(css).toMatch(/\.argus-map \.react-flow__node \.react-flow__handle\.canvas-connect,[^{]*\{[^}]*width: var\(--dot/);
  expect(css).toMatch(/\.argus-map \.react-flow__node \.react-flow__handle\.canvas-rim,/);
});

it("grows a person's card with its words and keeps new cards clear of the map's chrome", async () => {
  const { humanFrame, cardTextLines, clearOfChrome, HUMAN_FRAME } = await import("../map/canvas");
  expect(humanFrame({ kind: "idea" })).toEqual(HUMAN_FRAME.idea);
  expect(humanFrame({ kind: "idea", text: "短句" }).height).toBe(HUMAN_FRAME.idea.height);
  const long = "想加一个功能：按产品分别画出月度销售折线，并标注同比变化。".repeat(3);
  const grown = humanFrame({ kind: "idea", text: long });
  expect(grown.height).toBeGreaterThan(HUMAN_FRAME.idea.height);
  expect(cardTextLines({ kind: "idea", text: long })).toBeGreaterThan(3);
  expect(humanFrame({ kind: "idea", text: long.repeat(20) }).height).toBeLessThanOrEqual(1300);
  const area = { x: 50, y: 100, width: 1000, height: 600 };
  // Double-clicked just above the composer: the card opens inside the clear area.
  expect(clearOfChrome({ x: 500, y: 690 }, { width: 200, height: 120 }, area)).toEqual({ x: 500, y: 640 });
  expect(clearOfChrome({ x: 500, y: 300 }, { width: 200, height: 120 }, area)).toEqual({ x: 500, y: 300 });
});

it("lets a new card be a required step (必经步骤) before it is saved", async () => {
  act(() => wrap().props.onDoubleClick({ target: target(".react-flow__pane"), clientX: 1000, clientY: 500 }));
  const draft = human()[0];
  act(() => (draft.data.setDraftKind as (kind: "idea" | "step") => void)("step"));
  expect((human()[0].data.card as { kind: string }).kind).toBe("step");
  act(() => (human()[0].data.commit as (id: string, text: string) => void)(human()[0].id, "先复现 baseline"));
  await flush();
  expect(writes[0]).toMatchObject({ method: "POST", path: "/cards", body: { kind: "step", text: "先复现 baseline" } });
});

it("numbers steps along the gold path, shows where each stands and the tasks carrying it out", async () => {
  server = {
    ...structuredClone(EMPTY_CANVAS), revision: 3,
    cards: [
      { id: "hc-a", kind: "step", text: "读数据", attachment: null, checkpoint: false, created_ts: 1, updated_ts: 1 },
      { id: "hc-b", kind: "step", text: "画图给我看", attachment: null, checkpoint: true, created_ts: 2, updated_ts: 2 },
    ],
    links: [{ id: "hl-1", source: "hc-a", target: "hc-b", kind: "order", created_ts: 3 }],
    positions: { "hc-a": { x: 0, y: -900 }, "hc-b": { x: 1200, y: -900 } },
    route: { done: 1, total: 2, steps: [
      { id: "hc-a", number: 1, text: "读数据", checkpoint: false, after: [], status: "done", task_ids: ["parent"], question_item_id: "" },
      { id: "hc-b", number: 2, text: "画图给我看", checkpoint: true, after: ["hc-a"], status: "review", task_ids: ["child"], question_item_id: "q-1" },
    ] },
  };
  client.setQueryData(["map-canvas", "canvas"], server);
  remount({});
  await flush();
  const steps = human();
  expect(steps.map((n) => [(n.data.step as { number: number }).number, (n.data.step as { status: string }).status]))
    .toEqual([[1, "done"], [2, "review"]]);
  expect(steps[0].data.taskTitles).toEqual(["Route data"]);
  const path = flow().edges.find((e) => e.id === "hl-1")!;
  expect(path.data).toMatchObject({ kind: "order", path: true });
  const carries = flow().edges.filter((e) => e.type === "human" && e.data?.label === "执行");
  expect(carries.map((e) => e.source).sort()).toEqual(["hc-a", "hc-b"]);
  expect(renderer.root.findByProps({ "data-testid": "overview-steps" }).findByType("b").children.join("")).toBe("1/2");
});

it("keeps a request that started no task yet by the composer, and explains the cards in the map's key", async () => {
  remount({ data: { ...data, requests: [
    { id: "turn:web-1", text: "更正：按月汇总", full: "更正：按月汇总，再画图", ts: 5, status: "running" },
  ] } });
  await flush();
  const waiting = renderer.root.findByProps({ "data-testid": "map-loose-requests" });
  const chip = waiting.findByType("span");
  expect(chip.props.title).toBe("更正：按月汇总，再画图");
  expect(chip.children.join("")).toContain("你说：更正：按月汇总");
  // Requests are not cards: the flow holds the two task cards only.
  expect(flow().nodes.filter((n) => n.type === "task")).toHaveLength(2);
  const key = renderer.root.findByProps({ id: "map-legend-cards" });
  const text = (node: ReactTestInstance | string): string =>
    typeof node === "string" ? node : node.children.map(text).join("");
  expect(text(key)).toContain("任务卡 = 团队拆出来、能单独完成和验收的一件事");
});

it("keeps the team line, 暂停团队 and what waits on the person on the full canvas map", () => {
  const css = ["map.css", "design.css", "canvas.css"].map((name) => readFileSync(new URL(`../map/${name}`, import.meta.url), "utf8")).join("\n");
  const hidden = [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)]
    .filter(([, , body]) => /display:\s*none/.test(body))
    .flatMap(([, selectors]) => selectors.split(",").map((s) => s.replace(/\/\*[\s\S]*?\*\//g, "").trim()))
    .filter((s) => s.includes('[data-immersive="true"]'));
  expect(hidden.some((s) => /\.map-overview$/.test(s) || s.includes(".map-status-row") || s.includes(".map-attention-link"))).toBe(false);
  // The team line sits outside the overview's folded list.
  const next = { ...props, actions: { ...props.actions, teamStatus: <div data-testid="team-line"><button>暂停团队</button></div> } };
  act(() => renderer.update(<QueryClientProvider client={client}><MapCanvas {...next} immersive /></QueryClientProvider>));
  const overview = renderer.root.findByProps({ "data-testid": "map-overview" });
  expect(overview.findByProps({ "data-testid": "team-line" }).parent).toBe(overview);
});

it("says so when dependencies form a cycle", () => {
  remount({ data: { ...data, tasks: [
    { id: "a", title: "A", objective: "", status: "pending", deps: ["b"], ts: 1 },
    { id: "b", title: "B", objective: "", status: "pending", deps: ["a"], ts: 2 },
  ] } });
  expect(renderer.root.find((n) => n.props.className === "map-graph-warning").children).toEqual(["检测到循环依赖（红线）"]);
});
