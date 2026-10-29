import { createElement } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { expect, it, vi } from "vitest";
import { ChatBox } from "../components/ChatBox";

const listeners = { addEventListener() {}, removeEventListener() {} };
vi.stubGlobal("document", { ...listeners, activeElement: null });
vi.stubGlobal("window", { ...listeners, innerWidth: 800 });

it("sends a follow-up from the chat view while a reply is running instead of swallowing Enter", async () => {
  const onSend = vi.fn(async () => true);
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(createElement(ChatBox, {
      value: "Also cover the empty input", onChange: () => {}, onSend, 
      disabled: false, pending: true, attachments: [], onAttachmentsChange: () => {},
      completionSelection: 0, onCompletionSelectionChange: () => {},
    }));
  });
  await act(async () => {
    renderer.root.findByType("textarea").props.onKeyDown({
      key: "Enter", shiftKey: false, nativeEvent: {}, preventDefault() {}, stopPropagation() {}, defaultPrevented: true,
    });
  });
  expect(onSend).toHaveBeenCalledExactlyOnceWith("Also cover the empty input", [], undefined, { whileRunning: true });
});

it("offers a send button, and no stop, for a follow-up typed while a reply is running", async () => {
  const onSend = vi.fn(async () => true);
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(createElement(ChatBox, {
      value: "Also cover the empty input", onChange: () => {}, onSend,
      disabled: false, pending: true, attachments: [], onAttachmentsChange: () => {},
      completionSelection: 0, onCompletionSelectionChange: () => {},
    }));
  });
  const send = renderer.root.findByProps({ type: "submit", className: "map-send" });
  expect(send.props.disabled).toBe(false);
  await act(async () => { renderer.root.findByType("form").props.onSubmit({ preventDefault() {} }); });
  expect(onSend).toHaveBeenCalledExactlyOnceWith("Also cover the empty input", [], undefined, { whileRunning: true });
  expect(renderer.root.findAll(node => node.props["aria-label"] === "Stop")).toHaveLength(0);
});

it("shows the runtime line under the chat input, as the map composer does", () => {
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(createElement(ChatBox, {
      value: "", onChange: () => {}, onSend: async () => true, 
      disabled: false, pending: false, attachments: [], onAttachmentsChange: () => {},
      completionSelection: 0, onCompletionSelectionChange: () => {},
      footer: createElement("div", { "data-runtime-line": true }, "Copilot · model-a · high"),
    }));
  });
  expect(renderer.root.findByProps({ "data-runtime-line": true }).children).toEqual(["Copilot · model-a · high"]);
});
