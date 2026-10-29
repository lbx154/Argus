import type { Query, QueryClient, QueryKey } from '@tanstack/react-query';

/** What a reload needs to draw a project at once: the API handshake, the
 * project list, and each project's snapshot, conversation and event window. */
const PERSISTED_ROOTS = new Set(['api-meta', 'projects', 'snapshot', 'conversation', 'events']);
export const QUERY_CACHE_KEY = 'argus.query-cache.v1';
// Browsers give an origin about five million characters of session storage;
// leave most of it to drafts and the rest of the workbench.
export const QUERY_CACHE_MAX_CHARS = 1_000_000;
const MAX_AGE_MS = 12 * 60 * 60_000;
// Snapshots poll every few seconds; one write per interval is plenty.
const WRITE_INTERVAL_MS = 2_000;

type StoredQuery = { key: QueryKey; data: unknown; updatedAt: number };

const persistable = (key: QueryKey) => PERSISTED_ROOTS.has(String(key[0]));

/**
 * Seed the cache from this tab's last visit. Entries keep their old update
 * time, so they count as stale and every view refreshes them in the
 * background. Storage that is missing, blocked or corrupt restores nothing.
 */
export function restoreQueryCache(
  client: QueryClient,
  storage: () => Storage | undefined,
  build: string,
  now = Date.now(),
): number {
  try {
    const raw = storage()?.getItem(QUERY_CACHE_KEY);
    if (!raw) return 0;
    const stored = JSON.parse(raw) as { build?: unknown; queries?: unknown };
    if (stored.build !== build || !Array.isArray(stored.queries)) return 0;
    let restored = 0;
    for (const entry of stored.queries as StoredQuery[]) {
      if (!entry || !Array.isArray(entry.key) || !persistable(entry.key) || entry.data === undefined) continue;
      if (typeof entry.updatedAt !== 'number' || now - entry.updatedAt > MAX_AGE_MS) continue;
      // A query a view already fetched this session is newer than storage.
      if (client.getQueryState(entry.key)?.dataUpdatedAt) continue;
      client.setQueryData(entry.key, entry.data, { updatedAt: entry.updatedAt });
      restored += 1;
    }
    return restored;
  } catch {
    return 0;
  }
}

/**
 * Keep the newest persisted views in storage, newest first until the size cap,
 * at most once per interval and again when the page is hidden. Returns a stop
 * function. Failing storage only costs the instant reload.
 */
export function persistQueryCache(
  client: QueryClient,
  storage: () => Storage | undefined,
  build: string,
  target: Pick<Window, 'addEventListener' | 'removeEventListener' | 'setTimeout' | 'clearTimeout'> = window,
): () => void {
  const serialized = new Map<string, { updatedAt: number; text: string }>();
  let timer: number | undefined;
  const write = () => {
    if (timer !== undefined) target.clearTimeout(timer);
    timer = undefined;
    const queries = client.getQueryCache().getAll()
      .filter((query: Query) => persistable(query.queryKey) && query.state.status === 'success' && query.state.data !== undefined)
      .sort((left, right) => right.state.dataUpdatedAt - left.state.dataUpdatedAt);
    const kept: string[] = [];
    let size = 0;
    const live = new Set<string>();
    for (const query of queries) {
      live.add(query.queryHash);
      let entry = serialized.get(query.queryHash);
      if (!entry || entry.updatedAt !== query.state.dataUpdatedAt) {
        try {
          entry = {
            updatedAt: query.state.dataUpdatedAt,
            text: JSON.stringify({ key: query.queryKey, data: query.state.data, updatedAt: query.state.dataUpdatedAt }),
          };
        } catch {
          continue;
        }
        serialized.set(query.queryHash, entry);
      }
      if (size + entry.text.length > QUERY_CACHE_MAX_CHARS) continue;
      kept.push(entry.text);
      size += entry.text.length;
    }
    for (const hash of serialized.keys()) if (!live.has(hash)) serialized.delete(hash);
    try {
      storage()?.setItem(QUERY_CACHE_KEY, `{"build":${JSON.stringify(build)},"queries":[${kept.join(',')}]}`);
    } catch {
      try {
        storage()?.removeItem(QUERY_CACHE_KEY);
      } catch {
        // Storage is unavailable; the next visit simply loads.
      }
    }
  };
  const unsubscribe = client.getQueryCache().subscribe((event) => {
    if (!persistable(event.query.queryKey)) return;
    if (event.type === 'updated' ? event.action.type !== 'success' : event.type !== 'removed') return;
    if (timer === undefined) timer = target.setTimeout(write, WRITE_INTERVAL_MS);
  });
  const flush = () => {
    if (timer !== undefined) write();
  };
  target.addEventListener('pagehide', flush);
  return () => {
    unsubscribe();
    target.removeEventListener('pagehide', flush);
    if (timer !== undefined) target.clearTimeout(timer);
    timer = undefined;
  };
}
