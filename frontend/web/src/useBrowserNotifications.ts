import { useEffect, useState } from 'react';
import { readLocalStorage, writeLocalStorage } from './lib/storage';

const PREFERENCE = 'argus.browserNotifications';
export type BrowserNotificationPermission = NotificationPermission | 'unsupported';

function permission(): BrowserNotificationPermission {
  return typeof Notification === 'undefined' || !globalThis.isSecureContext || !globalThis.navigator?.locks ? 'unsupported' : Notification.permission;
}

export const readBrowserNotificationPreference = () => readLocalStorage(PREFERENCE) === 'on';

/** A browser preference. Only the settings button may ask for permission. */
export function useBrowserNotifications() {
  const [allowed, setAllowed] = useState(permission);
  const [enabled, setEnabled] = useState(readBrowserNotificationPreference);
  const [requesting, setRequesting] = useState(false);
  useEffect(() => {
    const update = () => {
      setAllowed(permission());
      setEnabled(readBrowserNotificationPreference());
    };
    const storage = (event: StorageEvent) => {
      if (event.key === PREFERENCE || event.key === null) update();
    };
    window.addEventListener('focus', update);
    window.addEventListener('storage', storage);
    return () => {
      window.removeEventListener('focus', update);
      window.removeEventListener('storage', storage);
    };
  }, []);
  const change = async () => {
    if (enabled && permission() === 'granted') {
      writeLocalStorage(PREFERENCE, 'off');
      setEnabled(false);
      return;
    }
    if (requesting) return;
    const current = permission();
    if (current === 'unsupported' || current === 'denied') {
      setAllowed(current);
      setEnabled(false);
      writeLocalStorage(PREFERENCE, 'off');
      return;
    }
    setRequesting(true);
    try {
      const next = await Notification.requestPermission();
      setAllowed(next);
      setEnabled(next === 'granted');
      writeLocalStorage(PREFERENCE, next === 'granted' ? 'on' : 'off');
    } catch {
      setAllowed(permission());
      setEnabled(false);
      writeLocalStorage(PREFERENCE, 'off');
    } finally {
      setRequesting(false);
    }
  };
  return { enabled: enabled && allowed === 'granted', permission: allowed, requesting, change };
}
