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
  onCancel: () => {},
  focusSignal: 0,
  sessionName: "Study",
  historical: false,
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

it("keeps Stop and offers a separate send arrow while a turn is running", () => {
  let renderer!: ReactTestRenderer;
  act(() => { renderer = create(createElement(MapComposer, base)); });
  const labels = renderer.root.findAllByType("button").map((node) => node.props["aria-label"]);
  expect(labels).toContain("Stop reply");
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
