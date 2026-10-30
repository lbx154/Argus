import { useEffect, useMemo, useState } from 'react';
import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import { newestPerRole, type ConversationPage } from '@argus/contracts';
import { api, openConversation, type EventMsg } from './api';
import { roleLogKey, type RoleLog } from './useRoleLog';
import { mergeConversationEvents } from './lib/conversationEvents';

export type ConversationHistoryStatus = 'loading' | 'error' | 'ready';

export interface ConversationPagination {
  hasOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
}

/** Page over server rows; the stream replaces rows by their durable identity. */
export function useConversationHistory(
  sid: string | null, enabled: boolean, optimistic: EventMsg[], optimisticSid: string | null,
) {
  const client = useQueryClient();
  const query = useInfiniteQuery({
    queryKey: ['conversation', sid],
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) => api.conversation(sid!, pageParam, signal),
    getNextPageParam: page => page.before ?? undefined,
    enabled: !!sid && enabled,
  });
  const [connection, setConnection] = useState({ sid: null as string | null, connected: false });
  const [browserOnline, setBrowserOnline] = useState(() => typeof navigator === 'undefined' || navigator.onLine !== false);
  useEffect(() => {
    const sync = () => setBrowserOnline(navigator.onLine !== false);
    window.addEventListener('online', sync);
    window.addEventListener('offline', sync);
    return () => { window.removeEventListener('online', sync); window.removeEventListener('offline', sync); };
  }, []);
  // On a revisit, start from the opening REST refresh, rather than a stale
  // cached cursor that would replay existing deliveries into a second file read.
  const ready = query.data !== undefined && (!query.isRefetching || query.isFetchedAfterMount);
  useEffect(() => {
    if (!sid || !enabled || !ready) return;
    const data = client.getQueryData<InfiniteData<ConversationPage>>(['conversation', sid])!;
    let cursor = Math.max(...data.pages.map(page => page.cursor));
    let closed = false;
    setConnection({ sid, connected: false });
    const close = openConversation(sid, cursor, frame => {
      if (closed) return;
      const updates = frame.rows as EventMsg[];
      const roleEvents = frame.role_events as EventMsg[];
      if (updates.length) client.setQueryData<InfiniteData<ConversationPage>>(['conversation', sid], current => {
        if (!current) return current;
        const changed = new Set(updates.map(row => row.row_id));
        return { ...current, pages: current.pages.map((page, index) => ({
          ...page, cursor: Math.max(page.cursor, Number(frame.cursor)),
          rows: [...page.rows.filter(row => !changed.has(row.row_id)), ...index === 0 ? updates : []],
        })) };
      });
      if (roleEvents.length) client.setQueryData<RoleLog>(roleLogKey(sid), current => ({ round: Number(frame.round),
        events: newestPerRole([...new Map([...(current?.events ?? []).filter(row => Number(row.event_id) >= Number(frame.round)),
          ...roleEvents].map(row => [row.event_id, row])).values()].sort((a, b) => Number(a.event_id) - Number(b.event_id))),
      }));
      const fresh = [...updates, ...roleEvents].filter(event => Number(event.event_id) > cursor);
      if (fresh.some(event => event.delivery || event.type === 'provider.request.completed')) {
        void client.invalidateQueries({ queryKey: ['artifacts', sid], exact: true });
        for (const key of ['workspace-tree', 'workspace-git', 'workspace-file']) {
          void client.invalidateQueries({ queryKey: [key, sid] });
        }
      }
      cursor = Math.max(cursor, Number(frame.cursor));
    }, {
      onOpen: () => {
        if (!closed) {
          setConnection({ sid, connected: true });
        }
      },
      onClose: () => { if (!closed) setConnection({ sid, connected: false }); },
    });
    return () => { closed = true; close(); };
  }, [sid, enabled, ready, client]);
  const events = useMemo(() => {
    if (!sid) return [];
    const rows = new Map<string, EventMsg>();
    for (const page of query.data?.pages ?? []) for (const row of page.rows) {
      const previous = rows.get(String(row.row_id));
      if (!previous || Number(row.event_id) > Number(previous.event_id)) rows.set(String(row.row_id), row);
    }
    const recorded = [...rows.values()].sort((a, b) => Number(a.order) - Number(b.order));
    return mergeConversationEvents(recorded, optimisticSid === sid ? optimistic : []);
  }, [sid, query.data, optimistic, optimisticSid]);
  const status: ConversationHistoryStatus = query.isError ? 'error' : query.isPending ? 'loading' : 'ready';
  const pagination = useMemo<ConversationPagination>(() => ({
    hasOlder: query.hasNextPage,
    loadingOlder: query.isFetchingNextPage,
    onLoadOlder: () => { void query.fetchNextPage(); },
  }), [query.hasNextPage, query.isFetchingNextPage, query.fetchNextPage]);
  return { query, events, status, pagination, connected: browserOnline && enabled && connection.sid === sid && connection.connected };
}
