import type { EventMsg, Role } from './types.js';
import { eventKey } from './events.js';

export type ActivityStatus = 'running' | 'done' | 'error';
export const ACTIVE_ACTIVITY_WINDOW_S = 120;

export interface ActivityView {
  id: string;
  role: string;
  label: string;
  detail: string;
  status: ActivityStatus;
  startedTs: number;
  updatedTs: number;
  elapsedS: number;
  model: string;
  backend: string;
  milestone: boolean;
}

const S = (event: EventMsg, key: string): string =>
  String((event as Record<string, unknown>)[key] ?? '').trim();

const N = (event: EventMsg, key: string): number => {
  const value = Number((event as Record<string, unknown>)[key]);
  return Number.isFinite(value) ? value : 0;
};

function oneLine(value: unknown, max = 180): string {
  const line = String(value ?? '').split('\n').find((part) => part.trim())?.trim() ?? '';
  return line.length <= max ? line : `${line.slice(0, max - 1).trimEnd()}…`;
}

function roleForRunLabel(runLabel: string): string {
  const label = runLabel.toLowerCase();
  if (label.includes('compaction_batch') || label.includes('compaction-batch')) return 'maintenance';
  if (label.includes('reviewer') || label.startsWith('review')) return 'reviewer';
  if (label.includes('planner') || label.startsWith('plan')) return 'planner';
  if (
    label.includes('manager') ||
    label.startsWith('router') ||
    label.startsWith('chat-') ||
    label.startsWith('simple-')
  ) return 'manager';
  return 'engineer';
}

function roundFrom(label: string): string {
  const match = label.match(/(?:^|[-_.])r(?:ound)?[-_.]?(\d+)/i);
  return match ? ` · round ${match[1]}` : '';
}

function describeRun(runLabel: string): { role: string; label: string } {
  const lower = runLabel.toLowerCase();
  const role = roleForRunLabel(runLabel);
  if (role === 'maintenance') {
    return { role, label: 'compacting the reusable skill library' };
  }
  if (lower === 'matcher' || lower.includes('skill-match')) {
    return { role: 'engineer', label: 'matching reusable skills' };
  }
  if (lower === 'idea-search' || lower.includes('idea.search')) {
    return { role: 'engineer', label: 'searching recent papers + generating candidate ideas' };
  }
  if (lower.includes('distill') || lower.includes('scientist')) {
    return { role: 'engineer', label: 'building a task-specific playbook' };
  }
  if (role === 'reviewer') return { role, label: `reviewing evidence${roundFrom(lower)}` };
  if (role === 'planner') return { role, label: 'planning the next work' };
  if (lower.includes('vertical')) return { role: 'manager', label: 'choosing the task workflow' };
  if (role === 'manager') return { role, label: 'handling the operator request' };
  if (lower.startsWith('engineer') || lower.startsWith('main')) {
    return { role: 'engineer', label: `working on the mission${roundFrom(lower)}` };
  }
  const readable = runLabel.replace(/[._-]+/g, ' ').replace(/\s+/g, ' ').trim();
  return { role, label: readable ? `running ${readable}` : 'working' };
}

function activityId(runLabel: string, callId: string, ts: number): string {
  const lower = runLabel.toLowerCase();
  if (lower === 'matcher') return 'phase:matcher';
  if (lower === 'idea-search') return 'phase:idea-search';
  return callId || `run:${runLabel || 'unknown'}:${ts}`;
}

function startedFromCallId(callId: string, fallback: number): number {
  const millis = Number(callId.split('-', 1)[0]);
  return Number.isFinite(millis) && millis > 0 ? millis / 1000 : fallback;
}

function modelFromSafeStreamMetadata(event: EventMsg): string {
  if (S(event, 'type') !== 'agent.io.stream') return '';
  try {
    const inner = JSON.parse(S(event, 'line')) as Record<string, unknown>;
    const data = inner.data && typeof inner.data === 'object'
      ? inner.data as Record<string, unknown>
      : {};
    const model = String(inner.model ?? data.model ?? '').trim();
    // Model identifiers are small machine tokens. Never surface arbitrary
    // nested stream content through this metadata-only recovery path.
    return /^[A-Za-z0-9._:/+-]{1,80}$/.test(model) ? model : '';
  } catch {
    return '';
  }
}

