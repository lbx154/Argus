import type { TimelineReport } from './types';

export function TimelineResults({ report, selectedId, text }: { report: TimelineReport; selectedId: string; text: (zh: string, en: string) => string }) {
  const selected = report.proposals.find((p) => p.id === selectedId)!;
  const end = Math.max(1, ...selected.schedule.map((row) => row.finish_hours));
  return <div className="space-y-5" aria-label={text('排期结果', 'Timeline results')}>
    <div className="grid gap-3 sm:grid-cols-2">{report.proposals.map((p) => <div key={p.id} className={`rounded-xl border p-4 ${p.id === selectedId ? 'border-blue' : 'border-line'}`}>
      <div className="text-xs text-ink-faint">{p.id === selectedId ? text('已选方案', 'Selected proposal') : text('备选方案', 'Alternative')}</div>
      <h3 className="mt-1 text-sm font-medium">{p.title}</h3>
      <div className="mt-3 text-2xl font-semibold">{p.finish_hours ? `${p.finish_hours.expected.toFixed(1)} h` : text('待重规划', 'Needs replanning')}</div>
      {p.finish_hours && <p className="mt-1 text-xs text-ink-faint">{text('区间', 'Range')} {p.finish_hours.lower.toFixed(1)}–{p.finish_hours.upper.toFixed(1)} h · {text('剩余', 'Remaining')} {p.remaining_hours?.toFixed(1)} h</p>}
      {p.deadline_gap_hours != null && <p className={`mt-2 text-xs ${p.deadline_gap_hours > 0 ? 'text-red' : 'text-ink-faint'}`}>{text('预计超期', 'Forecast overrun')} {p.deadline_gap_hours.toFixed(1)} h</p>}
    </div>)}</div>
    <p className="text-xs text-ink-faint">{text('初步估计，非成功承诺。区间覆盖已列出的工作；验证失败后的新增实验可能超出上限。各方案作为备选单独计算。', 'Preliminary estimates, not success guarantees. New experiments after a failed premise can exceed the range. Proposals are estimated as alternatives.')}</p>
    <section className="rounded-xl border border-line p-4">
      <h3 className="mb-4 text-sm font-medium">{text('实验排布', 'Experiment schedule')} · {text('从项目开始计时', 'Hours from project start')}</h3>
      <div className="space-y-3">{selected.schedule.map((row) => <div key={row.id}>
        <div className="mb-1 flex justify-between gap-3 text-xs"><span>{row.title}</span><span className="shrink-0 text-ink-faint">{row.start_hours.toFixed(1)}–{row.finish_hours.toFixed(1)} h</span></div>
        <div className="h-2.5 rounded-full bg-line/40" role="img" aria-label={`${row.title}: ${row.start_hours.toFixed(1)}–${row.finish_hours.toFixed(1)} h`}>
          <div className={`h-full rounded-full ${row.status === 'failed' ? 'bg-red' : row.status === 'completed' ? 'bg-emerald-500' : 'bg-blue'}`} style={{ marginLeft: `${row.start_hours / end * 100}%`, width: `${Math.max(0.3, (row.finish_hours - row.start_hours) / end * 100)}%` }} />
        </div>
      </div>)}</div>
    </section>
    {selected.deferred_task_ids.length > 0 && <p className="rounded-lg border border-line p-3 text-sm">{text('为满足期限延后的可选任务', 'Optional tasks deferred for the deadline')}: {selected.deferred_task_ids.join(', ')}</p>}
    {[...selected.failed_tasks, ...selected.blocked_tasks].map((row) => <p role="alert" className="rounded-lg border border-red/40 p-3 text-sm" key={row.id}>{row.id}: {row.reason}</p>)}
    <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr>{[text('任务', 'Task'), text('资源', 'Resources'), text('排队', 'Queue'), text('估计依据', 'Estimate basis')].map((h) => <th key={h} className="border-b border-line p-2">{h}</th>)}</tr></thead><tbody>
      {selected.schedule.map((row) => <tr key={row.id}><td className="border-b border-line p-2">{row.title}</td><td className="border-b border-line p-2">{Object.entries(row.resources).map(([k, v]) => `${k} × ${v}`).join(', ') || '—'}</td><td className="border-b border-line p-2">{row.resource_wait_hours.toFixed(1)} h</td><td className="border-b border-line p-2">{row.basis}</td></tr>)}
    </tbody></table></div>
    {report.revision && <section className="rounded-xl border border-line p-4"><h3 className="text-sm font-medium">{text('版本对照与延期原因', 'Revision and delay reasons')}</h3>
      <p className="mt-2 text-sm">{report.revision.reason}</p>
      <p className="mt-2 text-xs text-ink-faint">{text('初版预计', 'Original forecast')}: {report.revision.baseline_finish_hours?.toFixed(1) ?? '—'} h · {text('相对初版变化', 'Change from original')}: {report.revision.baseline_delta_hours?.toFixed(1) ?? '—'} h</p>
      {report.revision.task_variances.map((row, i) => <p className="mt-3 text-xs" key={`${row.id}-${i}`}>{row.id} · {row.observed ? text('实际延期', 'Observed delay') : text('预测延期', 'Forecast delay')} {row.delay_hours.toFixed(1)} h — {row.reason}<br />{row.evidence.join(', ')}</p>)}
    </section>}
  </div>;
}
