import type { BacklogItem, ContinuousState, Snapshot } from './types.js';

export type MissionState = 'working' | 'waiting' | 'complete' | 'idle' | 'offline';

export interface MissionView {
  state: MissionState;
  objective: string;
  stateLabel: string;
}

const ACTIVE_STATUSES = new Set(['running', 'in_progress', 'claimed']);

function activeItem(items: BacklogItem[]): BacklogItem | undefined {
  return items.find((item) => ACTIVE_STATUSES.has(item.status));
}

function queuedItem(items: BacklogItem[]): BacklogItem | undefined {
  return items.find((item) => item.status === 'pending');
}

export function deriveMissionView(
  snapshot: Snapshot,
  continuousOverride?: ContinuousState,
): MissionView {
  const continuous = continuousOverride ?? snapshot.continuous;
  const active = activeItem(snapshot.backlog);
  const queued = queuedItem(snapshot.backlog);
  const pendingQuestion =
    (snapshot.pending_questions?.length ?? 0) > 0 ||
    snapshot.backlog.some((item) => Boolean(item.pending_question?.trim()));
  const objective =
    continuous?.objective?.trim() ||
    snapshot.session.objective?.trim() ||
    active?.title?.trim() ||
    active?.objective?.trim() ||
    queued?.title?.trim() ||
    queued?.objective?.trim() ||
    '';

  if (pendingQuestion) return { state: 'waiting', stateLabel: 'waiting on you', objective };
  const hasQueuedWork = Boolean(active || queued || continuous?.enabled);
  if (snapshot.roles.some((role) => role.active) || (snapshot.daemon.alive && hasQueuedWork)) {
    return { state: 'working', stateLabel: 'working', objective };
  }
  if (continuous?.done_reason || continuous?.done_at) {
    return { state: 'complete', stateLabel: 'complete', objective };
  }
  if (hasQueuedWork) return { state: 'waiting', stateLabel: 'queued', objective };
  if (snapshot.daemon.alive) return { state: 'idle', stateLabel: 'standing by', objective };
  // Reaching this function means the UI already fetched a live snapshot.
  // A fresh session intentionally has no executor until its first real task,
  // so daemon.alive=false is "ready", not a connectivity failure.
  return { state: 'idle', stateLabel: 'ready', objective };
}
