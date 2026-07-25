/**
 * Append-only step trail for one Manager turn.
 *
 * The cockpit used to keep a SINGLE `phase` string that every new fragment
 * overwrote, and that `onDelta` then cleared. The operator therefore saw one
 * flickering line and, once the reply landed, no record at all of what Argus
 * had actually done — the "I can't see what the system is doing" complaint.
 *
 * A trail keeps every real step in order, marks the newest one active, and
 * survives the turn so it can be folded into the scrollback. This module is
 * pure so both the Ink CLI and the web cockpit share the exact same reducer.
 */

export interface PhaseStep {
  /** Stable key for React lists. */
  id: string;
  role: string;
  label: string;
  detail: string;
  /** Progress kind ('command_execution', 'tool_use', …) when the backend sent one. */
  kind: string;
  startedTs: number;
  endedTs: number;
  /** Heartbeat rows report "still alive, nothing new" and are replaced in place. */
  heartbeat: boolean;
}

export interface PhaseFragment {
  label: string;
  role?: string;
  detail?: string;
  kind?: string;
  heartbeat?: boolean;
  quietS?: number;
}

/** Steps rendered live; older ones scroll out of the window. */
export const TRAIL_VISIBLE_STEPS = 6;

const nowSeconds = (): number => Date.now() / 1000;

function normalize(label: string): string {
  return label.trim().replace(/[.…]+$/u, '').toLowerCase();
}

/**
 * Fold one phase fragment into the trail.
 *
 * Rules (all mechanical — no judgment about whether a step "mattered"):
 *  - an empty label is ignored;
 *  - a heartbeat replaces a previous heartbeat instead of stacking duplicates,
 *    because it carries no new action, only a longer quiet time;
 *  - a repeat of the current label refreshes it in place;
 *  - anything else closes the active step and appends a new one.
 */
export function appendPhaseStep(
  steps: readonly PhaseStep[],
  fragment: PhaseFragment,
  ts: number = nowSeconds(),
): PhaseStep[] {
  const label = (fragment.label ?? '').trim();
  if (!label) return steps as PhaseStep[];
  const heartbeat = fragment.heartbeat === true;
  const next = steps.slice();
  const last = next[next.length - 1];

  if (last && !last.endedTs) {
    const sameLabel = normalize(last.label) === normalize(label);
    if (sameLabel || (heartbeat && last.heartbeat)) {
      next[next.length - 1] = {
        ...last,
        label,
        detail: fragment.detail || last.detail,
        kind: fragment.kind || last.kind,
        heartbeat,
        endedTs: 0,
      };
      return next;
    }
    next[next.length - 1] = { ...last, endedTs: ts };
  }

  next.push({
    id: `${next.length}:${label}:${ts}`,
    role: (fragment.role || 'manager').trim() || 'manager',
    label,
    detail: (fragment.detail || '').trim(),
    kind: (fragment.kind || '').trim(),
    startedTs: ts,
    endedTs: 0,
    heartbeat,
  });
  return next;
}

/** Close the active step — the turn produced a reply or ended. */
export function closePhaseTrail(
  steps: readonly PhaseStep[],
  ts: number = nowSeconds(),
): PhaseStep[] {
  if (steps.length === 0) return [];
  const next = steps.slice();
  const last = next[next.length - 1];
  if (last && !last.endedTs) next[next.length - 1] = { ...last, endedTs: ts };
  return next;
}

/** The tail of the trail, for a fixed-height live view. */
export function visibleTrail(
  steps: readonly PhaseStep[],
  max: number = TRAIL_VISIBLE_STEPS,
): PhaseStep[] {
  const limit = Math.max(1, max);
  return steps.length <= limit ? (steps as PhaseStep[]) : steps.slice(steps.length - limit);
}

export function stepElapsedS(step: PhaseStep, now: number = nowSeconds()): number {
  const end = step.endedTs || now;
  return Math.max(0, end - step.startedTs);
}

export function formatStepSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 1) return '';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
  return rest ? `${minutes}m${rest}s` : `${minutes}m`;
}

/**
 * A compact, plain-text record of the turn, appended to the scrollback once the
 * turn ends so the operator can still read what happened after the fact.
 * Heartbeat rows are dropped — they report waiting, not work. Returns '' when
 * there is nothing worth keeping.
 */
export function summarizeTrail(steps: readonly PhaseStep[]): string {
  const rows = steps.filter((step) => !step.heartbeat && step.label.trim());
  if (rows.length === 0) return '';
  const lines = rows.map((step) => {
    const seconds = formatStepSeconds(stepElapsedS(step, step.endedTs || step.startedTs));
    return `  ${step.label}${seconds ? ` · ${seconds}` : ''}`;
  });
  return [`did ${rows.length} step${rows.length === 1 ? '' : 's'}:`, ...lines].join('\n');
}
