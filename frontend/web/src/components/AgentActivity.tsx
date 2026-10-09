import { useEffect, useState, type CSSProperties, type SyntheticEvent } from 'react';
import { Activity, Check, ChevronDown, Clock3, FileText, Pause, Terminal, X } from 'lucide-react';
import type { EventMsg, MissionRoleWorkItem, MissionView, Role } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { MarkdownContent } from './MarkdownContent';
import { agentRoleColor, agentRoleName, isAgentRole } from '../lib/agentRoles';
import { currentWorkStartedAt } from '../lib/workStatus';
import { AGENT_ROLES, activityTitle, agentIsActive, agentWork, latestAgentTool, liveStaleness, cleanActivityText } from './agentActivityModel';
import { api } from '../api';
import { CopyButton } from './CopyButton';
import './agentActivity.css';

export function WorkRecordDetail({ sid, record, expanded = false, zh }: {
  sid?: string; record: MissionRoleWorkItem; expanded?: boolean; zh: boolean;
}) {
  const [open, setOpen] = useState(expanded);
  const [full, setFull] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!open || !sid) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    void api.workRecord(sid, record.role, record.id, controller.signal).then(result => {
      if (!controller.signal.aborted) setFull(result.detail);
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason));
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [open, sid, record.id, record.role, record.detail]);
  const detail = full.length >= record.detail.length ? full : record.detail;
  return <details open={open} onToggle={(event: SyntheticEvent<HTMLDetailsElement>) => setOpen(event.currentTarget.open)}>
    <summary><span>{open ? (zh ? '收起详情' : 'Hide details') : (zh ? '展开完整记录' : 'Read full record')}</span><ChevronDown size={12} /></summary>
    {<>
      {loading && <small role="status">{zh ? '正在读取完整记录…' : 'Loading the full record…'}</small>}
      {error && <small role="status">{zh ? '暂未读取到完整记录，已显示保存的摘要。可收起后重试。' : 'Full record unavailable; the saved preview is shown. Reopen to retry.'}</small>}
      <div className="agent-record-actions"><CopyButton text={detail} label={zh ? '复制完整记录' : 'Copy full record'} copiedLabel={zh ? '已复制' : 'Copied'} /></div>
      <div className="agent-record-detail"><MarkdownContent>{detail}</MarkdownContent></div>
      {record.round_index != null && <small>{zh ? `第 ${record.round_index} 轮` : `Round ${record.round_index}`}</small>}
    </>}
  </details>;
}

