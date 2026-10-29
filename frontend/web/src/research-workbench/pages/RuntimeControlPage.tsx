import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Check, Folder, Pause, Play, RefreshCw, RotateCcw, Search, Settings2, Trash2 } from 'lucide-react';
import { api } from '../../api';
import { ResourceStatusView } from '../../components/ResourceStatus';
import { RawDisclosure } from '../../components/primitives';
import { useI18n } from '../../i18n';
import { RESOURCES_CAPABILITY, useCapability } from '../../lib/capabilities';
import { isImeComposing } from '../../lib/ime';
import { requestFailureText, routeRefused } from '../../lib/requestFailure';
import { currentWorkStatus, workStatusLabel } from '../../lib/workStatus';
import type { ActiveWorkbenchPageProps } from './pageTypes';
import './runtimeControl.css';

async function requireCommandSuccess(operation: Promise<unknown>) {
  const result = await operation;
  if (result && typeof result === 'object') {
    const row = result as Record<string, unknown>;
    if (Number(row.rc ?? 0) !== 0 || row.command_status === 'failed' || row.command_status === 'rejected') {
      throw new Error(String(row.error || `Command ${row.command_status || 'failed'}`));
    }
  }
  return result;
}

export function RuntimeControlPage({
  sid, snapshot: snap, active, connected, controls, refresh, readOnly, onRestored, events, onCompose,
}: ActiveWorkbenchPageProps & { readOnly: boolean; onRestored: (sid: string) => void | Promise<void>; onCompose: () => void }) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [recoveryOpen, setRecoveryOpen] = useState(false);
  const [workdir, setWorkdir] = useState(snap.session.workdir ?? snap.session.cwd ?? '');
  const [trashDraft, setTrashDraft] = useState('');
  const [trashQuery, setTrashQuery] = useState('');
  const [busy, setBusy] = useState('');
  const busyRef = useRef(false);
  const [output, setOutput] = useState('');
  const [failure, setFailure] = useState<ReturnType<typeof requestFailureText> | null>(null);
  const hasResources = useCapability(RESOURCES_CAPABILITY);
  const resources = useQuery({
    queryKey: ['runtime-resources'], queryFn: ({ signal }) => api.resources(signal),
    enabled: active && hasResources, refetchInterval: 10_000, retry: false,
  });
  const trash = useQuery({
    queryKey: ['runtime-trash', trashQuery], queryFn: () => api.trash(trashQuery),
    enabled: active && recoveryOpen && !readOnly, retry: false,
  });
  useEffect(() => {
    setWorkdir(snap.session.workdir ?? snap.session.cwd ?? '');
  }, [snap.session.workdir, snap.session.cwd]);

  const disabled = readOnly || !!busy || controls.busy;
  const external = snap.daemon.alive && snap.daemon.control_available === false;
  const work = currentWorkStatus(snap, snap.mission_view, events);
  const empty = work.state === 'idle' && !snap.backlog.length && !snap.mission_view?.mission.id && !snap.session.objective;
  const foreground = !snap.daemon.alive && ['running', 'waiting'].includes(work.state);
  const run = async (key: string, operation: () => Promise<unknown>, message: string) => {
    if (readOnly || busyRef.current || controls.busy) return;
    busyRef.current = true;
    setBusy(key); setFailure(null); setOutput('');
    try {
      await requireCommandSuccess(operation());
      setOutput(message);
      await refresh();
    } catch (error) {
      setFailure(requestFailureText(error, t));
    } finally {
      busyRef.current = false;
      setBusy('');
    }
  };
  const confirmRun = (key: string, question: string, operation: () => Promise<unknown>, message: string) => {
    if (!disabled && window.confirm(question)) void run(key, operation, message);
  };
  const requestError = (error: unknown) => error
    ? routeRefused(error) ? t('operations.unavailableHere') : requestFailureText(error, t).text
    : '';

  return <div className="runtime-control-page">
    <section className="runtime-summary">
      <div className="runtime-summary-copy">
        <span className="runtime-eyebrow">{zh ? '运行控制' : 'Runtime controls'}</span>
        <h2>{snap.session.display_name || t('sidebar.unnamedSession')}</h2>
        <div className="runtime-status-line">
          <span className="runtime-status" data-running={work.state === 'running'}>
            <i aria-hidden="true" />{workStatusLabel(work, locale)}
          </span>
          <span>{connected ? zh ? '实时连接' : 'Live connection' : zh ? '连接恢复中，状态可能延迟' : 'Reconnecting; status may be delayed'}</span>
          {work.foreground && <span>{zh ? '对话处理中' : 'Reply in progress'}</span>}
        </div>
      </div>
      <div className="runtime-main-actions">
        <button className="button button--primary" disabled={disabled || external || foreground || work.state === 'unknown'}
          onClick={() => { if (empty) onCompose(); else if (snap.daemon.alive) void controls.stop(true); else void controls.start(); }}>
          {snap.daemon.alive ? <Pause size={16} /> : <Play size={16} />}
          {controls.busy || foreground ? zh ? '正在处理…' : 'Working…' : empty ? zh ? '描述任务' : 'Describe a task' : snap.daemon.alive ? zh ? '安全暂停' : 'Pause safely' : work.state === 'paused' ? zh ? '继续运行' : 'Resume' : zh ? '启动运行' : 'Start work'}
        </button>
        <button className="button button--ghost" disabled={resources.isFetching} onClick={() => { if (hasResources) void resources.refetch(); void refresh(); }}><RefreshCw size={15} />{zh ? '刷新状态' : 'Refresh'}</button>
      </div>
      <p className="runtime-summary-hint">{empty
        ? zh ? '在对话里描述目标后，这里会显示运行状态。' : 'Describe your goal in the conversation; work status will appear here.'
        : zh ? '安全暂停会等待当前工作结束。新的任务和调整直接在对话中告诉 Argus。' : 'Safe pause waits for current work to finish. Send new tasks and adjustments in the conversation.'}</p>
      {readOnly && <p>{zh ? '当前为只读展示，不能执行控制操作。' : 'Read-only view; control actions are disabled.'}</p>}
      {external && <p role="alert">{zh ? '此运行实例由外部管理，请在其启动端控制。' : 'This instance is externally managed. Control it from its host.'}</p>}
      {controls.error && <p role="alert" className="text-err">{controls.error}</p>}
    </section>

    {hasResources && <ResourceStatusView status={resources.isError ? null : resources.data ?? null} error={requestError(resources.error)} />}

    {failure && <div className="runtime-feedback" role="alert"><p>{failure.text}</p>{failure.technical && <RawDisclosure label={t('operations.technicalDetails')}><pre>{failure.technical}</pre></RawDisclosure>}</div>}
    {output && <p className="runtime-feedback" role="status"><Check size={16} />{output}</p>}
    {!!busy && <p role="status" className="text-sm text-ink-dim">{zh ? '正在处理，请稍候…' : 'Working, please wait…'}</p>}

    <details className="runtime-section" open={settingsOpen} onToggle={event => setSettingsOpen(event.currentTarget.open)}>
      <summary><Settings2 size={17} /><span>{zh ? '运行设置' : 'Runtime settings'}</span><small>{zh ? '目录与维护' : 'Directory & maintenance'}</small></summary>
      <div className="runtime-section-body">
        <label htmlFor={`runtime-workdir-${sid}`}><Folder size={15} />{t('operations.workdir')}</label>
        <div className="runtime-field-row">
          <input id={`runtime-workdir-${sid}`} value={workdir} onChange={event => setWorkdir(event.target.value)} disabled={disabled} />
          <button className="button" disabled={disabled || !workdir.trim() || workdir === (snap.session.workdir ?? snap.session.cwd ?? '')}
            onClick={() => confirmRun('cwd', zh ? '更改后，后续工作将使用新的会话目录。确认更改？' : 'Future work will use the new session directory. Apply?',
              () => api.setWorkdir(sid, workdir), t('operations.workdirUpdated'))}><Check size={15} />{zh ? '应用目录' : 'Apply directory'}</button>
        </div>
        <div className="runtime-field-row">
          <button className="button" disabled={disabled} onClick={() => confirmRun('reset', zh ? '将清空当前对话的上下文，任务和文件会保留。确认重置？' : 'Clear conversation context? Tasks and files are preserved.',
            () => api.resetManager(sid), zh ? '对话上下文已重置。' : 'Conversation context reset.')}><RotateCcw size={15} />{zh ? '重置对话上下文' : 'Reset conversation context'}</button>
        </div>
      </div>
    </details>
    {!readOnly && <details className="runtime-section" open={recoveryOpen} onToggle={event => setRecoveryOpen(event.currentTarget.open)}>
      <summary><Trash2 size={17} /><span>{zh ? '恢复已移除项目' : 'Restore removed projects'}</span></summary>
      <div className="runtime-section-body">
        <div className="runtime-field-row"><input aria-label={t('operations.searchTrash')} placeholder={t('operations.searchTrash')} value={trashDraft}
          onChange={event => setTrashDraft(event.target.value)} onKeyDown={event => { if (!isImeComposing(event) && event.key === 'Enter') { event.preventDefault(); setTrashQuery(trashDraft); } }} />
          <button className="button" onClick={() => setTrashQuery(trashDraft)}><Search size={15} />{zh ? '搜索' : 'Search'}</button></div>
        {trash.error ? <p role="alert">{requestError(trash.error)}</p> : trash.isLoading ? <p>{t('common.loading')}</p> : !trash.data?.entries.length ? <p>{t('operations.trashEmpty')}</p> :
          trash.data.entries.map(entry => <div className="runtime-restore-row" key={entry.trash_id}><span>{entry.label}</span><button className="button" disabled={disabled}
            onClick={() => void run(`restore:${entry.trash_id}`, async () => { const result = await api.restoreTrash(entry.trash_id); await trash.refetch(); await onRestored(result.sid); }, zh ? '项目已恢复。' : 'Project restored.')}><RotateCcw size={15} />{zh ? '恢复' : 'Restore'}</button></div>)}
        {trash.data && trash.data.total > trash.data.entries.length && <p>{zh ? `显示最近 ${trash.data.entries.length} 条，请缩小搜索范围。` : `Showing ${trash.data.entries.length} recent matches. Narrow the search for more.`}</p>}
      </div>
    </details>}
  </div>;
}
