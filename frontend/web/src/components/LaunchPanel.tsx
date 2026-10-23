import { useState } from 'react';
import type { BacklogItem, LaunchHold, LaunchSettings } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { describeRequirement, launchHoldPlan, launchSettingsSummary, stripLaunchNote } from '../lib/launch';

const HOUR_CHOICES: Array<number | null> = [null, 2, 4, 24];

/** Settings for a project's first task, shown above the composer until that task is sent. */
export function LaunchSettingsBar({
  value,
  onChange,
  disabled,
}: {
  value: LaunchSettings;
  onChange: (next: LaunchSettings) => void;
  disabled?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const hourLabel = (hours: number | null) => hours == null
    ? (zh ? '不限' : 'No limit')
    : hours >= 24 ? (zh ? '1 天内' : 'Within 1 day') : (zh ? `${hours} 小时内` : `Within ${hours} h`);
  const selectClass = 'rounded border border-line bg-bg px-1.5 py-0.5 text-xs text-ink focus:border-blue focus:outline-none disabled:opacity-60';
  return (
    <div className="launch-settings mb-2 rounded-md border border-line/70 bg-panel/60 px-3 py-2 text-xs text-ink-dim" aria-label={zh ? '开工设置' : 'Launch settings'}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="font-medium text-ink">{zh ? '开工设置' : 'Launch settings'}</span>
        <label className="flex items-center gap-1">
          <span>{zh ? '首个结果' : 'First result'}</span>
          <select className={selectClass} disabled={disabled} value={value.first_result_hours ?? ''}
            onChange={(event) => onChange({ ...value, first_result_hours: event.target.value ? Number(event.target.value) : null })}>
            {HOUR_CHOICES.map((hours) => <option key={String(hours)} value={hours ?? ''}>{hourLabel(hours)}</option>)}
          </select>
        </label>
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
        <label className="flex items-center gap-1.5">
          <input type="checkbox" disabled={disabled} checked={value.hold}
            onChange={(event) => onChange({ ...value, hold: event.target.checked })} />
          <span>{zh ? '开始前先让我确认计划' : 'Let me confirm the plan before it starts'}</span>
        </label>
      </div>
      <p className="mt-1 text-[11px] text-ink-faint">
        {zh
          ? '只对这个项目的第一个任务生效。普通提问不受影响；需要团队执行的任务会先排好计划，等你点“开始执行”。'
          : 'Applies to this project’s first task. Questions are answered as usual; team work is planned first and waits for you to press Start.'}
      </p>
    </div>
  );
}

/** A planned first task waiting for the operator: what Argus understood, the queued plan, start or rewrite. */
export function LaunchHoldCard({
  hold,
  backlog,
  onStart,
  onRewrite,
  readOnly,
}: {
  hold: LaunchHold;
  backlog: BacklogItem[];
  onStart: () => Promise<void>;
  onRewrite: () => Promise<void>;
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
          <dd className="line-clamp-4 whitespace-pre-wrap">{objective}</dd>
        </div>
        {understood.requirements?.length ? <div>
          <dt className="font-medium text-ink">{zh ? '要求' : 'Requirements'}</dt>
          <dd><ul className="list-disc pl-4">{understood.requirements.map((requirement) => <li key={requirement.text}>{describeRequirement(requirement, zh)}</li>)}</ul></dd>
        </div> : null}
        {understood.assumptions?.length ? <div>
          <dt className="font-medium text-warn">{zh ? '经理替你定的理解（开始前可以改）' : 'Readings the Manager chose for you (you can change them first)'}</dt>
          <dd><ul className="list-disc pl-4">{understood.assumptions.map((text) => <li key={text}>{text}</li>)}</ul></dd>
        </div> : null}
        <div>
          <dt className="font-medium text-ink">{zh ? `计划${plan.length ? `（${plan.length} 项）` : ''}` : `Plan${plan.length ? ` (${plan.length})` : ''}`}</dt>
          <dd>{plan.length
            ? <ol className="list-decimal pl-4">{plan.map((title, index) => <li key={`${index}:${title}`}>{title}</li>)}</ol>
            : (zh ? '开始后由规划者把第一阶段拆成具体任务。' : 'The Planner splits the first stage into tasks once it starts.')}</dd>
        </div>
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
