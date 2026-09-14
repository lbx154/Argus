import { useEffect, useState } from 'react';
import { useI18n } from '../../i18n';
import { downloadBlob } from '../../lib/downloadBlob';
import { api } from '../api';
import { TaskEditor } from './TaskEditor';
import { TimelineResults } from './TimelineResults';
import type { TimelineEntry, TimelineInput, TimelineReport } from './types';
import { normalizeTimelineInput } from './types';

const field = 'w-full rounded-lg border border-line bg-transparent px-3 py-2 text-sm text-ink';
const button = 'rounded-lg border border-line px-3 py-2 text-sm disabled:opacity-40';

export function TimelinePage({ sid }: { sid: string }) {
  const { locale } = useI18n();
  const text = (zh: string, en: string) => locale === 'zh-CN' ? zh : en;
  const [input, setInput] = useState<TimelineInput | null>(null);
  const [saved, setSaved] = useState<TimelineEntry | null>(null);
  const [report, setReport] = useState<TimelineReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [computing, setComputing] = useState(false);
  const [error, setError] = useState('');
  const [estimateError, setEstimateError] = useState('');
  const [notice, setNotice] = useState('');
  const [reason, setReason] = useState('');
  const [importText, setImportText] = useState('');
  const [reload, setReload] = useState(0);
  const dirty = Boolean(input && JSON.stringify(input) !== JSON.stringify(saved?.input));

  useEffect(() => {
    let live = true;
    setLoading(true); setReady(false); setError(''); setInput(null); setSaved(null); setReport(null);
    api.timelineLatest(sid).then(({ latest }) => {
      if (!live) return;
      const normalized = latest ? { ...latest, input: normalizeTimelineInput(latest.input) } : null;
      setSaved(normalized); setInput(normalized?.input ?? null); setReport(normalized?.report ?? null);
      setReason(''); setNotice(''); setReady(true);
    }).catch((e: Error) => { if (live) setError(e.message); })
      .finally(() => { if (live) setLoading(false); });
    return () => { live = false; };
  }, [sid, reload]);

  useEffect(() => {
    setReport(null); setEstimateError('');
    if (!input) { setComputing(false); return; }
    if (saved && JSON.stringify(input) === JSON.stringify(saved.input)) {
      setReport(saved.report); setComputing(false); return;
    }
    const controller = new AbortController();
    setComputing(true);
    const timeout = setTimeout(() => {
      api.timelineEstimate(input, controller.signal).then((value) => {
        if (!controller.signal.aborted) setReport(value);
      }).catch((e: Error) => {
        if (!controller.signal.aborted) setEstimateError(e.message);
      }).finally(() => { if (!controller.signal.aborted) setComputing(false); });
    }, 350);
    return () => { clearTimeout(timeout); controller.abort(); };
  }, [input, saved]);

  const loadExample = async () => {
    setBusy(true); setError(''); setNotice('');
    try {
      setInput(await api.timelineExample());
      setReason(text('加载演示 proposal', 'Load demonstration proposal'));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const save = async () => {
    if (!input || !report || computing || !reason.trim()) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const entry = await api.timelineSave(sid, input, saved?.version ?? 0, reason);
      setSaved(entry); setInput(entry.input); setReport(entry.report); setReason('');
      setNotice(text(`已保存版本 ${entry.version}，刷新页面后仍可查看。`, `Version ${entry.version} saved. It survives a page refresh.`));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const importProposal = async () => {
    setBusy(true); setError('');
    try {
      const value = JSON.parse(importText) as TimelineInput;
      await api.timelineEstimate(value); // Use the canonical validator before rendering imported data.
      setInput(normalizeTimelineInput(value));
      setReason(text('导入 proposal', 'Import proposal'));
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };

  if (loading) return <div role="status" className="p-6 text-sm text-ink-faint">{text('正在读取已保存计划…', 'Loading saved timeline…')}</div>;

  return <div className="mx-auto w-full max-w-7xl space-y-5 p-4 sm:p-6">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><div className="text-xs text-blue">PROPOSAL → EXPERIMENT → PAPER</div><h2 className="mt-1 text-xl font-semibold">{text('研究排期', 'Research timeline')}</h2>
        <p className="mt-2 text-sm text-ink-faint">{text('选择 idea 和期望期限，查看具体工期、实验排布与调整原因。', 'Choose an idea and deadline to see estimates, experiment schedules, and reasons for changes.')}</p></div>
      <div className="flex flex-wrap gap-2">
        <button className={button} disabled={busy || !ready} onClick={loadExample}>{text('加载论文示例', 'Load paper example')}</button>
        <button className={button} disabled={busy} onClick={() => setReload((n) => n + 1)}>{text('重新载入已保存计划', 'Reload saved timeline')}</button>
        {input && <button className={button} onClick={() => downloadBlob(new Blob([JSON.stringify(input, null, 2)], { type: 'application/json' }), 'proposal.json')}>{text('导出 proposal', 'Export proposal')}</button>}
      </div>
    </header>
    {error && <div role="alert" className="rounded-xl border border-red/40 p-3 text-sm">{error}<p className="mt-2 text-ink-faint">{text('若提示版本冲突，先导出当前草稿，再重新载入已保存计划。', 'For a version conflict, export your draft before reloading the saved timeline.')}</p></div>}
    {notice && <p role="status" className="rounded-xl border border-blue/40 p-3 text-sm">{notice}</p>}
    {!input && <section className="rounded-xl border border-dashed border-line p-8 text-center"><h3 className="font-medium">{text('还没有研究排期', 'No research timeline yet')}</h3><p className="mt-2 text-sm text-ink-faint">{text('点击“加载论文示例”即可验证完整流程，也可以在下方导入自己的 proposal。示例数字仅用于演示。', 'Load the paper example to try the complete flow, or import your proposal below. Example numbers are illustrative.')}</p></section>}
    {input && <div className="grid items-start gap-6" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 360px), 1fr))' }}>
      <fieldset disabled={busy} className="min-w-0 space-y-5">
        <section className="space-y-3 rounded-xl border border-line p-4">
          <label className="block text-xs">{text('选择 idea / proposal', 'Choose idea / proposal')}<select aria-label={text('选择 idea / proposal', 'Choose idea / proposal')} className={field} value={input.selected_proposal_id} onChange={(e) => setInput({ ...input, selected_proposal_id: e.target.value })}>{input.proposals.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}</select></label>
          <label className="block text-xs">{text('方案名称', 'Proposal title')}<input className={field} value={input.proposals.find((p) => p.id === input.selected_proposal_id)!.title} onChange={(e) => setInput({ ...input, proposals: input.proposals.map((p) => p.id === input.selected_proposal_id ? { ...p, title: e.target.value } : p) })} /></label>
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs">{text('期望完成时间（小时）', 'Target finish (hours)')}<input className={field} type="number" min="0" step="any" value={input.deadline_hours ?? ''} placeholder={text('不限', 'No deadline')} onChange={(e) => setInput({ ...input, deadline_hours: e.target.value === '' ? null : Number(e.target.value) })} /></label>
            <label className="text-xs">{text('已过去时间（小时）', 'Elapsed time (hours)')}<input className={field} type="number" min="0" step="any" value={input.now_hours} onChange={(e) => setInput({ ...input, now_hours: Number(e.target.value) })} /></label>
            {Object.entries(input.resources).map(([name, count]) => <label className="text-xs" key={name}>{name} {text('可用槽位', 'available slots')}<input className={field} type="number" min="1" step="1" value={count} onChange={(e) => setInput({ ...input, resources: { ...input.resources, [name]: Number(e.target.value) } })} /></label>)}
          </div>
          <p className="text-xs text-ink-faint">{text('所有时间从项目开始计时；例如 120 小时 = 第 5 天。资源按持续可用计算。', 'All times count from project start; 120 hours means day 5. Resources are assumed continuously available.')}</p>
          <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={input.defer_optional} onChange={(e) => setInput({ ...input, defer_optional: e.target.checked })} />{text('超期时延后可选任务，保留必需实验', 'Defer optional tasks when over deadline; keep required experiments')}</label>
          <button className={button} onClick={() => {
            let n = input.proposals.length + 1; while (input.proposals.some((p) => p.id === `idea-${n}`)) n++;
            const id = `idea-${n}`; setInput({ ...input, selected_proposal_id: id, proposals: [...input.proposals, { id, title: text('新 proposal', 'New proposal'), tasks: [{ id: 'validation', title: text('关键假设验证', 'Validate the key premise'), phase: 'validation', duration_hours: [2, 4, 16], basis: text('初步估计，待校准', 'Preliminary, uncalibrated estimate') }] }] });
          }}>{text('添加候选方案', 'Add proposal')}</button>
        </section>
        <TaskEditor input={input} onChange={setInput} text={text} />
        <section className="space-y-3 rounded-xl border border-line p-4">
          <label className="block text-xs">{text('本次保存 / 调整原因', 'Reason for this revision')}<textarea className={field} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={text('说明为什么改变计划；延期请在具体任务中填写证据', 'Explain the change; add delay evidence on the affected task')} /></label>
          <div className="flex items-center justify-between gap-2"><span className="text-xs text-ink-faint">{saved ? text(`已保存 v${saved.version}`, `Saved v${saved.version}`) : text('未保存', 'Not saved')}{dirty ? text(' · 有未保存修改', ' · Unsaved changes') : ''}</span>
            <button className={`${button} border-blue bg-blue/10`} disabled={!ready || !dirty || !report || computing || Boolean(estimateError) || !reason.trim()} onClick={save}>{text('保存计划版本', 'Save timeline version')}</button></div>
          <p className="text-xs text-ink-faint">{text('保存计划不会启动实验。实际进展和失败原因由实验结果或操作者更新。', 'Saving a timeline does not start experiments. Update progress and failure reasons from actual results.')}</p>
        </section>
      </fieldset>
      <div className="min-w-0 space-y-3">
        {computing && <p role="status" className="text-sm text-ink-faint">{text('正在更新排期…', 'Updating schedule…')}</p>}
        {estimateError && <p role="alert" className="rounded-xl border border-red/40 p-3 text-sm">{text('无法计算，请检查估计区间、依赖和资源：', 'Cannot calculate; check ranges, dependencies, and resources: ')}{estimateError}</p>}
        {!computing && report && <TimelineResults report={report} selectedId={input.selected_proposal_id} text={text} />}
      </div>
    </div>}
    <details className="rounded-xl border border-line p-4"><summary className="cursor-pointer text-sm">{text('导入自己的 proposal（JSON）', 'Import your proposal (JSON)')}</summary><textarea className={`${field} mt-3 font-mono`} rows={6} aria-label={text('Proposal JSON', 'Proposal JSON')} value={importText} onChange={(e) => setImportText(e.target.value)} /><button className={`${button} mt-2`} disabled={!ready || busy || !importText.trim()} onClick={importProposal}>{text('校验并导入', 'Validate and import')}</button></details>
  </div>;
}
