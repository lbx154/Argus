import { useState } from 'react';
import type { BacklogItem, LaunchHold, LaunchSettings } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { ASK_MODES, askModeLabel, describeRequirement, launchHoldPlan, launchSettingsSummary, stripLaunchNote, type AskMode } from '../lib/launch';

const HOUR_CHOICES: Array<number | null> = [null, 2, 4, 24];

/** When to ask the person, plus the first task's settings while the project has not started.
 * Once work has started only the ask choice remains, folded into one line. */
export function LaunchSettingsBar({
  value,
  onChange,
  ask,
  onAskChange,
  started = false,
  research = null,
  disabled,
}: {
  value: LaunchSettings;
  onChange: (next: LaunchSettings) => void;
  ask: AskMode;
  onAskChange: (next: AskMode) => void;
  /** The project already ran work: first-task settings no longer apply. */
  started?: boolean;
  /** Whether this is a research project; null while unknown. */
  research?: boolean | null;
  disabled?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const hourLabel = (hours: number | null) => hours == null
    ? (zh ? '不限' : 'No limit')
    : hours >= 24 ? (zh ? '1 天内' : 'Within 1 day') : (zh ? `${hours} 小时内` : `Within ${hours} h`);
  const selectClass = 'rounded border border-line bg-bg px-1.5 py-0.5 text-xs text-ink focus:border-blue focus:outline-none disabled:opacity-60';
  const askHint = {
    every_task: zh
      ? '交给团队的新任务都会先排好计划，等你点“开始执行”。团队已在运行时追加的任务会接着做。'
      : 'New team tasks are planned first and wait for you to press Start. Tasks added while the team is running continue directly.',
    first_task: zh
      ? '只有这个项目的第一个任务先给你看计划；之后的任务直接开始。'
      : 'Only this project’s first task waits for your go-ahead; later tasks start directly.',
    beyond_authority: zh
      ? '任务直接开始，阶段性的判断由经理来做；只有超出你授权的事才会问你。'
      : 'Tasks start directly and the Manager makes stage calls; you are asked only about things beyond what you authorised.',
  }[ask];
  const askSelect = <label className="flex items-center gap-1">
    <span>{zh ? '什么时候问我' : 'When to ask me'}</span>
    <select className={selectClass} disabled={disabled} value={ask} aria-label={zh ? '什么时候问我' : 'When to ask me'}
      onChange={(event) => onAskChange(event.target.value as AskMode)}>
      {ASK_MODES.map((mode) => <option key={mode} value={mode}>{askModeLabel(mode, zh)}</option>)}
    </select>
  </label>;
  if (started) {
    return (
      <details className="launch-settings mb-2 rounded-md border border-line/70 bg-panel/60 px-3 py-1.5 text-xs text-ink-dim" aria-label={zh ? '什么时候问我' : 'When to ask me'}>
        <summary className="cursor-pointer select-none">{zh ? '什么时候问我：' : 'When to ask me: '}<span className="text-ink">{askModeLabel(ask, zh)}</span></summary>
        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">{askSelect}</div>
        <p className="mt-1 text-[11px] text-ink-faint">{askHint}</p>
      </details>
    );
  }
  const researchKnobs = <>
    <label className="flex items-center gap-1" title={zh ? '可发表级别的研究通常需要数天甚至更久' : 'Publication-level research usually takes days or longer'}>
      <span>{zh ? '研究定位' : 'Ambition'}</span>
      <select className={selectClass} disabled={disabled} value={value.target_level}
        onChange={(event) => onChange({ ...value, target_level: event.target.value as LaunchSettings['target_level'] })}>
        <option value="auto">{zh ? '由 Argus 判断' : 'Let Argus decide'}</option>
        <option value="exploratory">{zh ? '探索性（较快）' : 'Exploratory (faster)'}</option>
        <option value="publishable">{zh ? '冲刺可发表（耗时长）' : 'Aim to publish (long)'}</option>
      </select>
    </label>
    <label className="flex items-center gap-1">
      <span>{zh ? '成果形式' : 'Output'}</span>
      <select className={selectClass} disabled={disabled} value={value.deliverable}
        onChange={(event) => onChange({ ...value, deliverable: event.target.value as LaunchSettings['deliverable'] })}>
        <option value="any">{zh ? '不限' : 'Any'}</option>
        <option value="report">{zh ? '报告' : 'Report'}</option>
        <option value="code">{zh ? '代码' : 'Code'}</option>
        <option value="paper">{zh ? '论文稿' : 'Paper draft'}</option>
        <option value="data">{zh ? '数据/结果表' : 'Data tables'}</option>
      </select>
    </label>
  </>;
  return (
    <div className="launch-settings mb-2 rounded-md border border-line/70 bg-panel/60 px-3 py-2 text-xs text-ink-dim" aria-label={zh ? '开工设置' : 'Launch settings'}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="font-medium text-ink">{zh ? '开工设置' : 'Launch settings'}</span>
        {askSelect}
        <label className="flex items-center gap-1" title={zh ? '希望多久内先看到一个能打开的结果。选了时间，团队会先交一个早期版本，再继续完善。' : 'How soon you want a first result you can open. With a time set, the team delivers an early version first and keeps improving it.'}>
          <span>{zh ? '多久先看到结果' : 'First result within'}</span>
          <select className={selectClass} disabled={disabled} value={value.first_result_hours ?? ''}
            onChange={(event) => onChange({ ...value, first_result_hours: event.target.value ? Number(event.target.value) : null })}>
            {HOUR_CHOICES.map((hours) => <option key={String(hours)} value={hours ?? ''}>{hourLabel(hours)}</option>)}
          </select>
        </label>
        {research === true ? researchKnobs : null}
      </div>
      {research === null ? <details className="mt-1">
        <summary className="cursor-pointer select-none text-ink-faint">{zh ? '研究类项目的选项' : 'Options for research projects'}</summary>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1.5">{researchKnobs}</div>
      </details> : null}
      <p className="mt-1 text-[11px] text-ink-faint">{askHint}</p>
    </div>
  );
}

/** A planned first task waiting for the operator: what Argus understood, its plan, start or rewrite. */
export function LaunchHoldCard({
  hold,
  backlog,
  onStart,
  onRewrite,
  onOpenArtifact,
  readOnly,
}: {
  hold: LaunchHold;
  backlog: BacklogItem[];
  onStart: () => Promise<void>;
  onRewrite: () => Promise<void>;
  /** Previews a workspace file, as a link in the conversation does. */
  onOpenArtifact?: (path: string) => void;
  readOnly?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [busy, setBusy] = useState<'start' | 'rewrite' | null>(null);
  const understood = hold.understood ?? {};
  const objective = stripLaunchNote(understood.objective || hold.request || hold.title);
  const plan = launchHoldPlan(backlog);
  const settings = launchSettingsSummary(hold.settings, zh);
  const run = (kind: 'start' | 'rewrite', action: () => Promise<void>) => async () => {
    setBusy(kind);
    try { await action(); } finally { setBusy(null); }
  };
  return (
    <section className="launch-hold mx-auto my-3 w-full max-w-full rounded-lg border border-gold/45 bg-gold/5 px-4 py-3 text-sm lg:max-w-[61.8vw]" aria-label={zh ? '等待你确认开始' : 'Waiting for your go-ahead'}>
      <div className="text-[10px] font-semibold uppercase tracking-[0.14em] text-gold">{zh ? '计划已排好 · 等你确认后开始' : 'Plan ready · starts when you confirm'}</div>
      <h3 className="mt-1 text-sm font-semibold text-ink">{hold.title || objective}</h3>
      <dl className="mt-2 space-y-2 text-xs leading-5 text-ink-dim">
        <div>
          <dt className="font-medium text-ink">{zh ? 'Argus 理解的目标' : 'Goal as Argus understood it'}</dt>
          <dd><details className="group">
            <summary className="cursor-pointer list-none">
              <span className="line-clamp-4 whitespace-pre-wrap group-open:line-clamp-none">{objective}</span>
              <span className="text-blue group-open:hidden">{zh ? '展开全部' : 'Show all'}</span>
            </summary>
          </details></dd>
        </div>
        {understood.requirements?.length ? <div>
          <dt className="font-medium text-ink">{zh ? '要求' : 'Requirements'}</dt>
          <dd><ul className="list-disc pl-4">{understood.requirements.map((requirement) => <li key={requirement.text}>{describeRequirement(requirement, zh)}</li>)}</ul></dd>
        </div> : null}
        {understood.assumptions?.length ? <div>
          <dt className="font-medium text-warn">{zh ? '经理替你定的理解（开始前可以改）' : 'Readings the Manager chose for you (you can change them first)'}</dt>
          <dd><ul className="list-disc pl-4">{understood.assumptions.map((text) => <li key={text}>{text}</li>)}</ul></dd>
        </div> : null}
        {hold.plan ? <div>
          <dt className="font-medium text-ink">{zh ? '计划' : 'Plan'}</dt>
          <dd>
            <p className="whitespace-pre-wrap">{hold.plan.summary}</p>
            {onOpenArtifact ? <button type="button" onClick={() => onOpenArtifact(hold.plan!.path)}
              className="mt-1 text-blue underline decoration-blue/35 underline-offset-2 hover:decoration-blue">
              {zh ? '查看完整计划' : 'Open the full plan'}
            </button> : null}
          </dd>
        </div> : <div>
          <dt className="font-medium text-ink">{zh ? `计划${plan.length ? `（${plan.length} 项）` : ''}` : `Plan${plan.length ? ` (${plan.length})` : ''}`}</dt>
          <dd>{plan.length
            ? <ol className="list-decimal pl-4">{plan.map((title, index) => <li key={`${index}:${title}`}>{title}</li>)}</ol>
            : (zh ? '开始后由规划者把第一阶段拆成具体任务。' : 'The Planner splits the first stage into tasks once it starts.')}</dd>
        </div>}
        {settings ? <div><dt className="font-medium text-ink">{zh ? '开工设置' : 'Launch settings'}</dt><dd>{settings}</dd></div> : null}
      </dl>
      {!readOnly ? <div className="mt-3 flex flex-wrap gap-2">
        <button type="button" disabled={busy !== null} onClick={run('start', onStart)}
          className="rounded-md border border-ok/50 bg-ok/10 px-3 py-1.5 text-xs font-medium text-ok hover:border-ok hover:bg-ok/15 disabled:opacity-60">
          {busy === 'start' ? (zh ? '正在启动…' : 'Starting…') : (zh ? '开始执行' : 'Start')}
        </button>
        <button type="button" disabled={busy !== null} onClick={run('rewrite', onRewrite)}
          className="rounded-md border border-line px-3 py-1.5 text-xs text-ink-dim hover:border-ink-faint hover:text-ink disabled:opacity-60">
          {busy === 'rewrite' ? (zh ? '正在撤回…' : 'Withdrawing…') : (zh ? '撤回并修改要求' : 'Withdraw and edit request')}
        </button>
      </div> : null}
    </section>
  );
}

/** The "开始执行" of the chat's confirm card, for places outside it (the map's
 * overview and the held task's card): the same action, the same words. */
export function LaunchStartButton({ onStart, zh, className = '' }: { onStart: () => Promise<void>; zh: boolean; className?: string }) {
  const [busy, setBusy] = useState(false);
  return <button type="button" disabled={busy} data-testid="launch-start" className={className}
    onClick={async (event) => {
      event.stopPropagation();
      setBusy(true);
      try { await onStart(); } finally { setBusy(false); }
    }}>
    {busy ? (zh ? '正在启动…' : 'Starting…') : (zh ? '开始执行' : 'Start')}
  </button>;
}
