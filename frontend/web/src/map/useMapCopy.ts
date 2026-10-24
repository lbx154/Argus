import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { mapCopyKey, readerPreview } from './copyMode';
import type { Dataset } from "./model";
import { buildSubmap, type SubmapStep } from "./submap";
import { briefEvidence, briefRequest } from '../research-brief/model';
import { useSelectedFoundation } from '../research-brief/foundation';
import {
  mergeMapCopy,
  requestsFor,
  type CardRequest,
  type MapCopy,
} from "./presentation";

/** The card a reader opened and the records it covers. */
export function focusedCopyRequests(data: Dataset, steps: SubmapStep[], focused: string | null, readingKey: string | null = focused): CardRequest[] {
  if (!focused || !readingKey) return [];
  const task = data.tasks.find(task => task.id === focused);
  if (!task || task.turn_kind === 'qa') return [];
  if (readingKey === focused) return [briefRequest(task, briefEvidence(data, task, task.started_ts ?? 0))];
  const owners = new Map(data.events.map(event => [event.id, event.item_id]));
  return requestsFor(data, steps, focused).filter(card => card.task_id === focused && card.key === readingKey
    // A layout retained during a focus change must not lend another task's evidence.
    && card.event_ids.every(id => !owners.get(id) || owners.get(id) === focused));
}

/**
 * Explanations already saved for this map. Opening a card only reads them:
 * nothing here asks a model to write one, so a card without a saved
 * explanation shows its own record.
 */
export function useMapCopy(
  data: Dataset,
  focused: string | null,
  zh: boolean,
  visibleSteps?: SubmapStep[],
  sessionId?: string,
  readingKey: string | null = focused,
  pinnedFoundationId?: string | null,
) {
  const locale = zh ? "zh-CN" as const : "en-US" as const;
  const source = data.kind === "live" ? "project" : "dataset";
  const name = data.id.replace(/^live:/, "");
  const preview = readerPreview();
  const foundationChoice = useSelectedFoundation(sessionId ?? name, locale);
  const foundationId = preview === 'question-foundation' ? pinnedFoundationId === undefined ? foundationChoice.id : pinnedFoundationId : null;
  const key = mapCopyKey(source, name, locale, sessionId, preview, foundationId);
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
  const steps = useMemo(
    () => visibleSteps ?? (task ? buildSubmap(task, data.events, zh) : []),
    [task, data.events, zh, visibleSteps],
  );
  return { copy: copy.data, ready: copy.isFetched, questionContext: { locale, preview, foundationId },
    readingRequest: focusedCopyRequests(data, steps, focused, readingKey).find(card => card.key === readingKey) };
}
