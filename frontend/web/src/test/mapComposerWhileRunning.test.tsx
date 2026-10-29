import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { expect, it, vi } from "vitest";
import { MapComposer, type MapComposerProps } from "../map/MapComposer";

// The composer listens on document/window; this suite has no DOM.
const listeners = { addEventListener() {}, removeEventListener() {} };
vi.stubGlobal("document", { ...listeners, activeElement: null });
vi.stubGlobal("window", { ...listeners, innerWidth: 800 });

const base: MapComposerProps = {
  value: "Also cover the empty input",
  onChange: () => {},
  onSend: async () => true,
  attachments: [],
  onAttachmentsChange: () => {},
  pending: true,
  focusSignal: 0,

  zh: false,
};

const enter = async (renderer: ReactTestRenderer) => {
  await act(async () => {
    renderer.root.findByType("textarea").props.onKeyDown({
      key: "Enter", shiftKey: false, nativeEvent: {}, preventDefault() {}, stopPropagation() {},
    });
  });
};

it("sends a message typed while Argus is working instead of swallowing Enter", async () => {
  const onSend = vi.fn(async () => true);
  let renderer!: ReactTestRenderer;
  act(() => { renderer = create(createElement(MapComposer, { ...base, onSend })); });
  await enter(renderer);
  expect(onSend).toHaveBeenCalledExactlyOnceWith("Also cover the empty input", [], undefined, { whileRunning: true });
  const caption = renderer.root.findAll((node) => node.props.role === "status")[0];
  expect(JSON.stringify(caption.children)).toContain("Queued");
});

it("offers a send arrow, and no stop of its own, while a turn is running", () => {
  let renderer!: ReactTestRenderer;
  act(() => { renderer = create(createElement(MapComposer, base)); });
  const labels = renderer.root.findAllByType("button").map((node) => node.props["aria-label"]);
  expect(labels.filter((label) => /stop/i.test(String(label)))).toEqual([]);
  expect(labels).toContain("Send while Argus works");
});

it("does not queue a second message until the first one is confirmed", async () => {
  let release!: (accepted: boolean) => void;
  const onSend = vi.fn(() => new Promise<boolean>((resolve) => { release = resolve; }));
  let renderer!: ReactTestRenderer;
  act(() => { renderer = create(createElement(MapComposer, { ...base, onSend })); });
  void enter(renderer);
  await enter(renderer);
  expect(onSend).toHaveBeenCalledTimes(1);
  await act(async () => { release(true); });
});

it('disables sending by button and Enter throughout a rewrite', async () => {
  const onSend = vi.fn(async () => true);
  let renderer!: ReactTestRenderer;
  act(() => { renderer = create(createElement(MapComposer, { value: "draft", onChange: () => {}, attachments: [], onAttachmentsChange: () => {}, pending: false, focusSignal: 0, zh: false, onSend, rewriting: true })); });
  expect(renderer.root.findAllByType('button').find(node => node.props.type === 'submit')!.props.disabled).toBe(true);
  await act(async () => {
    renderer.root.findByType('textarea').props.onKeyDown({ key: 'Enter', shiftKey: false, nativeEvent: {}, preventDefault() {}, stopPropagation() {}, defaultPrevented: false });
  });
  expect(onSend).not.toHaveBeenCalled();
  act(() => renderer.unmount());
});
