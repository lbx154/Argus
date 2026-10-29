import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, FilePen, Flag, Loader2, MessageSquareText, Search, SquareTerminal } from 'lucide-react';
import type { EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { roleLabel } from '../lib/enumLabels';
import { clockLabel, liveActivity, type LiveLine } from '../lib/liveActivity';
import { progressAgeLabel } from '../lib/workStatus';
import './liveWork.css';

const EXPANDED_KEY = 'argus.liveWork.expanded.v1';
const COLLAPSED_LINES = 3;

function readExpanded(): boolean {
  try { return globalThis.localStorage?.getItem(EXPANDED_KEY) === '1'; } catch { return false; }
}

function writeExpanded(value: boolean) {
  try { globalThis.localStorage?.setItem(EXPANDED_KEY, value ? '1' : '0'); } catch { /* per-browser convenience only */ }
}

const ICONS = { command: SquareTerminal, tool: Search, edit: FilePen, message: MessageSquareText, milestone: Flag };

/** Ticks once a second without recomputing the activity above it. */
function Ticking({ render }: { render: (now: number) => string }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, []);
  return <>{render(now)}</>;
}

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

/**
 * The project's work as it happens, pinned above the composer while it runs:
 * the current step, who is on it, its latest actions, and plainly why it is
 * not moving when that is the case.
 */
export function LiveWorkCard({ snapshot, view, events }: {
  snapshot: Snapshot;
  view: MissionView | null | undefined;
  events: EventMsg[];
}) {
  const { locale, t } = useI18n();
  const zh = locale === 'zh-CN';
  const [expanded, setExpanded] = useState(readExpanded);
  // Activity is recomputed every few seconds; the clocks tick on their own.
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!snapshot.daemon.alive) return;
    const timer = setInterval(() => setNow(Date.now() / 1000), 5_000);
    return () => clearInterval(timer);
  }, [snapshot.daemon.alive]);
  const activity = useMemo(() => liveActivity(snapshot, view, events, now, locale),
    [snapshot, view, events, now, locale]);
  if (!activity.show) return null;
  const roleName = (role: string) => roleLabel(role, t);
  const visible = expanded ? activity.lines : activity.lines.slice(-COLLAPSED_LINES);
  const toggle = () => { setExpanded((value) => { writeExpanded(!value); return !value; }); };
  return <section className="live-work" data-tone={activity.tone} data-expanded={expanded} data-testid="live-work"
    aria-label={zh ? '正在进行的工作' : 'Work in progress'}>
    <header className="live-work-head">
      <span className="live-work-pulse" aria-hidden="true"><span /></span>
      <div className="live-work-title">
        <div className="live-work-headline" role="status">{activity.headline}</div>
        <div className="live-work-meta">
          {activity.role ? <span>{roleName(activity.role)}</span> : null}
          {activity.position ? <span>{zh ? `第 ${activity.position.index}/${activity.position.total} 项` : `Task ${activity.position.index} of ${activity.position.total}`}</span> : null}
          {activity.startedAt ? <span><Ticking render={(clock) => `${zh ? '已进行' : 'Running'} ${clockLabel(clock - activity.startedAt!)}`} /></span> : null}
          {activity.lastAt ? <span><Ticking render={(clock) => {
            const age = Math.max(0, clock - activity.lastAt!);
            return age < 60
              ? zh ? `最近动作 ${Math.floor(age)} 秒前` : `Last action ${Math.floor(age)}s ago`
              : zh ? `最近动作 ${progressAgeLabel(age, locale)}` : `Last action ${progressAgeLabel(age, locale)}`;
          }} /></span> : null}
        </div>
      </div>
      {activity.lines.length > COLLAPSED_LINES || expanded ? <button type="button" className="live-work-toggle" onClick={toggle} aria-expanded={expanded}>
        {expanded ? <><ChevronUp size={13} aria-hidden="true" />{zh ? '收起' : 'Less'}</> : <><ChevronDown size={13} aria-hidden="true" />{zh ? `展开过程（${activity.lines.length}）` : `Show all (${activity.lines.length})`}</>}
      </button> : null}
    </header>
    {activity.note ? <p className="live-work-note">{activity.note}</p> : null}
    {visible.length ? <ol className="live-work-lines">
      {visible.map((line) => <LineRow key={line.key} line={line} zh={zh} roleName={roleName} />)}
    </ol> : <p className="live-work-empty">{zh ? '已开始，第一批动作出现后会显示在这里。' : 'Started. The first actions will appear here.'}</p>}
  </section>;
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
