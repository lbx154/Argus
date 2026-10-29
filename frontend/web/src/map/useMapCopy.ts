import { useMemo } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api";
import { MAP_CAPABILITY, useCapability } from "../lib/capabilities";
import type { Dataset, MapEvent, MapTask } from "./model";
import { buildSubmap, type SubmapStep } from "./submap";
import {
  mergeMapCopy,
  requestsFor,
  type CardRequest,
  type MapCopy,
} from "./presentation";

const SEMANTIC_EVENTS = new Set([
  'life.planner.task_added', 'life.mission.started', 'round.main.completed', 'round.review.completed',
  'turn.asked', 'turn.replied',
]);

/** Stable task milestones only. Public deltas and tool updates do not buy new prose. */
function taskEvidence(data: Dataset, task: MapTask): MapEvent[] {
  const startedAt = task.started_ts ?? 0;
  const matched = data.events.filter(event => event.item_id === task.id && SEMANTIC_EVENTS.has(event.type)
    && Number.isFinite(event.ts) && event.ts >= startedAt);
  const latestStart = Math.max(startedAt, ...matched.filter(event => event.type === 'life.mission.started').map(event => event.ts));
  const current = matched.filter(event => event.ts >= latestStart);
  const selected = new Map<string, MapEvent>();
  for (const kind of SEMANTIC_EVENTS) {
    const limit = kind.startsWith('round.') ? 2 : 1;
    for (const event of current.filter(item => item.type === kind).sort((a, b) => a.ts - b.ts || a.id.localeCompare(b.id)).slice(-limit)) {
      selected.set(event.id, event);
    }
  }
  return [...selected.values()].sort((a, b) => a.ts - b.ts || a.id.localeCompare(b.id));
}

/** The card a reader opened and the records it covers. */
export function focusedCopyRequests(data: Dataset, steps: SubmapStep[], focused: string | null, readingKey: string | null = focused): CardRequest[] {
  if (!focused || !readingKey) return [];
  const task = data.tasks.find(task => task.id === focused);
  if (!task || task.turn_kind === 'qa') return [];
  if (readingKey === focused) return [{ key: task.id, task_id: task.id, kind: 'task', event_ids: taskEvidence(data, task).map(event => event.id) }];
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
) {
  const locale = zh ? "zh-CN" as const : "en-US" as const;
  const source = data.kind === "live" ? "project" : "dataset";
  const name = data.id.replace(/^live:/, "");
  const key = ['map-copy', source, name, locale, sessionId];
  const queryClient = useQueryClient();
  // A server that keeps no explanations has none to wait for.
  const kept = useCapability(MAP_CAPABILITY);
  const copy = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) => {
      const result = await api.mapCopy(source, name, locale, signal, sessionId);
      const previous = queryClient.getQueryData<MapCopy>(key);
      return mergeMapCopy(previous, result, previous?.model_revision);
    },
    enabled: kept,
    staleTime: Infinity,
    gcTime: 2 * 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });
  const task = data.tasks.find((t) => t.id === focused);
  const steps = useMemo(
    () => visibleSteps ?? (task ? buildSubmap(task, data.events, zh) : []),
    [task, data.events, zh, visibleSteps],
  );
  return { copy: copy.data, ready: !kept || copy.isFetched, readingRequest: focusedCopyRequests(data, steps, focused, readingKey).find(card => card.key === readingKey) };
}
