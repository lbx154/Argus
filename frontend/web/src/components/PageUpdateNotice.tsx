import { useSyncExternalStore } from 'react';
import { useI18n } from '../i18n';
import { getPageUpdateAvailable, subscribePageUpdate } from '../lib/pageUpdate';

/** Keep the current draft on screen; refreshing is always the user's action. */
export function PageUpdateNotice() {
  const available = useSyncExternalStore(subscribePageUpdate, getPageUpdateAvailable, () => false);
  const { locale } = useI18n();
  if (!available) return null;
  const zh = locale === 'zh-CN';
  return <div role="status" className="fixed left-1/2 top-3 z-[110] flex w-[min(92vw,42rem)] -translate-x-1/2 items-center gap-3 rounded-xl border border-blue/40 bg-panel px-4 py-3 text-sm text-ink shadow-xl">
    <div className="min-w-0 flex-1">
      <strong>{zh ? '页面已更新' : 'Page update available'}</strong>
      <p className="mt-1 text-xs text-ink-dim">{zh
        ? '刷新后显示最新选项和功能。请先复制未发送的内容。'
        : 'Refresh for the latest options and features. Copy any unsent text first.'}</p>
    </div>
    <button type="button" onClick={() => window.location.reload()} className="shrink-0 rounded-md border border-blue/40 px-3 py-2 text-xs text-blue">
      {zh ? '刷新页面' : 'Refresh page'}
    </button>
  </div>;
}
