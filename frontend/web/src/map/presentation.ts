import type { Dataset } from "./model";
import type { SubmapStep } from "./submap";

export interface CardCopy {
  version?: number;
  model_revision?: string;
  title: string;
  summary: string;
  detail: string;
  generated_at: number;
  task_revision?: string;
  task_status?: string;
  event_ids?: string[];
}
export interface MapRelation {
  source: string;
  target: string;
  label: string;
  evidence: string;
  kind: "semantic";
}
export interface MapCopy {
  version?: number;
  model_revision?: string;
  cards: Record<string, CardCopy>;
  relations: MapRelation[];
  available?: boolean;
  retry_after?: number;
}

export function mergeMapCopy(previous: MapCopy | undefined, result: MapCopy, requestedRevision?: string): MapCopy {
  const settingsChanged = previous?.model_revision && previous.model_revision !== requestedRevision;
  return {
    ...previous,
    ...result,
    available: result.available ?? true,
    ...(settingsChanged ? { model_revision: previous.model_revision, available: previous.available } : {}),
  };
}
export interface CardRequest {
  key: string;
  task_id: string;
  kind: string;
  event_ids: string[];
}
export interface CardReference {
  source: string;
  task_id: string;
  task_title: string;
  part?: number;
  step_id?: string;
  step_title?: string;
  event_ids: string[];
}
export const referenceText = (ref: CardReference) =>
  `[[Argus引用 ${JSON.stringify(ref)}]]\n`;
export function splitDraft(value: string) {
  const refs: CardReference[] = [];
  const lines = value.split("\n").filter((line) => {
    if (line.startsWith("[[Argus引用 ") && line.endsWith("]]")) {
      try {
        const ref = JSON.parse(line.slice(10, -2));
        if (
          typeof ref.task_id === "string" &&
          typeof ref.source === "string" &&
          typeof ref.task_title === "string" &&
          (ref.part === undefined ||
            (Number.isInteger(ref.part) && ref.part > 0)) &&
          (ref.step_title === undefined ||
            typeof ref.step_title === "string") &&
          (ref.step_id === undefined || typeof ref.step_id === "string") &&
          Array.isArray(ref.event_ids) &&
          ref.event_ids.every((id: unknown) => typeof id === "string")
        ) {
          refs.push(ref);
          return false;
        }
      } catch {
        /* Keep malformed text editable. */
      }
    }
    return true;
  });
  return { refs, text: lines.join("\n").replace(/^\n+/, "") };
}
export function requestsFor(
  data: Dataset,
  steps: SubmapStep[],
  focused: string | null,
): CardRequest[] {
  const focusedTask = data.tasks.find((t) => t.id === focused);
  const sorted = focusedTask
    ? [focusedTask, ...data.tasks.filter((t) => t.id !== focused)]
    : data.tasks;
  const outcomes = (id: string, through = Infinity) => {
    const start = Math.max(
      -Infinity,
      ...data.events
        .filter(
          (e) =>
            e.item_id === id &&
            e.type === "life.mission.started" &&
            e.ts <= through,
        )
        .map((e) => e.ts),
    );
    return data.events
      .filter(
        (e) =>
          e.item_id === id &&
          e.ts >= start &&
          e.ts <= through &&
          ["round.main.completed", "round.review.completed"].includes(e.type),
      )
      .slice(-2)
      .map((e) => e.id);
  };
  const roots = sorted.map((t) => ({
    key: t.id,
    task_id: t.id,
    kind: "task",
    event_ids: [
      ...new Set([
        ...outcomes(t.id),
        ...data.events
          .filter((e) => e.item_id === t.id)
          .slice(-2)
          .map((e) => e.id),
      ]),
    ],
  }));
  const children = focusedTask
    ? steps.map((s) => ({
        key: s.id,
        task_id: focusedTask.id,
        kind: s.kind,
        event_ids: [
          ...new Set([
            ...(s.kind === "result" ? outcomes(focusedTask.id, s.ts) : []),
            ...s.eventIds,
          ]),
        ].slice(-16),
      }))
    : [];
  return [...roots.slice(0, 1), ...children, ...roots.slice(1)];
}
