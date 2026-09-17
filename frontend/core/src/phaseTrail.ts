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
 *
 * Tool calls carry a call id from the runner: the call's result then closes
 * the step that started it, instead of adding a row of its own, and two calls
 * running side by side both stay open until each one's own result arrives.
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
  /** Plain title of the tool call, when the runner named it. */
  tool?: string;
  toolKind?: string;
  /** Runner-side id pairing a call with its result. */
  callId?: string;
  /** '', 'running', 'completed', 'failed', … */
  status?: string;
  /** Short excerpt of what the call produced. */
  output?: string;
}

export interface PhaseFragment {
  label: string;
  role?: string;
  detail?: string;
  kind?: string;
  heartbeat?: boolean;
  quietS?: number;
  tool?: string;
  toolKind?: string;
  callId?: string;
  status?: string;
  output?: string;
}

/** One step as journaled with the reply (the server's shape, snake_case). */
export interface TurnStep {
  kind: string;
  label: string;
  detail?: string;
  tool?: string;
  tool_kind?: string;
  call_id?: string;
  status: string;
  started_ts: number;
  ended_ts: number;
  output?: string;
}

/** Steps rendered live; older ones scroll out of the window. */
export const TRAIL_VISIBLE_STEPS = 6;

/** Phase kinds that are observable work, as opposed to routing narration. */
export const TURN_STEP_KINDS: ReadonlySet<string> = new Set(['tool_use', 'command_execution', 'file_change']);

const TERMINAL_STATUSES: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled', 'canceled', 'error', 'done', 'stopped']);

const nowSeconds = (): number => Date.now() / 1000;

function normalize(label: string): string {
  return label.trim().replace(/[.…]+$/u, '').toLowerCase();
}

/**
 * Fold one phase fragment into the trail.
 *
 * Rules (all mechanical — no judgment about whether a step "mattered"):
 *  - an empty label is ignored;
 *  - a fragment carrying the call id of a step already in the trail updates
 *    that step (its status, detail, output) and, on a terminal status, ends
 *    it; a result for a call the trail never saw is dropped;
 *  - a heartbeat replaces a previous heartbeat instead of stacking duplicates,
 *    because it carries no new action, only a longer quiet time;
 *  - a repeat of the current label refreshes it in place;
 *  - anything else appends a new step; an anonymous open step ends then,
 *    while a step identified by call id waits for its own result.
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
  const callId = (fragment.callId ?? '').trim();
  const status = (fragment.status ?? '').trim().toLowerCase();
  const kind = (fragment.kind ?? '').trim();

  if (callId) {
    let index = -1;
    for (let cursor = next.length - 1; cursor >= 0; cursor -= 1) {
      if (next[cursor].callId === callId) { index = cursor; break; }
    }
    if (index >= 0) {
      const step = next[index];
      next[index] = {
        ...step,
        // A result names the call again; keep the row's own label.
        label: kind === 'tool_result' ? step.label : label,
        detail: (fragment.detail || '').trim() || step.detail,
        tool: (fragment.tool || '').trim() || step.tool,
        output: (fragment.output || '').trim() || step.output,
        status: status || step.status,
        endedTs: TERMINAL_STATUSES.has(status) ? ts : step.endedTs,
      };
      return next;
    }
    if (kind === 'tool_result') return next;
  }

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
    if (!last.callId) next[next.length - 1] = { ...last, endedTs: ts };
  }

  next.push({
    id: `${next.length}:${label}:${ts}`,
    role: (fragment.role || 'manager').trim() || 'manager',
    label,
    detail: (fragment.detail || '').trim(),
    kind,
    startedTs: ts,
    endedTs: 0,
    heartbeat,
    ...(fragment.tool?.trim() ? { tool: fragment.tool.trim() } : {}),
    ...(fragment.toolKind?.trim() ? { toolKind: fragment.toolKind.trim() } : {}),
    ...(callId ? { callId } : {}),
    ...(status || callId ? { status: status || 'running' } : {}),
    ...(fragment.output?.trim() ? { output: fragment.output.trim() } : {}),
  });
  return next;
}

/** Close every step still open — the turn produced a reply or ended. */
export function closePhaseTrail(
  steps: readonly PhaseStep[],
  ts: number = nowSeconds(),
): PhaseStep[] {
  if (steps.length === 0) return [];
  return steps.map((step) => (step.endedTs ? step : { ...step, endedTs: ts }));
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

/**
 * The tool work in a trail, in the journaled shape. ``finished`` means the
 * turn is over: anything still running is then recorded as completed at
 * ``now``, matching what the server journals with the reply.
 */
export function trailToTurnSteps(
  steps: readonly PhaseStep[],
  now: number = nowSeconds(),
  finished = false,
): TurnStep[] {
  return steps
    .filter((step) => !step.heartbeat && TURN_STEP_KINDS.has(step.kind))
    .map((step) => {
      const running = !step.endedTs && (!step.status || step.status === 'running');
      const status = running ? (finished ? 'completed' : 'running') : (step.status || 'completed');
      return {
        kind: step.kind,
        label: step.label,
        ...(step.detail ? { detail: step.detail } : {}),
        ...(step.tool ? { tool: step.tool } : {}),
        ...(step.toolKind ? { tool_kind: step.toolKind } : {}),
        ...(step.callId ? { call_id: step.callId } : {}),
        status,
        started_ts: step.startedTs,
        ended_ts: step.endedTs || (finished ? now : 0),
        ...(step.output ? { output: step.output } : {}),
      };
    });
}

const text = (value: unknown): string => (typeof value === 'string' ? value.trim() : '');
const num = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0);

/** Steps as they arrive from the server or the transcript, made safe to render. */
export function turnStepsFrom(value: unknown): TurnStep[] {
  if (!Array.isArray(value)) return [];
  const steps: TurnStep[] = [];
  value.forEach((item) => {
    if (!item || typeof item !== 'object') return;
    const row = item as Record<string, unknown>;
    const label = text(row.label);
    if (!label) return;
    steps.push({
      kind: text(row.kind) || 'tool_use',
      label,
      ...(text(row.detail) ? { detail: text(row.detail) } : {}),
      ...(text(row.tool) ? { tool: text(row.tool) } : {}),
      ...(text(row.tool_kind) ? { tool_kind: text(row.tool_kind) } : {}),
      ...(text(row.call_id) ? { call_id: text(row.call_id) } : {}),
      status: text(row.status).toLowerCase() || 'completed',
      started_ts: num(row.started_ts),
      ended_ts: num(row.ended_ts),
      ...(text(row.output) ? { output: text(row.output) } : {}),
    });
  });
  return steps;
}

/** Wall-clock span of the steps, from the first start to the last end. */
export function turnStepsElapsedS(steps: readonly TurnStep[], now: number = nowSeconds()): number {
  if (!steps.length) return 0;
  const start = Math.min(...steps.map((step) => step.started_ts || now));
  const end = Math.max(...steps.map((step) => step.ended_ts || now));
  return Math.max(0, end - start);
}
