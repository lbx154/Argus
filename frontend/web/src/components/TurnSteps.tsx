import { useEffect, useState } from 'react';
import { useI18n } from '../i18n';
import { spinnerFrame } from '../lib/soul';
import { formatStepSeconds, turnStepsElapsedS, type TurnStep } from '../../../core/src/phaseTrail';

const GLYPH: Record<string, string> = { command_execution: '$', tool_use: '⚙', file_change: '✎' };
const FAILED = new Set(['failed', 'error', 'cancelled', 'canceled']);
const LEADING_GLYPH = /^(?:[⚙✎↳$…∴▸]|✗ \$)\s*/u;

/** The plain title of a step, then the command or arguments it ran with. */
export function stepText(step: TurnStep): { primary: string; secondary: string } {
  const label = step.label.replace(LEADING_GLYPH, '').trim();
  const tool = (step.tool ?? '').trim();
  if (tool && tool !== label) {
    return { primary: tool, secondary: step.kind === 'command_execution' ? label : (step.detail ?? '') };
  }
  return { primary: label || tool, secondary: step.detail ?? '' };
}

/**
 * The work behind one Argus reply: which tools ran, on what, and how each
 * ended. Live turns show every step as it happens; finished turns fold the
 * list under a one-line summary so the answer stays in front.
 */
export function TurnSteps({ steps, live }: { steps: TurnStep[]; live: boolean }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(live);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setTick((value) => value + 1), 1000);
    return () => clearInterval(id);
  }, [live]);
  useEffect(() => { if (live) setOpen(true); }, [live]);
  if (!steps.length) return null;
  const now = Date.now() / 1000;
  const running = live && steps.some((step) => step.status === 'running');
  const failed = steps.filter((step) => FAILED.has(step.status)).length;
  const seconds = formatStepSeconds(turnStepsElapsedS(steps, now));
  const summary = running
    ? t('chat.stepsLive', { count: steps.length })
    : t('chat.stepsDone', { count: steps.length, seconds: seconds || '<1s' });
  return (
    <div className="turn-steps mb-2 rounded-lg border border-line/50 bg-surface/50 text-xs" data-live={live || undefined}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink-dim hover:text-ink"
      >
        <span className={`w-3 shrink-0 text-center font-mono ${running ? 'text-manager' : failed ? 'text-err' : 'text-ok'}`}>
          {running ? spinnerFrame(tick) : failed ? '✗' : '✓'}
        </span>
        <span className="min-w-0 flex-1 truncate">{summary}</span>
        <span className="shrink-0 text-ink-faint">{open ? '▾' : '▸'}</span>
      </button>
      {open ? (
        <ol className="space-y-1 border-t border-line/40 px-3 py-2">
          {steps.map((step, index) => {
            const { primary, secondary } = stepText(step);
            const isRunning = step.status === 'running';
            const isFailed = FAILED.has(step.status);
            const elapsed = formatStepSeconds(Math.max(0, (step.ended_ts || now) - step.started_ts));
            return (
              <li key={step.call_id || `${index}:${step.started_ts}`} className="flex min-w-0 items-baseline gap-2">
                <span className={`w-3 shrink-0 text-center font-mono ${isFailed ? 'text-err' : 'text-ink-faint'}`}>
                  {GLYPH[step.kind] ?? '▸'}
                </span>
                <span className="min-w-0 flex-1">
                  <span className={`${isRunning && live ? 'text-ink' : isFailed ? 'text-err' : 'text-ink-dim'}`} title={secondary || primary}>
                    {primary}
                  </span>
                  {secondary ? (
                    <span className="ml-2 font-mono text-[11px] text-ink-faint" title={secondary}>
                      {secondary.length > 96 ? `${secondary.slice(0, 95)}…` : secondary}
                    </span>
                  ) : null}
                  {isFailed && step.output ? (
                    <span className="mt-0.5 block font-mono text-[11px] text-err/80">{step.output}</span>
                  ) : null}
                </span>
                <span className={`shrink-0 font-mono tabular-nums ${isRunning && live ? 'text-manager' : 'text-ink-faint'}`}>
                  {isRunning && live ? spinnerFrame(tick) : isFailed ? t('chat.stepFailed') : elapsed}
                </span>
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}
