import { useState } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MapComposer, type MapComposerProps } from "../map/MapComposer";
import { referenceText, type CardReference } from "../map/presentation";

let renderer: ReactTestRenderer | undefined;
let listeners: Map<string, (event: unknown) => void>;
let inputNode: { style: Record<string, string>; scrollHeight: number; focus: ReturnType<typeof vi.fn>; blur: ReturnType<typeof vi.fn> };
const defaults: MapComposerProps = {
  value: "", onChange: () => {}, onSend: async () => false,
  attachments: [], onAttachmentsChange: () => {}, pending: false,
  onCancel: () => {}, focusSignal: 0, sessionName: "Demo", historical: false, zh: false,
};
const island = () => renderer!.root.findByProps({ className: "map-composer-dock map-island-dock" });
const textarea = () => renderer!.root.findByType("textarea");
const launch = () => renderer!.root.findByProps({ className: "map-island-launch" });
const key = (name: string, composing = false) => ({
  key: name, keyCode: composing ? 229 : name === "Enter" ? 13 : 27,
  nativeEvent: { isComposing: composing }, shiftKey: false,
  preventDefault: vi.fn(), stopPropagation: vi.fn(),
});
const hotkey = (overrides: Record<string, unknown> = {}) => ({
  key: "c", metaKey: false, ctrlKey: false, altKey: false, isComposing: false,
  defaultPrevented: false, target: { tagName: "DIV" }, preventDefault: vi.fn(),
  ...overrides,
});
const outside = { closest: () => null };
const ref: CardReference = { source: "map", task_id: "t1", task_title: "Coastal study", event_ids: [] };
const nodeMock = (element: { type: unknown }) => {
  if (element.type === "textarea") return inputNode;
  if (element.type === "div") return { contains: (node: unknown) => node === inputNode };
  return null;
};

beforeEach(() => {
  listeners = new Map();
  const doc = {
    activeElement: null as unknown,
    addEventListener: (type: string, listener: (event: unknown) => void) => listeners.set(type, listener),
    removeEventListener: (type: string) => listeners.delete(type),
  };
  inputNode = {
    style: {}, scrollHeight: 44,
    focus: vi.fn(() => { doc.activeElement = inputNode; }),
    blur: vi.fn(() => { doc.activeElement = null; listeners.get("focusout")?.({ type: "focusout", relatedTarget: null }); }),
  };
  vi.stubGlobal("document", doc);
  vi.stubGlobal("window", { addEventListener: vi.fn(), removeEventListener: vi.fn() });
});
afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  vi.unstubAllGlobals();
});

it("keeps an empty map as an island until activation, then focuses synchronously and closes with Escape", () => {
  act(() => { renderer = create(<MapComposer {...defaults} />, { createNodeMock: nodeMock }); });
  expect(island().props["data-compact"]).toBe(true);
  act(() => listeners.get("focusin")?.({ type: "focusin", target: { closest: () => true } }));
  expect(island().props["data-compact"]).toBe(true);

  act(() => launch().props.onClick());
  expect(inputNode.focus).toHaveBeenCalledOnce();
  expect(island().props["data-compact"]).toBe(false);
  act(() => textarea().props.onKeyDown(key("Escape", true)));
  expect(island().props["data-compact"]).toBe(false);
  act(() => textarea().props.onKeyDown(key("Escape")));
  expect(island().props["data-compact"]).toBe(true);
});

it("collapses an accepted send into live status and allows another draft while waiting", async () => {
  function Harness() {
    const [value, setValue] = useState("Make a city map");
    const [pending, setPending] = useState(false);
    return <MapComposer {...defaults} value={value} onChange={setValue} pending={pending} onCancel={() => setPending(false)}
      pendingLabel="Engineer · building the map" onSend={async () => { setValue(""); setPending(true); return true; }} />;
  }
  act(() => { renderer = create(<Harness />, { createNodeMock: nodeMock }); });
  await act(async () => textarea().props.onKeyDown(key("Enter")));
  expect(island().props["data-compact"]).toBe(true);
  expect(island().props["data-state"]).toBe("working");
  expect(renderer!.root.findByType("small").children).toEqual(["Engineer · building the map"]);
  expect(renderer!.root.findByProps({ className: "map-island-stop" })).toBeDefined();

  act(() => launch().props.onClick());
  act(() => textarea().props.onChange({ target: { value: "Use blue for the river" } }));
  act(() => listeners.get("focusout")?.({ type: "focusout", relatedTarget: null }));
  expect(island().props["data-compact"]).toBe(false);
  expect(textarea().props.value).toBe("Use blue for the river");
  const preventDefault = vi.fn();
  act(() => renderer!.root.findByProps({ className: "map-send is-pending" }).props.onClick({ preventDefault }));
  expect(preventDefault).toHaveBeenCalledOnce();
  expect(island().props["data-pending"]).toBe(false);
  expect(textarea().props.value).toBe("Use blue for the river");
});

