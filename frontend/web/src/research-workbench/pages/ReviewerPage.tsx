import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, CheckCircle2, Code2, FileSearch2, FileText, History, ListChecks, RotateCcw, Scale, Send, ShieldCheck, Sparkles } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { Badge, EmptyState, EventTimeline, Markdown } from '../components/Common';
import { formatDate, statusTone } from '../utils';
import { workspaceApi } from '../workspaceApi';
import { useWorkbenchText } from '../useWorkbenchText';
import { useWorkspaceProfile } from '../useWorkspaceProfile';
import type { WorkspacePageProps } from './pageTypes';

const VENUES = ['ICLR', 'NeurIPS', 'ICML', 'TMLR', 'ACL', 'EMNLP', 'NAACL', 'CVPR', 'ICCV', 'ECCV', 'AAAI', 'KDD', 'Nature Machine Intelligence', 'JMLR', 'IEEE TPAMI', '__custom__'];
const REVIEW_DIMENSIONS = ['Novelty', 'Technical soundness', 'Experimental rigor', 'Baseline fairness', 'Statistical validity', 'Reproducibility', 'Writing clarity', 'Ethics / limitations', 'Artifact availability'];
const DEFAULT_SCOPE_ZH = '请特别检查 train/dev/test 泄漏、baseline 是否公平，以及 novelty claim 是否被现有直接工作覆盖。';
const DEFAULT_SCOPE_EN = 'Pay special attention to train/dev/test leakage, baseline fairness, and whether direct prior work covers the novelty claim.';
type ReviewMode = 'process' | 'final';

function ReviewFlow({ mode, reviewerActive, hasReport }: { mode: ReviewMode; reviewerActive: boolean; hasReport: boolean }) {
  const { text } = useWorkbenchText();
  const steps = mode === 'process'
    ? [
        [text('Engineer 执行', 'Engineer execution'), text('代码、实验与证据', 'Code, experiments, and evidence'), Code2],
        [text('Reviewer 检查', 'Reviewer check'), text('独立核验当前轮次', 'Independent round verification'), ShieldCheck],
        [text('形成 Verdict', 'Produce verdict'), 'done / continue / blocked', Scale],
        [text('回流下一轮', 'Return to next round'), text('修复任务进入 backlog', 'Repair tasks enter the backlog'), RotateCcw],
      ] as const
    : [
        [text('选择最终稿', 'Select final draft'), text('LaTeX / PDF 与证据包', 'LaTeX / PDF and evidence package'), FileText],
        [text('独立最终审稿', 'Independent final review'), text('按目标 venue 全面检查', 'Full target-venue review'), ShieldCheck],
        [text('生成审稿报告', 'Generate review report'), text('评分、问题与置信度', 'Scores, issues, and confidence'), FileSearch2],
        [text('修改清单', 'Revision checklist'), text('投稿前人工确认', 'Human confirmation before submission'), ListChecks],
      ] as const;
  return <div className="review-flow">{steps.map(([title, detail, Icon], index) => <div className={(reviewerActive && index === 1) || (hasReport && index >= 2) ? 'is-active' : index === 0 ? 'is-done' : ''} key={title}><span>{index + 1}</span><Icon size={17} /><div><strong>{title}</strong><small>{detail}</small></div>{index < steps.length - 1 ? <b>→</b> : null}</div>)}</div>;
}

