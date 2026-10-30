import type { QueryClient } from '@tanstack/react-query';

/** Frames arriving during a map read are coalesced into a read after it finishes.
 * Invalidating alone loses those frames when a successful read clears stale state. */
export function mapRefresher(client: QueryClient, sid: string) {
  let pending = false;
  let refreshing = false;
  return async () => {
    pending = true;
    if (refreshing) return;
    refreshing = true;
    try {
      do {
        // An opening/refetch already in flight predates this event. Joining it
        // must leave one read pending, even when this is the only notification.
        const query = client.getQueryCache().find({ queryKey: ['map-live', sid], exact: true });
        pending = query?.isActive() === true && query.state.fetchStatus === 'fetching';
        await client.invalidateQueries({ queryKey: ['map-live', sid], exact: true }, { cancelRefetch: false });
      } while (pending);
    } finally { refreshing = false; }
  };
}
