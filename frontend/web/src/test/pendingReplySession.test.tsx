import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { usePendingReplySession } from '../usePendingReplySession';
import { api } from '../api';
import record from '../../../../packages/core/test/fixtures/pending-answer/record.json';

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

it('answers only the displayed question even when the next project opens', async () => {
  let finish!: (value: { resolved: boolean }) => void;
  vi.mocked(api.resolveDecision).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  act(() => { renderer = create(<Probe />); });
  let answer!: Promise<boolean>;
  act(() => { answer = session.answerPendingReply('  Use the small dataset  '); });
  expect(await session.answerPendingReply('duplicate')).toBe(false);
  act(() => renderer.update(<Probe sid="p-two" pending={[questions[1]]} />));
  await act(async () => { finish({ resolved: true }); expect(await answer).toBe(true); });
  expect(api.resolveDecision).toHaveBeenCalledExactlyOnceWith('p-one', 'reviewer-t1', 'custom', 'Use the small dataset', []);
  expect(notify).not.toHaveBeenCalled();
  expect(refetchSnapshot).toHaveBeenCalledOnce();
  expect(session.pendingReply.id).toBe('engineer-t1');
});

it('keeps API errors visible and never claims the answer was delivered', async () => {
  vi.mocked(api.resolveDecision).mockRejectedValue(new Error('Connection refused'));
  act(() => { renderer = create(<Probe />); });
  await act(async () => { expect(await session.answerPendingReply('Use UTF-8')).toBe(false); });
  expect(notify).toHaveBeenCalledWith('error', expect.any(String), expect.objectContaining({ message: 'Connection refused' }));
  expect(session.pendingReplyBusy).toBe(false);
  expect(await session.answerPendingReply('   ')).toBe(false);
  expect(api.resolveDecision).toHaveBeenCalledOnce();
});

it('reports a rejected answer and refreshes the pending question', async () => {
  vi.mocked(api.resolveDecision).mockResolvedValue({ resolved: false, reply: 'Question already answered' });
  act(() => { renderer = create(<Probe />); });
  await act(async () => { expect(await session.answerPendingReply('Use UTF-8')).toBe(false); });
  expect(notify).toHaveBeenCalledWith('error', expect.any(String), 'Question already answered');
  expect(refetchSnapshot).toHaveBeenCalledOnce();
});

it('waits for attachments and sends their receipts to the question captured before switching projects', async () => {
  let uploaded!: (value: typeof record.message.attachments) => void;
  const prepare = vi.fn(() => new Promise<typeof record.message.attachments>(resolve => { uploaded = resolve; }));
  vi.mocked(api.resolveDecision).mockResolvedValue({ resolved: true });
  act(() => { renderer = create(<Probe />); });
  let answer!: Promise<boolean>;
  act(() => { answer = session.answerPendingReply(record.message.text, prepare); });
  expect(session.pendingReplyBusy).toBe(true);
  expect(api.resolveDecision).not.toHaveBeenCalled();
  expect(await session.answerPendingReply('duplicate')).toBe(false);
  act(() => renderer.update(<Probe sid="p-two" pending={[questions[1]]} />));
  await act(async () => { uploaded(record.message.attachments); expect(await answer).toBe(true); });
  expect(api.resolveDecision).toHaveBeenCalledExactlyOnceWith('p-one', 'reviewer-t1', 'custom', record.message.text, record.message.attachments);
});

it('keeps an upload failure visible without resolving the question', async () => {
  act(() => { renderer = create(<Probe />); });
  await act(async () => {
    expect(await session.answerPendingReply(record.message.text, async () => { throw new Error('Upload failed'); })).toBe(false);
  });
  expect(api.resolveDecision).not.toHaveBeenCalled();
  expect(notify).toHaveBeenCalledWith('error', expect.any(String), expect.objectContaining({ message: 'Upload failed' }));
  expect(session.pendingReplyBusy).toBe(false);
});
