import type { Dataset, MapEvent, MapTask } from "./model";
import type { SubmapStep } from "./submap";
import { humanizeHarnessNote, readableRecord } from "./submap";

/** Why a task waits on the reader: its open question, or the reason its last
 * attempt failed, said the way the cards say it. A record that is only a
 * technical receipt becomes the colleague's sentence for it; a record that
 * says nothing is reported as exactly that. */
export function attentionReason(task: MapTask, events: MapEvent[], zh: boolean): string {
  if (task.pending_question) return task.pending_question;
  const failure = events.filter((event) => event.item_id === task.id &&
    (event.status === "failed" || event.success === false || event.type.endsWith(".failed")))
    .sort((a, b) => b.ts - a.ts)[0];
  const raw = failure?.reason || failure?.text || "";
  const note = humanizeHarnessNote(raw, zh);
  const cut = note.receipt ? raw.lastIndexOf(note.receipt) : -1;
  const prose = readableRecord(cut >= 0 ? raw.slice(0, cut) : raw);
  return note.summary || prose ||
    (zh ? "这项任务没有完成，记录里没有写明原因。" : "This task did not finish, and the record does not say why.");
}

export interface CardCopy {
  copy_revision?: number;
  version?: number;
  model_revision?: string;
  title: string;
  summary: string;
  detail: string;
  generated_at: number;
  task_revision?: string;
  task_content_revision?: string;
  task_status?: string;
  event_ids?: string[];
  event_revisions?: string[];
  input_revision?: string;
}
export interface MapRelation {
  source: string;
  target: string;
  label: string;
  evidence: string;
  kind: "semantic";
}
export interface MapCopy {
  cache_revision?: number;
  version?: number;
  model_revision?: string;
  cards: Record<string, CardCopy>;
  relations: MapRelation[];
  available?: boolean;
  retry_after?: number;
}

export function mergeMapCopy(previous: MapCopy | undefined, result: MapCopy, requestedRevision?: string): MapCopy {
  const settingsChanged = previous?.model_revision && previous.model_revision !== requestedRevision;
  const cards = { ...previous?.cards };
  for (const [key, card] of Object.entries(result.cards)) {
    if (settingsChanged && cards[key]?.model_revision === previous.model_revision) continue;
    const old = cards[key];
    if (!old || (card.copy_revision ?? 0) > (old.copy_revision ?? 0) ||
      ((card.copy_revision ?? 0) === (old.copy_revision ?? 0) &&
        (card.generated_at > old.generated_at ||
          (card.generated_at === old.generated_at && !old.input_revision)))) cards[key] = card;
  }
  const older = (result.cache_revision ?? 0) < (previous?.cache_revision ?? 0);
  return {
    ...previous,
    ...result,
    cards,
    cache_revision: Math.max(result.cache_revision ?? 0, previous?.cache_revision ?? 0),
    relations: (settingsChanged || older) && previous ? previous.relations : result.relations,
    available: result.available ?? true,
    ...(settingsChanged ? { model_revision: previous.model_revision, available: previous.available } : {}),
  };
}

export function needsCardCopy(
  card: CardRequest,
  data: Dataset,
  copy?: MapCopy,
  eventIndex?: Map<string, MapEvent>,
): boolean {
  const saved = copy?.cards[card.key];
  const task = data.tasks.find((t) => t.id === card.task_id);
  if (!saved || !task) return true;
  const dynamic = [task.id, task.id + ":active", task.id + ":outcome"].includes(card.key);
  if (dynamic || !saved.task_content_revision || !task.content_revision) {
    if (task.revision && saved.task_revision !== task.revision) return true;
    if (dynamic && saved.task_status !== task.status) return true;
  } else if (saved.task_content_revision !== task.content_revision) return true;
  const ids = saved.event_ids || [];
  // A full-history summary may contain additional valid evidence when only
  // current progress is being viewed. Do not rewrite it with a poorer subset.
  return card.event_ids.some((id) => {
    const index = ids.indexOf(id);
    const event = eventIndex ? eventIndex.get(id) : data.events.find((e) => e.id === id);
    return index < 0 || (saved.event_revisions && event?.revision &&
      saved.event_revisions[index] !== event.revision);
  }) || (!dynamic && JSON.stringify(card.event_ids) !== JSON.stringify(ids));
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
  team_id?: string;
  team_task_id?: string;
  /** Locale at quote time; the server renders its expansion to match. */
  lang?: string;
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
          (ref.team_id === undefined || typeof ref.team_id === "string") &&
          (ref.team_task_id === undefined || typeof ref.team_task_id === "string") &&
          (ref.lang === undefined || typeof ref.lang === "string") &&
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
/** Ids of the latest main/review outcomes of a task, optionally as of a moment. */
function outcomeIds(data: Dataset, id: string, through = Infinity): string[] {
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
}
/** One request per step of a task's sub-map, in reading order. */
export function stepRequests(data: Dataset, task: MapTask, steps: SubmapStep[]): CardRequest[] {
  return steps.map((s) => ({
    key: s.id,
    task_id: task.id,
    kind: s.kind,
    event_ids: [
      ...new Set([
        ...(s.kind === "result" ? outcomeIds(data, task.id, s.ts) : []),
        ...s.eventIds,
      ]),
    ].slice(-16),
  }));
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
  const roots = sorted.map((t) => ({
    key: t.id,
    task_id: t.id,
    kind: "task",
    event_ids: [
      ...new Set([
        ...outcomeIds(data, t.id),
        ...data.events
          .filter((e) => e.item_id === t.id)
          .slice(-2)
          .map((e) => e.id),
      ]),
    ],
  }));
  const children = focusedTask ? stepRequests(data, focusedTask, steps) : [];
  return [...roots.slice(0, 1), ...children, ...roots.slice(1)];
}
