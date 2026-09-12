import { startTransition, useCallback, useEffect, useRef, useState } from 'react';
import type { ThemeMode } from './components/TopBar';
import { readLocalStorage, writeLocalStorage } from './lib/storage';
import { normalizeThemeStyle, readThemeStyle } from './lib/themePreference';

function publishThemeMode(mode: ThemeMode) {
  document.documentElement.dataset.theme = mode;
  if (window.parent !== window) {
    window.parent.postMessage({ type: 'argus:theme-changed', payload: mode }, '*');
  }
}

/** One appearance preference for the user workbench and administrator views. */
export function useWorkbenchTheme() {
  const [manualTheme, setManualTheme] = useState<ThemeMode | null>(() => {
    const desktop = new URLSearchParams(window.location.search).get('desktopTheme');
    if (desktop === 'light' || desktop === 'dark') return desktop;
    const stored = readLocalStorage('argus.theme');
    return stored === 'light' || stored === 'dark' ? stored : null;
  });
  const [systemDark, setSystemDark] = useState(() => window.matchMedia('(prefers-color-scheme: dark)').matches);
  const themeStyle = readThemeStyle();
  const themeMode: ThemeMode = manualTheme ?? (systemDark ? 'dark' : 'light');
  const themeModeRef = useRef(themeMode);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const sync = () => setSystemDark(media.matches);
    sync();
    media.addEventListener('change', sync);
    return () => media.removeEventListener('change', sync);
  }, []);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key !== 'argus.theme' && event.key !== null) return;
      const stored = readLocalStorage('argus.theme');
      setManualTheme(stored === 'light' || stored === 'dark' ? stored : null);
    };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, []);
  useEffect(() => {
    themeModeRef.current = themeMode;
    publishThemeMode(themeMode);
  }, [themeMode]);
  useEffect(() => {
    document.documentElement.dataset.themeStyle = themeStyle;
    normalizeThemeStyle();
  }, [themeStyle]);
  useEffect(() => {
    if (window.parent !== window) {
      window.parent.postMessage({ type: 'argus:theme-preference', payload: manualTheme || 'system' }, '*');
    }
  }, [manualTheme]);
  const cycleTheme = useCallback(() => {
    const next = themeModeRef.current === 'light' ? 'dark' : 'light';
    themeModeRef.current = next;
    publishThemeMode(next);
    writeLocalStorage('argus.theme', next);
    const url = new URL(window.location.href);
    if (url.searchParams.has('desktopTheme')) {
      url.searchParams.set('desktopTheme', next);
      window.history.replaceState(window.history.state, '', url.toString());
    }
    startTransition(() => setManualTheme(next));
  }, []);
  return { themeMode, themeStyle, cycleTheme };
}
