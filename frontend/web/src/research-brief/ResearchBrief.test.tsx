import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { splitDraft } from '../map/presentation';
import { MarkdownContent } from '../components/MarkdownContent';
import type { Dataset } from '../map/model';
import type { MapCopy } from '../map/presentation';
import { ReaderEvidence } from './ReaderEvidence';
import { ReaderExplanation } from './ReaderExplanation';
import type { ReactNode } from 'react';
import ResearchBrief from './ResearchBrief';
import { briefCopyKey, briefLiveKey, briefSelection, currentBriefData } from './model';
import { completedCopy, inputs, source } from './testFixtures';

vi.mock('../components/Modal', () => ({
  Modal: ({ open, children }: { open: boolean; children: ReactNode }) => open ? <div role="dialog">{children}</div> : null,
  ModalHeader: ({ title }: { title: string }) => <h2>{title}</h2>,
}));

let renderer: ReactTestRenderer | undefined;
let client: QueryClient | undefined;
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers(); });

function cachedClient() {
  const props = inputs();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(briefCopyKey(props.sid, 'en-US'), completedCopy(source.tasks[0], ['start-a', 'main-a']));
  client.setQueryData(briefLiveKey(props.sid, briefSelection(props.snapshot, props.view)), currentBriefData(source, props.sid, 'a'));
  return client;
}

it('keeps MissionView fallback status out of both current and selected-reader facts when the actual task is not loaded', () => {
  const props = inputs(), queryClient = cachedClient();
  props.view.mission.status = 'complete';
  queryClient.removeQueries({ queryKey: briefLiveKey(props.sid, briefSelection(props.snapshot, props.view)) });
  const generate = vi.spyOn(api, 'generateMapCopy'), load = vi.spyOn(api, 'liveMap');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>); });
  const body = renderer!.root.findByProps({ 'data-testid': 'research-brief-body' });
  expect(body.findByProps({ 'data-reader-task-facts': 'a' }).findAllByProps({ 'data-reader-fact-state': 'current' })).toHaveLength(0);
  const open = renderer!.root.findAllByType('button').find(node => node.children.includes('Read explanation'))!;
  act(() => open.props.onClick());
  const facts = renderer!.root.findByProps({ role: 'dialog' }).findByProps({ 'data-reader-task-facts': 'a' });
  expect(facts.findAllByProps({ 'data-reader-fact-state': 'current' })).toHaveLength(0);
  expect(facts.findAllByType('p').some(node => node.children.includes('A verifiable task status has not been loaded.'))).toBe(true);
  expect(load).not.toHaveBeenCalled();
  expect(generate).not.toHaveBeenCalled();
});

it('reads from the full problem through definition, example and connection before scope and next steps', () => {
  const props = inputs(), queryClient = cachedClient();
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  const paragraphs = ['先理解原始问题的条件与目标。'.repeat(40), '再说明当前任务怎样缩小仍未解决的范围。'.repeat(15)];
  copy.cards.a.reader_brief = { ...copy.cards.a.reader_brief!, why: paragraphs.join('\n\n') };
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), copy);
  const markup = renderToStaticMarkup(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>);
  expect(markup).toContain('Why this step helps');
  for (const paragraph of paragraphs) expect(markup).toContain(`${paragraph}</p>`);
  expect(markup).not.toContain('line-clamp-3');
  expect(markup).toContain('Counterexample');
  expect(markup).toContain('the general problem remains open');
  expect(markup).toContain('Background explanations are not research progress');
  const readingOrder = ['Why this step helps', 'Definition', 'Example', 'Connection to this step', 'What this does and does not establish', 'How the explanation interprets the follow-up', 'Detailed explanation and conditions', 'Recorded task status', 'Recorded results and follow-up'];
  for (const [index, heading] of readingOrder.entries()) {
    expect(markup).toContain(heading);
    if (index) expect(markup.indexOf(readingOrder[index - 1])).toBeLessThan(markup.indexOf(heading));
  }
  expect(markup.indexOf(copy.cards.a.reader_brief!.concept!.connection)).toBeLessThan(markup.indexOf('<details'));
  expect(markup).toContain('View evidence');
  expect(markup).toContain('A result was reported');
  expect(markup).not.toContain('data-evidence-json=');
  expect(markup).toContain('data-testid="research-brief-footer"');
});

