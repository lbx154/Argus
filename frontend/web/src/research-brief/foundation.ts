import { skipToken, useQuery, type QueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { readerPreview } from '../map/copyMode';
import { readLocalStorage, writeLocalStorage } from '../lib/storage';

interface FoundationChoice { id: string | null; choice: number }
export interface FoundationRequest {
  id: string;
  draft: { question: string; sourceTaskId?: string; sourceTitle?: string };
  beforeChoice: number;
}
const choiceKey = (sid: string, locale: string) => ['reader-foundation-choice', sid, locale];
const storageKey = (sid: string, locale: string) => `argus:reader-foundation:${sid}:${locale}`;
export const foundationListKey = (sid: string) => ['artifacts', sid, 'reader'];
const requestKey = (sid: string, locale: string) => ['reader-foundation-request', sid, locale];
const requestStorageKey = (sid: string, locale: string) => `${storageKey(sid, locale)}:request`;

function readChoice(sid: string, locale: string): FoundationChoice {
  const raw = readLocalStorage(storageKey(sid, locale));
  if (!raw) return { id: null, choice: 0 };
  try {
    const value = JSON.parse(raw);
    if ((typeof value?.id === 'string' || value?.id === null)
      && Number.isSafeInteger(value.choice) && value.choice >= 0) return value;
  } catch { /* Earlier local selections stored the ID alone. */ }
  return { id: raw, choice: 0 };
}

function readRequest(sid: string, locale: string): FoundationRequest | null {
  try {
    const value = JSON.parse(readLocalStorage(requestStorageKey(sid, locale)) || 'null');
    return typeof value?.id === 'string' && typeof value.draft?.question === 'string'
      && typeof value.beforeChoice === 'number' ? value : null;
  } catch { return null; }
}

export function saveFoundationRequest(client: QueryClient, sid: string, locale: string, request: FoundationRequest | null) {
  client.setQueryData(requestKey(sid, locale), request);
  writeLocalStorage(requestStorageKey(sid, locale), JSON.stringify(request));
}

/** Reloading an unconfirmed submission retains its identity; it never resubmits. */
export function useFoundationRequest(sid: string, locale: string) {
  return useQuery<FoundationRequest | null>({
    queryKey: requestKey(sid, locale), queryFn: skipToken, enabled: false,
    initialData: () => readRequest(sid, locale), staleTime: Infinity, gcTime: Infinity,
  }).data ?? null;
}

export function selectFoundation(client: QueryClient, sid: string, locale: string, id: string | null) {
  const value = { id, choice: foundationChoice(client, sid, locale).choice + 1 };
  client.setQueryData(choiceKey(sid, locale), value);
  writeLocalStorage(storageKey(sid, locale), JSON.stringify(value));
  return value;
}

export function foundationChoice(client: QueryClient, sid: string, locale: string) {
  const current = client.getQueryData<FoundationChoice>(choiceKey(sid, locale));
  const stored = readChoice(sid, locale);
  return current && current.choice >= stored.choice ? current : stored;
}

/** A reader's explicit question choice survives task changes without creating work. */
export function useSelectedFoundation(sid: string, locale: string) {
  const query = useQuery<FoundationChoice>({
    queryKey: choiceKey(sid, locale), queryFn: skipToken, enabled: false,
    initialData: () => readChoice(sid, locale),
    staleTime: Infinity, gcTime: Infinity,
  });
  return query.data!;
}

export function useFoundationList(sid: string) {
  return useQuery({
    queryKey: foundationListKey(sid),
    queryFn: async ({ signal }) => (await api.artifacts(sid, signal, true))
      .filter(item => item.source === 'reader_foundation'),
    enabled: !!sid && readerPreview() === 'question-foundation',
    refetchInterval: query => query.state.data?.some(item => item.reader_foundation?.state === 'generating') ? 5_000 : false,
    retry: false, refetchOnWindowFocus: false,
  });
}

export function foundationQuestionDraft(question: string, path: string, zh: boolean) {
  return zh
    ? `我正在阅读这个问题的基础说明：${question}\n说明文件：${path}\n我还不理解的地方：\n`
    : `I am reading the foundation for this question: ${question}\nExplanation file: ${path}\nWhat I still do not understand:\n`;
}
