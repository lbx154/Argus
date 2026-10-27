import { useEffect, useState } from 'react';
import { useI18n, type Locale } from '../i18n';
import { plainToolLabel } from '../lib/feedSteps';
import type { EventMsg } from '../api';
import { Check, ChevronDown, ChevronRight, CircleAlert, FilePenLine, LoaderCircle, Square, Terminal, Wrench } from 'lucide-react';
import { formatStepSeconds, turnStepsElapsedS, type TurnStep } from '../../../core/src/phaseTrail';

const STEP_ICONS = { command_execution: Terminal, tool_use: Wrench, file_change: FilePenLine };
const FAILED = new Set(['failed', 'error']);
const STOPPED = new Set(['cancelled', 'canceled', 'stopped']);
const LEADING_GLYPH = /^(?:[⚙✎↳$…∴▸]|✗ \$)\s*/u;
const LEGACY_TOOL_LABEL = /^⚙\s*([\w.-]+)\s*·\s*(.*)$/su;

/**
 * The plain "verb + object" label is what a step says; the raw command or
 * arguments it ran with are `secondary`, folded away until asked for.
 */
export function stepText(step: TurnStep, locale: Locale = 'zh-CN'): { primary: string; secondary: string } {
  const tool = (step.tool ?? '').trim();
  const detail = (step.detail ?? '').trim();
  // Turns saved before plain labels stored `⚙ name · {json args}`; say them
  // the same way new turns do instead of showing the raw call.
  const legacy = step.label.match(LEGACY_TOOL_LABEL);
  if (legacy) {
    const name = legacy[1];
    const args = detail || legacy[2];
    const event = { type: 'engineer.progress', kind: 'tool_use', tool_name: tool || name, text: `${name}: ${args}` } as unknown as EventMsg;
    return { primary: plainToolLabel(event, locale), secondary: args };
  }
  const label = step.label.replace(LEADING_GLYPH, '').trim();
  return { primary: label || tool, secondary: detail && detail !== label ? detail : '' };
}

/**
 * The work behind one Argus reply: which tools ran, on what, and how each
 * ended. One compact line sits below the reply; details are opt-in.
 */
export function TurnSteps({ steps, live, startedTs, status = '', replyShown = false }: {
  steps: TurnStep[]; live: boolean; startedTs?: number; status?: string;
  /** Reply text is already on screen (it may be an interim message). */
  replyShown?: boolean;
}) {
  const { t, locale } = useI18n();
  const [open, setOpen] = useState(false);
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!live) return;
    const id = setInterval(() => setTick((value) => value + 1), 1000);
    return () => clearInterval(id);
  }, [live]);
  useEffect(() => { if (!live) setOpen(false); }, [live]);
  if (!steps.length && !live) return null;
  const now = Date.now() / 1000;
  const failed = FAILED.has(status) || steps.some((step) => FAILED.has(step.status));
  const stopped = STOPPED.has(status) || steps.some((step) => STOPPED.has(step.status));
  const seconds = formatStepSeconds(live && startedTs ? Math.max(0, now - startedTs) : turnStepsElapsedS(steps, now)) || '<1s';
  const active = [...steps].reverse().find(step => step.status === 'running');
  // Once the answer is visible a spinning "preparing a reply" would contradict
  // it; a step still running says what the turn is doing instead.
  const finishing = live && replyShown && !active;
  const summary = finishing ? (locale === 'zh-CN' ? '收尾中' : 'Wrapping up')
    : live
    ? `${active ? stepText(active, locale).primary : locale === 'zh-CN' ? '正在准备回复' : 'Preparing a reply'} · ${seconds}`
    : failed ? `${locale === 'zh-CN' ? '过程出现问题，请查看详情' : 'An issue occurred; see details'} · ${seconds}`
      : stopped ? `${locale === 'zh-CN' ? '已停止' : 'Stopped'} · ${seconds}`
        : t('chat.stepsDone', { count: steps.length, seconds });
  const StatusIcon = finishing ? LoaderCircle : live ? LoaderCircle : failed ? CircleAlert : stopped ? Square : Check;
  const Chevron = open ? ChevronDown : ChevronRight;
  return (
    <div className="turn-steps mt-3 rounded-lg border border-line/50 bg-surface/50 text-xs" data-live={live || undefined}>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={locale === 'zh-CN' ? '查看工作过程' : 'View work progress'}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink-dim hover:text-ink"
      >
        <StatusIcon size={14} aria-hidden="true" className={`shrink-0 ${finishing ? 'text-ink-faint' : live ? 'animate-spin text-manager motion-reduce:animate-none' : failed ? 'text-err' : stopped ? 'text-ink-faint' : 'text-ok'}`} />
        <span className="min-w-0 flex-1 truncate">{summary}</span>
        <Chevron size={14} aria-hidden="true" className="shrink-0 text-ink-faint" />
      </button>
      {open ? (
        <ol className="space-y-1 border-t border-line/40 px-3 py-2">
          {!steps.length && <li className="text-ink-faint">{locale === 'zh-CN' ? '尚未记录工具操作。' : 'No tool operations recorded yet.'}</li>}
          {steps.map((step, index) => {
            const { primary, secondary } = stepText(step, locale);
            const isRunning = step.status === 'running';
            const isFailed = FAILED.has(step.status);
            const isStopped = STOPPED.has(step.status);
            const Icon = step.kind === 'command_execution' ? STEP_ICONS.command_execution
              : step.kind === 'file_change' ? STEP_ICONS.file_change : STEP_ICONS.tool_use;
            const elapsed = formatStepSeconds(Math.max(0, (step.ended_ts || now) - step.started_ts));
            return (
              <li key={step.call_id || `${index}:${step.started_ts}`} className="flex min-w-0 items-baseline gap-2">
                <Icon size={13} aria-hidden="true" className={`shrink-0 self-start mt-0.5 ${isFailed ? 'text-err' : 'text-ink-faint'}`} />
                <span className="min-w-0 flex-1">
                  <span className={`${isRunning && live ? 'text-ink' : isFailed ? 'text-err' : 'text-ink-dim'}`} title={secondary || primary}>
                    {primary}
                  </span>
                  {secondary ? (
                    <details className="turn-step-raw inline">
                      <summary className="ml-2 inline cursor-pointer list-none text-[11px] text-ink-faint hover:text-ink-dim">{t('chat.stepRaw')}</summary>
                      <span className="mt-0.5 block break-all font-mono text-[11px] text-ink-faint">{secondary}</span>
                    </details>
                  ) : null}
                  {isFailed && step.output ? (
                    <span className="mt-0.5 block font-mono text-[11px] text-err/80">{step.output}</span>
                  ) : null}
                </span>
                <span className={`shrink-0 font-mono tabular-nums ${isRunning && live ? 'text-manager' : 'text-ink-faint'}`}>
                  {isRunning && live ? <LoaderCircle size={12} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
                    : isFailed ? t('chat.stepFailed') : isStopped ? (locale === 'zh-CN' ? '已停止' : 'Stopped') : elapsed}
                </span>
              </li>
            );
          })}
        </ol>
      ) : null}
    </div>
  );
}
