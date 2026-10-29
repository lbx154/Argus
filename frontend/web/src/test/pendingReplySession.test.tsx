import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { usePendingReplySession } from '../usePendingReplySession';
import { api } from '../api';

vi.mock('../api', () => ({ api: { resolveDecision: vi.fn() } }));
const notify = vi.fn();
const refetchSnapshot = vi.fn(async () => {});
const questions = ['reviewer', 'engineer'].map(role => ({ id: 't1', operator_decision: {
  id: `${role}-t1`, item_id: 't1', status: 'pending', title: 'Recorded task', question: `${role}'s question`, options: [],
} }));
let session: ReturnType<typeof usePendingReplySession>;
let renderer: ReactTestRenderer;
function Probe({ sid = 'p-one', pending = questions }: { sid?: string; pending?: typeof questions }) {
  session = usePendingReplySession({ activeSid: sid, pendingQuestions: pending,
    backlog: [], notify, refetchSnapshot });
  return null;
}
afterEach(() => { act(() => renderer?.unmount()); vi.restoreAllMocks(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it('loads a question without session storage or an opened-dialog state', () => {
  const storage = { getItem: vi.fn(), setItem: vi.fn() };
  vi.stubGlobal('window', { sessionStorage: storage });
  act(() => { renderer = create(<Probe />); });
  expect(session.pendingReply.id).toBe('reviewer-t1');
  expect(session).not.toHaveProperty('pendingReplyOpen');
  expect(storage.getItem).not.toHaveBeenCalled();
  expect(storage.setItem).not.toHaveBeenCalled();
});

it('delivers once to the displayed asker and names that role even when the next project opens', async () => {
  let finish!: (value: { resolved: boolean }) => void;
  vi.mocked(api.resolveDecision).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  act(() => { renderer = create(<Probe />); });
  let answer!: Promise<boolean>;
  act(() => { answer = session.answerPendingReply('  Use the small dataset  '); });
  expect(await session.answerPendingReply('duplicate')).toBe(false);
  act(() => renderer.update(<Probe sid="p-two" pending={[questions[1]]} />));
  await act(async () => { finish({ resolved: true }); expect(await answer).toBe(true); });
  expect(api.resolveDecision).toHaveBeenCalledExactlyOnceWith('p-one', 'reviewer-t1', 'custom', 'Use the small dataset');
  expect(notify).toHaveBeenCalledWith('success', 'Answer delivered to the Reviewer.');
  expect(refetchSnapshot).toHaveBeenCalledOnce();
  expect(session.pendingReply.id).toBe('engineer-t1');
});

it('keeps API errors visible and never claims the answer was delivered', async () => {
  vi.mocked(api.resolveDecision).mockRejectedValue(new Error('Connection refused'));
  act(() => { renderer = create(<Probe />); });
  await act(async () => { expect(await session.answerPendingReply('Use UTF-8')).toBe(false); });
  expect(session.pendingReplyError).toBeTruthy();
  expect(session.pendingReplyBusy).toBe(false);
  expect(notify).not.toHaveBeenCalled();
  expect(await session.answerPendingReply('   ')).toBe(false);
  expect(api.resolveDecision).toHaveBeenCalledOnce();
});

it('keeps the answer draft through a view rerender and clears it when its question changes', () => {
  act(() => { renderer = create(<Probe />); });
  act(() => session.setPendingReplyNote('Keep this answer while I inspect the map'));
  act(() => renderer.update(<Probe />));
  expect(session.pendingReplyNote).toBe('Keep this answer while I inspect the map');
  act(() => renderer.update(<Probe pending={[questions[1]]} />));
  expect(session.pendingReplyNote).toBe('');
});
