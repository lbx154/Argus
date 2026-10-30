import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { useWorkbenchTheme } from './useWorkbenchTheme';
import { readLocalStorage, writeLocalStorage } from './lib/storage';
import { preferredPreviewWidth, PREVIEW_DEFAULT_WIDTH, PREVIEW_MAX_WIDTH } from './lib/previewLayout';

type WorkspaceView = 'activity' | 'workbench' | 'map';
const WORKSPACE_VIEWS: readonly WorkspaceView[] = ['activity', 'workbench', 'map'];
const MAP_FULL_KEY = 'argus.map.fullscreen.v1';

function isWorkspaceView(value: string | null): value is WorkspaceView {
  return WORKSPACE_VIEWS.includes(value as WorkspaceView);
}

function storedBoolean(key: string, fallback: boolean): boolean {
  const value = readLocalStorage(key);
  return value == null ? fallback : value === 'true';
}

export function useWorkbenchLayout() {
  const params = new URLSearchParams(window.location.search);
  const kiosk = params.get('kiosk') === '1';
  const project = params.get('project');
  const requested = params.get('view');
  const openingView = isWorkspaceView(requested) ? requested : 'activity';
  const [workspaceView, setWorkspaceView] = useState<WorkspaceView>(openingView);
  const [mobileView, setMobileView] = useState<'activity' | 'preview'>('activity');
  useEffect(() => {
    setWorkspaceView(openingView);
    setMobileView('activity');
  }, [project, openingView]);
  const [rightPanelOpen, setRightPanelOpen] = useState(false);
  useEffect(() => setRightPanelOpen(false), [project]);
  const [narrow, setNarrow] = useState(() => window.innerWidth < 1200);
  const rightPanelVisible = mobileView === 'preview' || (!narrow && rightPanelOpen && workspaceView !== 'map');
  const [leftWidth, setLeftWidth] = useState(() => {
    const value = Number(readLocalStorage('argus.sidebar.width.v2') || 256);
    return Number.isFinite(value) ? Math.max(220, Math.min(400, value)) : 256;
  });
  const [rightWidth, setRightWidth] = useState(() => {
    const value = Number(readLocalStorage('argus.preview.width.v2') || PREVIEW_DEFAULT_WIDTH);
    return Number.isFinite(value) ? Math.max(320, Math.min(PREVIEW_MAX_WIDTH, value)) : PREVIEW_DEFAULT_WIDTH;
  });
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [leftPanelOpen, setLeftPanelOpen] = useState(() => storedBoolean('argus.sidebar.expanded.v4', true));
  // The research map opens full-window: the project list folds to its rail and
  // the map's own header, overview and legend step aside, so a person has the
  // whole canvas to draw on. Their choice to unfold it is remembered here.
  const [mapFull, setMapFullState] = useState(() => readLocalStorage(MAP_FULL_KEY) !== '0');
  const setMapFull = useCallback((next: boolean) => {
    setMapFullState(next);
    writeLocalStorage(MAP_FULL_KEY, next ? '1' : '0');
  }, []);
  const mapImmersive = workspaceView === 'map' && mapFull && !kiosk;
  // What the screen shows: full canvas folds the list without changing the
  // person's own choice for the other views. All layout math reads this.
  const sidebarShown = leftPanelOpen && !mapImmersive;
  const toggleSidebar = useCallback(() => {
    if (mapImmersive) { setMapFull(false); setLeftPanelOpen(true); }
    else setLeftPanelOpen((value) => !value);
  }, [mapImmersive, setMapFull]);
  const { themeMode, themeStyle, cycleTheme } = useWorkbenchTheme();
  const shellRef = useRef<HTMLDivElement>(null);
  const resizeFrameRef = useRef<number | null>(null);

  useEffect(() => {
    writeLocalStorage('argus.sidebar.expanded.v4', String(leftPanelOpen));
    writeLocalStorage('argus.sidebar.width.v2', String(leftWidth));
    writeLocalStorage('argus.preview.width.v2', String(rightWidth));
  }, [leftPanelOpen, leftWidth, rightWidth]);

  const openPreview = useCallback(() => {
    setRightPanelOpen(true);
    setMobileView('preview');
    const width = shellRef.current?.clientWidth ?? window.innerWidth;
    // Mobile already dedicates a full screen to reading. Desktop grows only
    // on an explicit open action, never on a background query refresh.
    if (width >= 1200) {
      const preferred = preferredPreviewWidth(width, leftWidth, sidebarShown);
      setRightWidth((current) => Math.max(current, preferred));
    }
  }, [sidebarShown, leftWidth]);

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
        const occupiedRight = rightPanelOpen ? rightWidth + 8 : 0;
        const max = Math.max(220, Math.min(400, rect.width - occupiedRight - 360 - 8));
        pendingWidth = Math.max(220, Math.min(max, pointer.clientX - rect.left));
      } else {
        const occupiedLeft = sidebarShown ? leftWidth + 8 : 56;
        const max = Math.max(320, Math.min(PREVIEW_MAX_WIDTH, rect.width - occupiedLeft - 360 - 8));
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
  }, [sidebarShown, leftWidth, rightPanelOpen, rightWidth]);

  useEffect(() => {
    const fit = () => {
      setNarrow(window.innerWidth < 1200);
      if (window.innerWidth < 1200 || !shellRef.current) return;
      const shellWidth = shellRef.current.clientWidth;
      const left = sidebarShown ? leftWidth : 56;
      const right = rightPanelOpen ? rightWidth : 0;
      const handles = (sidebarShown ? 8 : 0) + (rightPanelOpen ? 8 : 0);
      const availableForSides = Math.max(540, shellWidth - 360 - handles);
      if (left + right <= availableForSides) return;
      let nextRight = rightPanelOpen
        ? Math.max(320, Math.min(rightWidth, availableForSides - left))
        : right;
      const nextLeft = sidebarShown
        ? Math.max(220, Math.min(leftWidth, availableForSides - nextRight))
        : left;
      if (nextLeft + nextRight > availableForSides && rightPanelOpen) {
        nextRight = Math.max(320, availableForSides - nextLeft);
      }
      if (sidebarShown) setLeftWidth(nextLeft);
      if (rightPanelOpen) setRightWidth(nextRight);
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [sidebarShown, leftWidth, rightPanelOpen, rightWidth]);

  return {
    cycleTheme,
    kiosk,
    leftWidth,
    mapImmersive,
    mobileView,
    openPreview,
    resizeSidebar,
    rightPanelVisible,
    rightWidth,
    setLeftWidth,
    // Presentation mode never folds into full canvas, so it offers no toggle.
    setMapFull: kiosk ? undefined : setMapFull,
    setMobileView,
    setRightPanelOpen,
    setRightWidth,
    setSidebarOpen,
    setWorkspaceView,
    shellRef,
    sidebarOpen,
    sidebarShown,
    themeMode,
    themeStyle,
    toggleSidebar,
    workspaceView,
  };
}
