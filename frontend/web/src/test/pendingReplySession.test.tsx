import { act, create, type ReactTestRenderer } from "react-test-renderer";
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { usePendingReplySession } from "../usePendingReplySession";
import { api } from "../api";
import { PendingReplyDialog } from "../components/PendingReplyDialog";

vi.mock("../api", () => ({ api: { resolveDecision: vi.fn() } }));
vi.mock("../components/Modal", () => ({
  Modal: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <div>{children}</div> : null,
  ModalHeader: ({ title, sub }: { title: string; sub: string }) => <div>{title}{sub}</div>,
}));

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
  vi.mocked(api.resolveDecision).mockReset();
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

it("auto-opens and answers the current task instead of an older unresolved provider question", async () => {
  const pending = [
    { id: 'old', status: 'paused_operator', operator_decision: {
      id: 'decision-old', item_id: 'old', status: 'pending', title: 'Old planner question',
      question: 'Recorded 429: trial_tpm_exceeded',
    } },
    { id: 'current', status: 'paused_operator', operator_decision: {
      id: 'decision-current', item_id: 'current', status: 'pending', title: 'Submission facts',
      question: 'Supply real author details',
    } },
  ];
  const unchanged = JSON.stringify(pending);
  const refetch = vi.fn(async () => {});
  let session: ReturnType<typeof usePendingReplySession>;
  function CurrentProbe() {
    session = usePendingReplySession({
      activeSid: 's-1', currentTaskId: 'current', backlog: [], pendingQuestions: pending,
      notify: vi.fn(), refetchSnapshot: refetch,
    });
    return null;
  }
  let renderer: ReactTestRenderer;
  act(() => { renderer = create(<CurrentProbe />); });
  expect(session!.pendingReplyOpen).toBe(true);
  expect(session!.pendingReply?.id).toBe('decision-current');
  expect(api.resolveDecision).not.toHaveBeenCalled();
  vi.mocked(api.resolveDecision).mockResolvedValue({ resolved: true, reply: 'Recorded' });
  await act(async () => { await session!.answerPendingReply('custom', 'Author facts for this task'); });
  expect(api.resolveDecision).toHaveBeenCalledExactlyOnceWith('s-1', 'decision-current', 'custom', 'Author facts for this task');
  expect(JSON.stringify(pending)).toBe(unchanged);
  expect(refetch).toHaveBeenCalledOnce();
  act(() => renderer!.unmount());
});

const taskQuestions = ['a', 'b'].map(id => ({
  id, title: `Task ${id}`, status: 'paused_operator',
  operator_decision: {
    id: `decision-${id}`, item_id: id, status: 'pending',
    title: `Task ${id}`, question: `Question for task ${id}`,
  },
}));
let taskSession: ReturnType<typeof usePendingReplySession>;
function TaskProbe({ currentTaskId, autoOpen = true, activeSid = 's-1', loaded = true, questions = taskQuestions }: {
  currentTaskId: string;
  autoOpen?: boolean;
  activeSid?: string;
  loaded?: boolean;
  questions?: Array<Record<string, unknown>>;
}) {
  taskSession = usePendingReplySession({
    activeSid, currentTaskId, autoOpen, backlog: loaded ? [] : undefined,
    pendingQuestions: loaded ? questions : undefined,
    notify: () => {}, refetchSnapshot: async () => {},
  });
  return <PendingReplyDialog
    reply={taskSession.pendingReply} open={taskSession.pendingReplyOpen} busy={taskSession.pendingReplyBusy}
    onClose={() => taskSession.setPendingReplyOpen(false)} onSubmit={taskSession.answerPendingReply}
  />;
}

it('keeps the open question and draft when the current task changes, then opens the current task next', () => {
  let renderer: ReactTestRenderer;
  act(() => { renderer = create(<TaskProbe currentTaskId="a" autoOpen={false} />); });
  act(() => taskSession.setPendingReplyOpen(true));
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'My answer for task a' } }));

  act(() => renderer!.update(<TaskProbe currentTaskId="b" autoOpen={false} />));
  expect(taskSession.pendingReply?.id).toBe('decision-a');
  expect(taskSession.pendingReply?.is_current_task).toBe(false);
  expect(renderer!.root.findByType('textarea').props.value).toBe('My answer for task a');

  act(() => taskSession.setPendingReplyOpen(false));
  expect(taskSession.pendingReplyOpen).toBe(false);
  act(() => taskSession.setPendingReplyOpen(true));
  expect(taskSession.pendingReply?.id).toBe('decision-b');
  expect(renderer!.root.findByType('textarea').props.value).toBe('');
  act(() => renderer!.unmount());
});

