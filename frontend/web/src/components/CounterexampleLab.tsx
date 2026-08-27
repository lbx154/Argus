import { useEffect, useId, useState } from 'react';

import {
  clampCounterexampleProgress,
  deriveCounterexampleProgress,
  formatCounterexampleUpdate,
  isCounterexampleStageActive,
  isCounterexampleStageBlocked,
  isCounterexampleStageComplete,
  type CounterexampleConjecture,
  type CounterexampleEvidenceStatus,
  type CounterexampleStageStatus,
  type CounterexampleStatus,
  type CounterexampleTimestamp,
} from '../lib/counterexampleProgress';

export interface CounterexampleLabProps {
  conjectures: readonly CounterexampleConjecture[];
  selectedConjectureId?: string;
  defaultSelectedConjectureId?: string;
  onSelectedConjectureChange?: (conjectureId: string) => void;
  title?: string;
  subtitle?: string;
  className?: string;
  live?: boolean;
  lastUpdatedAt?: CounterexampleTimestamp;
  now?: number;
  staleAfterMs?: number;
  emptyMessage?: string;
  conjecturesLabel?: string;
  currentActivityLabel?: string;
}

const CONJECTURE_LABELS: Record<CounterexampleStatus, string> = {
  queued: 'Queued',
  active: 'Active',
  paused: 'Paused',
  blocked: 'Blocked',
  refuted: 'Refuted',
  confirmed: 'Confirmed',
  inconclusive: 'Inconclusive',
};

const STAGE_LABELS: Record<CounterexampleStageStatus, string> = {
  pending: 'Pending',
  queued: 'Queued',
  running: 'Running',
  in_progress: 'In progress',
  claimed: 'Claimed',
  active: 'Active',
  done: 'Done',
  completed: 'Completed',
  blocked: 'Blocked',
  failed: 'Failed',
  skipped: 'Skipped',
};

const EVIDENCE_LABELS: Record<CounterexampleEvidenceStatus, string> = {
  pending: 'Pending',
  candidate: 'Candidate',
  verified: 'Verified',
  rejected: 'Rejected',
};

function conjectureTone(status: CounterexampleStatus): string {
  if (status === 'refuted' || status === 'confirmed') return 'border-ok/35 bg-ok/10 text-ok';
  if (status === 'blocked') return 'border-err/35 bg-err/10 text-err';
  if (status === 'active') return 'border-blue/35 bg-blue/10 text-blue-sky';
  if (status === 'inconclusive' || status === 'paused') return 'border-warn/35 bg-warn/10 text-warn';
  return 'border-line bg-bg/60 text-ink-faint';
}

function evidenceTone(status: CounterexampleEvidenceStatus): string {
  if (status === 'verified') return 'border-ok/35 bg-ok/10 text-ok';
  if (status === 'rejected') return 'border-err/35 bg-err/10 text-err';
  if (status === 'candidate') return 'border-warn/35 bg-warn/10 text-warn';
  return 'border-line bg-bg/60 text-ink-faint';
}

function stageDot(status: CounterexampleStageStatus): string {
  if (isCounterexampleStageComplete(status)) return 'border-ok bg-ok text-bg';
  if (isCounterexampleStageBlocked(status)) return 'border-err bg-err/10 text-err';
  if (isCounterexampleStageActive(status)) return 'border-blue bg-blue text-white';
  return 'border-line bg-panel text-ink-faint';
}

function stageSymbol(status: CounterexampleStageStatus): string {
  if (isCounterexampleStageComplete(status)) return '✓';
  if (isCounterexampleStageBlocked(status)) return '!';
  if (isCounterexampleStageActive(status)) return '•';
  return '';
}

function initialSelection(
  conjectures: readonly CounterexampleConjecture[],
  preferredId?: string,
): string {
  if (preferredId && conjectures.some((item) => item.id === preferredId)) return preferredId;
  return conjectures.find((item) => item.active || item.status === 'active')?.id
    ?? conjectures[0]?.id
    ?? '';
}

