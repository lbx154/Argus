import { useEffect, useMemo } from 'react';
import { useInfiniteQuery, useQueryClient, type InfiniteData } from '@tanstack/react-query';
import type { ConversationPage } from '@argus/contracts';
import { api, openConversation, type EventMsg, type Snapshot } from './api';
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
  const ready = query.data !== undefined;
  useEffect(() => {
    if (!sid || !enabled || !ready) return;
    const data = client.getQueryData<InfiniteData<ConversationPage>>(['conversation', sid])!;
    const cursor = Math.max(...data.pages.map(page => page.cursor));
    return openConversation(sid, cursor, frame => {
      const updates = frame.rows as EventMsg[];
      client.setQueryData<InfiniteData<ConversationPage>>(['conversation', sid], current => {
        if (!current) return current;
        const changed = new Set(updates.map(row => row.row_id));
        return { ...current, pages: current.pages.map((page, index) => ({
          ...page, cursor: Number(frame.cursor),
          rows: [...page.rows.filter(row => !changed.has(row.row_id)), ...index === 0 ? updates : []],
        })) };
      });
      client.setQueryData<Snapshot>(['snapshot', sid], current => current && { ...current,
        role_events: [...new Map([...(current.role_events ?? []).filter(row => Number(row.event_id) >= Number(frame.round)),
          ...frame.role_events as EventMsg[]].map(row => [row.event_id, row])).values()].sort((a, b) => Number(a.event_id) - Number(b.event_id)),
      });
    });
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
  return { query, events, status, pagination };
}
