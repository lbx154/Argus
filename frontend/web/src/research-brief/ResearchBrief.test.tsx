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
afterEach(() => { act(() => renderer?.unmount()); renderer = undefined; client?.clear(); vi.restoreAllMocks(); vi.useRealTimers(); });

function cachedClient() {
  const props = inputs();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
  client.setQueryData(briefCopyKey(props.sid, 'en-US'), completedCopy(source.tasks[0], ['start-a', 'main-a']));
  client.setQueryData(briefLiveKey(props.sid, briefSelection(props.snapshot, props.view)), currentBriefData(source, props.sid, 'a'));
  return client;
}

it('leads with a concept and example while retaining task scope, next steps, and the fixed evidence footer', () => {
  const props = inputs(), queryClient = cachedClient();
  const markup = renderToStaticMarkup(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>);
  expect(markup).toContain('Why this step helps');
  expect(markup).toContain('Counterexample');
  expect(markup).toContain('the general problem remains open');
  expect(markup).toContain('Background explanations are not research progress');
  expect(markup.indexOf('One useful concept')).toBeLessThan(markup.indexOf('Illustrative example'));
  expect(markup.indexOf('Illustrative example')).toBeLessThan(markup.indexOf('Concept explanation'));
  expect(markup.indexOf('Illustrative example')).toBeLessThan(markup.indexOf('Why this step helps'));
  expect(markup.indexOf('Illustrative example')).toBeLessThan(markup.indexOf('What this does and does not establish'));
  expect(markup.indexOf('Illustrative example')).toBeLessThan(markup.indexOf('The recorded next step'));
  expect(markup).toContain('View evidence');
  expect(markup).toContain('Illustrative example');
  expect(markup).toContain('How it connects to this step');
  expect(markup).not.toContain('main-a');
  expect(markup).toContain('data-testid="research-brief-footer"');
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
  expect(record.findByType(MarkdownContent).props.children).toBe('Checked the stated conditions.\nCheck the remaining case.');
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
  expect(renderer!.root.findAllByProps({ 'data-testid': 'research-brief-status' })).toHaveLength(0);
});

it('fills a referenced draft when asked and does not send a model request', () => {
  const props = inputs(), queryClient = cachedClient(), onAsk = vi.fn();
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} onAsk={onAsk} /></QueryClientProvider>); });
  const button = renderer!.root.findAllByType('button').find(item => item.children.includes('Ask about latest progress'))!;
  act(() => button.props.onClick());
  expect(onAsk).toHaveBeenCalledTimes(1);
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
  expect(headings.findIndex(heading => heading.startsWith('One useful concept'))).toBeLessThan(headings.indexOf('What this does and does not establish'));
  expect(reading.findAllByType(MarkdownContent).map(item => item.props.children).join('\n')).toContain('the general problem remains open');
  expect(reading.findAllByType('span').some(item => item.children.join('').includes('update pending'))).toBe(true);
  expect(onAsk).not.toHaveBeenCalled();
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
  expect(markup).toContain('Ask about latest progress');
  expect(markup).not.toContain('internal_failure_code');
  expect(markup).not.toContain('Counterexample');
  expect(markup).not.toContain('data-reader-teaching');
  expect(markup).toContain('the general problem remains open');
  expect(markup).toContain('The recorded next step');
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
  expect(markup).toContain('Ask about latest progress');
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
  const ask = renderer!.root.findAllByType('button').find(item => item.children.includes('Ask about latest progress'))!;
  act(() => ask.props.onClick());
  expect(splitDraft(onAsk.mock.calls[0][0]).refs[0]).toMatchObject({ task_id: 'a', event_ids: ['start-a', 'main-a', 'review-a'] });
  expect(generate).toHaveBeenCalledTimes(1);
  expect(read).not.toHaveBeenCalled();
});
