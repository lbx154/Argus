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
      value: "Also cover the empty input", onChange: () => {}, onSend, onCancel: () => {},
      disabled: false, pending: true, attachments: [], onAttachmentsChange: () => {},
      slashSelection: 0, onSlashSelectionChange: () => {},
    }));
  });
  await act(async () => {
    renderer.root.findByType("textarea").props.onKeyDown({
      key: "Enter", shiftKey: false, nativeEvent: {}, preventDefault() {}, stopPropagation() {}, defaultPrevented: true,
    });
  });
  expect(onSend).toHaveBeenCalledExactlyOnceWith("Also cover the empty input", [], undefined, { whileRunning: true });
});

it("shows the runtime line under the chat input, as the map composer does", () => {
  let renderer!: ReactTestRenderer;
  act(() => {
    renderer = create(createElement(ChatBox, {
      value: "", onChange: () => {}, onSend: async () => true, onCancel: () => {},
      disabled: false, pending: false, attachments: [], onAttachmentsChange: () => {},
      slashSelection: 0, onSlashSelectionChange: () => {},
      footer: createElement("div", { "data-runtime-line": true }, "Copilot · model-a · high"),
    }));
  });
  expect(renderer.root.findByProps({ "data-runtime-line": true }).children).toEqual(["Copilot · model-a · high"]);
});
