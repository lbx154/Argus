import { createContext, useContext, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, FilePen, Flag, Loader2, MessageSquareText, Search, SquareTerminal } from 'lucide-react';
import { useI18n } from '../i18n';
import { roleLabel } from '../lib/enumLabels';
import type { LiveLine } from '../lib/liveActivity';
import './liveWork.css';

const ICONS = { command: SquareTerminal, tool: Search, edit: FilePen, message: MessageSquareText, milestone: Flag };

function LineRow({ line, zh, roleName }: { line: LiveLine; zh: boolean; roleName: (role: string) => string }) {
  const Icon = line.running ? Loader2 : ICONS[line.kind];
  const time = new Date(line.ts * 1000);
  return <li className="live-work-line" data-kind={line.kind} data-failed={line.failed || undefined} data-running={line.running || undefined}>
    <Icon size={13} aria-hidden="true" className={line.running ? 'animate-spin' : undefined} />
    <span className="live-work-line-text">
      {line.kind === 'message' ? <b>{roleName(line.role)}：</b> : null}
      {line.text}
      {line.failed ? <em>{zh ? ' · 失败' : ' · failed'}</em> : null}
    </span>
    {Number.isFinite(time.getTime()) && line.ts > 0
      ? <time dateTime={time.toISOString()}>{time.toLocaleTimeString(zh ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit', hour12: false })}</time>
      : null}
  </li>;
}

/** A finished step's actions, looked up by its task id (empty when the page never saw them). */
export const StepProcessContext = createContext<((taskId: string) => LiveLine[]) | null>(null);

/** "查看过程" under a step's report: what the team did for it, folded away. */
export function StepProcessDisclosure({ taskId }: { taskId: string }) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const lookup = useContext(StepProcessContext);
  const [open, setOpen] = useState(false);
  const lines = useMemo(() => (lookup && taskId ? lookup(taskId) : []), [lookup, taskId]);
  if (!lines.length) return null;
  const roleName = (role: string) => roleLabel(role, t);
  return <div className="live-work-process" data-open={open}>
    <button type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      {open ? <ChevronUp size={13} aria-hidden="true" /> : <ChevronDown size={13} aria-hidden="true" />}
      {zh ? `${open ? '收起' : '查看'}过程（${lines.length} 个动作）` : `${open ? 'Hide' : 'Show'} the process (${lines.length} action${lines.length === 1 ? '' : 's'})`}
    </button>
    {open ? <ol className="live-work-lines">
      {lines.map((line) => <LineRow key={line.key} line={{ ...line, running: false }} zh={zh} roleName={roleName} />)}
    </ol> : null}
  </div>;
}