it('finishes the displayed task before auto-opening the next current question, once per tab', async () => {
  let finish!: (result: Awaited<ReturnType<typeof api.resolveDecision>>) => void;
  vi.mocked(api.resolveDecision).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let renderer: ReactTestRenderer;
  act(() => { renderer = create(<TaskProbe currentTaskId="a" />); });
  let answer!: Promise<void>;
  act(() => { answer = taskSession.answerPendingReply('custom', 'Answer for a'); });
  act(() => renderer!.update(<TaskProbe currentTaskId="b" />));
  expect(taskSession.pendingReply?.id).toBe('decision-a');
  expect(taskSession.pendingReplyOpen).toBe(true);
  expect(taskSession.pendingReplyBusy).toBe(true);

  await act(async () => { finish({ resolved: true, reply: 'Recorded' }); await answer; });
  expect(api.resolveDecision).toHaveBeenCalledExactlyOnceWith('s-1', 'decision-a', 'custom', 'Answer for a');
  expect(taskSession.pendingReply?.id).toBe('decision-b');
  expect(taskSession.pendingReplyOpen).toBe(true);
  expect(taskSession.pendingReplyBusy).toBe(false);

  act(() => taskSession.setPendingReplyOpen(false));
  act(() => renderer!.update(<TaskProbe currentTaskId="b" />));
  expect(taskSession.pendingReplyOpen).toBe(false);
  act(() => renderer!.unmount());
  act(() => { renderer = create(<TaskProbe currentTaskId="b" />); });
  expect(taskSession.pendingReplyOpen).toBe(false);
  act(() => renderer!.unmount());
});

it('does not close another project’s newly opened dialog when the previous answer finishes', async () => {
  let finish!: (result: Awaited<ReturnType<typeof api.resolveDecision>>) => void;
  vi.mocked(api.resolveDecision).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  let renderer: ReactTestRenderer;
  act(() => { renderer = create(<TaskProbe currentTaskId="a" autoOpen={false} />); });
  act(() => taskSession.setPendingReplyOpen(true));
  let answer!: Promise<void>;
  act(() => { answer = taskSession.answerPendingReply('custom', 'Answer for a'); });

  act(() => renderer!.update(<TaskProbe currentTaskId="b" activeSid="s-2" autoOpen={false} />));
  expect(taskSession.pendingReplyOpen).toBe(false);
  act(() => taskSession.setPendingReplyOpen(true));
  expect(taskSession.pendingReply?.id).toBe('decision-b');

  await act(async () => { finish({ resolved: true, reply: 'Recorded' }); await answer; });
  expect(api.resolveDecision).toHaveBeenCalledExactlyOnceWith('s-1', 'decision-a', 'custom', 'Answer for a');
  expect(taskSession.pendingReply?.id).toBe('decision-b');
  expect(taskSession.pendingReplyOpen).toBe(true);
  act(() => renderer!.unmount());
});

it('keeps the open question and draft while snapshot data is temporarily unavailable', () => {
  let renderer: ReactTestRenderer;
  act(() => { renderer = create(<TaskProbe currentTaskId="a" autoOpen={false} />); });
  act(() => taskSession.setPendingReplyOpen(true));
  act(() => renderer!.root.findByType('textarea').props.onChange({ target: { value: 'Keep this draft' } }));

  act(() => renderer!.update(<TaskProbe currentTaskId="a" autoOpen={false} loaded={false} />));
  expect(taskSession.pendingReplyOpen).toBe(true);
  expect(taskSession.pendingReply?.id).toBe('decision-a');
  expect(renderer!.root.findByType('textarea').props.value).toBe('Keep this draft');
  act(() => renderer!.update(<TaskProbe currentTaskId="a" autoOpen={false} />));
  expect(renderer!.root.findByType('textarea').props.value).toBe('Keep this draft');
  act(() => renderer!.unmount());
});

it('closes a question removed by a loaded snapshot and uses the remaining task on the next open', () => {
  let renderer: ReactTestRenderer;
  act(() => { renderer = create(<TaskProbe currentTaskId="a" autoOpen={false} />); });
  act(() => taskSession.setPendingReplyOpen(true));

  act(() => renderer!.update(<TaskProbe currentTaskId="b" autoOpen={false} questions={[taskQuestions[1]]} />));
  expect(taskSession.pendingReplyOpen).toBe(false);
  expect(renderer!.root.findAllByType('textarea')).toHaveLength(0);
  act(() => taskSession.setPendingReplyOpen(true));
  expect(taskSession.pendingReply?.id).toBe('decision-b');

  act(() => renderer!.update(<TaskProbe currentTaskId="b" autoOpen={false} questions={[]} />));
  expect(taskSession.pendingReplyOpen).toBe(false);
  expect(taskSession.pendingReply).toBeNull();
  act(() => renderer!.unmount());
});