it('uses the current card’s learning path in the shared reader while retaining evidence and scope', () => {
  const props = inputs(), queryClient = cachedClient();
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  copy.cards.a.learning_path = { question: 'Current task teaching question', steps: [{ title: 'Current task meaning',
    explanation: 'Current task explanation', example: 'Current task illustration',
    check: { question: 'Current task check', answer: 'Current task answer' } }] };
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), copy);
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>); });
  const explanation = renderer!.root.findByType(ReaderExplanation);
  expect(explanation.props.learningPath).toEqual(copy.cards.a.learning_path);
  expect(explanation.findByProps({ 'data-reader-learning-path': 'a' }).findAllByType('li')).toHaveLength(1);
  expect(explanation.findAllByType(MarkdownContent).map(node => node.props.children)).toContain(copy.cards.a.reader_brief!.scope);
  act(() => renderer!.root.findAllByType('button').find(node => node.children.includes('View evidence'))!.props.onClick());
  expect(renderer!.root.findByType(ReaderEvidence).props.selection.taskId).toBe('a');
  expect(generate).not.toHaveBeenCalled();
});

it('opens readable source text before the folded original JSON without generating an explanation', () => {
  const props = inputs(), queryClient = cachedClient();
  const data = currentBriefData(source, props.sid, 'a');
  data.events = data.events.map(event => event.id === 'main-a' ? { ...event, text: 'SUMMARY=Checked the stated conditions.\nDecision: continue\nMILESTONE_STATUS=pending\nNEXT_ACTION=Check the remaining case.' } : event);
  queryClient.setQueryData(briefLiveKey(props.sid, briefSelection(props.snapshot, props.view)), data);
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>); });
  const open = renderer!.root.findAllByType('button').find(item => item.children.includes('View evidence'))!;
  act(() => open.props.onClick());
  const record = renderer!.root.findByProps({ 'data-event-id': 'main-a' });
  expect(record.findByType(MarkdownContent).props.children).toBe(data.events.find(event => event.id === 'main-a')!.text);
  expect(record.findByType('time').props.dateTime).toBe('1970-01-01T00:00:04.000Z');
  expect(record.findByType('code').children).toContain('round.main.completed');
  expect(record.findByType('details').props.open).toBeUndefined();
  expect(record.findByType('pre').children.join('')).toContain('MILESTONE_STATUS=pending');
  expect(JSON.stringify(renderer!.toJSON())).toContain(source.tasks[0].objective);
  expect(generate).not.toHaveBeenCalled();
});

it('retains the same explanation title and generation time when new evidence makes it stale, while exposing original records', () => {
  const props = inputs(), queryClient = cachedClient();
  const copy = completedCopy(source.tasks[0], ['start-a'], 1789216023);
  copy.cards.a.title = '先检查这个有限情形的边界';
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), copy);
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>); });
  const body = renderer!.root.findByProps({ 'data-testid': 'research-brief-body' });
  const displayed = body.findAllByType(MarkdownContent).map(item => item.props.children);
  expect(displayed[0]).toBe(copy.cards.a.title);
  expect(displayed).toContain(copy.cards.a.reader_brief!.why);
  const explanation = body.findByType(ReaderExplanation);
  expect(explanation.props.detail).toBe(copy.cards.a.detail);
  const details = explanation.findAllByType('details').filter(node => node.findByType('summary').children.includes('Detailed explanation and conditions'));
  expect(details).toHaveLength(1);
  expect(details[0].props.open).toBeUndefined();
  expect(details[0].findByType(MarkdownContent).props.children).toBe(copy.cards.a.detail);
  const status = renderer!.root.findByProps({ 'data-testid': 'research-brief-status' });
  expect(status.findAllByType('span').some(item => item.children.includes('Previous explanation · '))).toBe(true);
  expect(status.findByType('time').props.dateTime).toBe('2026-09-12T12:27:03.000Z');
  expect(status.findAllByType('span').some(item => item.children.includes(' · update pending'))).toBe(true);

  const open = renderer!.root.findAllByType('button').find(item => item.children.includes('View evidence'))!;
  act(() => open.props.onClick());
  const dialog = renderer!.root.findByProps({ role: 'dialog' });
  expect(dialog.findAllByType(MarkdownContent).map(item => item.props.children)).toContain(source.tasks[0].objective);
  expect(dialog.findByProps({ 'data-event-id': 'main-a' }).findByType('pre').children.join('')).toContain('A result was reported');
  expect(generate).not.toHaveBeenCalled();
});

