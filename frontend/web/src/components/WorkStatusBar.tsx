import { useEffect, useState } from 'react';
import type { EventMsg, MissionView, Snapshot } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { currentWorkStatus, workStatusLabel } from '../lib/workStatus';

/** Shared, factual status for the conversation and its progress panel. */
export function WorkStatusBar({ snapshot, view, events = [], connected, compact = false }: {
  snapshot?: Snapshot; view?: MissionView | null; events?: EventMsg[]; connected: boolean; compact?: boolean;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now() / 1000), 10_000); return () => window.clearInterval(timer); }, []);
  const status = currentWorkStatus(snapshot, view, events, now);
  const seconds = status.activityAgeSeconds;
  const age = seconds === null ? (zh ? '尚无本步进展记录' : 'No progress record for this step yet')
    : seconds < 60 ? (zh ? '刚刚有进展' : 'Progress in the last minute')
      : seconds < 3600 ? (zh ? `${Math.floor(seconds / 60)} 分钟前有进展` : `Progress ${Math.floor(seconds / 60)} min ago`)
        : (zh ? `${Math.floor(seconds / 3600)} 小时前有进展` : `Progress ${Math.floor(seconds / 3600)} hr ago`);
  return <div className="shrink-0 border-b border-line/60 bg-panel px-4 py-2.5 text-xs" data-testid="work-status" data-state={status.state}>
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${!connected ? 'bg-warn' : status.state === 'running' ? 'bg-blue' : 'bg-ink-faint'}`} aria-hidden="true" />
      <span className="font-medium text-ink">{connected ? workStatusLabel(status, locale) : zh ? '实时连接已断开' : 'Live connection lost'}</span>
      <time className="text-ink-faint" dateTime={status.activityAt ? new Date(status.activityAt * 1000).toISOString() : undefined}>{age}</time>
    </div>
    {!connected ? <p className="mt-1 leading-5 text-ink-faint">{zh ? '这里保留的是已收到的记录；连接恢复后继续更新。' : 'These are the records already received. Updates resume when the connection returns.'}</p>
      : !compact && status.title ? <p className="mt-1 line-clamp-2 leading-5 text-ink-dim" title={status.title}>{status.title}</p> : null}
  </div>;
}
