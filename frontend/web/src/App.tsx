import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { useProjects, useSnapshot, useEventStream, useProjectActions, useArtifacts, useTranscript, useJournal, useGitDiff } from './hooks';
import { api } from './api';
import { TopBar, type ThemeMode } from './components/TopBar';
import { EventStream } from './components/EventStream';
import { ChatBox } from './components/ChatBox';
import { CommandPalette, type PaletteItem } from './components/CommandPalette';
import { KeybindingHelp } from './components/KeybindingHelp';
import { DoctorModal, ConfigModal, IdentityModal, TranscriptModal } from './components/InfoModals';
import { PendingBanner } from './components/PendingBanner';
import { PendingReplyDialog, type PendingReply } from './components/PendingReplyDialog';
import { GuardianBanner } from './components/GuardianBanner';
import { Wordmark } from './components/Wordmark';
import { BackendHandshake } from './components/BackendHandshake';
import { TAGLINE } from './lib/soul';
import { rankProjects, reconcileProjectSelection, resolveProjectSelection } from '../../core/src/projects';
import { ArtifactModal } from './components/ArtifactModal';
import { ResearchCanvas } from './components/ResearchCanvas';
import { ActionNotice, type NoticeTone, type UiNotice } from './components/ActionNotice';
import { NewDaemonModal } from './components/NewDaemonModal';
import { DaemonManageModal } from './components/DaemonManageModal';
import { Sidebar } from './components/Sidebar';
import { ProjectInspectorModal } from './components/ProjectInspectorModal';
import { TaskDetailModal } from './components/TaskDetailModal';
import { SplitHandle } from './components/SplitHandle';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faAnglesLeft } from '@fortawesome/free-solid-svg-icons';
import { MissionControl } from './components/MissionControl';
import { OperationsModal } from './components/OperationsModal';
import { Button } from './components/primitives';
import { activeGuardianAlert } from './lib/guardian';
import { projectMissionView } from '../../core/src/missionView';
import { useQueryClient } from '@tanstack/react-query';
import { parseRenameCommand } from './lib/projectName';

type Overlay = 'none' | 'palette' | 'help' | 'doctor' | 'config' | 'identity' | 'transcript' | 'inspector' | 'operations';
type ProjectHistoryMode = 'push' | 'replace';
interface ActiveMessageRequest {
  id: number;
  sid: string;
  controller: AbortController;
}
let noticeSequence = 0;
const BROWSER_PROJECT_KEY = 'argus.browser.project.v1';

function storedBoolean(key: string, fallback: boolean): boolean {
  const value = localStorage.getItem(key);
  return value == null ? fallback : value === 'true';
}

function storedBrowserProject(): string | null {
  try {
    return window.sessionStorage.getItem(BROWSER_PROJECT_KEY);
  } catch {
    return null;
  }
}

function storeBrowserProject(id: string | null): void {
  try {
    if (id) window.sessionStorage.setItem(BROWSER_PROJECT_KEY, id);
    else window.sessionStorage.removeItem(BROWSER_PROJECT_KEY);
  } catch {
    /* storage can be disabled; the URL remains authoritative */
  }
}

const errorText = (error: unknown): string =>
  error instanceof Error ? error.message : String(error || 'Unknown error');

function writeProjectLocation(id: string | null, mode: ProjectHistoryMode): void {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('project', id);
  else url.searchParams.delete('project');
  const method = mode === 'push' ? 'pushState' : 'replaceState';
  window.history[method](window.history.state, '', url.toString());
}

