import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePendingReplySession } from "../usePendingReplySession";

vi.mock("../api", () => ({ api: {} }));

const question = {
  id: "decision-task-1",
  item_id: "task-1",
  title: "Choose an experiment",
  question: "Continue with the local baseline?",
  options: [{ id: "local", label: "Run the local baseline" }],
};

let opened: boolean | null = null;
function Probe({ autoOpen }: { autoOpen?: boolean }) {
  const { pendingReplyOpen } = usePendingReplySession({
    activeSid: "s-1",
    autoOpen,
    backlog: [],
    notify: () => {},
    pendingQuestions: [question],
    refetchSnapshot: async () => {},
  });
  opened = pendingReplyOpen;
  return null;
}

const storage = new Map<string, string>();
beforeEach(() => {
  storage.clear();
  opened = null;
  vi.stubGlobal("window", {
    sessionStorage: {
      getItem: (key: string) => storage.get(key) ?? null,
      setItem: (key: string, value: string) => void storage.set(key, value),
    },
  });
});
afterEach(() => vi.unstubAllGlobals());

const mount = (autoOpen?: boolean) => {
  let renderer: ReactTestRenderer | undefined;
  act(() => {
    renderer = create(<Probe autoOpen={autoOpen} />);
  });
  return renderer!;
};

it("surfaces a newly seen decision once", () => {
  const renderer = mount();
  expect(opened).toBe(true);
  act(() => renderer.unmount());
});

it("stays quiet when the view opts out of auto-open", () => {
  const renderer = mount(false);
  expect(opened).toBe(false);
  expect([...storage.keys()]).toEqual([]);
  act(() => renderer.unmount());
});

it("does not re-open the same decision after a reload", () => {
  const first = mount();
  expect(opened).toBe(true);
  act(() => first.unmount());

  const second = mount();
  expect(opened).toBe(false);
  act(() => second.unmount());
});
