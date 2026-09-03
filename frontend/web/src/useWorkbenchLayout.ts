import {
  startTransition,
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { type ThemeMode } from './components/TopBar';
import { readLocalStorage, writeLocalStorage } from './lib/storage';
import {
  readThemeStyle,
  THEME_STYLE_STORAGE_KEY,
  type ThemeStyle,
} from './lib/themePreference';

function storedBoolean(key: string, fallback: boolean): boolean {
  const value = readLocalStorage(key);
  return value == null ? fallback : value === 'true';
}

function publishThemeMode(themeMode: ThemeMode): void {
  document.documentElement.dataset.theme = themeMode;
  if (window.parent !== window) {
    window.parent.postMessage({ type: 'argus:theme-changed', payload: themeMode }, '*');
  }
}

export function useWorkbenchLayout() {
  const params = new URLSearchParams(window.location.search);
  const [kiosk, setKiosk] = useState(params.get('kiosk') === '1');
  // Match the operator knob and TUI privacy default: reasoning is opt-in.
  // Users can show it with Ctrl/⌘+O and the choice survives reloads.
  const [showReasoning, setShowReasoning] = useState(
    () => storedBoolean('argus.reasoning.visible.v1', false),
  );
  const [workspaceView, setWorkspaceView] = useState<'mission' | 'activity' | 'workbench'>(
    () => {
      const stored = readLocalStorage('argus.workspace.view');
      return stored === 'mission' || stored === 'workbench' ? stored : 'activity';
    },
  );
  const [mobileView, setMobileView] = useState<'activity' | 'preview'>('activity');
  const [rightPanelOpen, setRightPanelOpen] = useState(() => storedBoolean('argus.preview.expanded.v5', true));
  const [leftWidth, setLeftWidth] = useState(() => {
    const value = Number(readLocalStorage('argus.sidebar.width.v2') || 256);
    return Number.isFinite(value) ? Math.max(220, Math.min(400, value)) : 256;
  });
  const [rightWidth, setRightWidth] = useState(() => {
    const value = Number(readLocalStorage('argus.preview.width.v2') || 440);
    return Number.isFinite(value) ? Math.max(320, Math.min(600, value)) : 440;
  });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(() => storedBoolean('argus.sidebar.expanded.v4', true));
  const [manualTheme, setManualTheme] = useState<ThemeMode | null>(() => {
    const stored = readLocalStorage('argus.theme');
    return stored === 'light' || stored === 'dark' ? stored : null;
  });
  const [themeStyle, setThemeStyleState] = useState<ThemeStyle>(readThemeStyle);
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  );
  const themeMode: ThemeMode = manualTheme ?? (systemDark ? 'dark' : 'light');
  const themeModeRef = useRef(themeMode);
  const shellRef = useRef<HTMLDivElement>(null);
  const resizeFrameRef = useRef<number | null>(null);

  useEffect(() => {
    writeLocalStorage('argus.sidebar.expanded.v4', String(leftPanelOpen));
    writeLocalStorage('argus.preview.expanded.v5', String(rightPanelOpen));
    writeLocalStorage('argus.sidebar.width.v2', String(leftWidth));
    writeLocalStorage('argus.preview.width.v2', String(rightWidth));
  }, [leftPanelOpen, leftWidth, rightPanelOpen, rightWidth]);

  useEffect(() => {
    writeLocalStorage('argus.workspace.view', workspaceView);
  }, [workspaceView]);

  useEffect(() => {
    writeLocalStorage('argus.reasoning.visible.v1', String(showReasoning));
  }, [showReasoning]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const syncSystemTheme = () => setSystemDark(media.matches);
    syncSystemTheme();
    media.addEventListener('change', syncSystemTheme);
    return () => media.removeEventListener('change', syncSystemTheme);
  }, []);

  useEffect(() => {
    themeModeRef.current = themeMode;
    publishThemeMode(themeMode);
  }, [themeMode]);

  useEffect(() => {
    document.documentElement.dataset.themeStyle = themeStyle;
  }, [themeStyle]);

  const cycleTheme = useCallback(() => {
    const next = themeModeRef.current === 'light' ? 'dark' : 'light';
    themeModeRef.current = next;
    publishThemeMode(next);
    writeLocalStorage('argus.theme', next);
    startTransition(() => setManualTheme(next));
  }, []);

  const setThemeStyle = useCallback((next: ThemeStyle) => {
    setThemeStyleState(next);
    writeLocalStorage(THEME_STYLE_STORAGE_KEY, next);
  }, []);

  const resizeSidebar = useCallback((
    side: 'left' | 'right',
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    const shell = shellRef.current;
    if (!shell) return;
    event.preventDefault();
    const rect = shell.getBoundingClientRect();
    let pendingWidth = side === 'left' ? leftWidth : rightWidth;
    shell.dataset.resizing = side;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const move = (pointer: PointerEvent) => {
      if (side === 'left') {
        const occupiedRight = rightPanelOpen ? rightWidth + 8 : 56;
        const max = Math.max(220, Math.min(400, rect.width - occupiedRight - 360 - 8));
        pendingWidth = Math.max(220, Math.min(max, pointer.clientX - rect.left));
      } else {
        const occupiedLeft = leftPanelOpen ? leftWidth + 8 : 56;
        const max = Math.max(320, Math.min(600, rect.width - occupiedLeft - 360 - 8));
        pendingWidth = Math.max(320, Math.min(max, rect.right - pointer.clientX));
      }
      if (resizeFrameRef.current != null) return;
      resizeFrameRef.current = window.requestAnimationFrame(() => {
        shell.style.setProperty(
          side === 'left' ? '--sidebar-width' : '--preview-width',
          `${pendingWidth}px`,
        );
        resizeFrameRef.current = null;
      });
    };
    const stop = () => {
      if (resizeFrameRef.current != null) window.cancelAnimationFrame(resizeFrameRef.current);
      resizeFrameRef.current = null;
      shell.style.setProperty(
        side === 'left' ? '--sidebar-width' : '--preview-width',
        `${pendingWidth}px`,
      );
      if (side === 'left') setLeftWidth(pendingWidth);
      else setRightWidth(pendingWidth);
      delete shell.dataset.resizing;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', stop, { once: true });
    window.addEventListener('pointercancel', stop, { once: true });
  }, [leftPanelOpen, leftWidth, rightPanelOpen, rightWidth]);

  useEffect(() => {
    const fit = () => {
      if (window.innerWidth < 1024 || !shellRef.current) return;
      const shellWidth = shellRef.current.clientWidth;
      const left = leftPanelOpen ? leftWidth : 56;
      const right = rightPanelOpen ? rightWidth : 56;
      const handles = (leftPanelOpen ? 8 : 0) + (rightPanelOpen ? 8 : 0);
      const availableForSides = Math.max(540, shellWidth - 360 - handles);
      if (left + right <= availableForSides) return;
      let nextRight = rightPanelOpen
        ? Math.max(320, Math.min(rightWidth, availableForSides - left))
        : right;
      const nextLeft = leftPanelOpen
        ? Math.max(220, Math.min(leftWidth, availableForSides - nextRight))
        : left;
      if (nextLeft + nextRight > availableForSides && rightPanelOpen) {
        nextRight = Math.max(320, availableForSides - nextLeft);
      }
      if (leftPanelOpen) setLeftWidth(nextLeft);
      if (rightPanelOpen) setRightWidth(nextRight);
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [leftPanelOpen, leftWidth, rightPanelOpen, rightWidth]);

  return {
    cycleTheme,
    kiosk,
    leftPanelOpen,
    leftWidth,
    mobileView,
    resizeSidebar,
    rightPanelOpen,
    rightWidth,
    setKiosk,
    setLeftPanelOpen,
    setLeftWidth,
    setMobileView,
    setRightPanelOpen,
    setRightWidth,
    setShowReasoning,
    setSidebarOpen,
    setThemeStyle,
    setWorkspaceView,
    shellRef,
    showReasoning,
    sidebarOpen,
    themeMode,
    themeStyle,
    workspaceView,
  };
}
