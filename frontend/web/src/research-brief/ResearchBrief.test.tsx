import type { ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { Modal } from '../components/Modal';
import { MarkdownContent } from '../components/MarkdownContent';
import { splitDraft, type MapCopy } from '../map/presentation';
import { ReaderExplanation } from './ReaderExplanation';
import ResearchBrief, { type ResearchBriefProps } from './ResearchBrief';
import { briefCopyKey, briefLiveKey, briefSelection, currentBriefData } from './model';
import { completedCopy, inputs, source } from './testFixtures';

vi.mock('../components/Modal', () => ({
  Modal: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <div role="dialog">{children}</div> : null,
  ModalHeader: ({ title }: { title: string }) => <h2>{title}</h2>,
}));

let renderer: ReactTestRenderer | undefined;
let client: QueryClient;
let props: ResearchBriefProps;
const copyKey = () => briefCopyKey(props.sid, 'en-US');
const liveKey = () => briefLiveKey(props.sid, briefSelection(props.snapshot, props.view));
const tree = () => <QueryClientProvider client={client}><ResearchBrief {...props} /></QueryClientProvider>;
const text = () => JSON.stringify(renderer!.toJSON());
const button = (label: string) => renderer!.root.findAllByType('button').find(node => node.children.includes(label))!;
const reading = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
const body = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-body' });
async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(25); }); }
async function mount() { await act(async () => { renderer = create(tree()); }); await flush(); }
async function open() { act(() => button('Read explanation').props.onClick()); await flush(); }
function noEvidenceShells() {
  for (const label of ['View evidence', 'Source record', 'Original record', 'Currently loaded task and step record',
    '查看依据', '来源记录', '原始记录', 'data-reader-task-facts', 'data-evidence-', 'reader-evidence']) {
    expect(text()).not.toContain(label);
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  props = inputs();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(copyKey(), completedCopy(source.tasks[0], ['start-a', 'main-a']));
  client.setQueryData(liveKey(), currentBriefData(source, props.sid, 'a'));
  vi.spyOn(api, 'liveMap').mockResolvedValue(source);
  vi.spyOn(api, 'mapCopy').mockResolvedValue({ cards: {}, relations: [], available: true, version: 14 });
  vi.spyOn(api, 'generateMapCopy').mockRejectedValue(new Error('Unexpected automatic explanation'));
});
afterEach(() => {
  act(() => renderer?.unmount()); renderer = undefined;
  client.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});

it('keeps the useful task objective once without any source disclosure when no explanation exists', async () => {
  const goal = 'Compare latency for the same batch size.';
  props.active = false;
  props.view.mission = { ...props.view.mission, title: goal, objective: goal };
  client.setQueryData(liveKey(), currentBriefData({ ...source, tasks: [{ ...source.tasks[0], title: goal, objective: goal }] }, props.sid, 'a'));
  client.setQueryData(copyKey(), { cards: {}, relations: [], available: false, version: 14 });
  await mount();
  expect(body().findAllByType(MarkdownContent).map(node => node.props.children)).toEqual([goal]);
  expect(text()).toContain('Explanations are temporarily unavailable');
  noEvidenceShells();
});

