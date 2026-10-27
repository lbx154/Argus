import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../api';
import { Wordmark } from './Wordmark';
import { useI18n } from '../i18n';
import { useWorkbenchTheme } from '../useWorkbenchTheme';
import { errorText } from '../lib/format';
import type { WorkflowAction, WorkflowCreate, WorkflowSnapshot } from '../../../core/src/applicationWorkflows';
import { workflowActorLabel } from '../../../core/src/applicationWorkflows';

export default function WorkflowWorkbench() {
  const { locale, setLocale } = useI18n();
  const text = (zh: string, en: string) => locale === 'zh-CN' ? zh : en;
  const { cycleTheme } = useWorkbenchTheme();
  const cache = useQueryClient();
  const [selected, setSelected] = useState(new URLSearchParams(window.location.search).get('project') || '');
  const [draft, setDraft] = useState<WorkflowCreate>({
    project: '', objective: '', domain: '', profile: '', mode: 'design', timeout_s: 600, max_rounds: 3,
  });
  const [message, setMessage] = useState('');
  const [reason, setReason] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [documentPath, setDocumentPath] = useState('');
  const [document, setDocument] = useState<{ path: string; text: string; truncated: boolean } | null>(null);
  const catalog = useQuery({ queryKey: ['workflow-catalog'], queryFn: api.workflows.catalog });
  const snapshot = useQuery({
    queryKey: ['workflow', selected], queryFn: () => api.workflows.snapshot(selected),
    enabled: Boolean(selected), refetchInterval: 2000,
  });
  const work = snapshot.data;
  const activeJobs = work?.jobs.filter(job => ['starting', 'running'].includes(job.status)) ?? [];
  const executionActive = activeJobs.some(job => job.action !== 'chat')
    || Boolean(work?.calls.some(call => call.operation !== 'chat' && call.live));
  const chatting = activeJobs.some(job => job.action === 'chat');
  const button = 'rounded-lg border border-line px-3 py-2 text-sm disabled:opacity-40 hover:bg-panel';
  const field = 'w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink';

  function selectProject(id: string) {
    setSelected(id);
    setDocument(null);
    const url = new URL(window.location.href);
    url.searchParams.set('project', id);
    window.history.replaceState(null, '', url);
  }

  async function perform(task: () => Promise<WorkflowSnapshot>, select = false) {
    setBusy(true);
    setError('');
    try {
      const result = await task();
      if (select) selectProject(result.id);
      await cache.invalidateQueries({ queryKey: ['workflow', result.id] });
      await cache.invalidateQueries({ queryKey: ['workflow-catalog'] });
      return true;
    } catch (failure) {
      setError(errorText(failure));
      return false;
    } finally {
      setBusy(false);
    }
  }

  const action = (body: WorkflowAction) => perform(() => api.workflows.action(selected, body));
  const openDocument = async (path: string) => {
    setError('');
    try { setDocument(await api.workflows.document(selected, path)); }
    catch (failure) { setError(errorText(failure)); }
  };
  const legacy = () => {
    const url = new URL(window.location.href);
    url.searchParams.set('legacy', '1');
    url.searchParams.delete('project');
    window.location.assign(url);
  };

  return <main className="min-h-screen bg-surface text-ink">
    <header className="flex flex-wrap items-center justify-between gap-4 border-b border-line px-6 py-4">
      <Wordmark size={36} tag={text('工程工作台', 'Engineering workbench')} />
      <div className="flex gap-2">
        <button className={button} onClick={() => setLocale(locale === 'zh-CN' ? 'en' : 'zh-CN')}>EN / 中文</button>
        <button className={button} onClick={cycleTheme}>{text('切换主题', 'Theme')}</button>
        <button className={button} onClick={legacy}>{text('Argus 兼容会话', 'Legacy Argus sessions')}</button>
      </div>
    </header>
    <div className="mx-auto grid max-w-[1600px] gap-6 p-6 lg:grid-cols-[280px_minmax(0,1fr)]">
      <aside className="space-y-5">
        <h2 className="font-semibold">{text('工程工单', 'Work orders')}</h2>
        {catalog.data?.projects.map(project => <button key={project.id}
          className={`${button} w-full text-left ${selected === project.id ? 'text-blue' : ''}`}
          onClick={() => selectProject(project.id)}>
          <span className="block font-mono">{project.id}</span>
          <span className="block truncate text-xs text-ink-faint">{project.project}</span>
        </button>)}
        <form className="space-y-3" onSubmit={event => { event.preventDefault(); void perform(() => api.workflows.create(draft), true); }}>
          <h3 className="font-medium">{text('新建或打开', 'Create or open')}</h3>
          <label className="block text-xs">{text('后端主机上的绝对目录', 'Absolute directory on the backend host')}
            <input className={field} value={draft.project} required placeholder="/path/to/design"
              onChange={event => setDraft({ ...draft, project: event.target.value })} />
          </label>
          <button type="button" className={button} disabled={busy || !draft.project.trim()}
            onClick={() => void perform(() => api.workflows.open(draft.project), true)}>
            {text('打开已有 Vulcan 工单', 'Open existing Vulcan work order')}
          </button>
          <label className="block text-xs">{text('原始工程目标', 'Original objective')}
            <textarea className={field} rows={4} required value={draft.objective}
              onChange={event => setDraft({ ...draft, objective: event.target.value })} />
          </label>
          <select aria-label={text('领域', 'Domain')} className={field} required value={draft.domain}
            onChange={event => setDraft({ ...draft, domain: event.target.value, profile: '' })}>
            <option value="">{text('选择硬件领域', 'Choose hardware domain')}</option>
            {Object.keys(catalog.data?.domains ?? {}).map(domain => <option key={domain}>{domain}</option>)}
          </select>
          <select aria-label={text('配置', 'Profile')} className={field} required value={draft.profile}
            onChange={event => setDraft({ ...draft, profile: event.target.value })}>
            <option value="">{text('选择配置', 'Choose profile')}</option>
            {(catalog.data?.domains[draft.domain] ?? []).map(profile => <option key={profile}>{profile}</option>)}
          </select>
          <select aria-label={text('模式', 'Mode')} className={field} value={draft.mode}
            onChange={event => setDraft({ ...draft, mode: event.target.value === 'verify' ? 'verify' : 'design' })}>
            <option value="design">{text('设计与验证', 'Design and verification')}</option>
            <option value="verify">{text('仅验证已有设计', 'Verify existing design')}</option>
          </select>
          <label className="block text-xs">{text('每次角色调用上限（秒）', 'Per-role time limit (seconds)')}
            <input type="number" min={1} className={field} required value={draft.timeout_s}
              onChange={event => setDraft({ ...draft, timeout_s: event.target.valueAsNumber })} />
          </label>
          <label className="block text-xs">{text('最大修复轮数', 'Maximum repair rounds')}
            <input type="number" min={1} max={32} className={field} required value={draft.max_rounds}
              onChange={event => setDraft({ ...draft, max_rounds: event.target.valueAsNumber })} />
          </label>
          <button className={button} disabled={busy}>{text('创建工单', 'Create work order')}</button>
        </form>
      </aside>
      <section className="min-w-0 space-y-5">
        {(error || catalog.error || snapshot.error) && <p role="alert" className="rounded-lg border border-line p-4 text-err">
          {error || errorText(catalog.error || snapshot.error)}
        </p>}
        {!work && <p className="text-ink-faint">{text('打开工单后查看规格、角色状态、实验和独立审查。',
          'Open a work order to inspect its specification, roles, experiments and independent reviews.')}</p>}
        {work && <>
          <div className="rounded-xl border border-line p-5">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <h1 className="text-xl font-semibold">{work.objective}</h1>
              <span className="font-mono text-sm">{work.independently_accepted ? text('独立审查已接受', 'Independently accepted') : work.status}</span>
            </div>
            <p className="mt-2 break-all font-mono text-xs text-ink-faint">{work.project}</p>
            <div className="mt-4 flex flex-wrap gap-2">
              {catalog.data?.roles.map(role => <span key={role} className="rounded-full border border-line px-3 py-1 text-xs">
                {role}{role === 'designer' && work.mode === 'verify' ? text(' · 未启用', ' · not selected')
                  : work.calls.some(call => call.role === role && call.live) ? text(' · 运行中', ' · active') : ''}
              </span>)}
            </div>
            <div className="mt-4 flex flex-wrap gap-2">
              <button className={button} disabled={busy || executionActive
                || ['completed', 'blocked', 'no_progress', 'interrupted', 'stale'].includes(work.status)}
                onClick={() => void action({ action: 'run', allow_model: true })}>{text('开始模型辅助工程', 'Run with models')}</button>
              <button className={button} disabled={busy || !activeJobs.length}
                onClick={() => void action({ action: 'stop' })}>{text('停止工作台请求', 'Stop workbench requests')}</button>
              <button className={button} disabled={busy || executionActive}
                onClick={() => void action({ action: 'recover' })}>{text('结算已中断进程', 'Recover interrupted work')}</button>
            </div>
            {work.feedback && <p className="mt-4 whitespace-pre-wrap">{work.feedback}</p>}
            {['blocked', 'no_progress', 'interrupted', 'stale'].includes(work.status) && <form className="mt-3 flex gap-2"
              onSubmit={event => { event.preventDefault(); void action({ action: 'resume', allow_model: true, reason }); }}>
              <input className={field} value={reason} required placeholder={text('说明诊断或恢复原因', 'Explain the diagnosis or recovery')}
                onChange={event => setReason(event.target.value)} />
              <button className={button} disabled={busy || executionActive}>{text('继续', 'Resume')}</button>
            </form>}
          </div>
          {work.plan && <details open className="rounded-xl border border-line p-5">
            <summary className="font-semibold">{text('路线图与当前规格', 'Roadmap and current specification')}</summary>
            <ol className="my-3 list-inside list-decimal">{work.plan.roadmap.map((item, index) => <li key={index}>{item}</li>)}</ol>
            <pre className="whitespace-pre-wrap text-sm">{work.plan.spec}</pre>
            {work.plan.checks.map(check => <p key={check.id} className="mt-3 break-all font-mono text-xs">{check.id}: {check.argv?.join(' ') ?? check.provider_stage}</p>)}
          </details>}
          <div className="rounded-xl border border-line p-5">
            <h2 className="font-semibold">Coordinator</h2>
            <p className="mt-1 text-xs text-ink-faint">{text('对话不派工，工程运行时仍可询问进度。', 'Dialogue does not dispatch work and remains available during execution.')}</p>
            {work.messages.map((turn, index) => <div key={index} className="my-4 space-y-2 whitespace-pre-wrap text-sm">
              <p className="font-semibold">{turn.user}</p><p>{turn.reply}</p>
            </div>)}
            <form className="mt-3 flex gap-2" onSubmit={async event => {
              event.preventDefault();
              if (await action({ action: 'chat', allow_model: true, message })) setMessage('');
            }}>
              <textarea className={field} rows={2} required value={message}
                aria-label={text('询问 Coordinator', 'Ask Coordinator')} onChange={event => setMessage(event.target.value)} />
              <button className={button} disabled={busy || chatting}>{text('发送', 'Send')}</button>
            </form>
          </div>
          <div className="rounded-xl border border-line p-5">
            <h2 className="font-semibold">{text('实际执行与审查记录', 'Execution and review records')}</h2>
            <p className="my-2 text-xs text-ink-faint">{text('时长为墙钟时间，含等待。', 'Durations are wall-clock time, including waits.')}</p>
            {work.calls.map(call => <div key={call.folder} className="border-t border-line py-3 text-sm">
              <p>{workflowActorLabel(call.role, text('工具执行', 'Tool execution'))} · {call.operation} · {call.status}
                {call.decision ? ` · ${text('审查决定', 'review decision')}: ${call.decision.status}` : ''}
                {call.duration_s !== null ? ` · ${call.duration_s.toFixed(2)}s` : ''}</p>
              {call.detail && <p className="my-1 whitespace-pre-wrap">{call.detail}</p>}
              {call.decision?.reason && <p className="my-1 whitespace-pre-wrap">{call.decision.reason}</p>}
              <button className="mr-3 text-xs text-blue" onClick={() => void openDocument(`${call.folder}/stdout.log`)}>stdout</button>
              <button className="text-xs text-blue" onClick={() => void openDocument(`${call.folder}/stderr.log`)}>stderr</button>
              {call.role !== 'host' && <button className="ml-3 text-xs text-blue"
                onClick={() => void openDocument(`${call.folder}/response.json`)}>{text('角色回复', 'Role response')}</button>}
            </div>)}
            {work.jobs.filter(job => job.detail).map(job => <p key={job.id} className="my-2 text-sm">
              {job.id} · {job.action} · {job.status}: {job.detail}<br /><span className="font-mono text-xs">{job.log}</span>
            </p>)}
            {work.resumes.map((item, index) => <p key={index} className="my-2 text-sm">{text('恢复原因', 'Resume reason')}: {item}</p>)}
            <form className="mt-3 flex gap-2" onSubmit={event => { event.preventDefault(); void openDocument(documentPath); }}>
              <input className={field} value={documentPath} required onChange={event => setDocumentPath(event.target.value)}
                aria-label={text('查看源文件或证据', 'Inspect source or evidence')}
                placeholder={text('工程内相对路径，例如 rtl/counter4.sv', 'Relative project path, e.g. rtl/counter4.sv')} />
              <button className={button}>{text('查看文件', 'Read file')}</button>
            </form>
          </div>
        </>}
        {document && <section className="rounded-xl border border-line p-5">
          <h2 className="break-all font-mono text-sm">{document.path}</h2>
          {document.truncated && <p role="status">{text('仅显示前 128 KiB；完整文件保留在工程目录。', 'Showing the first 128 KiB; the full file remains in the project.')}</p>}
          <pre className="mt-3 max-h-[600px] overflow-auto whitespace-pre-wrap text-xs">{document.text}</pre>
        </section>}
      </section>
    </div>
  </main>;
}