it('does not carry another task’s retained title or explanation across a task switch', () => {
  const props = inputs(), queryClient = cachedClient();
  const copy = completedCopy(source.tasks[0], ['start-a']);
  copy.cards.a.title = '只属于任务甲的中文说明';
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), copy);
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>); });
  const next = inputs('b');
  queryClient.setQueryData(briefLiveKey(next.sid, briefSelection(next.snapshot, next.view)), currentBriefData(source, next.sid, 'b'));
  act(() => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...next} active={false} readOnly /></QueryClientProvider>));
  const body = renderer!.root.findByProps({ 'data-testid': 'research-brief-body' });
  const displayed = body.findAllByType(MarkdownContent).map(item => item.props.children);
  expect(displayed).toContain(source.tasks[1].title);
  expect(displayed).not.toContain(copy.cards.a.title);
  expect(displayed).not.toContain(copy.cards.a.reader_brief!.why);
  expect(displayed).not.toContain(copy.cards.a.detail);
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-status' })).toHaveLength(0);
});

it('explicitly references a task in the research composer without asking or generating', () => {
  const props = inputs(), queryClient = cachedClient(), onAsk = vi.fn();
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} onAsk={onAsk} /></QueryClientProvider>); });
  const button = renderer!.root.findAllByType('button').find(item => item.children.includes('Reference task'))!;
  act(() => button.props.onClick());
  expect(onAsk).toHaveBeenCalledTimes(1);
  expect(splitDraft(onAsk.mock.calls[0][0]).text).toBe('');
  expect(splitDraft(onAsk.mock.calls[0][0]).refs[0]).toMatchObject({ source: 'live:s-research', task_id: 'a', event_ids: ['start-a', 'main-a'] });
  expect(generate).not.toHaveBeenCalled();
});

it('opens the existing explanation from compact chrome while retaining source and follow-up actions', () => {
  const props = inputs(), queryClient = cachedClient(), onAsk = vi.fn();
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), completedCopy(source.tasks[0], ['start-a']));
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} compact onAsk={onAsk} /></QueryClientProvider>); });
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-body' })).toHaveLength(0);
  const footer = renderer!.root.findByProps({ 'data-testid': 'research-brief-footer' });
  expect(footer.findAllByType('button')).toHaveLength(3);
  expect(footer.findAllByType('button').some(button => button.children.includes('View evidence'))).toBe(true);
  const read = footer.findAllByType('button').find(button => button.children.includes('Read explanation'))!;
  act(() => read.props.onClick());
  const reading = renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
  const headings = reading.findAllByType('h3').map(heading => heading.children.join(''));
  expect(headings.indexOf('Why this step helps')).toBeLessThan(headings.findIndex(heading => heading.startsWith('One useful concept')));
  expect(headings.findIndex(heading => heading.startsWith('One useful concept'))).toBeLessThan(headings.indexOf('What this does and does not establish'));
  expect(reading.findAllByType(MarkdownContent).map(item => item.props.children).join('\n')).toContain('the general problem remains open');
  const details = reading.findAllByType('details').find(node => node.findByType('summary').children.includes('Detailed explanation and conditions'))!;
  expect(details.props.open).toBeUndefined();
  expect(details.findByType(MarkdownContent).props.children).toBe(completedCopy(source.tasks[0], ['start-a']).cards.a.detail);
  expect(reading.findAllByType('span').some(item => item.children.join('').includes('update pending'))).toBe(true);
  expect(onAsk).not.toHaveBeenCalled();
  expect(generate).not.toHaveBeenCalled();
});

