import { useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import type { MissionView, Snapshot } from '../../../core/src/types';
import { mergeMapProgress } from '../map/incremental';
import type { Dataset } from '../map/model';
import { mergeMapCopy, needsCardCopy, type MapCopy } from '../map/presentation';
import {
  briefCopyKey, briefEvidence, briefInputSignature, briefLiveKey, briefRequest, briefSelection,
  currentBriefData, isReaderBrief, needsBrief, oldBriefService, READER_BRIEF_VERSION,
} from './model';

export interface ResearchBriefOptions {
  sid: string;
  snapshot: Snapshot;
  view: MissionView;
  active: boolean;
  readOnly?: boolean;
  locale: string;
}

export function useResearchBrief({ sid, snapshot, view, active, readOnly = false, locale }: ResearchBriefOptions) {
  const client = useQueryClient();
  const taskId = view.mission.id;
  const selection = briefSelection(snapshot, view);
  const liveKey = briefLiveKey(sid, selection);
  const copyKey = briefCopyKey(sid, locale);
  const enabled = active && !!sid && !!taskId && snapshot.session.id === sid;
  const live = useQuery({
    queryKey: liveKey,
    queryFn: async ({ signal }) => {
      const previous = client.getQueryData<Dataset>(liveKey);
      const next = await api.liveMap(sid, signal, previous?.cursor, selection);
      const current = currentBriefData(next, sid, taskId);
      return mergeMapProgress(client.getQueryData<Dataset>(liveKey), current);
    },
    enabled, refetchInterval: enabled ? 15_000 : false, staleTime: 10_000,
    retry: false, retryOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const copy = useQuery({
    queryKey: copyKey,
    queryFn: async ({ signal }) => {
      const result = await api.mapCopy('project', sid, locale, signal, sid);
      const previous = client.getQueryData<MapCopy>(copyKey);
      return mergeMapCopy(previous, result, previous?.model_revision);
    },
    enabled, staleTime: Infinity, gcTime: 2 * 60 * 60 * 1000,
    retry: false, retryOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const task = live.data?.tasks.find(item => item.id === taskId);
  const evidence = useMemo(() => briefEvidence(live.data, task, selection.eventSince), [live.data, task, selection.eventSince]);
  const inputSignature = briefInputSignature(task, evidence);
  const card = taskId ? copy.data?.cards[taskId] : undefined;
  const brief = isReaderBrief(card?.reader_brief) ? card.reader_brief : undefined;
  const needsUpdate = needsBrief(live.data, task, evidence, copy.data);
  const legacy = oldBriefService(copy.data, card);
  const canGenerate = enabled && !readOnly && !!task && copy.data?.available === true
    && !legacy && !live.isError && !copy.isError;
  const generationKey = ['research-brief-generation', sid, taskId, locale, selection.eventSince, inputSignature] as const;
  const previouslyFailed = client.getQueryState(generationKey)?.status === 'error';
  const generation = useQuery({
    queryKey: generationKey,
    queryFn: async () => {
      if (!task) throw new Error('The current task is not recorded yet.');
      const requestedRevision = client.getQueryData<MapCopy>(copyKey)?.model_revision;
      // The request is task/source-scoped. A tab switch may stop observing it,
      // but completed text still belongs in the shared map cache.
      const result = await api.generateMapCopy('project', sid, { cards: [briefRequest(task, evidence)], locale }, undefined, sid);
      const returned = result.cards?.[task.id];
      const valid = isReaderBrief(returned?.reader_brief);
      const oldVersion = result.version ?? returned?.version;
      const normalized = !valid && typeof oldVersion === 'number' && oldVersion < READER_BRIEF_VERSION
        ? { ...result, version: oldVersion } : result;
      client.setQueryData<MapCopy>(copyKey, previous => mergeMapCopy(previous, normalized, requestedRevision));
      // Coalescing can return a previous valid brief with retry_after. Its
      // existence alone does not mean the newly requested evidence was read.
      const current = valid && !!live.data && !needsCardCopy(briefRequest(task, evidence), live.data, result,
        new Map(evidence.map(event => [event.id, event])));
      return { available: current, retryAfter: result.retry_after ?? null, inputSignature };
    },
    enabled: canGenerate && needsUpdate && !previouslyFailed,
    // One attempt per semantic input, including across unmount/remount. A failed
    // generation is retried only by the button or by genuinely new evidence.
    staleTime: Infinity, gcTime: 2 * 60 * 60 * 1000,
    retry: false, retryOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });

  const retry = async () => {
    if (!enabled) return;
    if (live.isError || copy.isError || !copy.data?.available || legacy || !task) {
      await Promise.allSettled([live.refetch(), copy.refetch()]);
    } else if (canGenerate) {
      await generation.refetch();
    }
  };
  return {
    task, evidence, card, brief, inputSignature,
    needsUpdate: needsUpdate && generation.data?.available !== true,
    legacy,
    loading: enabled && (live.isPending || copy.isPending),
    generating: generation.isFetching,
    readError: live.error || copy.error,
    generationError: needsUpdate ? generation.error : null,
    generationUnavailable: generation.data?.available === false && needsUpdate,
    generationAvailable: copy.data?.available === true,
    retry,
  };
}
