import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import type { Dataset } from "./model";
import { buildSubmap, type SubmapStep } from "./submap";
import { mergeMapCopy, requestsFor, type MapCopy } from "./presentation";

export function useMapCopy(
  data: Dataset,
  focused: string | null,
  zh: boolean,
  allowGeneration = true,
  visibleSteps?: SubmapStep[],
  sessionId?: string,
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
    queryFn: ({ signal }) => api.mapCopy(source, name, locale, signal, sessionId),
    staleTime: 10000,
    refetchOnWindowFocus: true,
  });
  const task = data.tasks.find((t) => t.id === focused);
  const steps = useMemo(
    () => visibleSteps ?? (task ? buildSubmap(task, data.events, zh) : []),
    [task, data.events, zh, visibleSteps],
  );
  const [pulse, setPulse] = useState(0);
  const [generating, setGenerating] = useState(false);
  const mounted = useRef(true);
  const inflight = useRef<AbortController | null>(null);
  const retryAt = useRef(0);
  const cards = requestsFor(data, steps, focused)
    .filter((c) => {
      const saved = copy.data?.cards[c.key];
      const t = data.tasks.find((t) => t.id === c.task_id);
      const latest = Math.max(
        t?.started_ts || 0,
        t?.finished_ts || 0,
        ...data.events
          .filter((e) => c.event_ids.includes(e.id))
          .map((e) => e.ts),
      );
      return (
        !saved ||
        (copy.data?.model_revision !== undefined &&
          saved.model_revision !== copy.data.model_revision) ||
        (copy.data?.version !== undefined &&
          saved.version !== copy.data.version) ||
        latest > saved.generated_at ||
        saved.task_status !== t?.status ||
        (t?.revision && t.revision !== saved.task_revision) ||
        JSON.stringify(c.event_ids) !== JSON.stringify(saved.event_ids || [])
      );
    })
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
      inflight.current?.abort();
    };
  }, []);
  useEffect(() => {
    retryAt.current = 0;
    return () => inflight.current?.abort();
  }, [context]);
  useEffect(() => {
    if (
      !allowGeneration ||
      !copy.data?.available ||
      !cards.length ||
      inflight.current
    )
      return;
    const requestedModelRevision = copy.data?.model_revision;
    const timer = setTimeout(
      () => {
        const controller = new AbortController();
        inflight.current = controller;
        setGenerating(true);
        void api
          .generateMapCopy(source, name, { cards, locale }, controller.signal, sessionId)
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
            if (!controller.signal.aborted && contextRef.current === context)
              retryAt.current = Date.now() + 60000;
          })
          .finally(() => {
            if (inflight.current === controller) inflight.current = null;
            if (mounted.current) {
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
  }, [signature, pulse, copy.data?.available, allowGeneration]);
  return { copy: copy.data, generating };
}