export function ReviewerPage(props: WorkspacePageProps) {
  const { locale, text } = useWorkbenchText();
  const [mode, setMode] = useState<ReviewMode>('process');
  const view = props.snapshot.mission_view;
  const processReviews = useMemo(() => (view?.role_work ?? []).filter((item) => item.role === 'reviewer').filter((item) => /review|verdict|decision|completion|handoff/i.test(`${item.kind} ${item.title}`)).sort((a, b) => b.ts - a.ts), [view?.role_work]);
  const reviewEvents = useMemo(() => props.events.filter((event) => /review/.test(String(event.type ?? '')) || /review/.test(String(event.agent_layer ?? event.actor ?? ''))), [props.events]);
  const [selectedId, setSelectedId] = useState('');
  const selected = processReviews.find((item) => item.id === selectedId) ?? processReviews[0] ?? null;
  const workspace = useWorkspaceProfile(props.sid, 'review');
  const tree = useQuery({ queryKey: ['review-workspace-tree', props.sid, workspace.workspaceId], queryFn: ({ signal }) => workspaceApi.tree(props.sid, workspace.workspaceId, signal), enabled: Boolean(workspace.workspaceId), refetchInterval: 12_000 });
  const finalReports = (tree.data?.entries ?? []).filter((entry) => entry.type === 'file' && !/final[_-]?review[_-]?request/i.test(entry.path) && /final[_-]?review|final[_-]?.*verdict|submission[_-]?review/i.test(entry.path)).filter((entry) => ['.md', '.txt', '.json'].includes(entry.extension)).sort((a, b) => b.mtime - a.mtime);
  const manuscriptFiles = (tree.data?.entries ?? []).filter((entry) => entry.type === 'file' && ['.tex', '.md', '.pdf'].includes(entry.extension) && /(?:^|\/)(paper|manuscript|technical_report)(?:\/|$)/i.test(entry.path)).sort((a, b) => b.mtime - a.mtime);
  const [manuscriptPath, setManuscriptPath] = useState('');
  const [finalPath, setFinalPath] = useState('');
  const finalSelected = finalReports.find((item) => item.path === finalPath) ?? finalReports[0] ?? null;
  const finalFile = useQuery({ queryKey: ['final-review-file', props.sid, workspace.workspaceId, finalSelected?.path, finalSelected?.mtime], queryFn: ({ signal }) => workspaceApi.file(props.sid, workspace.workspaceId, finalSelected!.path, signal), enabled: Boolean(finalSelected && workspace.workspaceId), refetchInterval: 12_000 });
  const [venue, setVenue] = useState('ICLR');
  const [customVenue, setCustomVenue] = useState('');
  const [venueType, setVenueType] = useState<'conference' | 'journal' | 'workshop'>('conference');
  const [strictness, setStrictness] = useState<'preflight' | 'standard' | 'strict' | 'red-team'>('strict');
  const [dimensions, setDimensions] = useState<string[]>(['Novelty', 'Technical soundness', 'Experimental rigor', 'Baseline fairness', 'Reproducibility']);
  const [scope, setScope] = useState(() => locale === 'zh-CN' ? DEFAULT_SCOPE_ZH : DEFAULT_SCOPE_EN);
  const [finalBusy, setFinalBusy] = useState(false);
  const [finalError, setFinalError] = useState('');
  const [finalReceipt, setFinalReceipt] = useState('');
  const verdict = view?.review;
  const reviewerRole = view?.roles.find((role) => role.role === 'reviewer') || props.snapshot.roles.find((role) => role.role === 'reviewer');
  useEffect(() => {
    setScope((current) => current === DEFAULT_SCOPE_ZH || current === DEFAULT_SCOPE_EN
      ? locale === 'zh-CN' ? DEFAULT_SCOPE_ZH : DEFAULT_SCOPE_EN
      : current);
  }, [locale]);
  const launch = async () => {
    const resolvedVenue = venue === '__custom__' ? customVenue.trim() : venue;
    if (!resolvedVenue || !scope.trim() || !confirm(text(`确认在项目完成后按 ${resolvedVenue} 标准发起独立最终审稿？`, `Start an independent final review using ${resolvedVenue} standards?`))) return;
    setFinalBusy(true); setFinalError(''); setFinalReceipt('');
    try {
      const result = await api.createFinalReview(props.sid, { venue: resolvedVenue, venue_type: venueType, strictness, manuscript_path: manuscriptPath, emphasis: dimensions, scope });
      setFinalReceipt(text(`最终审稿已进入 Argus 队列 · ${result.manifest_path}`, `Final review queued in Argus · ${result.manifest_path}`));
      await Promise.all([props.refresh(), tree.refetch()]);
    } catch (caught) { setFinalError(caught instanceof Error ? caught.message : String(caught)); }
    finally { setFinalBusy(false); }
  };

  return (
    <div className="ros-page reviewer-v2">
      <header className="ros-page-header"><div><div className="eyebrow">REVIEWER ARENA</div><h1>{text('模拟审稿', 'Reviewer arena')}</h1><p>{text('过程审稿用于每轮 Engineer ⇄ Reviewer 纠偏；最终审稿用于论文完成后的投稿前独立检查。', 'Process review corrects each Engineer ⇄ Reviewer round; final review is an independent pre-submission check.')}</p></div><Badge tone={reviewerRole?.status ? statusTone(reviewerRole.status) : 'neutral'} dot={reviewerRole?.status === 'active'}>{reviewerRole?.status || 'waiting'}</Badge></header>
      <div className="review-mode-tabs"><button type="button" className={mode === 'process' ? 'is-active' : ''} onClick={() => setMode('process')}><History size={16} /><div><strong>{text('过程审稿', 'Process review')}</strong><small>{text('Argus 每轮执行中的 Reviewer 反馈', 'Reviewer feedback during each Argus round')}</small></div><Badge tone="neutral">{processReviews.length}</Badge></button><button type="button" className={mode === 'final' ? 'is-active' : ''} onClick={() => setMode('final')}><Scale size={16} /><div><strong>{text('最终审稿', 'Final review')}</strong><small>{text('项目完成后的独立投稿前审稿', 'Independent pre-submission review')}</small></div><Badge tone="neutral">{finalReports.length}</Badge></button></div>

      {mode === 'process' ? (
        <>
        <ReviewFlow mode="process" reviewerActive={reviewerRole?.status === 'active'} hasReport={Boolean(verdict?.status)} />
        <div className="process-review-layout">
          <aside className="ros-card review-rounds"><header><div><span>ENGINEER ⇄ REVIEWER</span><h2>{text('过程审稿轮次', 'Process review rounds')}</h2></div></header><div>{processReviews.length ? processReviews.map((item) => <button type="button" className={selected?.id === item.id ? 'is-active' : ''} key={item.id} onClick={() => setSelectedId(item.id)}><span className={`review-state review-state--${statusTone(item.status)}`}><ShieldCheck size={14} /></span><div><strong>{item.title}</strong><small>{formatDate(item.ts, locale)} · {item.status || item.kind}</small></div></button>) : <EmptyState icon={ShieldCheck} title={text('暂无过程审稿', 'No process reviews yet')} />}</div></aside>
          <main className="ros-card process-report"><header><div><span>ROUND VERDICT</span><h2>{selected?.title || text('选择一轮 Reviewer 反馈', 'Select reviewer feedback')}</h2></div>{selected ? <Badge tone={statusTone(selected.status)}>{selected.status}</Badge> : null}</header>{selected ? <article><div className="process-report__meta"><span>Round {selected.round_index ?? '—'}</span><time>{formatDate(selected.ts, locale)}</time></div><Markdown>{selected.detail || text('该轮没有留下可展示报告。', 'This round has no displayable report.')}</Markdown></article> : <EmptyState icon={FileSearch2} title={text('选择左侧过程审稿', 'Select a process review')} />}</main>
          <aside className="ros-card review-live"><header><div><span>LIVE REVIEW EVENTS</span><h2>{text('Reviewer 实时轨迹', 'Live reviewer activity')}</h2></div><Badge tone={props.connected ? 'live' : 'warn'} dot>{props.connected ? 'Live' : 'Polling'}</Badge></header><EventTimeline events={reviewEvents} limit={24} dense /></aside>
          <section className="process-verdict-card"><span className={`process-verdict-card__icon process-verdict-card__icon--${statusTone(verdict?.status)}`}>{statusTone(verdict?.status) === 'success' ? <CheckCircle2 size={21} /> : <AlertTriangle size={21} />}</span><div><span>{text('当前过程 Verdict', 'Current process verdict')}</span><strong>{verdict?.status || 'Awaiting review'}</strong><p>{verdict?.reason || text('Reviewer 完成下一轮后会写入判断和行动要求。', 'The Reviewer will record a decision and required actions after the next round.')}</p></div></section>
        </div>
        </>
      ) : (
        <>
        <ReviewFlow mode="final" reviewerActive={finalBusy} hasReport={Boolean(finalSelected || finalReceipt)} />
        <div className="final-review-layout">
          <aside className="ros-card final-review-files"><header><div><span>FINAL REPORTS</span><h2>{text('最终审稿报告', 'Final review reports')}</h2></div></header><div>{finalReports.length ? finalReports.map((file) => <button type="button" className={finalSelected?.path === file.path ? 'is-active' : ''} key={file.path} onClick={() => setFinalPath(file.path)}><FileSearch2 size={14} /><div><strong>{file.name}</strong><small>{formatDate(file.mtime, locale)}</small></div></button>) : <EmptyState icon={FileSearch2} title={text('还没有最终审稿报告', 'No final review report yet')} description={text('完成论文后可从右侧发起。', 'Start one from the form after the paper is complete.')} />}</div></aside>
          <main className="ros-card final-review-report"><header><div><span>INDEPENDENT REVIEW</span><h2>{finalSelected?.name || text('投稿前最终审稿', 'Pre-submission final review')}</h2></div>{finalSelected ? <Badge tone="success">Saved report</Badge> : null}</header>{finalFile.data ? <article><Markdown>{finalFile.data.content}</Markdown></article> : finalReceipt ? <article className="final-review-receipt"><Badge tone="success">Queued</Badge><p>{finalReceipt}</p><small>{text('Argus 将生成结构化最终审稿报告；可在过程事件和任务路线查看执行状态。', 'Argus will generate a structured final review report; execution remains visible in events and the task route.')}</small></article> : <EmptyState icon={Scale} title={text('项目完成后再发起最终审稿', 'Start final review after project completion')} description={text('最终 Reviewer 会读取完整稿件、实验、文献与过程审稿记录。', 'The final Reviewer reads the full manuscript, experiments, literature, and process-review history.')} />}</main>
          <aside className="ros-card final-review-form">
            <header><div><span>NEW FINAL REVIEW</span><h2>{text('发起独立最终审稿', 'Start independent final review')}</h2></div></header>
            <div>
              <label className="field"><span>{text('选择最终稿', 'Select final manuscript')}</span><select value={manuscriptPath} onChange={(event) => setManuscriptPath(event.target.value)}><option value="">{text('自动选择最新稿件', 'Automatically select latest')}</option>{manuscriptFiles.map((file) => <option key={file.path} value={file.path}>{file.path}</option>)}</select></label>
              <div className="review-form-row"><label className="field"><span>{text('Venue 类型', 'Venue type')}</span><select value={venueType} onChange={(event) => setVenueType(event.target.value as typeof venueType)}><option value="conference">Conference</option><option value="journal">Journal</option><option value="workshop">Workshop</option></select></label><label className="field"><span>{text('审稿严格度', 'Review strictness')}</span><select value={strictness} onChange={(event) => setStrictness(event.target.value as typeof strictness)}><option value="preflight">{text('快速预检', 'Quick preflight')}</option><option value="standard">{text('标准审稿', 'Standard review')}</option><option value="strict">{text('严格模拟审稿', 'Strict simulated review')}</option><option value="red-team">Red Team / Desk Reject</option></select></label></div>
              <label className="field"><span>{text('目标会议 / 期刊', 'Target venue')}</span><select value={venue} onChange={(event) => setVenue(event.target.value)}>{VENUES.map((item) => <option key={item} value={item}>{item === '__custom__' ? text('其他 / 自定义…', 'Other / custom…') : item}</option>)}</select></label>
              {venue === '__custom__' ? <label className="field"><span>{text('自定义 Venue 名称', 'Custom venue name')}</span><input value={customVenue} onChange={(event) => setCustomVenue(event.target.value)} placeholder={text('例如：Nature Machine Intelligence / CHI Workshop', 'Example: Nature Machine Intelligence / CHI Workshop')} /></label> : null}
              <fieldset className="review-emphasis"><legend>{text('重点审查维度', 'Review emphasis')}</legend>{REVIEW_DIMENSIONS.map((item) => <label key={item}><input type="checkbox" checked={dimensions.includes(item)} onChange={() => setDimensions((current) => current.includes(item) ? current.filter((value) => value !== item) : [...current, item])} /><span>{item}</span></label>)}</fieldset>
              <label className="field"><span>{text('特别强调', 'Special emphasis')}</span><textarea rows={5} value={scope} onChange={(event) => setScope(event.target.value)} placeholder={text('写明你最希望 Reviewer 严格检查的问题…', 'Describe what the Reviewer should scrutinize most…')} /></label>
              <div className="final-review-warning"><AlertTriangle size={15} /><p>{text('这是完成阶段的独立 Reviewer，不替代正式同行评审，也不会自动投稿。', 'This independent completion-stage Reviewer does not replace peer review and never submits automatically.')}</p></div>
              <button className="button button--primary button--full" type="button" disabled={finalBusy || !scope.trim() || (venue === '__custom__' && !customVenue.trim())} onClick={() => void launch()}>{finalBusy ? <Sparkles size={14} /> : <Send size={14} />}{finalBusy ? text('正在创建审稿任务', 'Creating review task') : text('开始最终审稿', 'Start final review')}</button>
              {finalError ? <div className="inline-error">{finalError}</div> : null}
            </div>
          </aside>
        </div>
        </>
      )}
    </div>
  );
}