function ProgressBar({ value, label }: { value: number; label: string }) {
  const rounded = Math.round(clampCounterexampleProgress(value));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={rounded}
      className="h-1.5 overflow-hidden rounded-full bg-line/55"
    >
      <div
        className="h-full rounded-full bg-blue transition-[width] duration-500 ease-panel"
        style={{ width: `${rounded}%` }}
      />
    </div>
  );
}

export function CounterexampleLab({
  conjectures,
  selectedConjectureId,
  defaultSelectedConjectureId,
  onSelectedConjectureChange,
  title = 'Counterexample Lab',
  subtitle = 'Argus live search, construction, and independent verification',
  className = '',
  live,
  lastUpdatedAt,
  now = Date.now(),
  staleAfterMs = 120_000,
  emptyMessage = 'No conjectures are being tracked yet.',
  conjecturesLabel = 'Conjectures',
  currentActivityLabel = 'Current activity',
}: CounterexampleLabProps) {
  const detailPanelId = useId();
  const [internalSelection, setInternalSelection] = useState(() => (
    initialSelection(conjectures, defaultSelectedConjectureId)
  ));
  const controlled = selectedConjectureId !== undefined;
  const requestedSelection = controlled ? selectedConjectureId : internalSelection;
  const fallbackSelection = initialSelection(conjectures, defaultSelectedConjectureId);
  const selected = conjectures.find((item) => item.id === requestedSelection)
    ?? conjectures.find((item) => item.id === fallbackSelection)
    ?? null;
  const summaries = conjectures.map((item) => ({
    item,
    summary: deriveCounterexampleProgress(item, now, staleAfterMs),
  }));
  const selectedSummary = selected
    ? deriveCounterexampleProgress(selected, now, staleAfterMs)
    : null;
  const overallLive = live ?? summaries.some(({ summary }) => summary.live);
  const latestSummaryUpdate = Math.max(
    ...summaries.map(({ summary }) => summary.latestUpdatedAt ?? 0),
    0,
  );
  const overallUpdatedAt = lastUpdatedAt ?? (latestSummaryUpdate || undefined);

  useEffect(() => {
    if (controlled || conjectures.some((item) => item.id === internalSelection)) return;
    setInternalSelection(initialSelection(conjectures, defaultSelectedConjectureId));
  }, [conjectures, controlled, defaultSelectedConjectureId, internalSelection]);

  const chooseConjecture = (conjectureId: string) => {
    if (!controlled) setInternalSelection(conjectureId);
    onSelectedConjectureChange?.(conjectureId);
  };

  return (
    <section
      aria-label={title}
      className={`min-h-0 overflow-hidden rounded-xl border border-line/70 bg-panel text-ink shadow-glow ${className}`}
      data-counterexample-lab="true"
    >
      <header className="flex flex-wrap items-start justify-between gap-4 border-b border-line/70 px-5 py-4">
        <div className="min-w-0">
          <div className="text-[10px] font-semibold uppercase tracking-[0.18em] text-blue-sky">
            Argus research control
          </div>
          <h2 className="mt-1 text-lg font-semibold tracking-tight text-ink">{title}</h2>
          <p className="mt-1 max-w-2xl text-xs leading-relaxed text-ink-dim">{subtitle}</p>
        </div>
        <div className="flex flex-col items-end gap-1.5" aria-live="polite">
          <span
            className={`inline-flex items-center gap-2 rounded-full border px-2.5 py-1 font-mono text-[10px] font-semibold uppercase tracking-[0.12em] ${overallLive ? 'border-ok/40 bg-ok/10 text-ok' : 'border-line bg-bg/60 text-ink-faint'}`}
            data-live={overallLive ? 'true' : 'false'}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${overallLive ? 'animate-pulse bg-ok motion-reduce:animate-none' : 'bg-ink-faint/50'}`} />
            {overallLive ? 'Live feed' : 'Snapshot'}
          </span>
          <span className="font-mono text-[10px] text-ink-faint">
            {formatCounterexampleUpdate(overallUpdatedAt, now)}
          </span>
        </div>
      </header>

      {!selected || !selectedSummary ? (
        <div className="grid min-h-72 place-items-center px-6 py-16 text-center">
          <div>
            <div className="mx-auto h-2 w-2 rounded-full bg-ink-faint/40" />
            <p className="mt-3 text-sm text-ink-dim">{emptyMessage}</p>
          </div>
        </div>
      ) : (
        <div className="grid min-h-[34rem] lg:grid-cols-[minmax(15rem,0.72fr)_minmax(0,1.8fr)]">
          <nav className="min-h-0 border-b border-line/70 bg-bg/35 lg:border-b-0 lg:border-r" aria-label={conjecturesLabel}>
            <div className="flex items-center justify-between border-b border-line/60 px-4 py-3">
              <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-ink-faint">
                Conjectures
              </span>
              <span className="font-mono text-[10px] text-ink-faint">{conjectures.length}</span>
            </div>
            <ul className="max-h-[34rem] space-y-1 overflow-y-auto p-2 scroll-thin">
              {summaries.map(({ item, summary }) => {
                const isSelected = item.id === selected.id;
                const status = item.status ?? (summary.active ? 'active' : 'queued');
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      aria-controls={detailPanelId}
                      aria-pressed={isSelected}
                      onClick={() => chooseConjecture(item.id)}
                      className={`w-full rounded-lg border px-3 py-3 text-left transition-colors ${isSelected ? 'border-blue/45 bg-blue/10' : 'border-transparent hover:border-line/70 hover:bg-panel/80'}`}
                      data-active={summary.active ? 'true' : 'false'}
                      data-live={summary.live ? 'true' : 'false'}
                      data-selected={isSelected ? 'true' : 'false'}
                    >
                      <div className="flex items-start gap-2.5">
                        <span className={`mt-1 h-2 w-2 shrink-0 rounded-full ${summary.live ? 'animate-pulse bg-ok motion-reduce:animate-none' : summary.active ? 'bg-blue' : 'bg-ink-faint/45'}`} />
                        <span className="min-w-0 flex-1">
                          <span className="flex items-start justify-between gap-2">
                            <span className="line-clamp-2 text-xs font-semibold leading-5 text-ink">
                              {item.shortTitle || item.title}
                            </span>
                            <span className="shrink-0 font-mono text-[10px] tabular-nums text-ink-dim">
                              {Math.round(summary.progress)}%
                            </span>
                          </span>
                          <span className="mt-1.5 flex flex-wrap items-center gap-1.5">
                            <span className={`rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${conjectureTone(status)}`}>
                              {CONJECTURE_LABELS[status]}
                            </span>
                            {summary.currentStage ? (
                              <span className="truncate text-[10px] text-ink-faint">
                                {summary.currentStage.label}
                              </span>
                            ) : null}
                          </span>
                          <span className="mt-2 block">
                            <ProgressBar value={summary.progress} label={`${item.title} progress`} />
                          </span>
                          <span className="mt-1.5 flex justify-between gap-2 font-mono text-[9px] text-ink-faint">
                            <span>{summary.completedStages}/{summary.totalStages || 0} stages</span>
                            <span>{summary.evidenceCount} evidence</span>
                          </span>
                        </span>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>

          <article id={detailPanelId} className="min-w-0 overflow-y-auto scroll-thin" aria-labelledby={`${detailPanelId}-title`}>
            <header className="border-b border-line/60 px-5 py-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    {selected.field ? (
                      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">
                        {selected.field}
                      </span>
                    ) : null}
                    {selectedSummary.live ? (
                      <span className="inline-flex items-center gap-1.5 rounded-full border border-ok/35 bg-ok/10 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-ok">
                        <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-ok motion-reduce:animate-none" /> Live
                      </span>
                    ) : null}
                    {selectedSummary.active ? (
                      <span className="rounded-full border border-blue/35 bg-blue/10 px-2 py-0.5 font-mono text-[9px] font-semibold uppercase tracking-wider text-blue-sky">
                        Active
                      </span>
                    ) : null}
                  </div>
                  <h3 id={`${detailPanelId}-title`} className="mt-2 text-xl font-semibold leading-snug text-ink">
                    {selected.title}
                  </h3>
                  {selected.statement ? (
                    <p className="mt-3 max-w-4xl text-sm leading-6 text-ink-dim">{selected.statement}</p>
                  ) : null}
                </div>
                <div className="min-w-28 text-right">
                  <div className="font-mono text-3xl font-semibold tabular-nums text-blue-sky">
                    {Math.round(selectedSummary.progress)}%
                  </div>
                  <div className="mt-1 text-[10px] uppercase tracking-[0.14em] text-ink-faint">overall progress</div>
                </div>
              </div>
              <div className="mt-4">
                <ProgressBar value={selectedSummary.progress} label={`${selected.title} overall progress`} />
              </div>
              <dl className="mt-4 grid grid-cols-2 gap-3 text-xs sm:grid-cols-4">
                <div className="rounded-lg border border-line/60 bg-bg/40 px-3 py-2.5">
                  <dt className="text-[10px] uppercase tracking-wider text-ink-faint">Current stage</dt>
                  <dd className="mt-1 truncate font-medium text-ink" title={selectedSummary.currentStage?.label}>
                    {selectedSummary.currentStage?.label ?? 'Not started'}
                  </dd>
                </div>
                <div className="rounded-lg border border-line/60 bg-bg/40 px-3 py-2.5">
                  <dt className="text-[10px] uppercase tracking-wider text-ink-faint">Stages</dt>
                  <dd className="mt-1 font-mono text-ink">{selectedSummary.completedStages} / {selectedSummary.totalStages}</dd>
                </div>
                <div className="rounded-lg border border-line/60 bg-bg/40 px-3 py-2.5">
                  <dt className="text-[10px] uppercase tracking-wider text-ink-faint">Evidence</dt>
                  <dd className="mt-1 font-mono text-ink">{selectedSummary.verifiedEvidenceCount} verified / {selectedSummary.evidenceCount}</dd>
                </div>
                <div className="rounded-lg border border-line/60 bg-bg/40 px-3 py-2.5">
                  <dt className="text-[10px] uppercase tracking-wider text-ink-faint">Updated</dt>
                  <dd className="mt-1 font-mono text-ink">{formatCounterexampleUpdate(selectedSummary.latestUpdatedAt, now)}</dd>
                </div>
              </dl>
            </header>

            {selected.activity ? (
              <section className="border-b border-line/60 bg-blue/5 px-5 py-4" aria-label={currentActivityLabel}>
                <div className="flex items-start gap-3">
                  <span className="mt-1.5 h-2 w-2 shrink-0 animate-pulse rounded-full bg-blue motion-reduce:animate-none" />
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                      <span className="text-[10px] font-semibold uppercase tracking-[0.14em] text-blue-sky">Now working</span>
                      {selected.activity.actor ? <span className="font-mono text-[10px] text-ink-faint">{selected.activity.actor}</span> : null}
                      {selected.activity.startedAt ? <span className="font-mono text-[10px] text-ink-faint">· {formatCounterexampleUpdate(selected.activity.startedAt, now)}</span> : null}
                    </div>
                    <div className="mt-1 text-sm font-medium text-ink">{selected.activity.label}</div>
                    {selected.activity.detail ? <p className="mt-1 text-xs leading-relaxed text-ink-dim">{selected.activity.detail}</p> : null}
                  </div>
                </div>
              </section>
            ) : null}

            <div className="grid xl:grid-cols-[minmax(0,1fr)_minmax(18rem,0.85fr)]">
              <section className="min-w-0 border-b border-line/60 px-5 py-5 xl:border-b-0 xl:border-r" aria-labelledby={`${detailPanelId}-stages`}>
                <div className="flex items-center justify-between gap-3">
                  <h4 id={`${detailPanelId}-stages`} className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
                    Research stages
                  </h4>
                  <span className="font-mono text-[10px] text-ink-faint">{selectedSummary.completedStages}/{selectedSummary.totalStages}</span>
                </div>
                {(selected.stages ?? []).length ? (
                  <ol className="mt-4 space-y-0">
                    {(selected.stages ?? []).map((stage, index, stages) => {
                      const activeStage = isCounterexampleStageActive(stage.status);
                      return (
                        <li key={stage.id} className="relative flex gap-3 pb-5 last:pb-0" data-stage-status={stage.status}>
                          {index < stages.length - 1 ? <span className="absolute left-[0.6875rem] top-6 h-[calc(100%-1rem)] w-px bg-line/70" /> : null}
                          <span className={`relative z-10 flex h-[1.4rem] w-[1.4rem] shrink-0 items-center justify-center rounded-full border text-[10px] font-bold ${stageDot(stage.status)} ${activeStage ? 'animate-pulse motion-reduce:animate-none' : ''}`}>
                            {stageSymbol(stage.status)}
                          </span>
                          <div className="min-w-0 flex-1 pt-0.5">
                            <div className="flex flex-wrap items-start justify-between gap-2">
                              <div>
                                <div className="text-xs font-semibold text-ink">{stage.label}</div>
                                <div className="mt-0.5 flex flex-wrap gap-2 font-mono text-[9px] uppercase tracking-wide text-ink-faint">
                                  <span>{STAGE_LABELS[stage.status]}</span>
                                  {stage.owner ? <span>{stage.owner}</span> : null}
                                </div>
                              </div>
                              {stage.updatedAt ? <span className="font-mono text-[9px] text-ink-faint">{formatCounterexampleUpdate(stage.updatedAt, now)}</span> : null}
                            </div>
                            {stage.detail ? <p className="mt-1.5 text-xs leading-relaxed text-ink-dim">{stage.detail}</p> : null}
                            {activeStage && stage.progress != null ? (
                              <div className="mt-2">
                                <ProgressBar value={stage.progress} label={`${stage.label} progress`} />
                              </div>
                            ) : null}
                          </div>
                        </li>
                      );
                    })}
                  </ol>
                ) : (
                  <p className="mt-4 text-xs text-ink-faint">No stage data is available.</p>
                )}
              </section>

              <section className="min-w-0 px-5 py-5" aria-labelledby={`${detailPanelId}-evidence`}>
                <div className="flex items-center justify-between gap-3">
                  <h4 id={`${detailPanelId}-evidence`} className="text-[10px] font-semibold uppercase tracking-[0.16em] text-ink-faint">
                    Evidence ledger
                  </h4>
                  <span className="font-mono text-[10px] text-ink-faint">{selectedSummary.evidenceCount}</span>
                </div>
                {(selected.evidence ?? []).length ? (
                  <div className="mt-4 space-y-2">
                    {(selected.evidence ?? []).map((item) => {
                      const status = item.status ?? 'pending';
                      const heading = item.href ? (
                        <a className="font-semibold text-ink underline decoration-line underline-offset-4 hover:text-blue-sky" href={item.href} target="_blank" rel="noreferrer">
                          {item.title}
                        </a>
                      ) : <span className="font-semibold text-ink">{item.title}</span>;
                      return (
                        <article key={item.id} className="rounded-lg border border-line/60 bg-bg/40 px-3 py-3" data-evidence-status={status}>
                          <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 text-xs leading-5">{heading}</div>
                            <span className={`shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${evidenceTone(status)}`}>
                              {EVIDENCE_LABELS[status]}
                            </span>
                          </div>
                          <div className="mt-1 flex flex-wrap gap-x-2 gap-y-1 font-mono text-[9px] uppercase tracking-wide text-ink-faint">
                            {item.kind ? <span>{item.kind}</span> : null}
                            {item.source ? <span>{item.source}</span> : null}
                            {item.updatedAt ? <span>{formatCounterexampleUpdate(item.updatedAt, now)}</span> : null}
                          </div>
                          {item.summary ? <p className="mt-2 text-xs leading-relaxed text-ink-dim">{item.summary}</p> : null}
                        </article>
                      );
                    })}
                  </div>
                ) : (
                  <p className="mt-4 text-xs text-ink-faint">No evidence has been recorded.</p>
                )}
              </section>
            </div>
          </article>
        </div>
      )}
    </section>
  );
}