it('retains the full teaching explanation, scope, conditions and artifact actions without raw-record chrome', async () => {
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  const paragraphs = ['Explain the whole research question. '.repeat(40), 'Define the unresolved part. '.repeat(15)];
  copy.cards.a.reader_brief = { ...copy.cards.a.reader_brief!, why: paragraphs.join('\n\n') };
  client.setQueryData(copyKey(), copy);
  await mount();
  const explanation = body().findByType(ReaderExplanation);
  expect(explanation.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(paragraphs.join('\n\n'));
  const headings = explanation.findAllByType('h3').map(node => node.children.join(''));
  expect(headings[0]).toBe('Why this step helps');
  expect(headings.findIndex(value => value.startsWith('One useful concept'))).toBeLessThan(headings.indexOf('What this does and does not establish'));
  expect(text()).toContain('the general problem remains open');
  const details = explanation.findByType('details');
  expect(details.props.open).toBeUndefined();
  expect(details.findByType(MarkdownContent).props.children).toBe(copy.cards.a.detail);
  noEvidenceShells();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
});

it('uses the selected card learning path in the shared reader without generating again', async () => {
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  copy.cards.a.learning_path = { question: 'Current teaching question', steps: [{ title: 'Meaning',
    explanation: 'Definition', example: 'Illustration', check: { question: 'Check', answer: 'Answer' } }] };
  client.setQueryData(copyKey(), copy);
  await mount(); await open();
  expect(reading().findByType(ReaderExplanation).props.learningPath).toEqual(copy.cards.a.learning_path);
  noEvidenceShells();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
});

it('retains stale text and its timestamp until explicitly opened, even when the daemon is stopped', async () => {
  const copy = completedCopy(source.tasks[0], ['start-a'], 1789216023);
  copy.cards.a.title = 'Earlier explanation';
  client.setQueryData(copyKey(), copy);
  props.snapshot.daemon = { ...props.snapshot.daemon, alive: false, pid: null };
  let finish!: (value: MapCopy) => void;
  vi.mocked(api.generateMapCopy).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await mount();
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(api.generateMapCopy).not.toHaveBeenCalled();
  expect(body().findByType(ReaderExplanation).props.brief).toEqual(copy.cards.a.reader_brief);
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-status' }).findByType('time').props.dateTime).toBe('2026-09-12T12:27:03.000Z');
  await open();
  expect(api.generateMapCopy).toHaveBeenCalledTimes(1);
  expect(api.generateMapCopy).toHaveBeenCalledWith('project', props.sid, {
    cards: [{ key: 'a', task_id: 'a', kind: 'task', event_ids: ['start-a', 'main-a'] }], locale: 'en-US',
  }, undefined, props.sid, null, expect.any(Function));
  expect(reading().findByType(ReaderExplanation).props.brief).toEqual(copy.cards.a.reader_brief);
  await act(async () => finish(completedCopy(source.tasks[0], ['start-a', 'main-a'], 1789216024)));
  await flush();
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(api.generateMapCopy).toHaveBeenCalledTimes(1);
  noEvidenceShells();
});

it('does not generate on compact mount or reference insertion, and read-only opening remains read-only', async () => {
  props = { ...props, compact: true, onAsk: vi.fn() };
  client.setQueryData(copyKey(), completedCopy(source.tasks[0], ['start-a']));
  await mount();
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-body' })).toHaveLength(0);
  act(() => button('Reference task').props.onClick());
  expect(props.onAsk).toHaveBeenCalledTimes(1);
  expect(splitDraft(vi.mocked(props.onAsk!).mock.calls[0][0])).toMatchObject({
    text: '', refs: [{ source: 'live:s-research', task_id: 'a', event_ids: ['start-a', 'main-a'] }],
  });
  props = { ...props, readOnly: true }; act(() => renderer!.update(tree()));
  await open();
  expect(reading().findByType(ReaderExplanation).props.brief).toBeTruthy();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
  noEvidenceShells();
});

it('preserves the full current blocking question independently of stale generated text', async () => {
  const question = 'Please choose the input and state its limits. '.repeat(40);
  client.setQueryData(liveKey(), currentBriefData({ ...source, tasks: [{ ...source.tasks[0], pending_question: question }] }, props.sid, 'a'));
  props.active = false;
  await mount();
  expect(renderer!.root.findByProps({ 'data-reader-pending-question': 'a' }).findByType(MarkdownContent).props.children).toBe(question.trim());
  await open();
  expect(reading().findByProps({ 'data-reader-pending-question': 'a' }).findByType(MarkdownContent).props.children).toBe(question.trim());
  noEvidenceShells();
});

it('keeps an opened task pinned through background task switches and a late result without generating the new task', async () => {
  const earlier = completedCopy(source.tasks[0], ['start-a']);
  const other = completedCopy(source.tasks[1], ['review-b']);
  client.setQueryData(copyKey(), { ...earlier, cards: { ...earlier.cards, ...other.cards } });
  let finish!: (value: MapCopy) => void;
  vi.mocked(api.generateMapCopy).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await mount();
  expect(api.generateMapCopy).not.toHaveBeenCalled();
  await open();
  await act(async () => { vi.mocked(api.generateMapCopy).mock.calls[0][6]?.('planning'); }); await flush();
  props = inputs('b');
  client.setQueryData(liveKey(), currentBriefData(source, props.sid, 'b'));
  act(() => renderer!.update(tree())); await flush();
  expect(reading().props['data-task-id']).toBe('a');
  expect(reading().findByProps({ 'data-explanation-phase': 'planning' })).toBeTruthy();
  expect(body().findByType(ReaderExplanation).props.brief).toEqual(other.cards.b.reader_brief);
  const complete = completedCopy(source.tasks[0], ['start-a', 'main-a'], 30);
  await act(async () => finish(complete)); await flush();
  expect(reading().findByType(ReaderExplanation).props.brief).toEqual(complete.cards.a.reader_brief);
  expect(api.generateMapCopy).toHaveBeenCalledTimes(1);
  props = { ...props, sid: 'other', active: false };
  act(() => renderer!.update(tree()));
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-reading' })).toHaveLength(0);
});

it('does not lend another task the retained title, text or pending question', async () => {
  await mount();
  props = inputs('b'); props.active = false;
  client.setQueryData(liveKey(), currentBriefData(source, props.sid, 'b'));
  act(() => renderer!.update(tree()));
  const displayed = body().findAllByType(MarkdownContent).map(node => node.props.children);
  expect(displayed).toContain(source.tasks[1].title);
  expect(displayed).not.toContain(completedCopy(source.tasks[0], []).cards.a.reader_brief!.why);
  expect(renderer!.root.findAllByProps({ 'data-reader-pending-question': 'a' })).toHaveLength(0);
});

it('keeps an opened reader in its captured preview and observes only its generation phases', async () => {
  vi.stubGlobal('window', { location: { search: '?reader_preview=source-first' } });
  const previous = completedCopy(source.tasks[0], ['start-a']);
  const alternate = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  alternate.cards.a.detail = 'Learning-path content';
  client.setQueryData(copyKey(), previous);
  client.setQueryData(briefCopyKey(props.sid, 'en-US', 'learning-path'), alternate);
  let finish!: (value: MapCopy) => void;
  vi.mocked(api.generateMapCopy).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await mount(); await open();
  vi.stubGlobal('window', { location: { search: '?reader_preview=learning-path' } });
  act(() => renderer!.update(tree()));
  await act(async () => { vi.mocked(api.generateMapCopy).mock.calls[0][6]?.('writing'); }); await flush();
  expect(reading().findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  expect(body().findByType(ReaderExplanation).props.detail).toBe(alternate.cards.a.detail);
  expect(reading().findByType(ReaderExplanation).props.detail).toBe(previous.cards.a.detail);
  expect(vi.mocked(api.generateMapCopy).mock.calls[0][5]).toBe('source-first');
  await act(async () => finish(completedCopy(source.tasks[0], ['start-a', 'main-a'], 30))); await flush();
  expect(api.generateMapCopy).toHaveBeenCalledTimes(1);
});

it.each(['teaching', 'reading'])('keeps useful teaching and follow-up when the %s review is unavailable', async mode => {
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  copy.cards.a.teaching_review = mode === 'teaching'
    ? { status: 'unavailable', kind: 'model_teaching_review', reason: 'internal_failure_code', reviewed_at: null, review_version: 1 }
    : { status: 'accepted', kind: 'model_teaching_review', review_version: 2, reading_review: { status: 'unavailable', kind: 'model_readability_review' } };
  if (mode === 'teaching') copy.cards.a.reader_brief = { ...copy.cards.a.reader_brief!, concept: null };
  client.setQueryData(copyKey(), copy); props.onAsk = vi.fn();
  await mount();
  expect(text()).toContain(mode === 'teaching' ? 'You can keep asking about this step' : 'The reading explanation still needs checking');
  expect(text()).toContain('the general problem remains open');
  expect(button('Reference task')).toBeTruthy();
  expect(text()).not.toContain('internal_failure_code');
  noEvidenceShells();
});

it('shows read failures without hiding useful task goals or starting generation', async () => {
  client.removeQueries({ queryKey: liveKey() }); client.removeQueries({ queryKey: copyKey() });
  vi.mocked(api.liveMap).mockRejectedValue(new Error('Record service unavailable'));
  await mount(); await flush();
  expect(text()).toContain(source.tasks[0].objective);
  expect(text()).toContain('Task records could not be refreshed');
  expect(api.generateMapCopy).not.toHaveBeenCalled();
});

it('does not retry a failed opened explanation automatically after closing and reopening', async () => {
  client.setQueryData(copyKey(), completedCopy(source.tasks[0], ['start-a']));
  await mount(); await open(); await flush();
  expect(api.generateMapCopy).toHaveBeenCalledTimes(1);
  expect(reading().findAllByProps({ role: 'status' }).length).toBeGreaterThan(0);
  act(() => renderer!.root.findAllByType(Modal).find(node => node.props.open)!.props.onClose());
  expect(button('Retry')).toBeUndefined();
  await open();
  await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
  expect(api.generateMapCopy).toHaveBeenCalledTimes(1);
  expect(reading().findAllByType('button').some(node => node.children.includes('Retry'))).toBe(true);
});
