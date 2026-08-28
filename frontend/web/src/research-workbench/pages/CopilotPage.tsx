import { Activity, ArrowUp, Check, FileCode2, FileUp, ListFilter, Paperclip, ShieldCheck, Sparkles, Square, TerminalSquare, WandSparkles, Wrench, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { api } from '../api';
import { ArgusMark } from '../components/Brand';
import { Badge, EmptyState, Markdown } from '../components/Common';
import { roleLabel, statusLabel } from '../enumLabels';
import type { EventMsg, PromptRewrite, Role } from '../types';
import { eventDetail, eventRole, eventTitle, formatClock } from '../utils';
import { useManagerRun } from '../useManagerRun';
import { useWorkbenchText } from '../useWorkbenchText';
import type { WorkspacePageProps } from './pageTypes';

const SUGGESTIONS = [
  ['总结当前研究进展、最强证据和最大风险', 'Summarize current progress, strongest evidence, and biggest risk'],
  ['Reviewer 最近要求补充什么？', 'What did the Reviewer most recently request?'],
  ['下一步最小且最有信息量的实验是什么？', 'What is the smallest, most informative next experiment?'],
] as const;

function ToolTrace({ events, roles, connected }: { events: EventMsg[]; roles: Role[]; connected: boolean }) {
  const { locale, text } = useWorkbenchText();
  const rows = useMemo(() => events.filter((event) => {
    const type = String(event.type ?? '');
    return !['ui.operator', 'ui.argus'].includes(type) && String(event.kind ?? '') !== 'reasoning' && !type.startsWith('provider.');
  }).slice(-100).reverse(), [events]);
  const [filter, setFilter] = useState<'all' | 'commands' | 'files' | 'review'>('all');
  const [expanded, setExpanded] = useState('');
  const roleNames = ['manager', 'planner', 'engineer', 'reviewer'];
  const visible = rows.filter((event) => {
    const kind = String(event.kind ?? '');
    const detail = eventDetail(event, 800);
    if (filter === 'commands') return kind === 'command_execution';
    if (filter === 'files') return kind === 'file_change' || /^(read|write|edit):/i.test(detail);
    if (filter === 'review') return eventRole(event) === 'reviewer' || /review/i.test(String(event.type ?? ''));
    return true;
  });
  const current = rows[0];
  const iconOf = (event: EventMsg) => String(event.kind ?? '') === 'command_execution' ? TerminalSquare : eventRole(event) === 'reviewer' ? ShieldCheck : /^(read|write|edit):/i.test(eventDetail(event, 120)) ? FileCode2 : Wrench;
  return (
    <div className="copilot-trace copilot-trace-v2">
      <div className="copilot-trace__header"><div><span>ARGUS TEAM</span><strong>{text('谁正在做什么', 'Who is doing what')}</strong></div><Badge tone={connected ? 'live' : 'warn'} dot>{connected ? text('实时', 'Live') : text('轮询中', 'Polling')}</Badge></div>
      <div className="team-pipeline">
        {roleNames.map((name, index) => {
          const role = roles.find((item) => item.role === name);
          const count = rows.filter((event) => eventRole(event) === name).length;
          return <div className={role?.active ? 'is-active' : role?.status === 'done' ? 'is-done' : ''} key={name}><span className={`role-orb role-orb--${name}`} /> <strong>{roleLabel(name, text)}</strong><small>{role?.active ? role.label : statusLabel(role?.status || 'waiting', text)} · {count}</small>{index < roleNames.length - 1 ? <b>↓</b> : null}</div>;
        })}
      </div>
      {current ? <section className="current-operation"><span><Activity size={15} /></span><div><small>{text('当前最新动作', 'Latest action')} · {formatClock(current.ts, locale)}</small><strong>{eventTitle(current)}</strong><code>{eventDetail(current, 180)}</code></div></section> : null}
      <div className="activity-filters"><ListFilter size={14} />{([['all', text('全部', 'All')], ['commands', text('命令', 'Commands')], ['files', text('文件', 'Files')], ['review', text('审稿', 'Review')]] as const).map(([id, label]) => <button type="button" className={filter === id ? 'is-active' : ''} key={id} onClick={() => setFilter(id)}>{label}</button>)}</div>
      <div className="visual-activity-timeline">
        {visible.slice(0, 40).map((event, index) => {
          const Icon = iconOf(event); const key = `${event.ts}-${index}`; const open = expanded === key;
          return <button type="button" className={open ? 'is-open' : ''} key={key} onClick={() => setExpanded(open ? '' : key)}><span className={`activity-icon activity-icon--${eventRole(event)}`}><Icon size={13} /></span><div><div><strong>{eventTitle(event)}</strong><time>{formatClock(event.ts, locale)}</time></div><p>{eventDetail(event, open ? 1_500 : 120) || eventTitle(event)}</p>{open ? <code>{eventDetail(event, 1_500) || eventTitle(event)}</code> : null}</div></button>;
        })}
        {!visible.length ? <p className="activity-empty">{text('当前筛选下没有事件', 'No events match this filter')}</p> : null}
      </div>
    </div>
  );
}

function PromptOptimizer({
  draft,
  busy,
  result,
  error,
  onOptimize,
  onApply,
  onClose,
}: {
  draft: string;
  busy: boolean;
  result: PromptRewrite | null;
  error: string;
  onOptimize: () => void;
  onApply: (value: string) => void;
  onClose: () => void;
}) {
  const { text } = useWorkbenchText();
  const dialogRef = useRef<HTMLElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const frame = requestAnimationFrame(() => dialogRef.current?.querySelector<HTMLElement>('button, textarea, input')?.focus());
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.preventDefault(); closeRef.current(); return; }
      if (event.key !== 'Tab' || !dialogRef.current) return;
      const items = [...dialogRef.current.querySelectorAll<HTMLElement>('button:not(:disabled), textarea:not(:disabled), input:not(:disabled), [tabindex]:not([tabindex="-1"])')];
      if (!items.length) return;
      const first = items[0]; const last = items.at(-1)!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    window.addEventListener('keydown', onKey);
    return () => { cancelAnimationFrame(frame); window.removeEventListener('keydown', onKey); requestAnimationFrame(() => previous?.focus()); };
  }, []);
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section ref={dialogRef} className="prompt-optimizer" role="dialog" aria-modal="true" aria-label={text('优化 Prompt', 'Optimize prompt')}>
        <header><div><span>PROMPT OPTIMIZER</span><h2>{text('发送前优化 Prompt', 'Optimize before sending')}</h2><p>{text('Manager 只重写和补全约束，不会在这一步创建任务。', 'The Manager only rewrites and completes constraints; it does not create a task here.')}</p></div><button className="icon-button" type="button" onClick={onClose} aria-label={text('关闭', 'Close')}><X size={16} /></button></header>
        <div className="prompt-optimizer__compare">
          <div><label>{text('原始 Prompt', 'Original prompt')}</label><pre>{draft}</pre></div>
          <div><label>{text('优化结果', 'Optimized prompt')}</label>{busy ? <div className="optimizer-thinking"><Sparkles size={18} /> {text('Manager 正在理解目标与约束…', 'Manager is interpreting the goal and constraints…')}</div> : result?.rewritten ? <pre>{result.rewritten}</pre> : <EmptyState icon={WandSparkles} title={text('等待优化', 'Ready to optimize')} description={text('点击下方按钮生成可执行但不擅自扩张范围的 Prompt。', 'Generate an actionable prompt without expanding its scope.')} />}</div>
        </div>
        {result?.changes.length ? <div className="optimizer-changes"><strong>{text('主要改动', 'Main changes')}</strong>{result.changes.map((item) => <span key={item}><Check size={12} />{item}</span>)}</div> : null}
        {result?.questions.length ? <div className="optimizer-questions"><strong>{text('发送前仍需确认', 'Confirm before sending')}</strong>{result.questions.map((item) => <p key={item}>· {item}</p>)}</div> : null}
        {error ? <div className="inline-error">{error}</div> : null}
        <footer><button className="button button--secondary" type="button" onClick={onClose}>{text('保留原文', 'Keep original')}</button>{result?.rewritten ? <button className="button button--primary" type="button" onClick={() => onApply(result.rewritten)}>{text('使用优化结果', 'Use optimized prompt')}</button> : <button className="button button--primary" type="button" disabled={busy || !draft.trim()} onClick={onOptimize}><WandSparkles size={14} />{text('开始优化', 'Optimize')}</button>}</footer>
      </section>
    </div>
  );
}