/** Full-viewport picker/empty landing shown until a daemon is selectable. */
function Landing({
  loading,
  hasProjects,
  error,
  onRetry,
  onNew,
  onChoose,
  canCreate,
}: {
  loading: boolean;
  hasProjects: boolean;
  error?: string;
  onRetry: () => void;
  onNew: () => void;
  onChoose: () => void;
  canCreate: boolean;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 text-center">
      {loading ? <BackendHandshake /> : (
        <>
          <Wordmark size={32} tag={TAGLINE} />
          <p className={`max-w-md text-sm leading-relaxed ${error ? 'text-err' : 'text-ink-faint'}`}>
            {error
              ? error
              : hasProjects
              ? 'Select a session from the sidebar, or create a new one.'
              : 'No sessions yet. Create one to begin.'}
          </p>
        </>
      )}
      {!loading && (
        <div className="flex flex-wrap justify-center gap-2">
          {error ? (
            <Button onClick={onRetry} variant="danger">Retry</Button>
          ) : null}
          {hasProjects ? (
            <Button onClick={onChoose}>Select session</Button>
          ) : canCreate ? (
            <Button onClick={onNew} variant="primary">New session</Button>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const queryClient = useQueryClient();
  const projectsQ = useProjects();
  const projects = useMemo(() => rankProjects(projectsQ.data?.projects ?? []), [projectsQ.data?.projects]);
  const localCwd = projectsQ.data?.local_cwd ?? '';

  const [sid, setSid] = useState<string | null>(
    params.get('project') || storedBrowserProject(),
  );
  const sidRef = useRef(sid);
  const initialSelectionResolvedRef = useRef(false);
  sidRef.current = sid;
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [kiosk, setKiosk] = useState(params.get('kiosk') === '1');
  const [showReasoning, setShowReasoning] = useState(false);
  const [workspaceView, setWorkspaceView] = useState<'mission' | 'activity'>(
    () => localStorage.getItem('argus.workspace.view') === 'mission' ? 'mission' : 'activity',
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
  const [composerFocus, setComposerFocus] = useState(0);
  const [chatPending, setChatPending] = useState(false);
  const [pendingReplyOpen, setPendingReplyOpen] = useState(false);
  const [pendingReplyBusy, setPendingReplyBusy] = useState(false);
  const promptedReplyRef = useRef('');
  const [managerPhase, setManagerPhase] = useState('');
  const [managerStartedAt, setManagerStartedAt] = useState(0);
  const [artifactPath, setArtifactPath] = useState<string | null>(null);
  const [taskItemId, setTaskItemId] = useState<string | null>(null);
  const [newDaemonOpen, setNewDaemonOpen] = useState(false);
  const [daemonManageOpen, setDaemonManageOpen] = useState(false);
  const [manageTargetSid, setManageTargetSid] = useState<string | null>(null);
  const [creatingDaemon, setCreatingDaemon] = useState(false);
  const creatingDaemonRef = useRef(false);
  const messageRequestRef = useRef<ActiveMessageRequest | null>(null);
  const messageEpochRef = useRef(0);
  const shellRef = useRef<HTMLDivElement>(null);
  const resizeFrameRef = useRef<number | null>(null);
  const [notice, setNotice] = useState<UiNotice | null>(null);
  const dismissNotice = useCallback(() => setNotice(null), []);
  const notify = useCallback((tone: NoticeTone, message: string) => {
    setNotice({ id: ++noticeSequence, tone, message });
  }, []);

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

  const cancelActiveMessage = useCallback(() => {
    const cancelled = Boolean(messageRequestRef.current);
    messageEpochRef.current += 1;
    messageRequestRef.current?.controller.abort();
    messageRequestRef.current = null;
    setChatPending(false);
    setManagerPhase('');
    setManagerStartedAt(0);
    return cancelled;
  }, []);

  const stopWaiting = useCallback(() => {
    if (!cancelActiveMessage()) return;
    notify('info', 'Stopped waiting for this reply. Server-side work may still finish in the project timeline.');
  }, [cancelActiveMessage, notify]);

  const activateProject = useCallback((id: string | null) => {
    if (id !== sidRef.current) {
      cancelActiveMessage();
      setArtifactPath(null);
      setTaskItemId(null);
    }
    sidRef.current = id;
    setSid(id);
    storeBrowserProject(id);
  }, [cancelActiveMessage]);

  useEffect(() => () => {
    messageEpochRef.current += 1;
    messageRequestRef.current?.controller.abort();
    messageRequestRef.current = null;
  }, []);

  const selectProject = useCallback((id: string, mode: ProjectHistoryMode = 'push') => {
    const locationId = new URLSearchParams(window.location.search).get('project');
    activateProject(id);
    if (locationId !== id) writeProjectLocation(id, mode);
  }, [activateProject]);

  const prefetchProject = useCallback((id: string) => {
    void queryClient.prefetchQuery({
      queryKey: ['snapshot', id],
      queryFn: ({ signal }) => api.snapshot(id, signal),
      staleTime: 3_000,
    });
  }, [queryClient]);

  const createDaemon = async (name: string, objective: string): Promise<boolean> => {
    if (creatingDaemonRef.current) return false;
    creatingDaemonRef.current = true;
    setCreatingDaemon(true);
    try {
      const r = await api.createDaemon(objective, name);
      await projectsQ.refetch();
      selectProject(r.sid);
      window.setTimeout(() => setComposerFocus((x) => x + 1), 0);
      notify('success', 'Daemon created and selected.');
      return true;
    } catch (error) {
      notify('error', `Could not create daemon: ${errorText(error)}`);
      return false;
    } finally {
      creatingDaemonRef.current = false;
      setCreatingDaemon(false);
    }
  };

  // Resolve exactly once. Project polling must NEVER auto-follow a newly live
  // session created by another browser/operator.
  useEffect(() => {
    if (!projectsQ.isSuccess) return;
    const wasResolved = initialSelectionResolvedRef.current;
    const selection = reconcileProjectSelection(
      projects,
      sidRef.current,
      wasResolved,
    );
    if (wasResolved) return;
    initialSelectionResolvedRef.current = true;
    if (selection.id !== sidRef.current) activateProject(selection.id);
    else storeBrowserProject(selection.id);
    const locationId = new URLSearchParams(window.location.search).get('project');
    if (locationId !== selection.id) {
      writeProjectLocation(selection.id, 'replace');
    }
    if (selection.recovered) {
      const fallback = projects.find((project) => project.id === selection.id);
      notify(
        'info',
        fallback
          ? `Project “${selection.requested}” was not found. Switched to ${fallback.label || fallback.id}.`
          : `Project “${selection.requested}” was not found. Create a daemon to continue.`,
      );
    }
  }, [activateProject, notify, projects, projectsQ.isSuccess]);

  // Project switches are real browser-history entries. Back/Forward restores
  // the corresponding cockpit without reloading the page.
  useEffect(() => {
    const onPopState = () => {
      const requested = new URLSearchParams(window.location.search).get('project');
      setSidebarOpen(false);
      if (!requested) {
        activateProject(null);
        return;
      }
      if (!projectsQ.isSuccess) {
        activateProject(requested);
        return;
      }
      const selection = resolveProjectSelection(projects, requested);
      activateProject(selection.id);
      if (selection.recovered) {
        writeProjectLocation(selection.id, 'replace');
        const fallback = projects.find((project) => project.id === selection.id);
        notify(
          'info',
          fallback
            ? `Project “${selection.requested}” was not found. Switched to ${fallback.label || fallback.id}.`
            : `Project “${selection.requested}” was not found. Create a daemon to continue.`,
        );
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, [activateProject, notify, projects, projectsQ.isSuccess]);

  const activeSid = projectsQ.isSuccess
    ? sid && projects.some((project) => project.id === sid)
      ? sid
      : null
    : projectsQ.isError
    ? sid
    : null;

  const snapQ = useSnapshot(activeSid);
  const snap = snapQ.data;
  const loadedSid = snap?.session.id === activeSid ? activeSid : null;
  const continuous = snap?.continuous;
  const artifactsQ = useArtifacts(loadedSid, true);
  const gitDiffQ = useGitDiff(loadedSid, workspaceView === 'mission');
  const { events, connected } = useEventStream(loadedSid);
  const guardianAlert = useMemo(() => activeGuardianAlert(events), [events]);
  const transcriptQ = useTranscript(loadedSid, workspaceView === 'activity', 120);
  const journalQ = useJournal(activeSid, 20, overlay === 'inspector');
  const pendingReply = useMemo<PendingReply | null>(() => {
    const row = (snap?.pending_questions ?? []).find((item) => {
      const id = String(item.id ?? '').trim();
      const question = String(item.pending_question ?? item.question ?? item.text ?? '').trim();
      return Boolean(id && question);
    });
    if (!row) return null;
    return {
      id: String(row.id),
      title: String(row.title ?? row.objective ?? 'Blocked task'),
      question: String(row.pending_question ?? row.question ?? row.text),
    };
  }, [snap?.pending_questions]);
  useEffect(() => {
    if (!pendingReply || !activeSid) {
      setPendingReplyOpen(false);
      return;
    }
    const key = `${activeSid}:${pendingReply.id}`;
    if (promptedReplyRef.current !== key) {
      promptedReplyRef.current = key;
      setPendingReplyOpen(true);
    }
  }, [activeSid, pendingReply]);
  const activityEvents = useMemo(() => {
    const liveCounts = new Map<string, number>();
    events.forEach((event) => {
      const type = String(event.type ?? '');
      if (type !== 'ui.operator' && type !== 'ui.argus') return;
      const key = `${type}\u0000${String(event.text ?? '')}`;
      liveCounts.set(key, (liveCounts.get(key) ?? 0) + 1);
    });
    const history = (transcriptQ.data ?? []).map((turn) => ({
      type: turn.role === 'operator' ? 'ui.operator' : 'ui.argus',
      agent_layer: turn.role === 'operator' ? 'operator' : 'manager',
      text: turn.text,
      ts: turn.ts,
      message_id: `transcript-${turn.ts}-${turn.role}`,
    }));
    const keep = new Array(history.length).fill(true);
    for (let index = history.length - 1; index >= 0; index -= 1) {
      const event = history[index];
      const key = `${event.type}\u0000${event.text}`;
      const count = liveCounts.get(key) ?? 0;
      if (count > 0) {
        keep[index] = false;
        liveCounts.set(key, count - 1);
      }
    }
    return [
      ...history.filter((_event, index) => keep[index]),
      ...events,
    ].sort((left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0));
  }, [events, transcriptQ.data]);
  const missionView = useMemo(
    () => snap ? projectMissionView(snap, activityEvents, artifactsQ.data ?? []) : null,
    [activityEvents, artifactsQ.data, snap],
  );
  const actions = useProjectActions(activeSid, snap?.daemon_commands?.revision);
  const daemonBusy = actions.startDaemon.isPending
    || actions.stopDaemon.isPending
    || actions.updateProject.isPending
    || actions.deleteProject.isPending;
  const actionFeedback = (success: string) => ({
    onSuccess: () => notify('success', success),
    onError: (error: Error) => notify('error', errorText(error)),
  });
  const requestStartDaemon = () =>
    actions.startDaemon.mutate(undefined, actionFeedback('Daemon start requested.'));
  const requestStopDaemon = () =>
    actions.stopDaemon.mutate(true, actionFeedback('Daemon is draining and will stop safely.'));
  const manageStartDaemon = async (): Promise<boolean> => {
    try {
      await actions.startDaemon.mutateAsync();
      notify('success', 'Daemon resumed.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  };
  const managePauseDaemon = async (): Promise<boolean> => {
    try {
      await actions.stopDaemon.mutateAsync(true);
      notify('success', 'Daemon paused safely.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  };
  const manageRenameProject = async (name: string): Promise<boolean> => {
    if (!activeSid) return false;
    try {
      await actions.updateProject.mutateAsync({ sid: activeSid, name });
      notify('success', 'Session name updated.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  };
  const manageDeleteProject = async (): Promise<boolean> => {
    if (!activeSid) return false;
    try {
      await actions.deleteProject.mutateAsync();
      setDaemonManageOpen(false);
      activateProject(null);
      writeProjectLocation(null, 'replace');
      const refreshed = await projectsQ.refetch();
      const next = rankProjects(refreshed.data?.projects ?? [])[0];
      if (next) selectProject(next.id, 'replace');
      notify('success', 'Session moved to recoverable trash.');
      return true;
    } catch (error) {
      notify('error', errorText(error));
      return false;
    }
  };
  const requestManageSession = useCallback((projectId: string) => {
    setDaemonManageOpen(false);
    if (projectId === activeSid && snap?.session.id === projectId) {
      setManageTargetSid(null);
      setDaemonManageOpen(true);
      return;
    }
    setManageTargetSid(projectId);
    selectProject(projectId);
  }, [activeSid, selectProject, snap?.session.id]);
  useEffect(() => {
    if (!manageTargetSid || activeSid !== manageTargetSid || snap?.session.id !== manageTargetSid) return;
    setManageTargetSid(null);
    setDaemonManageOpen(true);
  }, [activeSid, manageTargetSid, snap?.session.id]);
  const requestDispose = (id: string, op: 'done' | 'skip' | 'rm') =>
    actions.disposeBacklog.mutate(
      { id, op },
      actionFeedback(op === 'done' ? 'Work marked done.' : 'Work removed.'),
    );
  const requestStopIteration = (id: string) =>
    actions.stopBacklog.mutate(id, actionFeedback('Iteration stopped.'));
  const toggleContinuous = () => {
    if (!continuous) return;
    const enabled = !continuous.enabled;
    actions.setContinuous.mutate(
      { enabled, objective: continuous.objective },
      actionFeedback(enabled ? 'Continuous campaign enabled.' : 'Continuous campaign stopped.'),
    );
  };
  const cycleTheme = () => {
    const next = themeMode === 'light' ? 'dark' : 'light';
    setManualTheme(next);
    localStorage.setItem('argus.theme', next);
  };
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
      let nextLeft = leftPanelOpen
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

  // global keybindings — editor-style panel toggles plus cockpit actions.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement | null;
      const typing = el?.tagName === 'INPUT' || el?.tagName === 'TEXTAREA';
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOverlay((o) => (o === 'palette' ? 'none' : 'palette'));
      } else if (mod && e.key.toLowerCase() === 'o') {
        e.preventDefault();
        setShowReasoning((v) => !v);
      } else if (mod && e.key === '.') {
        e.preventDefault();
        setKiosk((v) => !v);
      } else if (mod && e.key.toLowerCase() === 'b') {
        e.preventDefault();
        setLeftPanelOpen((value) => !value);
      } else if (mod && e.key.toLowerCase() === 'j') {
        e.preventDefault();
        setComposerFocus((value) => value + 1);
      } else if (!typing && e.key === '?') {
        e.preventDefault();
        setOverlay('help');
      } else if (!typing && e.key === '/') {
        e.preventDefault();
        setComposerFocus((x) => x + 1);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const sendMessage = async (text: string) => {
    const requestSid = activeSid;
    if (!requestSid || messageRequestRef.current) return;
    const rename = parseRenameCommand(text);
    if (rename.matched) {
      if (!rename.name) {
        notify('error', 'Usage: /rename <name>');
        return;
      }
      try {
        const result = await actions.updateProject.mutateAsync({
          sid: requestSid,
          name: rename.name,
        });
        notify('success', `Renamed conversation to ${result.name}.`);
      } catch (error) {
        notify('error', `Rename failed: ${errorText(error)}`);
      }
      return;
    }
    const requestId = ++messageEpochRef.current;
    const controller = new AbortController();
    messageRequestRef.current = { id: requestId, sid: requestSid, controller };
    const isCurrent = () => {
      const request = messageRequestRef.current;
      return Boolean(
        request
        && request.id === requestId
        && request.sid === requestSid
        && sidRef.current === requestSid
        && !controller.signal.aborted
      );
    };

    setChatPending(true);
    setManagerPhase('');
    setManagerStartedAt(Date.now());

    const dispatchTask = (result: Record<string, unknown>) => {
      if (!isCurrent()) return;
      const daemon = result.daemon && typeof result.daemon === 'object'
        ? result.daemon as Record<string, unknown>
        : null;
      if (daemon?.admission_required) {
        notify(
          'error',
          `Task queued, but all daemon slots are busy: ${String(daemon.error || 'operator action required')}`,
        );
      } else if (daemon && Number(daemon.rc ?? 0) !== 0) {
        notify('error', `Task queued, but executor did not start: ${String(daemon.error || 'unknown error')}`);
      } else if (daemon?.auto_parked_idle) {
        notify('success', `Started · parked idle session ${String(daemon.auto_parked_idle)}`);
      }
      snapQ.refetch?.();
    };

    let gotDelta = false;
    let streamErr: Error | null = null;
    try {
      try {
        await api.messageStream(requestSid, text, {
          onPhase: (label) => {
            if (isCurrent()) setManagerPhase(label);
          },
          onDelta: () => {
            if (!isCurrent()) return;
            gotDelta = true;
          },
          onDone: (result) => {
            if (!isCurrent()) return;
            if (result.kind === 'task') dispatchTask(result);
            void transcriptQ.refetch();
          },
          onError: (err) => {
            if (isCurrent()) streamErr = err;
          },
        }, controller.signal);
      } catch (error) {
        if (isCurrent()) streamErr = error as Error;
      }

      if (!isCurrent()) return;

      // Fallback to the blocking endpoint only if streaming produced nothing.
      if (streamErr && !gotDelta) {
        try {
          const result = await api.message(requestSid, text, controller.signal);
          if (!isCurrent()) return;
          if (result.kind === 'task') dispatchTask(result);
          void transcriptQ.refetch();
        } catch (error) {
          if (!isCurrent()) return;
          notify('error', `Message failed: ${errorText(error)}`);
        }
      }
    } finally {
      if (messageRequestRef.current?.id === requestId) {
        messageRequestRef.current = null;
        setChatPending(false);
        setManagerPhase('');
        setManagerStartedAt(0);
      }
    }
  };

  const answerPendingReply = async (text: string) => {
    if (!activeSid || !pendingReply || pendingReplyBusy) return;
    setPendingReplyBusy(true);
    try {
      const result = await api.answerPending(activeSid, pendingReply.id, text);
      setPendingReplyOpen(false);
      await snapQ.refetch();
      if (result.daemon && Number(result.daemon.rc ?? 0) !== 0) {
        notify(
          'error',
          `Answer queued, but the daemon did not start: ${result.daemon.error || 'operator action required'}`,
        );
      } else {
        notify('success', 'Answer sent directly to the blocked task.');
      }
    } catch (error) {
      notify('error', `Could not send answer: ${errorText(error)}`);
    } finally {
      setPendingReplyBusy(false);
    }
  };

  const paletteItems: PaletteItem[] = useMemo(() => {
    const nav: PaletteItem[] = [
      ...(kiosk ? [] : [{ id: 'new', label: 'New daemon', hint: '+', group: 'View', run: () => setNewDaemonOpen(true) }]),
      { id: 'doctor', label: 'Open Doctor', hint: '/doctor', group: 'View', run: () => setOverlay('doctor') },
      { id: 'config', label: 'Open Config', hint: '/config', group: 'View', run: () => setOverlay('config') },
      { id: 'identity', label: 'Open Identity', hint: '/identity', group: 'View', run: () => setOverlay('identity') },
      { id: 'transcript', label: 'Open Transcript', hint: '/transcript', group: 'View', run: () => setOverlay('transcript') },
      { id: 'inspector', label: 'Open Project', hint: 'work · memory · agents', group: 'View', run: () => setOverlay('inspector') },
      { id: 'operations', label: 'Open Operations', hint: 'backend controls', group: 'View', run: () => setOverlay('operations') },
      { id: 'help', label: 'Keyboard shortcuts', hint: '?', group: 'View', run: () => setOverlay('help') },
      {
        id: 'reasoning',
        label: showReasoning ? 'Hide reasoning' : 'Show reasoning',
        hint: '⌘O',
        group: 'View',
        run: () => setShowReasoning((v) => !v),
      },
      {
        id: 'kiosk',
        label: kiosk ? 'Exit kiosk mode' : 'Enter kiosk mode',
        hint: '⌘.',
        group: 'View',
        run: () => setKiosk((v) => !v),
      },
    ];
    const acts: PaletteItem[] = kiosk
      ? []
      : [
          { id: 'message', label: 'Message Argus…', hint: '/', group: 'Action', run: () => setComposerFocus((x) => x + 1) },
          ...(chatPending
            ? [{ id: 'cancel-message', label: 'Stop waiting for Manager reply', hint: 'Esc', group: 'Action', run: stopWaiting }]
            : []),
          ...(continuous
            ? [
                {
                  id: 'continuous',
                  label: continuous.enabled ? 'Stop continuous campaign' : 'Start continuous campaign',
                  group: 'Action',
                  run: toggleContinuous,
                },
              ]
            : []),
          snap?.daemon.alive
            ? { id: 'stop', label: 'Stop daemon', group: 'Action', run: requestStopDaemon }
            : { id: 'start', label: 'Start daemon', group: 'Action', run: requestStartDaemon },
        ];
    const proj: PaletteItem[] = projects.map((p) => ({
      id: `p-${p.id}`,
      label: p.label || p.id,
      hint: p.daemon_alive ? '● live' : '○',
      keywords: `${p.id} ${p.display_name ?? ''} ${p.objective} ${p.daemon_alive ? 'live running' : 'stopped idle'}`,
      group: 'Project',
      run: () => selectProject(p.id),
    }));
    return [...nav, ...acts, ...proj];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, snap?.daemon.alive, kiosk, showReasoning, continuous?.enabled, chatPending, stopWaiting]);

  return (
    <div ref={shellRef} className="workbench-shell ambient-canvas flex h-screen h-[100dvh] w-screen max-w-full overflow-hidden text-ink">
      {!kiosk && sidebarOpen ? (
        <button
          type="button"
          aria-label="Close sessions"
          onClick={() => setSidebarOpen(false)}
          className="fixed inset-0 z-30 bg-black/40 lg:hidden"
        />
      ) : null}
      {!kiosk ? (
        <Sidebar
          projects={projects}
          activeId={activeSid}
          localCwd={localCwd}
          onSelect={(id) => {
            selectProject(id);
            setSidebarOpen(false);
          }}
          onPrefetch={prefetchProject}
          onManage={requestManageSession}
          onOpenPanel={(panel) => setOverlay(panel)}
          onNew={() => setNewDaemonOpen(true)}
          loading={projectsQ.isLoading}
          creating={creatingDaemon}
          error={projectsQ.isError ? errorText(projectsQ.error) : undefined}
          onRetry={() => void projectsQ.refetch()}
          mobileOpen={sidebarOpen}
          collapsed={!leftPanelOpen}
          onToggleCollapse={() => setLeftPanelOpen((value) => !value)}
          themeMode={themeMode}
          onCycleTheme={cycleTheme}
          expandedWidth={leftWidth}
        />
      ) : null}
      {!kiosk && leftPanelOpen ? (
        <SplitHandle
          label="Resize sessions"
          value={leftWidth}
          min={220}
          max={400}
          onPointerDown={(event) => resizeSidebar('left', event)}
          onReset={() => setLeftWidth(256)}
          onNudge={(delta) => setLeftWidth((value) => Math.max(220, Math.min(400, value + delta)))}
        />
      ) : null}

      <main className="flex min-w-0 flex-1 overflow-x-hidden">
        {snap ? (
          <>
            <section className={`${mobileView === 'activity' ? 'flex' : 'hidden'} glass-panel glass-panel--main h-full min-w-0 flex-1 flex-col lg:flex`}>
              <TopBar
                snap={snap}
                streamOk={connected}
                onStart={requestStartDaemon}
                onStop={requestStopDaemon}
                onManage={() => setDaemonManageOpen(true)}
                onOpenSessions={() => setSidebarOpen(true)}
                mobileView={mobileView}
                onToggleMobileView={() => setMobileView('preview')}
                busy={daemonBusy}
                snapshotStale={snapQ.isError}
                readOnly={kiosk}
                missionView={missionView}
              />
              <div className="flex h-10 shrink-0 items-center gap-1 border-b border-line/60 px-3">
                <div className="workspace-tabs" data-active={workspaceView}>
                  <span className="workspace-tab-indicator" aria-hidden="true" />
                  <button type="button" onClick={() => setWorkspaceView('mission')} className="workspace-tab" data-selected={workspaceView === 'mission'}>Mission</button>
                  <button type="button" onClick={() => setWorkspaceView('activity')} className="workspace-tab" data-selected={workspaceView === 'activity'}>Activity</button>
                </div>
                {workspaceView === 'mission' ? <span className="ml-auto hidden max-w-72 truncate text-[10px] text-ink-faint sm:block">{missionView?.active_role ? `${missionView.active_role} active` : 'mission overview'}</span> : <span className="ml-auto" />}
                {!kiosk ? <button type="button" onClick={() => setOverlay('operations')} className="rounded border border-line/60 px-2 py-1 text-[10px] text-ink-faint hover:border-blue/50 hover:text-blue">Operations</button> : null}
              </div>
              <GuardianBanner alert={guardianAlert} />
              {workspaceView === 'mission' && missionView ? (
                <MissionControl view={missionView} gitDiff={gitDiffQ.data} onOpenArtifact={setArtifactPath} />
              ) : (
                <EventStream
                  events={activityEvents}
                  connected={connected}
                  showReasoning={showReasoning}
                  onToggleReasoning={() => setShowReasoning((value) => !value)}
                  embedded
                />
              )}
              {!kiosk ? (
                <div className="shrink-0 px-4 pb-6 pt-3">
                  <div className="mx-auto w-full max-w-full lg:max-w-[61.8vw]">
                  <PendingBanner
                    questions={snap.pending_questions ?? []}
                    backlog={snap.backlog}
                    onAnswer={() => setPendingReplyOpen(true)}
                  />
                  <ChatBox
                    onSend={sendMessage}
                    onCancel={stopWaiting}
                    disabled={!activeSid}
                    pending={chatPending}
                    focusSignal={composerFocus}
                    embedded
                    phase={managerPhase}
                    startedAt={managerStartedAt}
                  />
                  </div>
                </div>
              ) : null}
            </section>
            {rightPanelOpen ? (
              <SplitHandle
                label="Resize preview"
                value={rightWidth}
                min={320}
                max={600}
                onPointerDown={(event) => resizeSidebar('right', event)}
                onReset={() => setRightWidth(440)}
                onNudge={(delta) => setRightWidth((value) => Math.max(320, Math.min(600, value - delta)))}
              />
            ) : null}

            <aside
              style={{ '--preview-width': `${rightWidth}px` } as React.CSSProperties}
              className={`${mobileView === 'preview' ? 'flex' : 'hidden'} relative min-w-0 flex-1 flex-col overflow-hidden border-l border-line/60 bg-panel transition-[width] duration-[250ms] ease-panel lg:flex lg:flex-none ${
              rightPanelOpen ? 'lg:w-[var(--preview-width)]' : 'lg:w-14'
            }`}>
              <div className="lg:hidden">
                <TopBar
                  snap={snap}
                  streamOk={connected}
                  onStart={requestStartDaemon}
                  onStop={requestStopDaemon}
                  onManage={() => setDaemonManageOpen(true)}
                  onOpenSessions={() => setSidebarOpen(true)}
                  mobileView={mobileView}
                  onToggleMobileView={() => setMobileView('activity')}
                  busy={daemonBusy}
                  snapshotStale={snapQ.isError}
                  readOnly={kiosk}
                  missionView={missionView}
                />
              </div>
              <ResearchCanvas
                sid={loadedSid}
                artifacts={artifactsQ.data}
                error={artifactsQ.isError}
                onExpand={setArtifactPath}
                className={`min-h-0 flex-1 ${rightPanelOpen ? 'lg:flex' : 'lg:hidden'}`}
                embedded
                onCollapse={() => setRightPanelOpen(false)}
              />
              {!rightPanelOpen ? (
                <div className="hidden h-12 items-center justify-center border-b border-line/50 text-ink-faint lg:flex">
                  <button type="button" onClick={() => setRightPanelOpen(true)} aria-label="Expand preview" title="Expand preview" className="flex h-8 w-8 items-center justify-center rounded-md border border-line/50 bg-bg/40 hover:border-blue/50 hover:text-ink">
                    <FontAwesomeIcon icon={faAnglesLeft} className="h-3.5 w-3.5" />
                  </button>
                </div>
              ) : null}
            </aside>
          </>
        ) : (
          <Landing
            loading={projectsQ.isLoading || Boolean(activeSid && snapQ.isLoading)}
            hasProjects={projects.length > 0}
            error={
              projectsQ.isError && projects.length === 0
                ? errorText(projectsQ.error)
                : snapQ.isError && !snap
                ? errorText(snapQ.error)
                : undefined
            }
            onRetry={() => {
              void projectsQ.refetch();
              if (activeSid) void snapQ.refetch();
            }}
            onNew={() => setNewDaemonOpen(true)}
            onChoose={() => setSidebarOpen(true)}
            canCreate={!kiosk}
          />
        )}
      </main>

      {/* global overlays */}
      <CommandPalette open={overlay === 'palette'} onClose={() => setOverlay('none')} items={paletteItems} />
      <KeybindingHelp open={overlay === 'help'} onClose={() => setOverlay('none')} />
      {activeSid && <DoctorModal sid={activeSid} open={overlay === 'doctor'} onClose={() => setOverlay('none')} />}
      {activeSid && <ConfigModal sid={activeSid} open={overlay === 'config'} onClose={() => setOverlay('none')} />}
      {activeSid && <IdentityModal sid={activeSid} open={overlay === 'identity'} onClose={() => setOverlay('none')} />}
      {activeSid && <TranscriptModal sid={activeSid} open={overlay === 'transcript'} onClose={() => setOverlay('none')} />}
      {activeSid && snap ? (
        <ProjectInspectorModal
          open={overlay === 'inspector'}
          snap={snap}
          journal={journalQ.data ?? []}
          busy={actions.disposeBacklog.isPending || actions.stopBacklog.isPending}
          onClose={() => setOverlay('none')}
          onDispose={requestDispose}
          onStop={requestStopIteration}
          onInspect={setTaskItemId}
        />
      ) : null}
      {activeSid && snap ? (
        <OperationsModal
          open={overlay === 'operations'}
          sid={activeSid}
          snap={snap}
          onClose={() => setOverlay('none')}
          onChanged={() => {
            void snapQ.refetch();
            void projectsQ.refetch();
          }}
          onRestored={async (restoredSid) => {
            await projectsQ.refetch();
            selectProject(restoredSid);
          }}
        />
      ) : null}
      <ArtifactModal sid={activeSid} path={artifactPath} onClose={() => setArtifactPath(null)} />
      <TaskDetailModal
        sid={activeSid}
        itemId={taskItemId}
        onClose={() => setTaskItemId(null)}
        onDone={(id) => requestDispose(id, 'done')}
        onSkip={(id) => requestDispose(id, 'rm')}
        onStop={requestStopIteration}
        busy={actions.disposeBacklog.isPending || actions.stopBacklog.isPending}
        readOnly={kiosk}
      />
      <NewDaemonModal
        open={newDaemonOpen}
        busy={creatingDaemon}
        onClose={() => setNewDaemonOpen(false)}
        onCreate={createDaemon}
      />
      <PendingReplyDialog
        reply={pendingReply}
        open={pendingReplyOpen}
        busy={pendingReplyBusy}
        onClose={() => setPendingReplyOpen(false)}
        onSubmit={answerPendingReply}
      />
      {activeSid && snap ? (
        <DaemonManageModal
          open={daemonManageOpen}
          sid={activeSid}
          name={snap.session.display_name || ''}
          alive={snap.daemon.alive}
          busy={daemonBusy}
          onClose={() => setDaemonManageOpen(false)}
          onRename={manageRenameProject}
          onStart={manageStartDaemon}
          onPause={managePauseDaemon}
          onDelete={manageDeleteProject}
        />
      ) : null}
      <ActionNotice notice={notice} onClose={dismissNotice} />
    </div>
  );
}
