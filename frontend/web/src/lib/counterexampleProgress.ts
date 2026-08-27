export type CounterexampleTimestamp = string | number | Date;

export type CounterexampleStatus =
  | 'queued'
  | 'active'
  | 'paused'
  | 'blocked'
  | 'refuted'
  | 'confirmed'
  | 'inconclusive';

export type CounterexampleStageStatus =
  | 'pending'
  | 'queued'
  | 'running'
  | 'in_progress'
  | 'claimed'
  | 'active'
  | 'done'
  | 'completed'
  | 'blocked'
  | 'failed'
  | 'skipped';

export type CounterexampleEvidenceStatus =
  | 'pending'
  | 'candidate'
  | 'verified'
  | 'rejected';

export type CounterexampleEvidenceKind =
  | 'paper'
  | 'claim'
  | 'calculation'
  | 'computation'
  | 'proof'
  | 'counterexample'
  | 'review'
  | 'note';

export interface CounterexampleStage {
  id: string;
  label: string;
  status: CounterexampleStageStatus;
  detail?: string;
  owner?: string;
  /** Completion percentage for this stage, from 0 to 100. */
  progress?: number;
  updatedAt?: CounterexampleTimestamp;
}

export interface CounterexampleEvidence {
  id: string;
  title: string;
  kind?: CounterexampleEvidenceKind;
  status?: CounterexampleEvidenceStatus;
  summary?: string;
  source?: string;
  href?: string;
  updatedAt?: CounterexampleTimestamp;
}

export interface CounterexampleActivity {
  label: string;
  detail?: string;
  actor?: string;
  startedAt?: CounterexampleTimestamp;
}

export interface CounterexampleConjecture {
  id: string;
  title: string;
  shortTitle?: string;
  statement?: string;
  field?: string;
  status?: CounterexampleStatus;
  active?: boolean;
  live?: boolean;
  /** Overall completion percentage, from 0 to 100. */
  progress?: number;
  currentStageId?: string;
  stages?: readonly CounterexampleStage[];
  evidence?: readonly CounterexampleEvidence[];
  activity?: CounterexampleActivity;
  updatedAt?: CounterexampleTimestamp;
}

export interface CounterexampleProgressSummary {
  progress: number;
  completedStages: number;
  totalStages: number;
  currentStage: CounterexampleStage | null;
  evidenceCount: number;
  verifiedEvidenceCount: number;
  active: boolean;
  live: boolean;
  latestUpdatedAt: number | null;
}

const COMPLETE_STAGE_STATUSES = new Set<CounterexampleStageStatus>([
  'done',
  'completed',
  'skipped',
]);
const ACTIVE_STAGE_STATUSES = new Set<CounterexampleStageStatus>([
  'running',
  'in_progress',
  'claimed',
  'active',
]);
const BLOCKED_STAGE_STATUSES = new Set<CounterexampleStageStatus>([
  'blocked',
  'failed',
]);

export function clampCounterexampleProgress(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

export function counterexampleTimestampMs(
  value: CounterexampleTimestamp | null | undefined,
): number | null {
  if (value == null) return null;
  if (value instanceof Date) {
    const timestamp = value.getTime();
    return Number.isFinite(timestamp) ? timestamp : null;
  }
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    return Math.abs(value) < 1_000_000_000_000 ? value * 1_000 : value;
  }
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function isCounterexampleStageComplete(status: CounterexampleStageStatus): boolean {
  return COMPLETE_STAGE_STATUSES.has(status);
}

export function isCounterexampleStageActive(status: CounterexampleStageStatus): boolean {
  return ACTIVE_STAGE_STATUSES.has(status);
}

export function isCounterexampleStageBlocked(status: CounterexampleStageStatus): boolean {
  return BLOCKED_STAGE_STATUSES.has(status);
}

function latestTimestamp(conjecture: CounterexampleConjecture): number | null {
  const timestamps = [
    counterexampleTimestampMs(conjecture.updatedAt),
    ...(conjecture.stages ?? []).map((stage) => counterexampleTimestampMs(stage.updatedAt)),
    ...(conjecture.evidence ?? []).map((item) => counterexampleTimestampMs(item.updatedAt)),
  ].filter((value): value is number => value != null);
  return timestamps.length ? Math.max(...timestamps) : null;
}

function resolveCurrentStage(conjecture: CounterexampleConjecture): CounterexampleStage | null {
  const stages = conjecture.stages ?? [];
  if (!stages.length) return null;
  if (conjecture.currentStageId) {
    const explicit = stages.find((stage) => stage.id === conjecture.currentStageId);
    if (explicit) return explicit;
  }
  return stages.find((stage) => isCounterexampleStageActive(stage.status))
    ?? stages.find((stage) => !isCounterexampleStageComplete(stage.status))
    ?? stages.at(-1)
    ?? null;
}

function derivedStageProgress(stages: readonly CounterexampleStage[]): number {
  if (!stages.length) return 0;
  const completedUnits = stages.reduce((total, stage) => {
    if (isCounterexampleStageComplete(stage.status)) return total + 1;
    if (!isCounterexampleStageActive(stage.status)) return total;
    const activeFraction = stage.progress == null
      ? 0.5
      : clampCounterexampleProgress(stage.progress) / 100;
    return total + activeFraction;
  }, 0);
  return clampCounterexampleProgress((completedUnits / stages.length) * 100);
}

export function deriveCounterexampleProgress(
  conjecture: CounterexampleConjecture,
  nowMs = Date.now(),
  staleAfterMs = 120_000,
): CounterexampleProgressSummary {
  const stages = conjecture.stages ?? [];
  const evidence = conjecture.evidence ?? [];
  const latestUpdatedAt = latestTimestamp(conjecture);
  const progress = conjecture.progress == null
    ? derivedStageProgress(stages)
    : clampCounterexampleProgress(conjecture.progress);
  const active = Boolean(conjecture.active)
    || conjecture.status === 'active'
    || stages.some((stage) => isCounterexampleStageActive(stage.status));
  const fresh = latestUpdatedAt == null || nowMs - latestUpdatedAt <= staleAfterMs;

  return {
    progress,
    completedStages: stages.filter((stage) => isCounterexampleStageComplete(stage.status)).length,
    totalStages: stages.length,
    currentStage: resolveCurrentStage(conjecture),
    evidenceCount: evidence.length,
    verifiedEvidenceCount: evidence.filter((item) => item.status === 'verified').length,
    active,
    live: Boolean(conjecture.live) && fresh,
    latestUpdatedAt,
  };
}

export function formatCounterexampleUpdate(
  value: CounterexampleTimestamp | null | undefined,
  nowMs = Date.now(),
): string {
  const timestamp = counterexampleTimestampMs(value);
  if (timestamp == null) return 'No updates yet';

  const deltaSeconds = Math.round((nowMs - timestamp) / 1_000);
  const future = deltaSeconds < 0;
  const absoluteSeconds = Math.abs(deltaSeconds);
  if (absoluteSeconds < 5) return 'just now';

  const units: Array<[number, string]> = [
    [86_400, 'd'],
    [3_600, 'h'],
    [60, 'm'],
    [1, 's'],
  ];
  const [unitSeconds, suffix] = units.find(([seconds]) => absoluteSeconds >= seconds) ?? units.at(-1)!;
  const amount = Math.floor(absoluteSeconds / unitSeconds);
  return future ? `in ${amount}${suffix}` : `${amount}${suffix} ago`;
}
