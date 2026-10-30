import { QueryClient, QueryObserver } from '@tanstack/react-query';
import { expect, it } from 'vitest';
import { mapRefresher } from '../map/refresh';
const settled = () => new Promise(resolve => setImmediate(resolve));
it('finishes in-flight reads and fetches the last frame even if no more frames arrive', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const reads: Array<() => void> = [];
  let frame = 0, aborted = 0;
  const observer = new QueryObserver(client, { queryKey: ['map-live', 's'], staleTime: Infinity,
    queryFn: ({ signal }) => new Promise<number>(resolve => {
      const at = frame;
      signal.addEventListener('abort', () => aborted++);
      reads.push(() => resolve(at));
    }) });
  const unsubscribe = observer.subscribe(() => {});
  reads[0](); await settled();
  const refresh = mapRefresher(client, 's');
  frame = 1; const pending = refresh();
  expect(reads).toHaveLength(2);
  frame = 2; void refresh();
  frame = 3; void refresh();
  expect(reads).toHaveLength(2);
  reads[1](); await settled();
  expect(client.getQueryData(['map-live', 's'])).toBe(1);
  expect(reads).toHaveLength(3);
  reads[2](); await pending;
  expect(client.getQueryData(['map-live', 's'])).toBe(3);
  expect(aborted).toBe(0);
  expect(reads).toHaveLength(3);
  unsubscribe(); client.clear();
});
it.each(['initial read', 'reopened map'] as const)('fetches a single frame arriving during an existing %s', async opening => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const key = ['map-live', 's'];
  const reads: Array<() => void> = [];
  let frame = 0, aborted = 0;
  const options = { queryKey: key, staleTime: Infinity, refetchOnMount: 'always' as const,
    queryFn: ({ signal }: { signal: AbortSignal }) => new Promise<number>(resolve => {
      const at = frame;
      signal.addEventListener('abort', () => aborted++);
      reads.push(() => resolve(at));
    }) };
  let unsubscribe = new QueryObserver(client, options).subscribe(() => {});
  try {
    if (opening === 'reopened map') {
      reads[0](); await settled();
      unsubscribe();
      unsubscribe = new QueryObserver(client, options).subscribe(() => {});
    }
    const openingReads = reads.length;
    const refresh = mapRefresher(client, 's');
    frame = 1;
    const pending = refresh(); // Exactly one event, with no later activity to rescue it.
    expect(reads).toHaveLength(openingReads);
    reads[openingReads - 1](); await settled();
    expect(client.getQueryData(key)).toBe(0);
    expect(reads).toHaveLength(openingReads + 1);
    reads[openingReads](); await pending;
    expect(client.getQueryData(key)).toBe(1);
    expect(aborted).toBe(0);
    expect(reads).toHaveLength(openingReads + 1);
  } finally { unsubscribe(); client.clear(); }
});
it('leaves inactive maps stale without starting reads', async () => {
  const client = new QueryClient();
  client.setQueryData(['map-live', 's'], 0);
  await mapRefresher(client, 's')();
  expect(client.getQueryState(['map-live', 's'])?.isInvalidated).toBe(true);
  expect(client.getQueryState(['map-live', 's'])?.fetchStatus).toBe('idle');
  client.clear();
});