function agentIoActivity(event: EventMsg): EventMsg | null {
  const type = S(event, 'type');
  if (!['agent.io.start', 'agent.io.stream', 'agent.io.complete', 'agent.io.error'].includes(type)) return event;

  const runLabel = S(event, 'run_label');
  const callId = S(event, 'call_id');
  const ts = N(event, 'ts') || Date.now() / 1000;
  const descriptor = describeRun(runLabel);
  const exitCode = (event as Record<string, unknown>).exit_code;
  const failed =
    type === 'agent.io.error' ||
    (typeof exitCode === 'number' && exitCode !== 0) ||
    (event as Record<string, unknown>).turn_failed === true ||
    Boolean(S(event, 'fatal_error'));
  const status: ActivityStatus = ['agent.io.start', 'agent.io.stream'].includes(type)
    ? 'running'
    : failed
    ? 'error'
    : 'done';
  const safe: EventMsg = {
    type: 'role.activity',
    activity_id: activityId(runLabel, callId, ts),
    role: descriptor.role,
    label: descriptor.label,
    status,
    run_label: runLabel,
    ts,
  };
  if (status === 'running') safe.started_ts = startedFromCallId(callId, ts);
  if (type === 'agent.io.stream') safe.heartbeat = true;
  const model = S(event, 'model') || modelFromSafeStreamMetadata(event);
  const backend = S(event, 'backend');
  if (model) safe.model = model;
  if (backend) safe.backend = backend;
  if (failed) safe.error = oneLine(S(event, 'fatal_error') || S(event, 'error') || `exit ${exitCode}`);
  return safe;
}

function matcherActivity(event: EventMsg): EventMsg {
  const text = S(event, 'text');
  const lower = text.toLowerCase();
  const ts = N(event, 'ts') || Date.now() / 1000;
  const picked = lower.includes('matcher picked:');
  const noMatch = lower.includes('no match') || lower.includes('matched: none');
  let label = 'matching reusable skills';
  let detail = oneLine(text, 120);
  if (picked) {
    const skill = text.split(':').slice(1).join(':').split('(')[0].trim();
    label = skill ? `selected skill · ${skill}` : 'selected a reusable skill';
    detail = '';
  } else if (noMatch) {
    label = 'no reusable skill matched';
    detail = '';
  } else {
    const query = text.match(/\(([^)]+)\) against (\d+) candidates/i);
    const narrowed = text.match(/pool\s+(\d+)→(\d+)/i);
    if (query) detail = `${query[1]} · ${query[2]} candidates`;
    else if (narrowed) detail = `${narrowed[1]}→${narrowed[2]} candidates`;
  }
  return {
    type: 'role.activity',
    activity_id: 'phase:matcher',
    role: 'engineer',
    label,
    detail,
    status: picked || noMatch ? 'done' : 'running',
    milestone: picked || noMatch,
    started_ts: ts,
    ts,
  };
}

function ideaSearchActivity(event: EventMsg): EventMsg {
  const type = S(event, 'type');
  const ts = N(event, 'ts') || Date.now() / 1000;
  const count = N(event, 'count');
  if (type === 'idea.search.completed') {
    return {
      type: 'role.activity',
      activity_id: 'phase:idea-search',
      role: 'engineer',
      label: count ? `generated ${count} candidate ideas` : 'candidate idea search completed',
      status: 'done',
      milestone: true,
      ts,
    };
  }
  if (type === 'idea.search.skipped') {
    return {
      type: 'role.activity',
      activity_id: 'phase:idea-search',
      role: 'engineer',
      label: 'candidate idea search skipped',
      status: 'done',
      ts,
    };
  }
  return {
    type: 'role.activity',
    activity_id: 'phase:idea-search',
    role: 'engineer',
    label: 'searching recent papers + generating candidate ideas',
    status: 'running',
    started_ts: ts,
    ts,
  };
}

/** Convert a wire/audit event into a small operator-safe event. */
export function normalizeOperatorEvent(event: EventMsg): EventMsg | null {
  const type = S(event, 'type');
  if (type.startsWith('agent.io.')) return agentIoActivity(event);
  if (type === 'match.info') return matcherActivity(event);
  if (type.startsWith('idea.search.')) return ideaSearchActivity(event);
  return event;
}

