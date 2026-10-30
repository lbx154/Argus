import { useEffect, useRef, useState } from 'react';
import { attentionBadge, type Attention } from './lib/attention';
import { readLocalStorage, writeLocalStorage } from './lib/storage';
import { readBrowserNotificationPreference } from './useBrowserNotifications';

/**
 * The tab title says what needs the operator: a reply keeps its badge until
 * answered; results badge only while the tab is in the background.
 */
export function useAttentionSignals({ projectId, projectName, attention, zh, running = false, notificationsEnabled = false }: {
  projectId: string | null;
  projectName: string;
  attention: Attention | null;
  zh: boolean;
  /** The project is working: say so in the tab when nothing needs the operator. */
  running?: boolean;
  notificationsEnabled?: boolean;
}) {
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.hidden);
  const baseTitle = useRef(typeof document !== 'undefined' && document.title || 'Argus');
  const notified = useRef(new Set<string>());

  useEffect(() => {
    if (!notificationsEnabled || !projectId || !attention || typeof Notification === 'undefined' || Notification.permission !== 'granted' || !globalThis.navigator?.locks) return;
    const key = `argus.notification:${JSON.stringify([projectId, attention.key])}`;
    if (notified.current.has(key) || readLocalStorage(key) === 'sent') return;
    notified.current.add(key);
    // Only one tab may claim this occurrence. ifAvailable never waits for another tab.
    void navigator.locks.request(key, { ifAvailable: true }, lock => {
      // Re-read the shared preference: its storage event may not have reached this tab yet.
      if (!lock || !readBrowserNotificationPreference() || Notification.permission !== 'granted'
        || readLocalStorage(key) === 'sent') return;
      // Claim before constructing: construction can yield to another browser process or fail.
      if (!writeLocalStorage(key, 'sent')) return;
      const notification = new Notification(`${attention.title} · ${projectName || 'Argus'}`, { body: attention.body, tag: key });
      notification.onclick = () => { window.focus(); notification.close(); };
    }).catch(() => {
      // A revoked permission or a closing context ends this attempt; nothing retries it.
    });
  }, [attention, notificationsEnabled, projectId, projectName]);

  useEffect(() => {
    const update = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  useEffect(() => {
    const base = projectName || baseTitle.current;
    const badge = attention && (attention.kind === 'reply' || hidden) ? attentionBadge(attention.kind, zh) : '';
    document.title = badge ? `(${badge}) ${base}`
      : running ? `● ${zh ? '运行中' : 'Running'} · ${base}` : base;
    return () => { document.title = baseTitle.current; };
  }, [attention, hidden, projectName, running, zh]);
}
