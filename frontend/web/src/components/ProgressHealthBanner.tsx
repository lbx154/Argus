import { useState } from 'react';
import { useI18n } from '../i18n';
import type { ProgressHealth } from '../lib/progressHealth';
import { readLocalStorage, writeLocalStorage } from '../lib/storage';
import { ErrorDetails } from './ErrorMessage';

/** A persistent, dismissible notice when work goes in circles; it returns for the next occurrence. */
export function ProgressHealthBanner({ sid, health, onAdjust }: {
  sid: string;
  health: ProgressHealth | null;
  onAdjust?: () => void;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const storageKey = `argus.health.dismissed.v1:${sid}`;
  const [dismissed, setDismissed] = useState(() => readLocalStorage(storageKey) || '');
  if (!health || dismissed === health.key) return null;
  const failure = health.latestFailure;
  const reason = failure?.reason || '';
  const detail = failure ? `${zh ? '最近一次：' : 'Latest: '}${failure.title}` : '';
  const message = health.problem === 'repeated_failures'
    ? (zh ? `最近 ${health.failures} 个任务都没有完成。Argus 还会继续尝试，但可能卡在同一个问题上。`
      : `The last ${health.failures} tasks did not finish. Argus keeps trying, but it may be stuck on the same problem.`)
    : (zh ? `已经 ${health.hoursWithoutAccepted} 小时没有新的通过审核的结果。工作仍在进行；如果这超出你的预期，可以先停止或调整要求。`
      : `No new reviewed result for ${health.hoursWithoutAccepted} hours. Work continues; if that is longer than you expected, stop or adjust the request.`);
  const dismiss = () => { writeLocalStorage(storageKey, health.key); setDismissed(health.key); };
  return (
    <div className="mx-3 mt-3 rounded-lg border border-warn/50 bg-warn/10 px-3.5 py-2 text-[13px] text-warn" role="status" data-testid="progress-health" data-problem={health.problem}>
      <div className="leading-5">{message}</div>
      {detail ? <div className="mt-0.5 line-clamp-2 text-xs text-ink-dim" title={detail}>{detail}</div> : null}
      {reason ? <ErrorDetails error={reason} /> : null}
      <div className="mt-1.5 flex flex-wrap gap-2 text-xs">
        {onAdjust ? <button type="button" className="rounded border border-warn/50 px-2 py-0.5 hover:bg-warn/10" onClick={onAdjust}>{zh ? '在对话里调整要求' : 'Adjust in the conversation'}</button> : null}
        <button type="button" className="rounded px-2 py-0.5 text-ink-faint hover:text-ink" onClick={dismiss}>{zh ? '知道了' : 'Dismiss'}</button>
      </div>
    </div>
  );
}
