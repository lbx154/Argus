import { useEffect, useState } from 'react';
import { formatBytes } from '../lib/format';
import type { DeliveryReceipt, MissionView, Snapshot } from '../../../core/src/types';
import { useI18n } from '../i18n';
import { deliveryHeadingKey } from '../lib/deliveryKind';
import { diskView, durationLabel, projectOverview, SPEND_ESTIMATE_HINT, spendEstimateLabel } from '../lib/projectOverview';
import { relativizeWorkspacePaths } from '../lib/workspacePaths';

export interface NotificationControl {
  supported: boolean;
  enabled: boolean;
  denied: boolean;
  enable: () => Promise<void>;
  disable: () => void;
}

/** One line a returning operator reads first: goal, progress, time, cost and the newest result. */
export function ProjectOverviewStrip({ snapshot, view, onOpenDelivery, notifications, latestDelivery }: {
  snapshot: Snapshot;
  /** Newest delivered result across the conversation (the mission view only knows the current task). */
  latestDelivery?: DeliveryReceipt | null;
  view?: MissionView | null;
  onOpenDelivery?: (delivery: DeliveryReceipt) => void;
  notifications?: NotificationControl;
}) {
  const { t, locale } = useI18n();
  const zh = locale === 'zh-CN';
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const timer = setInterval(() => setNow(Date.now() / 1000), 60_000); return () => clearInterval(timer); }, []);
  const overview = projectOverview(snapshot, view, now, latestDelivery);
  if (!overview) return null;
  const disk = diskView(snapshot.storage, zh, formatBytes);
  const elapsed = durationLabel(overview.elapsedSeconds, zh);
  const spend = spendEstimateLabel(overview, zh);
  // Free disk space is noise until it runs short.
  const diskWarning = disk?.warn ? disk : null;
  const delivery = overview.latestDelivery;
  return <div className="project-overview shrink-0 border-b border-line/60 bg-panel/70 px-4 py-2 text-xs text-ink-dim" data-testid="project-overview">
    {overview.goal ? <div className="truncate" title={overview.goal}>
      <span className="font-medium text-ink">{overview.goalIsTask ? (zh ? '当前任务：' : 'Current task: ') : (zh ? '目标：' : 'Goal: ')}</span>{relativizeWorkspacePaths(overview.goal, snapshot.session.workdir, snapshot.session.cwd)}
    </div> : null}
    <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1">
      <span>{zh ? `任务 ${overview.done}/${overview.total} 已完成` : `${overview.done}/${overview.total} tasks done`}{overview.active ? (zh ? ` · ${overview.active} 项进行中` : ` · ${overview.active} open`) : ''}</span>
      {elapsed ? <span>{zh ? `已用时 ${elapsed}` : `${elapsed} elapsed`}</span> : null}
      {spend ? <span title={zh ? SPEND_ESTIMATE_HINT.zh : SPEND_ESTIMATE_HINT.en} className="cursor-help">{spend}</span> : null}
      {diskWarning ? <span className="font-medium text-warn" title={diskWarning.warn ? (zh ? '建议清理不需要的中间文件，或让 Argus 只保留可复现所需的数据' : 'Consider removing intermediate files or keeping only what reproduction needs') : undefined}>{diskWarning.text}</span> : null}
      {delivery ? <span className="flex min-w-0 items-center gap-1">
        <span className="shrink-0">{t(deliveryHeadingKey(delivery))}：</span>
        {onOpenDelivery
          ? <button type="button" className="min-w-0 truncate text-blue hover:underline" title={delivery.title} onClick={() => onOpenDelivery(delivery)}>{delivery.title}</button>
          : <span className="min-w-0 truncate" title={delivery.title}>{delivery.title}</span>}
      </span> : null}
      {notifications?.supported ? <button type="button" className="ml-auto shrink-0 text-ink-faint hover:text-ink"
        title={notifications.denied
          ? (zh ? '浏览器已拒绝通知，请在浏览器的网站设置里允许' : 'Notifications are blocked; allow them in the browser’s site settings')
          : (zh ? '需要你回复、任务完成或出错时，在后台标签页也能收到提醒' : 'Get a browser alert when a reply is needed, or work finishes or fails, even in a background tab')}
        disabled={notifications.denied}
        onClick={() => { if (notifications.enabled) notifications.disable(); else void notifications.enable(); }}>
        {notifications.enabled ? (zh ? '🔔 通知已开启' : '🔔 Alerts on') : (zh ? '🔕 开启提醒' : '🔕 Turn on alerts')}
      </button> : null}
    </div>
  </div>;
}