it("does not send IME candidate confirmation or lose the draft on failure", async () => {
  const send = vi.fn(async () => false);
  act(() => { renderer = create(<MapComposer {...defaults} value="做一个城市地图" onSend={send} />, { createNodeMock: nodeMock }); });
  await act(async () => textarea().props.onKeyDown(key("Enter", true)));
  expect(send).not.toHaveBeenCalled();
  await act(async () => textarea().props.onKeyDown({ ...key("Enter"), shiftKey: true }));
  expect(send).not.toHaveBeenCalled();
  await act(async () => textarea().props.onKeyDown(key("Enter")));
  expect(send).toHaveBeenCalledExactlyOnceWith("做一个城市地图", []);
  act(() => renderer!.update(<MapComposer {...defaults} value="做一个城市地图" dispatchStatus="error" onSend={send} />));
  expect(island().props["data-compact"]).toBe(false);
  expect(island().props["data-state"]).toBe("error");
  expect(textarea().props.value).toBe("做一个城市地图");
});

it("does not blur or hide a newer draft when an earlier send is acknowledged", async () => {
  let accept!: (value: boolean) => void;
  const response = new Promise<boolean>((resolve) => { accept = resolve; });
  function Harness() {
    const [value, setValue] = useState("First goal");
    return <MapComposer {...defaults} value={value} onChange={setValue} onSend={() => response} />;
  }
  act(() => { renderer = create(<Harness />, { createNodeMock: nodeMock }); });
  act(() => textarea().props.onFocus());
  act(() => textarea().props.onKeyDown(key("Enter")));
  act(() => textarea().props.onChange({ target: { value: "A newer draft" } }));
  await act(async () => accept(true));
  expect(textarea().props.value).toBe("A newer draft");
  expect(island().props["data-compact"]).toBe(false);
  expect(inputNode.blur).not.toHaveBeenCalled();
});

it("keeps attachments and routing available in the expanded editor", () => {
  const route = vi.fn();
  act(() => { renderer = create(<MapComposer {...defaults} attachments={[new File(["test"], "report.txt", { type: "text/plain" })]}
    routeOverride="auto" onRouteOverrideChange={route} />, { createNodeMock: nodeMock }); });
  expect(island().props["data-compact"]).toBe(false);
  act(() => renderer!.root.findByType("select").props.onChange({ target: { value: "task" } }));
  expect(route).toHaveBeenCalledWith("task");
  expect(renderer!.root.findByProps({ "aria-label": "remove attachment report.txt" })).toBeDefined();
});

it("stays a pill when the camera focuses a card; only the pill click opens it", () => {
  act(() => { renderer = create(<MapComposer {...defaults} overview />, { createNodeMock: nodeMock }); });
  expect(island().props["data-compact"]).toBe(true);
  // Clicking a task card flips the camera to detail view (overview=false).
  // That is navigation, not composing intent — the pill must not expand.
  act(() => renderer!.update(<MapComposer {...defaults} overview={false} />));
  expect(island().props["data-compact"]).toBe(true);
  expect(inputNode.focus).not.toHaveBeenCalled();
  // Hover is not intent either: the dock no longer expands on pointer enter.
  expect(island().props.onPointerEnter).toBeUndefined();
  act(() => launch().props.onClick());
  expect(island().props["data-compact"]).toBe(false);
});