export function CopilotPage(props: WorkspacePageProps) {
  const { locale, text } = useWorkbenchText();
  const [draft, setDraft] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [optimizerOpen, setOptimizerOpen] = useState(false);
  const [optimizing, setOptimizing] = useState(false);
  const [optimization, setOptimization] = useState<PromptRewrite | null>(null);
  const [optimizeError, setOptimizeError] = useState('');
  const fileInput = useRef<HTMLInputElement>(null);
  const messagesRef = useRef<HTMLDivElement>(null);
  const manager = useManagerRun(props.sid, props.refresh);
  const pending = props.snapshot.pending_questions ?? props.status?.pending_questions ?? [];

  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (messagesRef.current) messagesRef.current.scrollTop = messagesRef.current.scrollHeight;
    });
    return () => cancelAnimationFrame(frame);
  }, [manager.output, manager.phase, props.transcript.length]);

  const submit = async (value = draft) => {
    const text = value.trim();
    if (!text || manager.busy) return;
    const acceptedFiles = files;
    setDraft(''); setFiles([]); setOptimization(null);
    await manager.run(text, acceptedFiles);
  };
  const optimize = async () => {
    if (!draft.trim()) return;
    setOptimizing(true); setOptimizeError(''); setOptimization(null);
    try { setOptimization(await api.rewritePrompt(props.sid, draft)); }
    catch (error) { setOptimizeError(error instanceof Error ? error.message : String(error)); }
    finally { setOptimizing(false); }
  };

  return (
    <div className="ros-page copilot-v2">
      <header className="ros-page-header"><div><div className="eyebrow">RESEARCH COPILOT</div><h1>{text('与 Argus 对话', 'Chat with Argus')}</h1><p>{text('普通问题由 Manager 回答；复杂研究目标会进入 Planner → Engineer ⇄ Reviewer。', 'The Manager answers ordinary questions; complex goals enter Planner → Engineer ⇄ Reviewer.')}</p></div><Badge tone={props.connected ? 'live' : 'warn'} dot>{props.connected ? text('实时连接', 'Live') : text('重新连接中', 'Reconnecting')}</Badge></header>
      {pending.length ? <div className="pending-question-banner"><Sparkles size={16} /><div><strong>{text('Argus 正在等待你的确认', 'Argus is waiting for your confirmation')}</strong><p>{String(pending[0]?.question || pending[0]?.title || text('请查看待确认问题', 'Review the pending question'))}</p></div><button className="button button--secondary" type="button" onClick={() => setDraft(text(`关于待确认问题：${String(pending[0]?.question || '')}\n我的回答是：`, `Regarding the pending question: ${String(pending[0]?.question || '')}\nMy answer is: `))}>{text('回复', 'Reply')}</button></div> : null}

      <div className="copilot-v2__layout">
        <main className="copilot-v2__thread">
          <div ref={messagesRef} className="copilot-v2__messages">
            {!props.transcript.length ? <div className="copilot-empty"><ArgusMark size={48} /><h2>{text('从当前项目上下文开始', 'Start from the current project context')}</h2><p>{text('Argus 已经知道任务、代码、Reviewer 反馈和现有证据。', 'Argus already knows the task, code, reviewer feedback, and available evidence.')}</p><div>{SUGGESTIONS.map(([zh, en]) => { const item = text(zh, en); return <button key={item} type="button" onClick={() => setDraft(item)}>{item}</button>; })}</div></div> : null}
            {props.transcript.map((turn, index) => turn.role === 'operator' ? (
              <article className="message message--user" key={`${turn.ts}-${index}`}><div><span>{text('你', 'You')}</span><time>{formatClock(turn.ts, locale)}</time></div><Markdown>{turn.text}</Markdown></article>
            ) : (
              <article className="message message--argus" key={`${turn.ts}-${index}`}><ArgusMark size={28} /><div><header><strong>Argus</strong><time>{formatClock(turn.ts, locale)}</time></header><Markdown>{turn.text}</Markdown></div></article>
            ))}
            {(manager.busy || manager.output || manager.error) ? <article className="message message--argus message--stream"><ArgusMark size={28} /><div><header><strong>Argus</strong>{manager.busy ? <Badge tone="live" dot>{manager.phase || text('思考中', 'Thinking')}</Badge> : manager.result?.kind ? <Badge tone="info">{manager.result.kind}</Badge> : null}</header>{manager.phases.length ? <ol className="phase-trail">{manager.phases.map((item, index) => <li key={`${item.at}-${index}`} className={index === manager.phases.length - 1 && manager.busy ? 'is-active' : ''}><span>{index === manager.phases.length - 1 && manager.busy ? '●' : '✓'}</span>{item.label}</li>)}</ol> : null}{manager.output ? <Markdown>{manager.output}</Markdown> : null}{manager.error ? <div className="inline-error">{manager.error}</div> : null}</div></article> : null}
          </div>

          <div className="copilot-composer-dock">
            {files.length ? <div className="attachment-chips">{files.map((file, index) => <span key={`${file.name}-${index}`}><FileUp size={13} />{file.name}<button type="button" onClick={() => setFiles((rows) => rows.filter((_, rowIndex) => rowIndex !== index))}><X size={12} /></button></span>)}</div> : null}
            <div className="copilot-composer">
              <input ref={fileInput} type="file" multiple hidden onChange={(event) => setFiles(Array.from(event.target.files ?? []))} />
              <button className="composer-tool" type="button" onClick={() => fileInput.current?.click()} aria-label={text('添加附件', 'Attach files')}><Paperclip size={17} /></button>
              <textarea rows={1} value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder={text('询问研究问题，或给 Argus 一个需要执行的目标…', 'Ask a research question or give Argus an executable goal…')} />
              <button className="optimize-button" type="button" disabled={!draft.trim() || manager.busy} onClick={() => { setOptimizerOpen(true); setOptimization(null); }} title="Ctrl/⌘ + R"><WandSparkles size={15} />{text('优化 Prompt', 'Optimize prompt')}</button>
              {manager.busy ? <button className="send-button is-stop" type="button" onClick={manager.cancel} aria-label={text('停止等待', 'Stop waiting')}><Square size={14} /></button> : <button className="send-button" type="button" disabled={!draft.trim()} onClick={() => void submit()} aria-label={text('发送', 'Send')}><ArrowUp size={17} /></button>}
            </div>
            <small>{text('Enter 发送 · Shift + Enter 换行 · 优化 Prompt 不会直接执行', 'Enter to send · Shift + Enter for a new line · Prompt optimization does not execute')}</small>
          </div>
        </main>
        <aside className="copilot-v2__aside"><ToolTrace events={props.events} roles={props.snapshot.roles} connected={props.connected} /></aside>
      </div>

      {optimizerOpen ? <PromptOptimizer draft={draft} busy={optimizing} result={optimization} error={optimizeError} onOptimize={() => void optimize()} onApply={(value) => { setDraft(value); setOptimizerOpen(false); }} onClose={() => setOptimizerOpen(false)} /> : null}
    </div>
  );
}