function mergeActivity(previous: EventMsg, next: EventMsg): EventMsg {
  const previousStart = N(previous, 'started_ts');
  const nextStart = N(next, 'started_ts');
  const started = previousStart && nextStart
    ? Math.min(previousStart, nextStart)
    : previousStart || nextStart || N(previous, 'ts') || N(next, 'ts');
  const merged: EventMsg = { ...previous, ...next, started_ts: started };
  const status = S(merged, 'status');
  const updated = N(merged, 'ts');
  if (status !== 'running' && started && updated >= started) {
    merged.elapsed_s = updated - started;
  }
  return merged;
}

/** Normalize, dedupe and coalesce one event into the bounded UI buffer. */
export function reduceOperatorEvent(
  events: EventMsg[],
  wireEvent: EventMsg,
  max = 400,
): EventMsg[] {
  const event = normalizeOperatorEvent(wireEvent);
  if (!event) return events;
  if (event.type === 'role.activity') {
    const id = S(event, 'activity_id');
    const index = events.findIndex(
      (candidate) => candidate.type === 'role.activity' && S(candidate, 'activity_id') === id,
    );
    if (index >= 0) {
      if (
        (event as Record<string, unknown>).heartbeat === true &&
        N(event, 'ts') - N(events[index], 'ts') < 1
      ) return events;
      const copy = events.slice();
      copy[index] = mergeActivity(copy[index], event);
      return copy;
    }
  } else {
    const key = eventKey(event);
    if (events.some((candidate) => eventKey(candidate) === key)) return events;
  }
  const next = events.concat(event);
  return next.length > max ? next.slice(next.length - max) : next;
}

function toActivityView(event: EventMsg): ActivityView | null {
  if (event.type !== 'role.activity') return null;
  const started = N(event, 'started_ts') || N(event, 'ts');
  const updated = N(event, 'ts') || started;
  return {
    id: S(event, 'activity_id'),
    role: S(event, 'role') || 'engineer',
    label: S(event, 'label') || 'working',
    detail: S(event, 'detail'),
    status: (S(event, 'status') || 'running') as ActivityStatus,
    startedTs: started,
    updatedTs: updated,
    elapsedS: N(event, 'elapsed_s') || Math.max(0, updated - started),
    model: S(event, 'model'),
    backend: S(event, 'backend'),
    milestone: Boolean((event as Record<string, unknown>).milestone),
  };
}

export function runningActivities(
  events: EventMsg[],
  excludeRoles: string[] = [],
): ActivityView[] {
  const excluded = new Set(excludeRoles);
  const now = Date.now() / 1000;
  return events
    .map(toActivityView)
    .filter(
      (activity): activity is ActivityView =>
        activity?.status === 'running' &&
        !excluded.has(activity.role) &&
        now - activity.updatedTs <= ACTIVE_ACTIVITY_WINDOW_S,
    )
    .sort((a, b) => b.updatedTs - a.updatedTs);
}

export function latestRunningActivity(
  events: EventMsg[],
  excludeRoles: string[] = [],
): ActivityView | null {
  return runningActivities(events, excludeRoles)[0] ?? null;
}

/** Overlay live wire activity immediately instead of waiting for snapshot polling. */
export function overlayRoleActivities(roles: Role[], events: EventMsg[]): Role[] {
  const byRole = new Map(runningActivities(events).map((activity) => [activity.role, activity]));
  const known = new Set(roles.map((role) => role.role));
  const now = Date.now() / 1000;
  const merged = roles.map((role) => {
    const activity = byRole.get(role.role);
    if (!activity) return role;
    return {
      ...role,
      active: true,
      label: activity.label,
      status: 'running',
      age_s: Math.max(0, now - activity.updatedTs),
    };
  });
  for (const activity of byRole.values()) {
    if (known.has(activity.role)) continue;
    merged.push({
      role: activity.role,
      backend: activity.backend,
      backend_label: activity.backend,
      model: activity.model,
      effort: null,
      active: true,
      label: activity.label,
      status: 'running',
      age_s: Math.max(0, now - activity.updatedTs),
    });
  }
  return merged;
}

export function activityHistory(events: EventMsg[], max = 10): ActivityView[] {
  return events
    .map(toActivityView)
    .filter((activity): activity is ActivityView => Boolean(activity))
    .sort((a, b) => a.updatedTs - b.updatedTs)
    .slice(-max);
}