it('keeps a desktop reader on its selected task through a background task switch and the original pending generation', async () => {
  vi.useFakeTimers();
  const props = inputs(), next = inputs('b'), queryClient = cachedClient();
  const previous = completedCopy(source.tasks[0], ['start-a'], 7);
  previous.cards.a.reader_brief = { ...previous.cards.a.reader_brief!, why: 'Earlier explanation of task A.' };
  const other = completedCopy(source.tasks[1], ['review-b'], 20);
  other.cards.b.reader_brief = { ...other.cards.b.reader_brief!, why: 'Current explanation of task B.' };
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), { ...previous, cards: { ...previous.cards, ...other.cards } });
  queryClient.setQueryData(briefLiveKey(next.sid, briefSelection(next.snapshot, next.view)), currentBriefData(source, next.sid, 'b'));
  let finish!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const live = vi.spyOn(api, 'liveMap'), copyRead = vi.spyOn(api, 'mapCopy');
  await act(async () => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(generate).toHaveBeenCalledTimes(1);
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  act(() => renderer!.root.findAllByType('button').find(node => node.children.includes('Read explanation'))!.props.onClick());
  const reading = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
  expect(reading().props['data-task-id']).toBe('a');
  expect(reading().findByType(ReaderExplanation).props.brief.why).toBe('Earlier explanation of task A.');
  expect(reading().findByProps({ 'data-explanation-phase': 'planning' })).toBeTruthy();

  await act(async () => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...next} /></QueryClientProvider>));
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(reading().props['data-task-id']).toBe('a');
  expect(reading().findByType(ReaderEvidence).props.selection.taskId).toBe('a');
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-body' }).findByType(ReaderExplanation).props.brief.why).toBe('Current explanation of task B.');
  await act(async () => { generate.mock.calls[0][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief' })
    .findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(generate).toHaveBeenCalledTimes(1);

  const completed = completedCopy(source.tasks[0], ['start-a', 'main-a'], 30);
  completed.cards.a.reader_brief = { ...completed.cards.a.reader_brief!, why: 'The completed explanation of task A.' };
  completed.cards.a.source_snapshot = { version: 2, card_key: 'a', task_id: 'a', captured_at: 6,
    task: { ...source.tasks[0] }, source_ids: ['start-a', 'main-a'],
    events: source.events.filter(event => ['start-a', 'main-a'].includes(event.id)).map(event => ({ ...event })) };
  await act(async () => finish(completed));
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findByType(ReaderExplanation).props.brief.why).toBe('The completed explanation of task A.');
  expect(reading().findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  const retained = reading().findByType(ReaderEvidence).props.selection;
  expect(retained.taskId).toBe('a');
  expect(retained.snapshot).toEqual(completed.cards.a.source_snapshot);
  expect(retained.used.map((row: { id: string }) => row.id)).toEqual(['start-a', 'main-a']);
  expect(reading().findAllByType(MarkdownContent).map(node => node.props.children).join('\n')).not.toContain('Other task accepted');
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-body' }).findByType(ReaderExplanation).props.brief.why).toBe('Current explanation of task B.');
  expect(generate).toHaveBeenCalledTimes(1);
  expect(live).not.toHaveBeenCalled();
  expect(copyRead).not.toHaveBeenCalled();

  const otherProject = { ...next, sid: 's-other', active: false,
    snapshot: { ...next.snapshot, session: { ...next.snapshot.session, id: 's-other' } } };
  act(() => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...otherProject} /></QueryClientProvider>));
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-reading' })).toHaveLength(0);
  act(() => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...next} active={false} /></QueryClientProvider>));
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-reading' })).toHaveLength(0);
});

