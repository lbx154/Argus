import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, FileText } from 'lucide-react';
import {
  api,
  type ResearchMethod,
  type ResearchMethodChange,
  type ResearchMethodChecks,
  type ResearchMethodComponent,
  type ResearchMethodComponentStatus,
  type ResearchMethodHyperparameter,
  type ResearchMethodReusedCode,
  type ResearchMethodTest,
} from '../api';
import { MarkdownContent } from '../components/MarkdownContent';
import { useI18n, type Locale } from '../i18n';
import { formatRelativeTime } from '../lib/format';

export interface MethodCardProps {
  sid: string;
  active: boolean;
  compact?: boolean;
  onOpenArtifact?: (path: string) => void;
}

export const methodQueryKey = (sid: string) => ['research-method', sid] as const;

type Text = (zh: string, en: string) => string;

/**
 * Visual tone per derived component status. `ok` is the quiet green of a
 * proven component; `warning` flags a contradiction the reader must see;
 * `caution` is a partial proof; `muted` means no test carries the marker;
 * `pending` means tests exist but the host has not run them yet.
 */
export type MethodStatusTone = 'ok' | 'warning' | 'caution' | 'muted' | 'pending' | 'unknown';

const STATUS_TONE: Record<ResearchMethodComponentStatus, MethodStatusTone> = {
  proven: 'ok',
  contradicted: 'warning',
  partial: 'caution',
  untested: 'muted',
  unchecked: 'pending',
};

export function methodStatusTone(status: string): MethodStatusTone {
  return STATUS_TONE[status as ResearchMethodComponentStatus] ?? 'unknown';
}

const TONE_CLASS: Record<MethodStatusTone, string> = {
  ok: 'text-ok border-ok/40 bg-ok/5',
  warning: 'text-warn border-warn/50 bg-warn/10 font-medium',
  caution: 'text-warn/80 border-warn/30',
  muted: 'text-ink-faint border-line/40',
  pending: 'text-ink-faint border-line/40 italic',
  unknown: 'text-ink-dim border-line/50',
};

function statusLabel(status: string, text: Text): string {
  switch (status) {
    case 'proven': return text('已证实', 'proven');
    case 'contradicted': return text('被推翻', 'contradicted');
    case 'partial': return text('部分证实', 'partial');
    case 'untested': return text('无测试', 'untested');
    case 'unchecked': return text('未运行', 'unchecked');
    default: return status || '—';
  }
}

function StatusCell({ status, text }: { status: string; text: Text }) {
  const tone = methodStatusTone(status);
  return <span className={`inline-block whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] leading-4 ${TONE_CLASS[tone]}`} data-method-status={status} data-method-status-tone={tone}>{statusLabel(status, text)}</span>;
}

const FAILING = new Set(['FAILED', 'ERROR']);

/** "3 tests · knockout ×1, invariant ×2"; failing test ids are listed in the hover title. */
export function summarizeTests(tests: ResearchMethodTest[], text: Text): { label: string; title: string; failing: number } {
  if (!tests.length) return { label: '—', title: text('没有测试带这个组件的标记', 'No test carries this component marker'), failing: 0 };
  const kinds = new Map<string, number>();
  for (const test of tests) kinds.set(test.kind || 'invariant', (kinds.get(test.kind || 'invariant') ?? 0) + 1);
  const kindText = [...kinds.entries()].map(([kind, count]) => `${kind} ×${count}`).join(', ');
  const failingIds = tests.filter(test => FAILING.has((test.outcome ?? '').toUpperCase())).map(test => test.id);
  const count = text(`${tests.length} 个测试`, `${tests.length} ${tests.length === 1 ? 'test' : 'tests'}`);
  const title = failingIds.length
    ? `${text('失败', 'Failing')}: ${failingIds.join('\n')}`
    : tests.map(test => `${test.id} — ${test.outcome ?? text('未运行', 'not run')}`).join('\n');
  return { label: `${count} · ${kindText}`, title, failing: failingIds.length };
}

