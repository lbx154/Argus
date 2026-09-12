import { emptyMissionView } from '../../../core/src/missionView';
import type { Snapshot } from '../../../core/src/types';
import type { Dataset, MapTask } from '../map/model';
import type { MapCopy, ReaderBrief } from '../map/presentation';
import { READER_BRIEF_VERSION } from './model';

export const readerBrief: ReaderBrief = {
  why: 'Checking the stated conditions prevents extending a limited result to every case.',
  concept: { name: 'Counterexample', explanation: 'One exception disproves a claim about every case.',
    example: 'Illustration: 3 is a counterexample to “every integer is even.”', connection: 'This task checks whether the proposed exception applies.' },
  scope: 'The executor reported a result. Independent review has not been recorded; the general problem remains open.',
  next: 'The next step has not been recorded.',
};

export const source: Dataset = {
  id: 'live:s-research', kind: 'live', title: 'Study', description: '', read_only: false, cursor: 'cursor-1',
  tasks: [
    { id: 'a', title: 'Check a boundary case', objective: 'Check the original conditions before generalizing', status: 'running', started_ts: 2, attempt: 1, revision: 'a1', content_revision: 'content-a' },
    { id: 'b', title: 'Different task', objective: 'A separate objective', status: 'running', started_ts: 20, attempt: 1, revision: 'b1', content_revision: 'content-b' },
  ],
  events: [
    { id: 'old-review', item_id: 'a', type: 'round.review.completed', ts: 1, text: 'An older attempt passed', status: 'done', revision: 'old' },
    { id: 'start-a', item_id: 'a', type: 'life.mission.started', ts: 2, text: '', revision: 'start-a1' },
    { id: 'main-a', item_id: 'a', type: 'round.main.completed', ts: 4, text: 'A result was reported', round_index: 1, revision: 'main-a1' },
    { id: 'tools-a', item_id: 'a', type: 'work.segment', ts: 5, text: 'Read a file', revision: 'tool1' },
    { id: 'review-b', item_id: 'b', type: 'round.review.completed', ts: 22, text: 'Other task accepted', status: 'done', revision: 'review-b1' },
  ],
};

export function inputs(taskId = 'a') {
  const task = source.tasks.find(item => item.id === taskId)!;
  const view = emptyMissionView();
  view.mission = { ...view.mission, id: task.id, title: task.title, objective: task.objective, status: 'working', started_at: task.started_ts ?? 2 };
  const snapshot: Snapshot = {
    session: { id: 's-research', display_name: 'Study', objective: 'Understand the original problem', last_active: 1, cwd: '/workspace' },
    daemon: { alive: true, pid: 1, uptime_seconds: 1, backend: 'test', global_daily_cap_usd: null },
    backlog: [{ id: task.id, title: task.title, objective: task.objective, status: task.status, started_ts: task.started_ts, priority: 100 }], roles: [], recent_events: [],
  };
  return { sid: 's-research', snapshot, view, active: true, locale: 'en-US' };
}

export function completedCopy(task: MapTask, eventIds: string[], generatedAt = 10): MapCopy {
  return { cards: { [task.id]: { title: task.title, summary: 'A bounded result', detail: 'Original evidence remains available.',
    reader_brief: readerBrief, generated_at: generatedAt, version: READER_BRIEF_VERSION, task_status: task.status, task_revision: task.revision,
    task_content_revision: task.content_revision, event_ids: eventIds, copy_revision: generatedAt,
    event_revisions: eventIds.map(id => source.events.find(event => event.id === id)?.revision ?? id),
  } }, relations: [], available: true, version: READER_BRIEF_VERSION, cache_revision: generatedAt };
}
