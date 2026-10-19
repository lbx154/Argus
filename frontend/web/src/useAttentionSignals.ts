import { useCallback, useEffect, useRef, useState } from 'react';
import { attentionBadge, type Attention } from './lib/attention';
import { readLocalStorage, writeLocalStorage } from './lib/storage';

const NOTIFY_KEY = 'argus.notifications.v1';

type Permission = 'unsupported' | 'default' | 'granted' | 'denied';

function browserPermission(): Permission {
  if (typeof window === 'undefined' || !('Notification' in window) || !window.isSecureContext) return 'unsupported';
  return Notification.permission as Permission;
}

/**
 * Tab title badge and opt-in browser notifications for what needs the operator.
 * Replies keep their badge until answered; results and errors badge only while
 * the tab is in the background. A notification fires once per new occurrence,
 * never for the state already present when the project was opened.
 */
export function useAttentionSignals({ sid, projectName, attention, zh, embedded, running = false }: {
  sid: string | null;
  projectName: string;
  attention: Attention | null;
  zh: boolean;
  /** The project is working: say so in the tab when nothing needs the operator. */
  running?: boolean;
  /** The desktop shell raises its own native notifications. */
  embedded: boolean;
}) {
  const [permission, setPermission] = useState<Permission>(browserPermission);
  const [optedIn, setOptedIn] = useState(() => readLocalStorage(NOTIFY_KEY) === 'on');
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.hidden);
  const baseTitle = useRef<string>(typeof document !== 'undefined' ? document.title : '');
  const observed = useRef<{ sid: string | null; key: string | null }>({ sid: null, key: null });

  useEffect(() => {
    const update = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', update);
    return () => document.removeEventListener('visibilitychange', update);
  }, []);

  useEffect(() => {
    const base = baseTitle.current;
    const badge = attention && (attention.kind === 'reply' || hidden) ? attentionBadge(attention.kind, zh) : '';
    document.title = badge ? `(${badge}) ${projectName ? `${projectName} · ` : ''}${base}`
      : running ? `● ${zh ? '运行中' : 'Running'} · ${projectName ? `${projectName} · ` : ''}${base}` : base;
    return () => { document.title = base; };
  }, [attention, hidden, projectName, running, zh]);

  useEffect(() => {
    const key = attention?.key ?? null;
    const previous = observed.current;
    observed.current = { sid, key };
    if (previous.sid !== sid || !attention || key === previous.key) return;
    if (embedded || !optedIn || permission !== 'granted' || (!document.hidden && document.hasFocus())) return;
    try {
      const notification = new Notification(`${attention.title}${projectName ? ` · ${projectName}` : ''}`, {
        body: attention.body.slice(0, 240), tag: attention.key,
      });
      notification.onclick = () => { window.focus(); notification.close(); };
    } catch {
      // Some browsers only allow notifications from a service worker; the badge remains.
    }
  }, [attention, embedded, optedIn, permission, projectName, sid]);

  const enable = useCallback(async () => {
    if (browserPermission() === 'unsupported') return;
    const result = Notification.permission === 'default' ? await Notification.requestPermission() : Notification.permission;
    setPermission(result as Permission);
    const on = result === 'granted';
    setOptedIn(on);
    writeLocalStorage(NOTIFY_KEY, on ? 'on' : 'off');
  }, []);
  const disable = useCallback(() => {
    setOptedIn(false);
    writeLocalStorage(NOTIFY_KEY, 'off');
  }, []);

  return {
    supported: permission !== 'unsupported' && !embedded,
    enabled: optedIn && permission === 'granted',
    denied: permission === 'denied',
    enable,
    disable,
  };
}
