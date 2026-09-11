import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Dataset } from "./model";
import { buildSubmap, type SubmapStep } from "./submap";
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
  prewarm = true,
) {
  const locale = zh ? "zh-CN" : "en-US";
  const source = data.kind === "live" ? "project" : "dataset";
  const name = data.id.replace(/^live:/, "");
  const key = ["map-copy", source, name, locale, sessionId];
  const context = JSON.stringify(key);
  const contextRef = useRef(context);
  contextRef.current = context;
  const queryClient = useQueryClient();
  const copy = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) => {
      const result = await api.mapCopy(source, name, locale, signal, sessionId);
      const previous = queryClient.getQueryData<MapCopy>(key);
      return mergeMapCopy(previous, result, previous?.model_revision);
    },
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
  const task = data.tasks.find((t) => t.id === focused);
  const steps = useMemo(
    () => visibleSteps ?? (task ? buildSubmap(task, data.events, zh) : []),
    [task, data.events, zh, visibleSteps],
  );
  const [pulse, setPulse] = useState(0);
  const [generating, setGenerating] = useState(false);
  const mounted = useRef(true);
  const inflight = useRef<Promise<MapCopy> | null>(null);
  const pausedRef = useRef(paused);
  pausedRef.current = paused;
  const retryAt = useRef(0);
  const eventIndex = useMemo(
    () => new Map(data.events.map((e) => [e.id, e])),
    [data.events],
  );
  const foreground = requestsFor(data, steps, focused);
  // Background warming only starts once everything on screen has its text.
  const background = useMemo(
    () => (prewarm ? prewarmRequests(data, zh, focused) : []),
    [data, zh, focused, prewarm],
  );
  const cards = [...foreground, ...background]
    .filter((c) => needsCardCopy(c, data, copy.data, eventIndex))
    .slice(0, 8);
  const signature = JSON.stringify([
    context,
    cards,
    cards.map((c) => data.tasks.find((t) => t.id === c.task_id)?.revision),
    copy.data?.model_revision,
  ]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    retryAt.current = 0;
  }, [context, paused]);
  useEffect(() => {
    if (
      !allowGeneration ||
      !copy.data?.available ||
      !cards.length ||
      !Number.isFinite(retryAt.current) ||
      inflight.current
    )
      return;
    const requestedModelRevision = copy.data?.model_revision;
    const timer = setTimeout(
      () => {
        const request = queryClient.fetchQuery({
          queryKey: ["map-copy-generation", ...key],
          queryFn: () => api.generateMapCopy(source, name, { cards, locale }, undefined, sessionId),
          staleTime: 0, gcTime: 0, retry: false,
        });
        inflight.current = request;
        setGenerating(true);
        void request
          .then((result) => {
            // Reconcile against the source captured by this request. Progress or zoom
            // can change while it runs, but must not discard completed card text.
            queryClient.setQueryData<MapCopy>(key, (previous) =>
              mergeMapCopy(previous, result, requestedModelRevision),
            );
            if (contextRef.current === context)
              retryAt.current = result.retry_after
                ? Date.now() + result.retry_after * 1000
                : 0;
          })
          .catch(() => {
            if (contextRef.current === context)
              retryAt.current = pausedRef.current ? Infinity : Date.now() + 60000;
          })
          .finally(() => {
            if (inflight.current === request) inflight.current = null;
            if (mounted.current && contextRef.current === context) {
              setGenerating(false);
              setPulse((n) => n + 1);
            }
          });
      },
      Math.max(700, retryAt.current - Date.now()),
    );
    // Cancel an unstarted debounce only. The active request is source-scoped;
    // cancelling it on every live event leaves completed results stuck on disk.
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, pulse, copy.data?.available, allowGeneration, paused]);
  return { copy: copy.data, generating, ready: copy.isFetched };
}
