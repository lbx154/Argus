import { useQuery, type QueryClient, type QueryKey } from '@tanstack/react-query';
import type { ExplanationPhase } from '../api';

interface ExplanationProgress {
  requestId: number;
  phase: ExplanationPhase | null;
}

let nextRequestId = 0;
const progressKey = (copyKey: QueryKey, cardKey: string, startedAt = 0) => ['explanation-progress', ...copyKey, cardKey, startedAt];

/** Milestones can grow during one attempt; a new attempt has its own progress. */
export function explanationRunStart(cardKey: string | null | undefined, task?: { id: string; started_ts?: number | null }, fallback = 0) {
  if (!task) return fallback;
  if (![task.id, `${task.id}:active`, `${task.id}:outcome`].includes(cardKey ?? '')) return 0;
  return typeof task.started_ts === 'number' && Number.isFinite(task.started_ts) && task.started_ts >= 0
    ? task.started_ts : fallback;
}

/** One observed request can update only the cards it actually requested. */
export function beginExplanationProgress(client: QueryClient, copyKey: QueryKey, cards: readonly { key: string; startedAt?: number }[]) {
  const requestId = ++nextRequestId;
  const keys = cards.map(card => progressKey(copyKey, card.key, card.startedAt));
  for (const key of keys) client.setQueryData<ExplanationProgress>(key, { requestId, phase: null });
  const update = (phase: ExplanationPhase) => {
    for (const key of keys) client.setQueryData<ExplanationProgress | null>(key, previous =>
      previous?.requestId === requestId ? { requestId, phase } : previous);
  };
  const finish = () => {
    for (const key of keys) client.setQueryData<ExplanationProgress | null>(key, previous =>
      previous?.requestId === requestId ? null : previous);
  };
  return { update, finish };
}

/** Passive current/history/modal observers share progress without sending work. */
export function useExplanationProgress(copyKey: QueryKey, cardKey: string | null | undefined, startedAt = 0) {
  const progress = useQuery<ExplanationProgress | null>({
    queryKey: progressKey(copyKey, cardKey ?? '', startedAt),
    queryFn: async () => null,
    initialData: null,
    enabled: false,
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
  });
  return { active: !!progress.data, phase: progress.data?.phase ?? undefined };
}

export function explanationProgressLabel(phase: ExplanationPhase | undefined, zh: boolean): string {
  const labels: Record<ExplanationPhase, readonly [string, string]> = {
    waiting_for_source: ['等待开始整理说明', 'Waiting to prepare the explanation'],
    planning: ['正在梳理概念与讲解顺序', 'Organizing the concepts and explanation'],
    writing: ['正在撰写说明', 'Writing the explanation'],
    reviewing: ['正在核对说明与来源', 'Checking the explanation against its sources'],
  };
  return phase ? labels[phase][zh ? 0 : 1] : zh ? '正在整理说明' : 'Preparing an explanation';
}
