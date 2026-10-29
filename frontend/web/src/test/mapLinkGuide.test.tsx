import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { LinkGuide } from "../map/CanvasNodes";

/** The connection React Flow reports while a line is drawn (null when none). */
let connection: { inProgress: true; fromNode: { id: string }; pointer: { x: number; y: number } } | { inProgress: false };
const cancelConnection = vi.fn();
vi.mock("@xyflow/react", async (original) => ({
  ...await original<typeof import("@xyflow/react")>(),
  useConnection: (select: (c: typeof connection) => unknown) => select(connection),
  useStoreApi: () => ({ getState: () => ({ cancelConnection, domNode: { getBoundingClientRect: () => ({ left: 100, top: 50 }) } }) }),
  ViewportPortal: ({ children }: { children: ReactNode }) => <div data-testid="portal">{children}</div>,
}));

const boxes = [
  { id: "hc-1", x: 0, y: 0, width: 1200, height: 480 },
  { id: "task-a", x: 2000, y: 100, width: 1440, height: 600 },
];
let keydown: ((e: KeyboardEvent) => void) | undefined;
let renderer: ReactTestRenderer;
beforeEach(() => {
  keydown = undefined;
  vi.stubGlobal("document", {
    addEventListener: (type: string, fn: (e: KeyboardEvent) => void) => { if (type === "keydown") keydown = fn; },
    removeEventListener: (type: string, fn: unknown) => { if (type === "keydown" && keydown === fn) keydown = undefined; },
  });
});
afterEach(() => {
  act(() => renderer?.unmount());
  vi.unstubAllGlobals();
  cancelConnection.mockReset();
});

const render = (targetAt: (source: string, point: { x: number; y: number }) => string | undefined, onCancel = vi.fn()) => {
  act(() => { renderer = create(<LinkGuide targetAt={targetAt} boxes={boxes} onCancel={onCancel} />); });
  return onCancel;
};
const outline = () => renderer.root.findAll((n) => n.props["data-testid"] === "canvas-link-target");

it("outlines the card a line would link to, at the pointer's screen point", () => {
  connection = { inProgress: true, fromNode: { id: "hc-1" }, pointer: { x: 400, y: 300 } };
  const targetAt = vi.fn(() => "task-a");
  render(targetAt);
  // The pointer is relative to the canvas; the drop is judged on the screen.
  expect(targetAt).toHaveBeenCalledWith("hc-1", { x: 500, y: 350 });
  expect(outline()).toHaveLength(1);
  expect(outline()[0].props["data-target"]).toBe("task-a");
  expect(outline()[0].props.style).toMatchObject({ transform: "translate(2000px, 100px)", width: 1440, height: 600 });
});

it("outlines nothing over empty canvas or when no line is drawn", () => {
  connection = { inProgress: true, fromNode: { id: "hc-1" }, pointer: { x: 10, y: 10 } };
  render(() => undefined);
  expect(outline()).toHaveLength(0);
  act(() => renderer.unmount());
  connection = { inProgress: false };
  render(() => "task-a");
  expect(outline()).toHaveLength(0);
  expect(keydown).toBeUndefined();
});

it("drops the line on Esc", () => {
  connection = { inProgress: true, fromNode: { id: "hc-1" }, pointer: { x: 400, y: 300 } };
  const onCancel = render(() => "task-a");
  const event = { key: "Escape", preventDefault: vi.fn(), stopPropagation: vi.fn() } as unknown as KeyboardEvent;
  keydown!({ ...event, key: "a" } as KeyboardEvent);
  expect(onCancel).not.toHaveBeenCalled();
  keydown!(event);
  expect(onCancel).toHaveBeenCalledOnce();
  expect(cancelConnection).toHaveBeenCalledOnce();
  // Another handler (the map's own Esc) does not also act on it.
  expect(event.stopPropagation).toHaveBeenCalled();
});
