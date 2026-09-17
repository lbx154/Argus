import { useMemo } from 'react';
import type { EventMsg } from './api';
import { useTranscript } from './hooks';
import { mergeConversationEvents } from './lib/conversationEvents';

export type ConversationHistoryStatus = 'loading' | 'error' | 'ready';

/** Keep history readiness and optimistic messages bound to the selected project. */
export function useConversationHistory(
  sid: string | null,
  enabled: boolean,
  live: EventMsg[],
  optimistic: EventMsg[],
  optimisticSid: string | null,
) {
  const query = useTranscript(sid, enabled, 120);
  const events = useMemo(() => sid ? mergeConversationEvents(
    live, query.data ?? [], optimisticSid === sid ? optimistic : [],
  ) : [], [sid, live, query.data, optimistic, optimisticSid]);
  const status: ConversationHistoryStatus = query.isError ? 'error' : query.isPending ? 'loading' : 'ready';
  return { query, events, status };
}