it('keeps observed phases with the opened reader’s preview while the same task uses another mode in the panel', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { location: { search: '?reader_preview=source-first' } });
  const props = inputs(), queryClient = cachedClient();
  const oldMode = completedCopy(source.tasks[0], ['start-a']);
  oldMode.cards.a.reader_brief = { ...oldMode.cards.a.reader_brief!, why: 'Previous source-first explanation.' };
  const newMode = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  newMode.cards.a.reader_brief = { ...newMode.cards.a.reader_brief!, why: 'Separate learning-path explanation.' };
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US', 'source-first'), oldMode);
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US', 'learning-path'), newMode);
  let finish!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await act(async () => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(generate.mock.calls[0][5]).toBe('source-first');
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  act(() => renderer!.root.findAllByType('button').find(node => node.children.includes('Read explanation'))!.props.onClick());

  vi.stubGlobal('window', { location: { search: '?reader_preview=learning-path' } });
  act(() => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...props} /></QueryClientProvider>));
  await act(async () => { generate.mock.calls[0][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  const reading = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
  expect(reading().findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  expect(reading().findByType(ReaderExplanation).props.brief.why).toBe('Previous source-first explanation.');
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-body' }).findByType(ReaderExplanation).props.brief.why).toBe('Separate learning-path explanation.');
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief' })
    .findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(generate).toHaveBeenCalledTimes(1);

  const complete = completedCopy(source.tasks[0], ['start-a', 'main-a'], 30);
  complete.cards.a.reader_brief = { ...complete.cards.a.reader_brief!, why: 'Finished source-first explanation.' };
  await act(async () => { finish(complete); await vi.advanceTimersByTimeAsync(25); });
  await act(async () => { generate.mock.calls[0][6]?.('reviewing'); await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(reading().findByType(ReaderExplanation).props.brief.why).toBe('Finished source-first explanation.');
  expect(queryClient.getQueryData<MapCopy>(briefCopyKey(props.sid, 'en-US', 'learning-path'))?.cards.a.reader_brief?.why).toBe('Separate learning-path explanation.');
  expect(generate).toHaveBeenCalledTimes(1);
});

it('shows phases in compact chrome while keeping an opened task A reader independent of the current task B', async () => {
  vi.useFakeTimers();
  const props = inputs(), next = inputs('b'), queryClient = cachedClient();
  const previous = completedCopy(source.tasks[0], ['start-a']);
  const other = completedCopy(source.tasks[1], ['review-b']);
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), { ...previous, cards: { ...previous.cards, ...other.cards } });
  queryClient.setQueryData(briefLiveKey(next.sid, briefSelection(next.snapshot, next.view)), currentBriefData(source, next.sid, 'b'));
  let finish!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await act(async () => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} compact /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-footer' })
    .findByProps({ 'data-explanation-phase': 'planning' })).toBeTruthy();
  act(() => renderer!.root.findAllByType('button').find(node => node.children.includes('Read explanation'))!.props.onClick());
  await act(async () => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...next} compact /></QueryClientProvider>));
  await act(async () => { generate.mock.calls[0][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });

  const panel = renderer!.root.findByProps({ 'data-testid': 'research-brief' });
  const reading = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
  expect(panel.props['data-task-id']).toBe('b');
  expect(panel.findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(reading().props['data-task-id']).toBe('a');
  expect(reading().findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  expect(generate).toHaveBeenCalledTimes(1);

  await act(async () => { finish(completedCopy(source.tasks[0], ['start-a', 'main-a'], 30)); await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-body' })).toHaveLength(0);
  expect(generate).toHaveBeenCalledTimes(1);
});

it('keeps a pinned reader’s phase with its original attempt when the same task starts another attempt', async () => {
  vi.useFakeTimers();
  const props = inputs(), queryClient = cachedClient();
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), completedCopy(source.tasks[0], ['start-a']));
  const nextTask = { ...source.tasks[0], started_ts: 100, attempt: 2, revision: 'a2' };
  const nextSource: Dataset = { ...source, tasks: [nextTask, source.tasks[1]], events: [...source.events,
    { id: 'start-a-2', item_id: 'a', type: 'life.mission.started', ts: 100, text: '', revision: 'start-a-2' },
    { id: 'main-a-2', item_id: 'a', type: 'round.main.completed', ts: 102, text: 'A new attempt result', revision: 'main-a-2' },
  ] };
  const next = { ...props,
    view: { ...props.view, mission: { ...props.view.mission, started_at: 100 } },
    snapshot: { ...props.snapshot, backlog: [{ ...props.snapshot.backlog[0], started_ts: 100 }] },
  };
  queryClient.setQueryData(briefLiveKey(next.sid, briefSelection(next.snapshot, next.view)), currentBriefData(nextSource, next.sid, 'a'));
  let finishOriginal!: (copy: MapCopy) => void, finishNext!: (copy: MapCopy) => void;
  const generate = vi.spyOn(api, 'generateMapCopy')
    .mockReturnValueOnce(new Promise(resolve => { finishOriginal = resolve; }))
    .mockReturnValueOnce(new Promise(resolve => { finishNext = resolve; }));
  await act(async () => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(generate).toHaveBeenCalledTimes(1);
  await act(async () => { generate.mock.calls[0][6]?.('planning'); await vi.advanceTimersByTimeAsync(25); });
  act(() => renderer!.root.findAllByType('button').find(node => node.children.includes('Read explanation'))!.props.onClick());
  const reading = () => renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' });
  const panel = () => renderer!.root.findByProps({ 'data-testid': 'research-brief' });

  await act(async () => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...next} /></QueryClientProvider>));
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(generate).toHaveBeenCalledTimes(2);
  expect(generate.mock.calls.map(call => call[2].cards[0].event_ids)).toEqual([
    ['start-a', 'main-a'], ['start-a-2', 'main-a-2'],
  ]);
  await act(async () => { generate.mock.calls[1][6]?.('writing'); await vi.advanceTimersByTimeAsync(25); });
  expect(panel().props['data-task-id']).toBe('a');
  expect(reading().props['data-task-id']).toBe('a');
  expect(panel().findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  expect(reading().findByProps({ 'data-explanation-phase': 'planning' })).toBeTruthy();
  await act(async () => { generate.mock.calls[0][6]?.('reviewing'); await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findByProps({ 'data-explanation-phase': 'reviewing' })).toBeTruthy();
  expect(panel().findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();

  await act(async () => { finishOriginal(completedCopy(source.tasks[0], ['start-a', 'main-a'], 20)); await vi.advanceTimersByTimeAsync(25); });
  expect(reading().findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(panel().findByProps({ 'data-explanation-phase': 'writing' })).toBeTruthy();
  await act(async () => { finishNext(completedCopy(nextTask, ['start-a-2', 'main-a-2'], 30)); await vi.advanceTimersByTimeAsync(25); });
  expect(panel().findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(reading().findAll(node => node.props['data-explanation-phase'] !== undefined)).toHaveLength(0);
  expect(generate).toHaveBeenCalledTimes(2);
});

it('keeps an opened reader in its captured cache mode while the current panel changes mode', async () => {
  vi.useFakeTimers();
  vi.stubGlobal('window', { location: { search: '?reader_preview=source-first' } });
  const props = inputs(), queryClient = cachedClient();
  const oldMode = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  oldMode.cards.a.reader_brief = { ...oldMode.cards.a.reader_brief!, why: 'Source-first retained content.' };
  const newMode = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  newMode.cards.a.reader_brief = { ...newMode.cards.a.reader_brief!, why: 'Learning-path retained content.' };
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US', 'source-first'), oldMode);
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US', 'learning-path'), newMode);
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>); });
  act(() => renderer!.root.findAllByType('button').find(node => node.children.includes('Read explanation'))!.props.onClick());
  vi.stubGlobal('window', { location: { search: '?reader_preview=learning-path' } });
  act(() => renderer!.update(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>));
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-reading' }).findByType(ReaderExplanation).props.brief.why).toBe('Source-first retained content.');
  expect(renderer!.root.findByProps({ 'data-testid': 'research-brief-body' }).findByType(ReaderExplanation).props.brief.why).toBe('Learning-path retained content.');
  expect(generate).not.toHaveBeenCalled();
});

it('keeps evidence and follow-up available when the concept explanation is unavailable', () => {
  const props = inputs(), queryClient = cachedClient();
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  copy.cards.a.reader_brief = { ...copy.cards.a.reader_brief!, concept: null };
  copy.cards.a.teaching_review = { status: 'unavailable', kind: 'model_teaching_review', reason: 'internal_failure_code', reviewed_at: null, review_version: 1 };
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), copy);
  const markup = renderToStaticMarkup(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} onAsk={() => {}} /></QueryClientProvider>);
  expect(markup).toContain('You can keep asking about this step');
  expect(markup).toContain('View evidence');
  expect(markup).toContain('Reference task');
  expect(markup).not.toContain('internal_failure_code');
  expect(markup).not.toContain('Counterexample');
  expect(markup).not.toContain('data-reader-teaching');
  expect(markup).toContain('the general problem remains open');
  expect(markup).toContain('How the explanation interprets the follow-up');
  expect(markup).toContain('Recorded results and follow-up');
});

