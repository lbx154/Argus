import type { MissionView, Snapshot } from '../../../core/src/types';
import type { MapSelection } from '../map/incremental';
import type { Dataset, MapEvent, MapTask } from '../map/model';
import { needsCardCopy, referenceText, type CardRequest, type MapCopy, type ReaderBrief } from '../map/presentation';

/** Compatible display schema; the server's copy.version controls regeneration. */
export const READER_BRIEF_VERSION = 14;
const SEMANTIC_EVENTS = new Set([
  'life.planner.task_added', 'life.mission.started', 'round.main.completed', 'round.review.completed',
  'turn.asked', 'turn.replied',
]);

export function briefCopyKey(sid: string, locale: string) {
  return ['map-copy', 'project', sid, locale, sid] as const;
}

export function briefSelection(snapshot: Snapshot, view: MissionView): MapSelection {
  const started = view.mission.started_at ?? snapshot.backlog.find(item => item.id === view.mission.id)?.started_ts ?? 0;
  const since = Number.isFinite(started) && started >= 0 ? started : 0;
  return { mode: 'current', since, eventSince: since, taskId: view.mission.id };
}

export function briefLiveKey(sid: string, selection: MapSelection) {
  return ['research-brief-map', sid, selection.taskId, selection.since, selection.eventSince] as const;
}

export function currentBriefData(data: Dataset, sid: string, taskId: string): Dataset {
  if (data.id !== `live:${sid}` || !Array.isArray(data.tasks) || !Array.isArray(data.events)) {
    throw new Error('The task records do not match this project.');
  }
  return { ...data, tasks: data.tasks.filter(task => task.id === taskId), events: data.events.filter(event => event.item_id === taskId) };
}

/** Stable task milestones only. Public deltas and tool updates do not buy new prose. */
export function briefEvidence(data: Dataset | undefined, task: MapTask | undefined, startedAt = 0): MapEvent[] {
  if (!data || !task) return [];
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

export function briefRequest(task: MapTask, events: readonly MapEvent[]): CardRequest {
  return { key: task.id, task_id: task.id, kind: 'task', event_ids: events.map(event => event.id) };
}

/** Exclude mutable collection cursors, task revision noise and tool/text events. */
export function briefInputSignature(task: MapTask | undefined, events: readonly MapEvent[]): string {
  if (!task) return '';
  return JSON.stringify({
    task: {
      id: task.id, title: task.title, objective: task.objective, status: task.status, summary: task.summary,
      acceptance_check: task.acceptance_check, goal_contribution: task.goal_contribution,
      plan_hypothesis: task.plan_hypothesis, non_goals: task.non_goals, outcome: task.outcome,
      pending_question: task.pending_question, started_ts: task.started_ts, finished_ts: task.finished_ts,
      attempt: task.attempt,
    },
    events: events.map(event => [event.id, event.revision ?? event]),
  });
}

export function isReaderBrief(value: unknown): value is ReaderBrief {
  if (!value || typeof value !== 'object') return false;
  const brief = value as Record<string, unknown>;
  if (!['why', 'scope', 'next'].every(key => typeof brief[key] === 'string' && (brief[key] as string).trim())) return false;
  if (brief.concept === null) return true;
  if (!brief.concept || typeof brief.concept !== 'object') return false;
  const concept = brief.concept as Record<string, unknown>;
  return ['name', 'explanation', 'example', 'connection'].every(key => typeof concept[key] === 'string' && (concept[key] as string).trim());
}

export function needsBrief(data: Dataset | undefined, task: MapTask | undefined, events: MapEvent[], copy?: MapCopy): boolean {
  if (!data || !task) return false;
  return (copy?.cards[task.id]?.version ?? 0) < READER_BRIEF_VERSION
    || !isReaderBrief(copy?.cards[task.id]?.reader_brief)
    || needsCardCopy(briefRequest(task, events), data, copy, new Map(events.map(event => [event.id, event])));
}

export function oldBriefService(copy: MapCopy | undefined): boolean {
  return !!copy && typeof copy.version === 'number' && copy.version < READER_BRIEF_VERSION;
}

export function questionAboutStep(sid: string, task: MapTask, events: readonly MapEvent[], zh: boolean): string {
  return referenceText({ source: `live:${sid}`, task_id: task.id, task_title: task.title,
    event_ids: events.map(event => event.id), lang: zh ? 'zh' : 'en' })
    + (zh ? '请用不需要专业背景的语言继续解释这一步：它为什么有用，已经核验了什么，还有哪些条件或缺口？'
      : 'Explain this step without assuming specialist knowledge: why does it help, what has been checked, and which conditions or gaps remain?');
}
