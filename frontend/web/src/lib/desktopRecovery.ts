import type { QueryClient } from '@tanstack/react-query';
import { useRef } from 'react';
import { isConnectionError } from '../api';

const SHELL_ORIGINS = new Set(['http://tauri.localhost', 'https://tauri.localhost', 'tauri://localhost']);
// Explicit GET-only allowlist. Never invalidate all queries: some features use
// query functions for generation, and mutations/POST receipts must not replay.
const RECOVERY_READS = new Set(['projects', 'project-costs', 'snapshot', 'status', 'config', 'model-choices']);
const trustedClients = new WeakSet<QueryClient>();

export function connectionProblemAfterRead(previous: unknown, query: { error: unknown; status: string }, desktop: boolean): unknown {
  if (isConnectionError(query.error)) return query.error;
  // React Query clears the error when retrying a read that has no cached data.
  // In a recovering desktop keep that visible until its actual success (or a
  // different definitive error), not merely until native ready/refetch starts.
  return desktop && query.status === 'pending' ? previous : undefined;
}

export function useDesktopConnectionError(client: QueryClient, queries: Array<{ error: unknown; status: string }>): unknown {
  const previous = useRef<unknown[]>([]);
  previous.current = queries.map((query, index) => connectionProblemAfterRead(previous.current[index], query, trustedClients.has(client)));
  return previous.current.find(isConnectionError);
}

export function installDesktopConnectionRecovery(client: QueryClient, target: Window = window): () => void {
  if (target.parent === target || target.location.protocol !== 'http:'
    || !['127.0.0.1', 'localhost', '[::1]'].includes(target.location.hostname)) return () => {};
  let generation = 0;
  let disposed = false;
  const receive = (event: MessageEvent) => {
    const data = event.data;
    if (event.source !== target.parent || !SHELL_ORIGINS.has(event.origin)
      || !data || data.type !== 'argus:backend-ready' || data.version !== 1
      || data.origin !== target.location.origin || !Number.isSafeInteger(data.generation)
      || data.generation <= generation) return;
    generation = data.generation;
    trustedClients.add(client);
    const current = generation;
    const filters = { predicate: (query: { queryKey: readonly unknown[] }) => RECOVERY_READS.has(String(query.queryKey[0])) };
    // Cancellation prevents late failures from the retired process overwriting
    // fresh data. Keep cached data/errors until a real read succeeds; native
    // readiness alone is not evidence that these endpoints/auth are healthy.
    void client.cancelQueries(filters).then(() => {
      if (!disposed && current === generation) return client.invalidateQueries({ ...filters, refetchType: 'active' });
    });
  };
  target.addEventListener('message', receive);
  // The listener may mount after iframe load/native ready. This bounded request
  // carries no credentials; only an exact Tauri parent can answer it.
  target.parent.postMessage({ type: 'argus:connection-listening', version: 1 }, '*');
  return () => { disposed = true; target.removeEventListener('message', receive); };
}