it('keeps task facts and a usable concept when the separate reading check is unavailable', () => {
  const props = inputs(), queryClient = cachedClient();
  const copy = completedCopy(source.tasks[0], ['start-a', 'main-a']);
  copy.cards.a.teaching_review = { status: 'accepted', kind: 'model_teaching_review', review_version: 2,
    reading_review: { status: 'unavailable', kind: 'model_readability_review' } };
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), copy);
  const markup = renderToStaticMarkup(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} onAsk={() => {}} /></QueryClientProvider>);
  expect(markup).toContain('The reading explanation still needs checking');
  expect(markup).toContain('the general problem remains open');
  expect(markup).toContain('Counterexample');
  expect(markup).toContain('View evidence');
  expect(markup).toContain('Reference task');
});

it('contains a record-read failure inside the card and leaves the original goal and neighboring content visible', async () => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  vi.spyOn(api, 'liveMap').mockRejectedValue(new Error('Record service unavailable'));
  vi.spyOn(api, 'mapCopy').mockResolvedValue({ cards: {}, relations: [], available: true, version: 10 });
  const generate = vi.spyOn(api, 'generateMapCopy');
  await act(async () => { renderer = create(<QueryClientProvider client={client!}><p>Other page content</p><ResearchBrief {...inputs()} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  const rendered = JSON.stringify(renderer!.toJSON());
  expect(rendered).toContain('Other page content');
  expect(rendered).toContain(source.tasks[0].objective);
  expect(rendered).toContain('Records could not be refreshed');
  expect(generate).not.toHaveBeenCalled();
});

it('keeps retained source excerpts and same-version originals distinct while a newer explanation is pending', async () => {
  vi.useFakeTimers();
  const props = inputs(), queryClient = cachedClient(), onAsk = vi.fn();
  const copy = completedCopy(source.tasks[0], ['main-a'], 7);
  const taskMaterial = { title: source.tasks[0].title, objective: source.tasks[0].objective,
    acceptance_check: 'Retained condition', acceptance_check_truncated: true };
  const eventMaterial = { id: 'main-a', item_id: 'a', revision: 'main-a1', type: 'round.main.completed', ts: 4,
    text: 'Retained result excerpt', text_truncated: true, next_action: 'Retained next action' };
  copy.cards.a.source_snapshot = { version: 1, card_key: 'a', task_id: 'a', captured_at: 6,
    task: taskMaterial, events: [eventMaterial], source_ids: ['main-a'] };
  const loaded = currentBriefData(source, props.sid, 'a');
  loaded.tasks = loaded.tasks.map(task => ({ ...task, acceptance_check: 'Complete condition and excluded cases' }));
  loaded.events = loaded.events.map(event => event.id === 'main-a' ? { ...event, text: 'Complete result including the final exception' } : event);
  const liveKey = briefLiveKey(props.sid, briefSelection(props.snapshot, props.view));
  queryClient.setQueryData(liveKey, loaded);
  queryClient.setQueryData(briefCopyKey(props.sid, 'en-US'), copy);
  const read = vi.spyOn(api, 'liveMap'), generate = vi.spyOn(api, 'generateMapCopy').mockReturnValue(new Promise<MapCopy>(() => {}));
  await act(async () => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} onAsk={onAsk} /></QueryClientProvider>); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  act(() => renderer!.root.findAllByType('button').find(item => item.children.includes('View evidence'))!.props.onClick());
  const evidence = () => renderer!.root.findByType(ReaderEvidence);
  const used = () => evidence().findByProps({ 'data-evidence-group': 'used' });
  const raw = (node: ReturnType<typeof used>, kind: string) => JSON.parse(node.findByProps({ 'data-evidence-json': kind }).children.join(''));
  expect(raw(used().findByProps({ 'data-evidence-task': 'used' }), 'task')).toEqual(taskMaterial);
  expect(raw(used().findByProps({ 'data-evidence-task': 'used' }), 'full-record')).toEqual(loaded.tasks[0]);
  expect(raw(used().findByProps({ 'data-event-id': 'main-a' }), 'excerpt')).toEqual(eventMaterial);
  expect(raw(used().findByProps({ 'data-event-id': 'main-a' }), 'full-record')).toEqual(loaded.events.find(event => event.id === 'main-a'));
  expect(evidence().findAllByProps({ 'data-evidence-full-record': true })).toHaveLength(2);
  expect(generate).toHaveBeenCalledTimes(1);

  act(() => { queryClient.setQueryData<Dataset>(liveKey, previous => ({ ...previous!,
    tasks: previous!.tasks.map(task => ({ ...task, revision: 'a2', content_revision: 'new-goal', objective: 'Latest task goal' })),
    events: [...previous!.events.map(event => event.id === 'main-a' ? { ...event, revision: 'main-a2', text: 'Updated result with a different condition', ts: 8 } : event),
      { id: 'review-a', item_id: 'a', revision: 'review-a1', type: 'round.review.completed', text: 'New semantic review', ts: 9 }],
  })); });
  await act(async () => { await vi.advanceTimersByTimeAsync(25); });
  expect(raw(used().findByProps({ 'data-evidence-task': 'used' }), 'task')).toEqual(taskMaterial);
  expect(raw(used().findByProps({ 'data-event-id': 'main-a' }), 'excerpt')).toEqual(eventMaterial);
  expect(used().findAllByProps({ 'data-evidence-full-record': true })).toHaveLength(0);
  const current = evidence().findByProps({ 'data-evidence-group': 'current' });
  expect(raw(current.findByProps({ 'data-event-id': 'main-a' }), 'event').text).toBe('Updated result with a different condition');
  expect(raw(current.findByProps({ 'data-event-id': 'review-a' }), 'event').text).toBe('New semantic review');
  expect(raw(current.findByProps({ 'data-evidence-task': 'current' }), 'task').objective).toBe('Latest task goal');
  expect(used().findByProps({ 'data-evidence-captured-at': true }).props.dateTime).toBe('1970-01-01T00:00:06.000Z');
  expect(evidence().findByProps({ 'data-evidence-summary': true }).findAllByProps({ 'data-evidence-captured-at': true })).toHaveLength(0);
  const ask = renderer!.root.findAllByType('button').find(item => item.children.includes('Reference task'))!;
  act(() => ask.props.onClick());
  expect(splitDraft(onAsk.mock.calls[0][0]).refs[0]).toMatchObject({ task_id: 'a', event_ids: ['start-a', 'main-a', 'review-a'] });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(read).not.toHaveBeenCalled();
});