export function AgentActivity({ sid, view, roles = [], events = [], taskId, paused = false, selectedRole,
  onSelectRole, onClose, showTabs = true }: {
  sid?: string; view?: MissionView | null; roles?: Role[]; events?: EventMsg[]; taskId?: string; paused?: boolean;
  selectedRole?: string; onSelectRole?: (role: string) => void; onClose?: () => void; showTabs?: boolean;
}) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const [choice, setChoice] = useState<string | null>(null);
  const [visibleCount, setVisibleCount] = useState(24);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState('');
  const [now, setNow] = useState(Date.now);
  const role = selectedRole || choice || roles.find((r) => r.active)?.role || view?.active_role || 'manager';
  const active = agentIsActive(view, roles, role, paused, taskId);
  const records = agentWork(view, role, taskId).map(record => {
    let detail = record.detail;
    for (const event of events) {
      if (event.kind === 'reasoning') continue;
      const id = String(event.event_id || event.id || '');
      const messageId = String(event.message_id || '');
      if (id !== record.id && (!messageId || record.id !== `${record.role}:${messageId}`)) continue;
      const text = cleanActivityText(String(event.text || event.reason || event.summary || ''));
      if (text.length > detail.length) detail = text;
    }
    return detail === record.detail ? record : { ...record, detail };
  });
  useEffect(() => { setVisibleCount(24); setExportError(''); }, [sid, role, taskId]);
  // The work log keeps every attempt. Only the selected current task's card
  // uses its attempt boundary; an unfiltered role can span multiple tasks.
  const started = taskId ? currentWorkStartedAt(undefined, view, taskId) : 0;
  const currentRecords = started ? agentWork(view, role, taskId, started) : records;
  const last = currentRecords[0];
  const update = currentRecords.find((row) => row.detail && ['agent_message', 'assistant_message', 'decision', 'verdict', 'handoff', 'review', 'completion'].includes(row.kind));
  const tool = latestAgentTool(events, role, taskId, started);
  const toolIsLatest = tool && Number(tool.ts || 0) >= (last?.ts ?? 0);
  const currentTitle = !toolIsLatest && last && ['agent_message', 'assistant_message'].includes(last.kind) && last.detail
    ? last.detail.split(/[。\n]/)[0].slice(0, 70)
    : activityTitle(toolIsLatest ? String(tool.kind) : last?.kind || 'task', zh, toolIsLatest ? String(tool.tool_name || '') : '');
  const model = roles.find((r) => r.role === role)?.model || view?.roles.find((r) => r.role === role)?.model;
  const seconds = last ? Math.max(0, Math.floor(now / 1000 - last.ts)) : 0;
  const newest = Math.max(last?.ts ?? 0, toolIsLatest ? Number(tool?.ts || 0) : 0);
  const stale = active && newest ? liveStaleness(now / 1000 - newest, zh) : '';
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [active]);
  const name = (value: string) => isAgentRole(value) ? agentRoleName(value, t) : value;
  return <section className="agent-activity" style={{ '--agent-role-color': agentRoleColor(role) } as CSSProperties} aria-label={zh ? '工作详情' : 'Work details'}>
    <header className="agent-activity-heading"><span><Activity size={15} />{zh ? '工作详情' : 'Work details'}</span>
      {onClose && <button type="button" onClick={onClose} aria-label={zh ? '关闭工作详情' : 'Close work details'}><X size={17} /></button>}
    </header>
    {showTabs && <div className="agent-activity-tabs" role="group" aria-label={zh ? '筛选 Agent' : 'Filter agents'}>
      {AGENT_ROLES.map((value) => <button type="button" key={value} data-role={value} aria-pressed={role === value} style={{ '--agent-role-color': agentRoleColor(value) } as CSSProperties}
        onClick={() => { setChoice(value); onSelectRole?.(value); }}>
        <i data-active={agentIsActive(view, roles, value, paused, taskId)} />{name(value)}
      </button>)}
    </div>}
    <div className="agent-current" data-active={active}>
      <div className="agent-current-kicker"><span>{active ? (zh ? '当前工作' : 'Current work') : paused ? (zh ? '工作已暂停' : 'Work paused') : (zh ? '最近进度' : 'Latest progress')}</span>
        {active ? <span className="agent-live-indicator" data-stale={stale ? true : undefined}><i />{zh ? '处理中' : 'In progress'}{stale && <small className="agent-live-stale">{stale}</small>}</span> : <Pause size={12} />}
      </div>
      <h3>{last || toolIsLatest ? currentTitle : active
        ? (zh ? '尚无本次工作记录' : 'No work recorded for this attempt yet')
        : (zh ? '等待任务分配' : 'Waiting for an assignment')}</h3>
      {update?.detail && <div className="agent-current-summary"><MarkdownContent>{update.detail}</MarkdownContent></div>}
      {!update && last?.detail && <p className="agent-current-summary">{last.detail}</p>}
      {last && <div className="agent-current-meta"><Clock3 size={12} /><span>{zh ? `${seconds < 60 ? seconds + ' 秒' : seconds < 3600 ? Math.floor(seconds / 60) + ' 分钟' : seconds < 86400 ? Math.floor(seconds / 3600) + ' 小时' : Math.floor(seconds / 86400) + ' 天'}前更新` : `Updated ${seconds < 60 ? seconds + 's' : seconds < 3600 ? Math.floor(seconds / 60) + 'm' : seconds < 86400 ? Math.floor(seconds / 3600) + 'h' : Math.floor(seconds / 86400) + 'd'} ago`}</span></div>}
      <details className="agent-technical-details"><summary>{zh ? '查看运行信息' : 'Runtime details'}</summary>
        <p>{name(role)}{model ? ` · ${model}` : ''}{last?.round_index != null ? (zh ? ` · 第 ${last.round_index} 轮` : ` · Round ${last.round_index}`) : ''}</p>
      </details>
    </div>
    <div className="agent-records-heading"><span>{zh ? '工作记录' : 'Work log'}</span><span>{records.length} {zh ? '条' : 'records'}</span></div>
    <div className="agent-records" role="log" aria-live="off">
      {records.slice(0, visibleCount).map((record, index) => {
        const running = active && record.id === last?.id;
        const done = ['done', 'completed'].includes(record.status);
        const failed = ['failed', 'error', 'rejected'].includes(record.status);
        const Icon = done ? Check : ['tool_use', 'command_execution'].includes(record.kind) ? Terminal : FileText;
        return <article className="agent-record" key={record.id} data-active={running} data-failed={failed}>
          <span className="agent-record-icon"><Icon size={13} /></span>
          <div><div className="agent-record-title"><strong>{activityTitle(record.kind, zh)}</strong><time>{new Date(record.ts * 1000).toLocaleTimeString(zh ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}</time></div>
            <small>{running ? (zh ? '进行中' : 'In progress') : failed ? (zh ? '未完成' : 'Did not finish') : done ? (zh ? '已完成' : 'Completed') : (zh ? '已记录' : 'Recorded')}</small>
            {(record.detail || sid) && <WorkRecordDetail sid={sid} record={record} zh={zh} expanded={index === 0 || record.id === update?.id} />}
          </div>
        </article>;
      })}
      {!records.length && <p className="agent-records-empty">{zh ? `${name(role)}尚未留下这个任务的工作记录。` : `No work has been recorded for this task by ${name(role)}.`}</p>}
    </div>
    <div className="agent-records-footer">
      {visibleCount < records.length && <button type="button" onClick={() => setVisibleCount(value => value + 24)}>{zh ? `加载更早记录（还剩 ${records.length - visibleCount} 条）` : `Load earlier records (${records.length - visibleCount} remaining)`}</button>}
      {sid && <button type="button" disabled={exporting} onClick={() => {
        setExporting(true); setExportError('');
        void api.downloadWorkLog(sid, role, taskId).catch(reason => setExportError(reason instanceof Error ? reason.message : String(reason))).finally(() => setExporting(false));
      }}>{exporting ? (zh ? '正在准备完整记录…' : 'Preparing full log…') : (zh ? '下载完整工作记录（含更早历史）' : 'Download full work log (including earlier history)')}</button>}
      {exportError && <small role="alert">{exportError}</small>}
    </div>
  </section>;
}
