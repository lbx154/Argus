import type { useBrowserNotifications } from '../useBrowserNotifications';
import { useI18n } from '../i18n';

export function BrowserNotificationSettings({ notifications }: {
  notifications: ReturnType<typeof useBrowserNotifications>;
}) {
  const { locale } = useI18n();
  const zh = locale === 'zh-CN';
  const { enabled, permission, requesting, change } = notifications;
  const unavailable = permission === 'unsupported';
  return <section className="settings-section mb-4 flex flex-wrap items-center gap-3">
    <div className="min-w-0 flex-1">
      <h3 id="browser-notifications-label" className="text-sm font-medium">{zh ? '浏览器通知' : 'Browser notifications'}</h3>
      <p id="browser-notifications-detail" className="mt-1 text-xs leading-5 text-ink-faint">
        {unavailable ? (zh ? '此浏览器或连接不支持通知。' : 'This browser or connection does not support notifications.')
          : permission === 'denied' ? (zh ? '通知权限已被拒绝。请在浏览器的网站设置中允许后，再开启。' : 'Notification permission was denied. Allow it in the browser’s site settings, then enable here.')
            : zh ? '默认关闭。开启后，在等你回答和交付时提醒，仅保存在此浏览器。' : 'Off by default. Enable reminders for questions and deliveries in this browser.'}
      </p>
    </div>
    <button type="button" role="switch" aria-checked={enabled} aria-labelledby="browser-notifications-label"
      aria-describedby="browser-notifications-detail" disabled={unavailable || requesting}
      onClick={() => void change()}
      className="shrink-0 rounded-lg border border-line px-3 py-2 text-sm text-ink disabled:opacity-40">
      {requesting ? (zh ? '正在请求权限…' : 'Requesting permission…') : enabled ? (zh ? '已开启' : 'On') : (zh ? '已关闭' : 'Off')}
    </button>
  </section>;
}