function TestsCell({ tests, text }: { tests: ResearchMethodTest[]; text: Text }) {
  const summary = summarizeTests(tests, text);
  return <span className={`whitespace-nowrap text-[11px] ${summary.failing ? 'text-warn' : 'text-ink-faint'}`} title={summary.title} data-method-tests={tests.length} data-method-failing={summary.failing}>{summary.label}</span>;
}

/** Epoch seconds, epoch milliseconds or an ISO-8601 string to a Date; null when unreadable. */
export function methodDate(value: number | string | null | undefined): Date | null {
  if (value == null || value === '') return null;
  const date = typeof value === 'number' ? new Date(value < 1e12 ? value * 1000 : value) : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

const inline = (value: string) => value ? <MarkdownContent>{value}</MarkdownContent> : <span className="text-ink-faint">—</span>;

function SectionTitle({ children }: { children: string }) {
  return <h3 className="text-xs font-semibold text-ink">{children}</h3>;
}

const HEAD_CLASS = 'text-[11px] uppercase tracking-wide text-ink-faint';
const TH_CLASS = 'pb-1 pr-3 font-medium';

function ComponentsTable({ rows, text }: { rows: ResearchMethodComponent[]; text: Text }) {
  if (!rows.length) return null;
  return <div className="mt-2 overflow-x-auto" data-testid="method-components">
    <table className="w-full border-collapse text-left text-xs">
      <thead>
        <tr className={HEAD_CLASS}>
          <th className={TH_CLASS}>{text('组件', 'Component')}</th>
          <th className={TH_CLASS}>{text('方法规定', 'Prescribes')}</th>
          <th className={TH_CLASS}>{text('备注', 'Notes')}</th>
          <th className={TH_CLASS}>{text('状态', 'Status')}</th>
          <th className="pb-1 font-medium">{text('测试', 'Tests')}</th>
        </tr>
      </thead>
      <tbody className="align-top text-ink-dim">
        {rows.map((row, index) => <tr key={`${row.component}-${index}`} className="border-t border-line/40" data-method-component={row.component}>
          <td className="py-1 pr-3 font-medium text-ink">{inline(row.component)}</td>
          <td className="py-1 pr-3">{inline(row.prescribes)}</td>
          <td className="py-1 pr-3">{inline(row.notes ?? '')}</td>
          <td className="py-1 pr-3"><StatusCell status={row.status} text={text} /></td>
          <td className="py-1"><TestsCell tests={row.tests ?? []} text={text} /></td>
        </tr>)}
      </tbody>
    </table>
  </div>;
}

function ReusedCodeTable({ rows, text }: { rows: ResearchMethodReusedCode[]; text: Text }) {
  if (!rows.length) return null;
  return <div className="mt-3 overflow-x-auto" data-testid="method-reused-code">
    <SectionTitle>{text('复用代码（从 import 扫描得出）', 'Reused code (from the import scan)')}</SectionTitle>
    <table className="mt-1 w-full border-collapse text-left text-xs">
      <thead>
        <tr className={HEAD_CLASS}>
          <th className={TH_CLASS}>{text('名称', 'Name')}</th>
          <th className={TH_CLASS}>{text('类型', 'Kind')}</th>
          <th className={TH_CLASS}>{text('版本 / 修订', 'Revision / version')}</th>
          <th className={TH_CLASS}>{text('用到的模块', 'Modules')}</th>
          <th className="pb-1 font-medium">{text('引用处', 'Imported from')}</th>
        </tr>
      </thead>
      <tbody className="align-top text-ink-dim">
        {rows.map((row, index) => <tr key={`${row.name}-${index}`} className="border-t border-line/40" data-method-row={row.name}>
          <td className="py-1 pr-3 font-medium text-ink" title={row.remote || undefined}>{row.name || '—'}</td>
          <td className="py-1 pr-3">{row.kind === 'third_party' ? text('第三方克隆', 'third_party clone') : text('已安装包', 'package')}</td>
          <td className="py-1 pr-3 font-mono text-[11px]">{row.revision_or_version || '—'}</td>
          <td className="py-1 pr-3 break-all font-mono text-[11px]">{row.modules?.length ? row.modules.join(', ') : '—'}</td>
          <td className="py-1" title={row.imported_from?.join('\n') || undefined}>{text(`${row.imported_from?.length ?? 0} 个文件`, `${row.imported_from?.length ?? 0} ${row.imported_from?.length === 1 ? 'file' : 'files'}`)}</td>
        </tr>)}
      </tbody>
    </table>
  </div>;
}

function HyperparametersTable({ rows, text }: { rows: ResearchMethodHyperparameter[]; text: Text }) {
  if (!rows.length) return null;
  return <div className="mt-3 overflow-x-auto" data-testid="method-hyperparameters">
    <SectionTitle>{text('超参数（从配置文件得出）', 'Hyperparameters (from the config files)')}</SectionTitle>
    <table className="mt-1 w-full border-collapse text-left text-xs">
      <thead>
        <tr className={HEAD_CLASS}>
          <th className={TH_CLASS}>{text('键', 'Key')}</th>
          <th className={TH_CLASS}>{text('值', 'Value')}</th>
          <th className="pb-1 font-medium">{text('为什么', 'Why')}</th>
        </tr>
      </thead>
      <tbody className="align-top text-ink-dim">
        {rows.map((row, index) => <tr key={`${row.file}:${row.key}-${index}`} className="border-t border-line/40" data-method-row={row.key} data-method-changed={row.changed ? 'true' : undefined}>
          <td className="py-1 pr-3 font-medium text-ink">
            <span className="font-mono text-[11px]">{row.key}</span>
            {row.file ? <span className="block text-[10px] font-normal text-ink-faint">{row.file}</span> : null}
          </td>
          <td className="py-1 pr-3">
            <span className="font-mono text-[11px]">{row.value}</span>
            {row.changed ? <span className="ml-1.5 inline-flex items-center gap-1 whitespace-nowrap rounded border border-warn/40 px-1 text-[10px] leading-4 text-warn" data-testid="method-hyperparameter-changed">
              {text('已改', 'changed')}{row.previous != null && row.previous !== '' ? <span className="font-mono text-ink-faint line-through">{row.previous}</span> : null}
            </span> : null}
          </td>
          <td className="py-1">{row.why ? inline(row.why) : <span className="text-ink-faint" title={text('值旁没有 # why: 注释', 'No # why: comment next to the value')}>—</span>}</td>
        </tr>)}
      </tbody>
    </table>
  </div>;
}

function ChangeLog({ entries, text }: { entries: ResearchMethodChange[]; text: Text }) {
  if (!entries.length) return null;
  return <div className="mt-3" data-testid="method-change-log">
    <SectionTitle>{text('变更记录（来自 git）', 'Change log (from git)')}</SectionTitle>
    <ul className="mt-1 space-y-0.5 text-xs text-ink-dim">
      {entries.map((entry, index) => <li key={`${entry.when}-${index}`} className="flex gap-2" title={entry.files?.join('\n') || undefined}>
        <span className="shrink-0 font-mono text-[11px] text-ink-faint">{entry.when}</span>
        <span className="min-w-0 truncate">{entry.summary}</span>
      </li>)}
    </ul>
  </div>;
}

function ChecksLine({ checks, locale, text }: { checks: ResearchMethodChecks | null | undefined; locale: Locale; text: Text }) {
  if (!checks) return null;
  const counts = Object.entries(checks.counts ?? {}).filter(([, count]) => count > 0).map(([outcome, count]) => `${outcome} ${count}`).join(', ');
  const exit = checks.exit_code == null ? text('未结束', 'no exit code') : text(`退出码 ${checks.exit_code}`, `exit ${checks.exit_code}`);
  const ranAt = methodDate(checks.ran_at);
  return <p className="mt-2 text-[11px] text-ink-faint" data-testid="method-checks" title={ranAt ? ranAt.toLocaleString(locale) : undefined}>
    {text(`最近一次检查：第 ${checks.round_index} 轮 · ${exit}`, `Latest checks: round ${checks.round_index} · ${exit}`)}{counts ? ` · ${counts}` : ''}{ranAt ? ` · ${formatRelativeTime(ranAt, locale)}` : ''}
  </p>;
}

/**
 * The method card for a human reader: the hand-written METHOD.md (written once,
 * touched only when the method changes) next to what the host derived from the
 * code, the tests, the config files and git. Nothing here gates a stage.
 */
export function MethodCard({ sid, active, compact = false, onOpenArtifact }: MethodCardProps) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const text: Text = (chinese, english) => zh ? chinese : english;
  const [open, setOpen] = useState(!compact);
  const enabled = active && !!sid;
  const query = useQuery({
    queryKey: methodQueryKey(sid),
    queryFn: ({ signal }) => api.researchMethod(sid, signal),
    enabled, refetchInterval: enabled ? 30_000 : false, staleTime: 20_000,
    retry: false, retryOnMount: false, refetchOnWindowFocus: false, refetchOnReconnect: false,
  });
  const method: ResearchMethod | undefined = query.data;
  if (!method || !method.exists) return null;
  const updatedDate = methodDate(method.updated_at);
  const components = method.components ?? [];
  const unlisted = method.unlisted_components ?? [];
  return <section className={`mx-4 flex min-h-0 flex-col overflow-hidden rounded-lg border border-line/70 bg-panel ${compact ? 'my-2 px-3 py-2' : 'my-3 px-4 py-3'}`}
    aria-label={text('方法卡', 'Method card')} data-testid="method-card" data-project-id={sid} data-compact={compact}>
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-2">
      <button type="button" className="flex min-w-0 items-center gap-2 text-left" aria-expanded={open} onClick={() => setOpen(value => !value)}>
        <FileText size={16} className="shrink-0 text-blue-sky" />
        <h2 className="text-sm font-semibold text-ink">{text('方法卡', 'Method card')}</h2>
        {method.title ? <span className="truncate text-xs text-ink-dim">{method.title}</span> : null}
        <ChevronDown size={12} className={`shrink-0 text-ink-faint ${open ? 'rotate-180' : ''}`} />
      </button>
      <span className="text-[11px] text-ink-faint" title={updatedDate ? updatedDate.toLocaleString(locale) : undefined}>
        {onOpenArtifact ? <button type="button" className="underline-offset-2 hover:underline" onClick={() => onOpenArtifact(method.path)}>{method.path}</button> : method.path}
        {updatedDate ? <>{' · '}{text('更新于', 'updated')} {formatRelativeTime(updatedDate, locale)}</> : null}
      </span>
    </header>
    {open ? <div className={`mt-2 min-h-0 overflow-y-auto overscroll-contain scroll-thin ${compact ? 'max-h-[30vh]' : 'max-h-[40vh]'}`} data-testid="method-card-body" tabIndex={0} aria-label={text('方法说明', 'Method text')}>
      {method.statement ? <p className="text-[13px] leading-6 text-ink-dim" data-testid="method-statement">{method.statement}</p> : null}
      <ComponentsTable rows={components} text={text} />
      {components.length === 0 ? <p className="mt-1 text-xs text-ink-faint">{text('文档里没有组件表；下面是全文。', 'The document has no component table; the full text follows.')}</p> : null}
      {unlisted.length ? <p className="mt-1 text-xs text-ink-faint" data-testid="method-unlisted-components">
        {text('测试里标记了但卡片未列出的组件：', 'Components marked in tests but not named in the card: ')}{unlisted.join(', ')}
      </p> : null}
      <ReusedCodeTable rows={method.reused_code ?? []} text={text} />
      <HyperparametersTable rows={method.hyperparameters ?? []} text={text} />
      <ChangeLog entries={method.change_log ?? []} text={text} />
      <ChecksLine checks={method.checks} locale={locale} text={text} />
      <div className="mt-3 border-t border-line/40 pt-2 text-[13px] leading-6 text-ink-dim" data-testid="method-card-markdown">
        <MarkdownContent sid={sid} onOpenArtifact={onOpenArtifact}>{method.markdown}</MarkdownContent>
      </div>
      {method.truncated ? <p className="mt-2 text-xs text-ink-faint" role="status" data-testid="method-card-truncated">
        {text('只显示了前 64 KiB。', 'Showing only the first 64 KiB.')}
      </p> : null}
    </div> : null}
  </section>;
}

export default MethodCard;
