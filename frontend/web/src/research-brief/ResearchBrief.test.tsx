import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { splitDraft } from '../map/presentation';
import { MarkdownContent } from '../components/MarkdownContent';
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

it('puts scope and next steps before background concepts and keeps evidence in the fixed footer', () => {
  const props = inputs(), queryClient = cachedClient();
  const markup = renderToStaticMarkup(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>);
  expect(markup).toContain('Why this step helps');
  expect(markup).toContain('Counterexample');
  expect(markup).toContain('the general problem remains open');
  expect(markup).toContain('Background explanations are not research progress');
  expect(markup.indexOf('What this does and does not establish')).toBeLessThan(markup.indexOf('One useful concept'));
  expect(markup.indexOf('The recorded next step')).toBeLessThan(markup.indexOf('One useful concept'));
  expect(markup).toContain('View evidence');
  expect(markup).toContain('See an illustrative example');
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

it('fills a referenced draft when asked and does not send a model request', () => {
  const props = inputs(), queryClient = cachedClient(), onAsk = vi.fn();
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} onAsk={onAsk} /></QueryClientProvider>); });
  const button = renderer!.root.findAllByType('button').find(item => item.children.includes('Ask about this step'))!;
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
  expect(headings.indexOf('What this does and does not establish')).toBeLessThan(headings.findIndex(heading => heading.startsWith('One useful concept')));
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
  expect(markup).toContain('Ask about this step');
  expect(markup).not.toContain('internal_failure_code');
  expect(markup).not.toContain('Counterexample');
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
