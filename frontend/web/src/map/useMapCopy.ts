import { useEffect, useMemo, useRef, useState } from "react";
import { useIsFetching, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { mapCopyKey, readerPreview } from './copyMode';
import type { Dataset } from "./model";
import { buildSubmap, type SubmapStep } from "./submap";
import { briefEvidence, briefInputSignature, briefRequest } from '../research-brief/model';
import { beginExplanationProgress, explanationRunStart, useExplanationProgress } from '../research-brief/progress';
import { useSelectedFoundation } from '../research-brief/foundation';
import {
  mergeMapCopy,
  needsCardCopy,
  requestsFor,
  stepRequests,
  type CardRequest,
  type MapCopy,
} from "./presentation";

/** How many step cards one canvas is willing to warm up for later readers. */
export const PREWARM_LIMIT = 600;

/** Existing history stays readable; generation follows what the reader opens. */
export function focusedCopyRequests(data: Dataset, steps: SubmapStep[], focused: string | null, readingKey: string | null = focused): CardRequest[] {
  if (!focused || !readingKey) return [];
  const task = data.tasks.find(task => task.id === focused);
  if (!task) return [];
  if (readingKey === focused) return [briefRequest(task, briefEvidence(data, task, task.started_ts ?? 0))];
  const owners = new Map(data.events.map(event => [event.id, event.item_id]));
  return requestsFor(data, steps, focused).filter(card => card.task_id === focused && card.key === readingKey
    // A layout retained during a focus change must not lend another task's evidence.
    && card.event_ids.every(id => !owners.get(id) || owners.get(id) === focused));
}

/** A failed explanation belongs to its selected sources, not live cursors or task revision noise. */
export function mapCopyInputSignature(data: Dataset, cards: CardRequest[]): string {
  const events = new Map(data.events.map(event => [event.id, event]));
  return JSON.stringify(cards.map(card => {
    const task = data.tasks.find(item => item.id === card.task_id);
    const dynamic = [card.task_id, `${card.task_id}:active`, `${card.task_id}:outcome`].includes(card.key);
    // Historical steps receive the task's contract and their own events, not
    // the mutable status, result or attempt of today's task.
    const sourceTask = !task || dynamic ? task : {
      ...task, status: '', summary: undefined, outcome: undefined, pending_question: undefined,
      started_ts: undefined, finished_ts: undefined, attempt: undefined,
    };
    return [card, briefInputSignature(sourceTask, card.event_ids.flatMap(id => events.get(id) ? [events.get(id)!] : []))];
  }));
}

/**
 * Step cards of every task that is not open right now. Written text for a card
 * is shared through the server cache, so warming it in the background while
 * this canvas is idle means the next reader who opens any task finds the
 * plain-language notes already written instead of watching them being drafted.
 * Tasks that have finished go first: their steps will not change again, so the
 * text written for them is never wasted.
 */
export function prewarmRequests(
  data: Dataset,
  zh: boolean,
  focused: string | null,
  limit = PREWARM_LIMIT,
): CardRequest[] {
  const settled = (status?: string) =>
    ["done", "failed", "cancelled", "recorded", "skipped"].includes(status || "");
  const tasks = data.tasks.filter((t) => t.id !== focused);
  const ordered = [...tasks.filter((t) => settled(t.status)), ...tasks.filter((t) => !settled(t.status))];
  const out: CardRequest[] = [];
  for (const task of ordered) {
    if (out.length >= limit) break;
    out.push(...stepRequests(data, task, buildSubmap(task, data.events, zh)));
  }
  return out.slice(0, limit);
}

export function useMapCopy(
  data: Dataset,
  focused: string | null,
  zh: boolean,
  allowGeneration = true,
  visibleSteps?: SubmapStep[],
  sessionId?: string,
  paused = false,
  prewarm = false,
  readingKey: string | null = focused,
  pinnedFoundationId?: string | null,
) {
  const locale = zh ? "zh-CN" : "en-US";
  const source = data.kind === "live" ? "project" : "dataset";
  const name = data.id.replace(/^live:/, "");
  const preview = readerPreview();
  const foundationChoice = useSelectedFoundation(sessionId ?? name, locale);
  const foundationId = preview === 'question-foundation' ? pinnedFoundationId === undefined ? foundationChoice.id : pinnedFoundationId : null;
  const foundationRequired = preview === 'question-foundation' && !foundationId;
  const key = mapCopyKey(source, name, locale, sessionId, preview, foundationId);
  const context = JSON.stringify(key);
  const contextRef = useRef(context);
  contextRef.current = context;
  const relatedChecks = useRef({ context, cards: new Map<string, string>() });
  if (relatedChecks.current.context !== context) relatedChecks.current = { context, cards: new Map() };
  const queryClient = useQueryClient();
  const copy = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) => {
      const result = preview === 'question-foundation'
        ? await api.mapCopy(source, name, locale, signal, sessionId, preview, foundationId)
        : await api.mapCopy(source, name, locale, signal, sessionId, preview);
      const previous = queryClient.getQueryData<MapCopy>(key);
      return mergeMapCopy(previous, result, previous?.model_revision);
    },
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
  const task = data.tasks.find((t) => t.id === focused);
  const progress = useExplanationProgress(key, readingKey, explanationRunStart(readingKey, task));
  const steps = useMemo(
    () => visibleSteps ?? (task ? buildSubmap(task, data.events, zh) : []),
    [task, data.events, zh, visibleSteps],
  );
  const [pulse, setPulse] = useState(0);
  const [generating, setGenerating] = useState(false);
  const mounted = useRef(true);
  const inflight = useRef<Promise<unknown> | null>(null);
  const eventIndex = useMemo(
    () => new Map(data.events.map((e) => [e.id, e])),
    [data.events],
  );
  const foreground = prewarm ? requestsFor(data, steps, focused) : focusedCopyRequests(data, steps, focused, readingKey);
  // Background warming only starts once everything on screen has its text.
  const background = useMemo(
    () => (prewarm ? prewarmRequests(data, zh, focused) : []),
    [data, zh, focused, prewarm],
  );
  const taskIds = useMemo(() => new Set(data.tasks.map(task => Array.from(task.id).slice(0, 160).join(''))), [data.tasks]);
  // Current-range views omit older neighbors, including later edits/deletions
  // of those neighbors. The feed cursor covers the full source. Ask the server
  // once per cursor/card version; enrich checks the saved sources before any
  // model call. A response only verifies its captured context and card version.
  const relatedCheckKey = (card: CardRequest, result = copy.data) => {
    const saved = result?.cards[card.key];
    return JSON.stringify([data.cursor, saved?.copy_revision, saved?.generated_at, saved?.model_revision]);
  };
  const needsRelatedCheck = (card: CardRequest) => {
    const snapshot = copy.data?.cards[card.key]?.source_snapshot;
    return Boolean(card.key === readingKey && focused && data.cursor && snapshot?.version === 2 &&
      snapshot.related_tasks?.some(task => !taskIds.has(task.id)) &&
      relatedChecks.current.cards.get(card.key) !== relatedCheckKey(card));
  };
  const cards = [...foreground, ...background]
    .filter((c) => needsCardCopy(c, data, copy.data, eventIndex) || needsRelatedCheck(c))
    .slice(0, 8);
  const inputSignature = mapCopyInputSignature(data, cards);
  const generationScope = ['map-copy-generation', source, name, locale, sessionId];
  const generationKey = [...generationScope, preview, ...(preview === 'question-foundation' ? [foundationId] : []), copy.data?.version ?? null,
    copy.data?.model_revision ?? null, inputSignature,
    cards.map(card => needsRelatedCheck(card) ? relatedCheckKey(card) : null)];
  const signature = JSON.stringify(generationKey);
  const activeGenerations = useIsFetching({ queryKey: generationScope });
  const generationOptions = {
    queryKey: generationKey,
    queryFn: async () => {
      const requestedModelRevision = copy.data?.model_revision;
      const checkingRelatedSources = cards.some(needsRelatedCheck);
      // Finish and save against this request's source even after its reader unmounts.
      const observed = beginExplanationProgress(queryClient, key, cards.map(card => ({
        key: card.key, startedAt: explanationRunStart(card.key, data.tasks.find(task => task.id === card.task_id)),
      })));
      let result: MapCopy;
      try {
        result = await api.generateMapCopy(source, name, { cards, locale,
          ...(foundationId ? { foundation_id: foundationId } : {}) }, undefined, sessionId, preview, observed.update);
      } finally {
        observed.finish();
      }
      // Only this submitted request confirms its captured cards and cursor.
      // A late response can still populate its own source cache after navigation.
      if (contextRef.current === context && !result.retry_after && result.available !== false) {
        for (const card of cards) {
          if (result.cards[card.key]) relatedChecks.current.cards.set(card.key, relatedCheckKey(card, result));
        }
      }
      queryClient.setQueryData<MapCopy>(key, previous => mergeMapCopy(previous, result, requestedModelRevision));
      return { available: cards.every(card => !needsCardCopy(card, data, result, eventIndex))
          && (!checkingRelatedSources || (!result.retry_after && result.available !== false)),
        retryAfter: typeof result.retry_after === 'number' && result.retry_after > 0 ? result.retry_after : null };
    },
    staleTime: Infinity, gcTime: 2 * 60 * 60 * 1000,
    retry: false, retryOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
  } as const;
  // Observe attempts through the same Query cache as the brief. Failures stay
  // settled across remounts; a successful server-directed wait may refresh later.
  const generation = useQuery({ ...generationOptions, enabled: false });
  const retryAfter = generation.isSuccess && generation.data.available === false
    ? generation.data.retryAfter : null;
  const cachedFailure = useMemo(() => copy.data?.generation_error
    ? new Error(copy.data.generation_error.message) : null,
  [copy.data?.generation_error?.code, copy.data?.generation_error?.message]);
  const startGeneration = () => {
    const request = queryClient.fetchQuery({ ...generationOptions, staleTime: 0 });
    inflight.current = request;
    setGenerating(true);
    void request.then(() => undefined, () => undefined).finally(() => {
      if (inflight.current === request) {
        inflight.current = null;
        if (mounted.current) {
          setGenerating(false);
          setPulse(value => value + 1);
        }
      }
    });
    return request;
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (
      !allowGeneration ||
      foundationRequired ||
      paused ||
      !copy.data?.available ||
      !cards.length ||
      generation.isError || (generation.isSuccess && !retryAfter) || activeGenerations > 0 ||
      inflight.current
    )
      return;
    // Failures cool down the source; successful coalescing belongs only to
    // generationKey's input. A finished task must bypass an active-task wait.
    const persistedDelay = copy.data.generation_error && typeof copy.data.retry_after === 'number' && copy.data.retry_after > 0
      ? copy.data.retry_after : 0;
    const notBefore = Math.max(
      retryAfter ? generation.dataUpdatedAt + retryAfter * 1000 : 0,
      persistedDelay ? copy.dataUpdatedAt + persistedDelay * 1000 : 0,
    );
    const timer = setTimeout(() => {
      if (queryClient.isFetching({ queryKey: generationScope }) === 0)
        void startGeneration();
    }, Math.max(700, notBefore - Date.now()));
    // Cancel an unstarted debounce only. The active request is source-scoped;
    // cancelling it on every live event leaves completed results stuck on disk.
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, pulse, copy.data?.available, copy.data?.retry_after, copy.dataUpdatedAt,
    allowGeneration, paused, foundationRequired, activeGenerations, generation.status, generation.dataUpdatedAt]);
  const busy = generating || generation.isFetching || activeGenerations > 0;
  const retry = async () => {
    if (!allowGeneration || paused || foundationRequired || !copy.data?.available || !cards.length || busy || inflight.current) return;
    await startGeneration().catch(() => undefined);
  };
  return { copy: copy.data, generating: busy, ready: copy.isFetched,
    foundationRequired,
    readingGenerating: progress.active, generationPhase: progress.phase,
    generationError: cards.length && !generation.isFetching && !progress.active ? generation.error || cachedFailure : null,
    generationUnavailable: !!cards.length && !generation.isFetching && !progress.active && !retryAfter && generation.data?.available === false,
    retry,
    readingRequest: foreground.find(card => card.key === readingKey),
    readingNeedsUpdate: generation.data?.available !== true && cards.some(card => card.key === readingKey) };
}
