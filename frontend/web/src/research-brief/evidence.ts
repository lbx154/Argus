import type { MapEvent, MapTask } from '../map/model';
import type { CardCopy, CardSourceSnapshot } from '../map/presentation';

export type EvidenceState = 'snapshot' | 'verified' | 'missing' | 'changed' | 'unverified' | 'owner-mismatch';
export interface UsedEvidence {
  id: string;
  revision?: string;
  state: EvidenceState;
  record?: Record<string, unknown>;
  fullRecord?: MapEvent;
}
export interface EvidenceTask {
  state: EvidenceState;
  record?: Record<string, unknown>;
  fullRecord?: MapTask;
}
export interface ReaderEvidenceSelection {
  cardKey: string;
  taskId: string;
  mode: 'snapshot' | 'legacy' | 'none';
  invalidSnapshot: boolean;
  snapshot?: CardSourceSnapshot;
  usedTask?: EvidenceTask;
  used: UsedEvidence[];
  currentTask?: MapTask;
  current: { record: MapEvent; reason: 'new' | 'changed' | 'unverified' }[];
}

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const revision = (value: unknown) => typeof value === 'string' && value.length > 0 ? value : undefined;

function sourceSnapshot(card: CardCopy, cardKey: string, taskId: string): CardSourceSnapshot | undefined {
  const value = card.source_snapshot;
  if (!object(value) || value.version !== 1 || value.card_key !== cardKey || value.task_id !== taskId
    || !object(value.task) || !Array.isArray(value.events) || !value.events.every(object)
    || !Array.isArray(value.source_ids) || !value.source_ids.every(id => typeof id === 'string')) return undefined;
  return value;
}

/** Goal/content versions are authoritative; a changed goal never falls back to a status revision. */
function taskVersion(card: CardCopy, task: MapTask): 'verified' | 'changed' | 'unverified' {
  const oldContent = revision(card.task_content_revision), currentContent = revision(task.content_revision);
  if (oldContent && currentContent) return oldContent === currentContent ? 'verified' : 'changed';
  const oldTask = revision(card.task_revision), currentTask = revision(task.revision);
  if (oldTask && currentTask) return oldTask === currentTask ? 'verified' : 'changed';
  return 'unverified';
}

/** Select source versions without fetching, generating, or treating a newer record as an old source. */
export function selectReaderEvidence({ cardKey, taskId, card, task, loadedEvents = [], currentEvents = [] }: {
  cardKey: string; taskId: string; card?: CardCopy; task?: MapTask;
  loadedEvents?: readonly MapEvent[]; currentEvents?: readonly MapEvent[];
}): ReaderEvidenceSelection {
  const currentTask = task?.id === taskId ? task : undefined;
  if (!card) return { cardKey, taskId, mode: 'none', invalidSnapshot: false, used: [], currentTask,
    current: currentEvents.filter(event => event.item_id === taskId).map(record => ({ record, reason: 'new' })) };
  const snapshot = sourceSnapshot(card, cardKey, taskId);
  const selected: ReaderEvidenceSelection = { cardKey, taskId, mode: snapshot ? 'snapshot' : 'legacy',
    invalidSnapshot: card.source_snapshot != null && !snapshot, snapshot, used: [], current: [] };
  if (snapshot) {
    selected.usedTask = { state: 'snapshot', record: snapshot.task };
    if (currentTask && revision(card.task_revision) && card.task_revision === currentTask.revision
      && taskVersion(card, currentTask) === 'verified') selected.usedTask.fullRecord = currentTask;
    selected.used = snapshot.source_ids.map(id => {
      const record = snapshot.events.find(event => event.id === id && event.item_id === taskId);
      if (record) {
        const expected = revision(record.revision);
        const fullRecord = expected ? [...loadedEvents, ...currentEvents].find(event => event.id === id
          && event.item_id === taskId && revision(event.revision) === expected) : undefined;
        return { id, revision: expected, state: 'snapshot', record, fullRecord };
      }
      return { id, state: snapshot.events.some(event => event.id === id) ? 'owner-mismatch' : 'missing' };
    });
  } else {
    selected.usedTask = currentTask ? { state: taskVersion(card, currentTask) } : { state: 'missing' };
    if (selected.usedTask.state === 'verified' && currentTask) {
      // Legacy caches did not retain the projected task state. Only the verified goal/name can be recovered.
      selected.usedTask.record = { title: currentTask.title, ...(currentTask.objective !== undefined ? { objective: currentTask.objective } : {}) };
    }
    selected.used = (card.event_ids ?? []).map((id, index) => {
      const expected = revision(card.event_revisions?.[index]);
      const owned = loadedEvents.filter(event => event.id === id && event.item_id === taskId);
      const exact = expected && owned.find(event => revision(event.revision) === expected);
      if (exact) return { id, revision: expected, state: 'verified', record: { ...exact } };
      const state = !owned.length ? loadedEvents.some(event => event.id === id) ? 'owner-mismatch' : 'missing'
        : !expected || owned.some(event => !revision(event.revision)) ? 'unverified' : 'changed';
      return { id, revision: expected, state };
    });
  }
  if (currentTask) {
    // Legacy recovery verifies only the goal. Keep the rest of the loaded task separately accessible.
    if (!selected.usedTask?.fullRecord) selected.currentTask = currentTask;
  }
  const sourceIds = new Set(selected.used.map(row => row.id));
  const currentCandidates = new Map<string, MapEvent>();
  for (const event of [...loadedEvents.filter(row => sourceIds.has(row.id)), ...currentEvents]) {
    if (event.item_id === taskId) currentCandidates.set(JSON.stringify([event.id, event.revision ?? null]), event);
  }
  selected.current = [...currentCandidates.values()].flatMap<ReaderEvidenceSelection['current'][number]>(record => {
    const used = selected.used.find(row => row.id === record.id);
    if (!used) return [{ record, reason: 'new' as const }];
    const actual = revision(record.revision);
    if (used.record && used.revision && actual === used.revision) return [];
    return [{ record, reason: actual && used.revision && actual !== used.revision ? 'changed' as const : 'unverified' as const }];
  });
  return selected;
}

export function hasTruncatedFields(record: Record<string, unknown>): boolean {
  return Object.entries(record).some(([key, value]) => key.endsWith('_truncated') && value === true);
}

export function evidenceDates(selection: ReaderEvidenceSelection): number[] {
  const timestamps = selection.used.flatMap(row => typeof row.record?.ts === 'number' && Number.isFinite(row.record.ts) && row.record.ts > 0 ? [row.record.ts] : []);
  return timestamps.length ? [Math.min(...timestamps), Math.max(...timestamps)] : [];
}
