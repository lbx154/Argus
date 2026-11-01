import type { ProjectUsageSummary, Snapshot, UsageGroup } from '@argus/contracts';
import { useI18n } from '../i18n';
import { roleLabel } from '../lib/enumLabels';
import { SPEND_ESTIMATE_HINT, spendEstimateLabel, workDurationLabel } from '../lib/projectOverview';

/** A view of the snapshot's shared summary; this panel makes no requests. */
export function UsageBreakdown({ usage }: { usage: ProjectUsageSummary }) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const price = (group: UsageGroup | ProjectUsageSummary) => spendEstimateLabel({ spendUsd: group.cost_usd,
    spendStatus: group.pricing_status as Snapshot['spend_status'] }, zh, true) || (zh ? '暂无' : 'None');
  return <section data-usage-breakdown className="min-w-0 rounded-lg border border-line bg-panel p-3 text-xs">
    <h3 className="font-semibold text-ink">{zh ? '花费分项' : 'Usage breakdown'}</h3>
    <p className="mt-2 text-ink-dim" data-usage-total>{zh ? '合计' : 'Total'} · {usage.call_count} {zh ? '次调用' : 'calls'} · {price(usage)}</p>
    <p className="mt-1 text-ink-dim">{workDurationLabel(usage.work_duration_ms, zh, usage.calls_without_duration > 0)}</p>
    <p className="mt-1 text-ink-faint">{zh ? '计入有用量记录及正在进行的调用；重叠只计一次，间歇不计。' : 'Calls with usage records and calls in progress contribute; overlaps count once and gaps add no time.'}</p>
    {!!usage.calls_without_duration && <p data-duration-incomplete className="mt-1 text-ink-faint">{zh
      ? `${usage.calls_without_duration} 次调用缺少时长或有效时间戳，未计入时长。`
      : `${usage.calls_without_duration} calls lack a duration or valid timestamp and add no time.`}</p>}
    <p className="mt-1 text-ink-faint">{SPEND_ESTIMATE_HINT[zh ? 'zh' : 'en']}</p>
    {(['by_route', 'by_role', 'by_model', 'by_price_source', 'by_date'] as const).map(dimension => <div key={dimension} className="mt-4 min-w-0">
      <h4 className="font-medium text-ink-dim">{{ by_route: zh ? '按角色 / CLI / 模型' : 'By role / CLI / model', by_price_source: zh ? '按计价来源' : 'By price source', by_role: zh ? '按角色' : 'By role', by_model: zh ? '按模型' : 'By model', by_date: zh ? '按日期（UTC）' : 'By date (UTC)' }[dimension]}</h4>
      <table className="mt-1 w-full table-fixed text-left" data-usage-dimension={dimension}>
        <thead className="text-ink-faint"><tr><th className="w-[45%] py-1 font-normal">{zh ? '分项' : 'Group'}</th><th className="w-[18%] text-right font-normal">{zh ? '调用' : 'Calls'}</th><th className="text-right font-normal">{zh ? '估算' : 'Estimate'}</th></tr></thead>
        <tbody>{(usage[dimension] ?? []).map(group => <tr key={group.key ?? ''} className="border-t border-line/50 text-ink-dim">
          <td className="break-words py-1.5 pr-2">{group.key === null ? (zh ? '未知' : 'Unknown') : dimension === 'by_role' ? roleLabel(group.key, t) : dimension === 'by_price_source' ? ({ 'cli-reported': zh ? 'CLI 报告金额（非账单）' : 'CLI reported (not a bill)', 'reference-estimate': zh ? '公开参考价估算' : 'Public reference estimate', 'catalog-estimate': zh ? 'CLI 目录价估算' : 'CLI catalog estimate', 'unknown': zh ? '未知费率' : 'Unknown rates', 'legacy-unspecified': zh ? '历史记录未注明来源' : 'Legacy source unspecified' }[group.key] ?? group.key) : group.key}</td>
          <td className="text-right tabular-nums">{group.call_count}</td><td className="text-right tabular-nums">{price(group)}</td>
        </tr>)}</tbody>
      </table>
    </div>)}
  </section>;
}
