import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { splitDraft } from '../map/presentation';
import ResearchBrief from './ResearchBrief';
import { briefCopyKey, briefLiveKey, briefSelection, currentBriefData } from './model';
import { completedCopy, inputs, source } from './testFixtures';

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

it('shows purpose, concept and scope by default while retaining the original objective and teaching boundary', () => {
  const props = inputs(), queryClient = cachedClient();
  const markup = renderToStaticMarkup(<QueryClientProvider client={queryClient}><ResearchBrief {...props} active={false} readOnly /></QueryClientProvider>);
  expect(markup).toContain('Why this step helps');
  expect(markup).toContain('Counterexample');
  expect(markup).toContain('the general problem remains open');
  expect(markup).toContain('Background explanations are not research progress');
  expect(markup).toContain(source.tasks[0].objective);
  expect(markup).toContain('See an illustrative example');
  expect(markup).toContain('main-a');
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
