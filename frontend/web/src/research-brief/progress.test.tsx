import { QueryClient, QueryClientProvider, type QueryKey } from '@tanstack/react-query';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { api } from '../api';
import { ReaderExplanationStatus } from './ReaderExplanation';
import { beginExplanationProgress, useExplanationProgress } from './progress';

let client: QueryClient;
let renderer: ReactTestRenderer | undefined;
const copyKey = ['map-copy', 'project', 'research', 'en-US', 'session'];

function Reader({ name, source = copyKey, card = 'a', startedAt = 0 }: { name: string; source?: QueryKey; card?: string; startedAt?: number }) {
  const progress = useExplanationProgress(source, card, startedAt);
  return <div data-reader={name}><ReaderExplanationStatus generating={progress.active} phase={progress.phase} /></div>;
}

const tree = (card = 'a') => <QueryClientProvider client={client}>
  <Reader name="current" card={card} /><Reader name="history" />
  <Reader name="other-task" card="b" /><Reader name="other-preview" source={[...copyKey, 'learning-path']} />
  <Reader name="other-attempt" startedAt={100} />
  <Reader name="other-project" source={['map-copy', 'project', 'another', 'en-US', 'other-session']} />
</QueryClientProvider>;

function phase(name: string) {
  return renderer!.root.findByProps({ 'data-reader': name })
    .findAll(node => node.props['data-explanation-phase'] !== undefined)
    .map(node => node.props['data-explanation-phase']);
}

async function flush() { await act(async () => { await vi.advanceTimersByTimeAsync(25); }); }

beforeEach(() => {
  vi.useFakeTimers();
  client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
});

afterEach(() => {
  act(() => renderer?.unmount());
  renderer = undefined;
  client.clear();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

it('shares one request with passive readers while isolating other cards, projects and modes', async () => {
  const generate = vi.spyOn(api, 'generateMapCopy');
  act(() => { renderer = create(tree()); });
  const observed = beginExplanationProgress(client, copyKey, [{ key: 'a' }]);
  observed.update('planning');
  await flush();
  expect(phase('current')).toEqual(['planning']);
  expect(phase('history')).toEqual(['planning']);
  for (const name of ['other-task', 'other-preview', 'other-project', 'other-attempt']) expect(phase(name)).toEqual([]);
  act(() => renderer!.update(tree('b')));
  observed.update('writing');
  await flush();
  expect(phase('current')).toEqual([]);
  expect(phase('history')).toEqual(['writing']);
  act(() => renderer!.unmount());
  act(() => { renderer = create(tree()); });
  expect(phase('current')).toEqual(['writing']);
  observed.finish();
  await flush();
  expect(phase('current')).toEqual([]);
  expect(phase('history')).toEqual([]);
  expect(generate).not.toHaveBeenCalled();
});

it('keeps a newer request when an older request reports or finishes late', async () => {
  act(() => { renderer = create(tree()); });
  const older = beginExplanationProgress(client, copyKey, [{ key: 'a' }]);
  older.update('writing');
  const newer = beginExplanationProgress(client, copyKey, [{ key: 'a' }]);
  newer.update('waiting_for_source');
  older.update('reviewing');
  older.finish();
  await flush();
  expect(phase('history')).toEqual(['waiting_for_source']);
  newer.update('reviewing');
  await flush();
  expect(phase('current')).toEqual(['reviewing']);
  newer.finish();
  await flush();
  expect(phase('current')).toEqual([]);
});
