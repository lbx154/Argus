import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { type ThemeMode } from './components/TopBar';

function storedBoolean(key: string, fallback: boolean): boolean {
  const value = localStorage.getItem(key);
  return value == null ? fallback : value === 'true';
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
      if (params.get('view') === 'workbench') return 'workbench';
      const stored = localStorage.getItem('argus.workspace.view');
      return stored === 'mission' || stored === 'workbench' ? stored : 'activity';
    },
  );
  const [mobileView, setMobileView] = useState<'activity' | 'preview'>('activity');
  const [rightPanelOpen, setRightPanelOpen] = useState(() => storedBoolean('argus.preview.expanded.v5', true));
  const [leftWidth, setLeftWidth] = useState(() => {
    const value = Number(localStorage.getItem('argus.sidebar.width.v2') || 256);
    return Number.isFinite(value) ? Math.max(220, Math.min(400, value)) : 256;
  });
  const [rightWidth, setRightWidth] = useState(() => {
    const value = Number(localStorage.getItem('argus.preview.width.v2') || 440);
    return Number.isFinite(value) ? Math.max(320, Math.min(600, value)) : 440;
  });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(() => storedBoolean('argus.sidebar.expanded.v4', true));
  const [manualTheme, setManualTheme] = useState<ThemeMode | null>(() => {
    const stored = localStorage.getItem('argus.theme');
    return stored === 'light' || stored === 'dark' ? stored : null;
  });
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  );
  const themeMode: ThemeMode = manualTheme ?? (systemDark ? 'dark' : 'light');
  const shellRef = useRef<HTMLDivElement>(null);
  const resizeFrameRef = useRef<number | null>(null);

  useEffect(() => {
    localStorage.setItem('argus.sidebar.expanded.v4', String(leftPanelOpen));
    localStorage.setItem('argus.preview.expanded.v5', String(rightPanelOpen));
    localStorage.setItem('argus.sidebar.width.v2', String(leftWidth));
    localStorage.setItem('argus.preview.width.v2', String(rightWidth));
  }, [leftPanelOpen, leftWidth, rightPanelOpen, rightWidth]);

  useEffect(() => {
    localStorage.setItem('argus.workspace.view', workspaceView);
  }, [workspaceView]);

  useEffect(() => {
    localStorage.setItem('argus.reasoning.visible.v1', String(showReasoning));
  }, [showReasoning]);

  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const syncSystemTheme = () => setSystemDark(media.matches);
    syncSystemTheme();
    media.addEventListener('change', syncSystemTheme);
    return () => media.removeEventListener('change', syncSystemTheme);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = themeMode;
  }, [themeMode]);

  useEffect(() => {
    const sync = () => {
      document.documentElement.dataset.pageVisible = String(!document.hidden);
    };
    sync();
    document.addEventListener('visibilitychange', sync);
    return () => document.removeEventListener('visibilitychange', sync);
  }, []);

  const cycleTheme = useCallback(() => {
    const next = themeMode === 'light' ? 'dark' : 'light';
    setManualTheme(next);
    localStorage.setItem('argus.theme', next);
  }, [themeMode]);

  const resizeSidebar = useCallback((
    side: 'left' | 'right',
    event: ReactPointerEvent<HTMLDivElement>,
  ) => {
    const shell = shellRef.current;
    if (!shell) return;
    event.preventDefault();
    const rect = shell.getBoundingClientRect();
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    const move = (pointer: PointerEvent) => {
      if (resizeFrameRef.current != null) window.cancelAnimationFrame(resizeFrameRef.current);
      resizeFrameRef.current = window.requestAnimationFrame(() => {
        if (side === 'left') {
          const occupiedRight = rightPanelOpen ? rightWidth + 8 : 56;
          const max = Math.max(220, Math.min(400, rect.width - occupiedRight - 360 - 8));
          setLeftWidth(Math.max(220, Math.min(max, pointer.clientX - rect.left)));
        } else {
          const occupiedLeft = leftPanelOpen ? leftWidth + 8 : 56;
          const max = Math.max(320, Math.min(600, rect.width - occupiedLeft - 360 - 8));
          setRightWidth(Math.max(320, Math.min(max, rect.right - pointer.clientX)));
        }
      });
    };
    const stop = () => {
      if (resizeFrameRef.current != null) window.cancelAnimationFrame(resizeFrameRef.current);
      resizeFrameRef.current = null;
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
    setWorkspaceView,
    shellRef,
    showReasoning,
    sidebarOpen,
    themeMode,
    workspaceView,
  };
}