it("auto-opens when a reference chip is quoted in from the context menu", () => {
  act(() => { renderer = create(<MapComposer {...defaults} />, { createNodeMock: nodeMock }); });
  expect(island().props["data-compact"]).toBe(true);
  act(() => renderer!.update(<MapComposer {...defaults} value={referenceText(ref)} />));
  expect(island().props["data-compact"]).toBe(false);
  expect(inputNode.focus).toHaveBeenCalled();
  expect(renderer!.root.findByProps({ className: "map-reference-chips" })).toBeDefined();
  // Escape with an empty textarea folds the island; the chip stays in the
  // draft and is shown again on the next expand.
  const onChange = vi.fn();
  act(() => renderer!.update(<MapComposer {...defaults} value={referenceText(ref)} onChange={onChange} />));
  act(() => textarea().props.onKeyDown(key("Escape")));
  expect(island().props["data-compact"]).toBe(true);
  expect(onChange).not.toHaveBeenCalled();
  act(() => launch().props.onClick());
  expect(renderer!.root.findByProps({ className: "map-reference-chips" })).toBeDefined();
});

it("expands with the c key unless the user is typing elsewhere", () => {
  act(() => { renderer = create(<MapComposer {...defaults} />, { createNodeMock: nodeMock }); });
  const ignored = [hotkey({ target: { tagName: "INPUT" } }), hotkey({ metaKey: true }),
    hotkey({ isComposing: true }), hotkey({ key: "x" })];
  for (const event of ignored) {
    act(() => listeners.get("keydown")?.(event));
    expect(island().props["data-compact"]).toBe(true);
  }
  const open = hotkey();
  act(() => listeners.get("keydown")?.(open));
  expect(island().props["data-compact"]).toBe(false);
  expect(open.preventDefault).toHaveBeenCalledOnce();
  expect(inputNode.focus).toHaveBeenCalledOnce();
});

it("expands on a fresh composer-focus request but ignores a stale one after remount", () => {
  // A session switch remounts the composer with whatever signal count the app
  // reached earlier; that history is not an ask to open the editor.
  act(() => { renderer = create(<MapComposer {...defaults} focusSignal={2} />, { createNodeMock: nodeMock }); });
  expect(island().props["data-compact"]).toBe(true);
  expect(inputNode.focus).not.toHaveBeenCalled();
  // "/", ⌘J, the palette, and prompt rewrite bump the signal — explicit asks.
  act(() => renderer!.update(<MapComposer {...defaults} focusSignal={3} />));
  expect(island().props["data-compact"]).toBe(false);
  expect(inputNode.focus).toHaveBeenCalledOnce();
});

it("collapses on an outside click only while empty; a draft keeps it open", () => {
  function Harness() {
    const [value, setValue] = useState("");
    return <MapComposer {...defaults} value={value} onChange={setValue} />;
  }
  act(() => { renderer = create(<Harness />, { createNodeMock: nodeMock }); });
  act(() => launch().props.onClick());
  expect(island().props["data-compact"]).toBe(false);
  // Empty editor: a tap on the canvas folds it away.
  act(() => listeners.get("pointerdown")?.({ target: outside }));
  expect(island().props["data-compact"]).toBe(true);
  // With typed text, neither outside clicks nor blur may hide the draft.
  act(() => launch().props.onClick());
  act(() => textarea().props.onChange({ target: { value: "Compare both baselines" } }));
  act(() => listeners.get("pointerdown")?.({ target: outside }));
  act(() => listeners.get("focusout")?.({ type: "focusout", relatedTarget: null }));
  expect(island().props["data-compact"]).toBe(false);
  expect(textarea().props.value).toBe("Compare both baselines");
});

it("preserves the draft across an explicit collapse and reopen", () => {
  function Harness() {
    const [value, setValue] = useState("");
    return <MapComposer {...defaults} value={value} onChange={setValue} />;
  }
  act(() => { renderer = create(<Harness />, { createNodeMock: nodeMock }); });
  act(() => launch().props.onClick());
  act(() => textarea().props.onChange({ target: { value: "Keep this draft" } }));
  // The chevron is the one explicit way to fold a panel that holds a draft.
  act(() => renderer!.root.findByProps({ className: "map-island-collapse" }).props.onClick());
  expect(island().props["data-compact"]).toBe(true);
  expect(renderer!.root.findByType("small").children.join("")).toContain("Draft saved");
  act(() => launch().props.onClick());
  expect(island().props["data-compact"]).toBe(false);
  expect(textarea().props.value).toBe("Keep this draft");
});
