import { lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { artifactRefreshEventKey, snapshotRefreshEventKey, useProjects, useProjectCosts, useSnapshot, useEventStream, useProjectActions, useArtifacts, useTranscript, useJournal, useGitDiff, useConfig } from './hooks';
import { api, isConnectionError, type EventMsg, type MessageRouteOverride } from './api';
import { TopBar } from './components/TopBar';
import { EventStream } from './components/EventStream';
import { ChatBox } from './components/ChatBox';
import {
  appendPhaseStep,
  closePhaseTrail,
  type PhaseStep,
} from '../../core/src/phaseTrail';
import { CommandPalette, commandPaletteRows, type PaletteItem } from './components/CommandPalette';
import { KeybindingHelp } from './components/KeybindingHelp';
import { DoctorModal, ConfigModal, IdentityModal, TranscriptModal } from './components/InfoModals';
import { PendingBanner } from './components/PendingBanner';
import { PendingReplyDialog } from './components/PendingReplyDialog';
import { GuardianBanner } from './components/GuardianBanner';
import { rankProjects } from '../../core/src/projects';
import { ArtifactModal } from './components/ArtifactModal';
import {
  missionIsComplete,
  ResearchCanvas,
  selectCompletionArtifact,
} from './components/ResearchCanvas';
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
import { Landing } from './components/Landing';
import { MobileTabBar } from './components/MobileTabBar';
import { useVisualViewport } from './useVisualViewport';
import { activeGuardianAlert } from './lib/guardian';
import { projectMissionView } from '../../core/src/missionView';
import { useQueryClient } from '@tanstack/react-query';
import { dispatchWebCommand, type WebCommandHandlers } from './lib/webCommands';
import { buildWebCommandHandlers } from './lib/commandHandlers';
import { finishManagerMessage } from './lib/messageResult';
import { COMMANDS } from '../../core/src/commands';
import { type EventViewFilter } from '../../core/src/events';
import { eventViewReducer, initialEventViewState } from './lib/eventView';
import {
  mergeConversationEvents,
  mergeOptimisticManagerDelta,
  optimisticOperatorEvent,
} from './lib/conversationEvents';
import { mergeProjectCosts } from './lib/projectCosts';
import { errorText, managerStreamFailureMessage } from './lib/format';
import { useCreateDaemonSession } from './useCreateDaemonSession';
import { useProjectDaemonActions } from './useProjectDaemonActions';
import { useGlobalKeyboardShortcuts } from './useGlobalKeyboardShortcuts';
import { usePendingReplySession } from './usePendingReplySession';
import { useProjectSelection } from './useProjectSelection';
import { useWorkbenchLayout } from './useWorkbenchLayout';
import { useI18n } from './i18n';
import { ConnectionProblemBanner } from './components/ConnectionProblemBanner';
import { DeliveryNotice } from './components/DeliveryNotice';
import type { ArtifactInfo, DeliveryReceipt, MissionView } from '../../core/src/types';
import { configuredBackend, configuredModel } from './lib/backend';
import {
  completionNotificationPayload,
  installDesktopExternalLinkBridge,
  notifyDesktopCompletion,
  subscribeDesktopDelivery,
  subscribeDesktopNewChat,
} from './lib/desktopBridge';

type Overlay = 'none' | 'palette' | 'help' | 'doctor' | 'config' | 'identity' | 'transcript' | 'inspector' | 'operations';
interface ActiveMessageRequest {
  id: number;
  sid: string;
  controller: AbortController;
}
interface CompletionContext {
  sid: string;
  completionId: string;
  view: MissionView | null;
  artifacts: ArtifactInfo[];
}
let noticeSequence = 0;
const MESSAGE_ROUTE_KEY = 'argus.message.route.v1';

function initialMessageRoute(): MessageRouteOverride {
  try {
    const stored = localStorage.getItem(MESSAGE_ROUTE_KEY);
    if (stored === 'auto' || stored === 'chat' || stored === 'task') return stored;
  } catch {
    // Storage is optional; the context-sensitive default below remains safe.
  }
  // Like Codex, Desktop treats the primary composer as work-first. Browser/PWA
  // keeps remote Auto behavior, and the visible selector can change either.
  return typeof window !== 'undefined' && window.parent !== window ? 'task' : 'auto';
}

const ResearchWorkbenchPanel = lazy(async () => {
  const module = await import('./research-workbench/ResearchWorkbenchPanel');
  return { default: module.ResearchWorkbenchPanel };
});

export default function App() {
  const { locale, t } = useI18n();
  const queryClient = useQueryClient();
  const projectsQ = useProjects();
  const projectCostsQ = useProjectCosts();
  const projects = useMemo(
    () => rankProjects(mergeProjectCosts(
      projectsQ.data?.projects ?? [],
      projectCostsQ.data?.projects ?? [],
    )),
    [projectCostsQ.data?.projects, projectsQ.data?.projects],
  );
  const localCwd = projectsQ.data?.local_cwd ?? '';
  const connectionError = [projectsQ.error, projectCostsQ.error]
    .find((error) => isConnectionError(error));

  const [overlay, setOverlay] = useState<Overlay>('none');
  const {
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
  } = useWorkbenchLayout();
  const [standardWorkspaceView, setStandardWorkspaceView] = useState<'mission' | 'activity'>(
    () => workspaceView === 'mission' ? 'mission' : 'activity',
  );
  const [workbenchOpened, setWorkbenchOpened] = useState(workspaceView === 'workbench');
  useEffect(() => {
    if (workspaceView === 'workbench') {
      setWorkbenchOpened(true);
      return;
    }
    setStandardWorkspaceView(workspaceView);
  }, [workspaceView]);
  // Publishes --keyboard-inset so the composer clears the software keyboard.
  useVisualViewport();
  const [composerFocus, setComposerFocus] = useState(0);
  const [composerDraft, setComposerDraft] = useState('');
  const [rewriting, setRewriting] = useState(false);
  const [slashSelection, setSlashSelection] = useState(0);
  const [routeOverride, setRouteOverride] = useState<MessageRouteOverride>(initialMessageRoute);
  const [chatPending, setChatPending] = useState(false);
  const [localConversationEvents, setLocalConversationEvents] = useState<EventMsg[]>([]);
  const [managerSteps, setManagerSteps] = useState<PhaseStep[]>([]);
  const [artifactPath, setArtifactPath] = useState<string | null>(null);
  const [previewPathRequest, setPreviewPathRequest] = useState({ path: '', token: 0 });
  const [dismissedDeliveryId, setDismissedDeliveryId] = useState('');
  const [taskItemId, setTaskItemId] = useState<string | null>(null);
  const [newDaemonOpen, setNewDaemonOpen] = useState(false);
  const [daemonManageOpen, setDaemonManageOpen] = useState(false);
  const [resumingSid, setResumingSid] = useState<string | null>(null);
  const messageSubmitLockRef = useRef(false);
  const messageRequestRef = useRef<ActiveMessageRequest | null>(null);
  const messageEpochRef = useRef(0);
  const observedDeliveryRef = useRef<string | null | undefined>(undefined);
  const observedCompletionRef = useRef<{ sid: string; id: string | null }>();
  const completionContextRef = useRef<CompletionContext>({
    sid: '',
    completionId: '',
    view: null,
    artifacts: [],
  });
  const deliveryRef = useRef<DeliveryReceipt | null>(null);
  const [notice, setNotice] = useState<UiNotice | null>(null);
  const [eventView, dispatchEventView] = useReducer(eventViewReducer, initialEventViewState);
  const [eventFilter, setEventFilter] = useState<EventViewFilter>('all');
  const [eventQuery, setEventQuery] = useState('');
  const dismissNotice = useCallback(() => setNotice(null), []);
  useEffect(() => {
    try {
      localStorage.setItem(MESSAGE_ROUTE_KEY, routeOverride);
    } catch {
      // A blocked storage area does not affect the current in-memory choice.
    }
  }, [routeOverride]);
  const notify = useCallback((tone: NoticeTone, message: string) => {
    setNotice({ id: ++noticeSequence, tone, message });
  }, []);

  const cancelActiveMessage = useCallback(() => {
    const cancelled = Boolean(messageRequestRef.current);
    messageEpochRef.current += 1;
    messageRequestRef.current?.controller.abort();
    messageRequestRef.current = null;
    setChatPending(false);
    setManagerSteps([]);
    return cancelled;
  }, []);

  const stopWaiting = useCallback(() => {
    if (!cancelActiveMessage()) return;
    notify('info', 'Stopped waiting for this reply. Server-side work may still finish in the project timeline.');
  }, [cancelActiveMessage, notify]);
  const {
    activeSid,
    clearProjectSelection,
    prefetchProject,
    selectProject,
    sidRef,
  } = useProjectSelection({
    cancelActiveMessage,
    notify,
    projects,
    projectsError: projectsQ.isError,
    projectsReady: projectsQ.isSuccess,
    queryClient,
    setArtifactPath,
    setSidebarOpen,
    setTaskItemId,
  });

  useEffect(() => () => {
    messageEpochRef.current += 1;
    messageRequestRef.current?.controller.abort();
    messageRequestRef.current = null;
  }, []);
  useEffect(() => installDesktopExternalLinkBridge(), []);

  /**
   * Let the Manager restate a short draft before it is sent.
   *
   * The rewrite replaces the composer draft — the operator always reads and
   * edits it before anything is dispatched. The Manager may propose metrics or
   * constraints the operator never mentioned, but only as questions surfaced
   * here — never silently inside the rewritten text. A failed rewrite leaves
   * the original untouched.
   */
  const rewriteDraft = useCallback((draft: string) => {
    const body = (draft || '').trim();
    const sid = sidRef.current;
    if (!body || !sid || rewriting) return;
    setRewriting(true);
    void api.rewritePrompt(sid, body).then(
      (result) => {
        setRewriting(false);
        if (result.error || !result.rewritten.trim()) {
          notify('error', `Rewrite failed: ${result.error || 'empty rewrite'} — your prompt is unchanged`);
          return;
        }
        setComposerDraft(result.rewritten);
        setComposerFocus((x) => x + 1);
        const open = result.questions.length
          ? ` Manager asks: ${result.questions.join(' · ')}`
          : '';
        notify('success', `Prompt rewritten — review it, then send.${open}`);
      },
      (error) => {
        setRewriting(false);
        notify('error', `Rewrite failed: ${errorText(error)} — your prompt is unchanged`);
      },
    );
  }, [notify, rewriting, sidRef]);


  const { createDaemon, creatingDaemon } = useCreateDaemonSession({
    localCwd,
    notify,
    onFocusComposer: () => setComposerFocus((value) => value + 1),
    queryClient,
    refetchProjects: projectsQ.refetch,
    selectProject,
  });
  useEffect(() => subscribeDesktopNewChat(() => setNewDaemonOpen(true)), []);


  const snapQ = useSnapshot(activeSid);
  const activeConfigQ = useConfig(activeSid, Boolean(activeSid));
  const snap = snapQ.data;
  const loadedSid = snap?.session.id === activeSid ? activeSid : null;
  const continuous = snap?.continuous;
  const artifactsQ = useArtifacts(loadedSid, true);
  const gitDiffQ = useGitDiff(loadedSid, standardWorkspaceView === 'mission');
  const { events, connected } = useEventStream(loadedSid, eventView.reconnectKey);
  const artifactRefreshKey = useMemo(() => artifactRefreshEventKey(events), [events]);
  const snapshotRefreshKey = useMemo(() => snapshotRefreshEventKey(events), [events]);
  useEffect(() => {
    if (!loadedSid || !artifactRefreshKey) return;
    // One tool turn can write several files back-to-back. Coalesce those
    // events into one artifact read instead of making JSON/list reconciliation
    // compete with typing and WebView painting.
    const timer = window.setTimeout(() => {
      void queryClient.invalidateQueries({
        queryKey: ['artifacts', loadedSid],
        exact: true,
      });
    }, 180);
    return () => window.clearTimeout(timer);
  }, [artifactRefreshKey, loadedSid, queryClient]);
  useEffect(() => {
    if (!loadedSid || !snapshotRefreshKey) return;
    const timer = window.setTimeout(() => {
      void queryClient.invalidateQueries({
        queryKey: ['snapshot', loadedSid],
        exact: true,
      });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [loadedSid, queryClient, snapshotRefreshKey]);
  const guardianAlert = useMemo(() => activeGuardianAlert(events), [events]);
  const transcriptQ = useTranscript(loadedSid, standardWorkspaceView === 'activity', 120);
  const journalQ = useJournal(activeSid, 20, overlay === 'inspector');
  const {
    answerPendingReply,
    pendingReply,
    pendingReplyBusy,
    pendingReplyOpen,
    setPendingReplyOpen,
  } = usePendingReplySession({
    activeSid,
    backlog: snap?.backlog,
    notify,
    pendingQuestions: snap?.pending_questions,
    refetchSnapshot: snapQ.refetch,
  });
  const activityEvents = useMemo(() => {
    return mergeConversationEvents(
      events,
      transcriptQ.data ?? [],
      localConversationEvents,
    );
  }, [events, localConversationEvents, transcriptQ.data]);
  const missionView = useMemo(
    () => snap ? projectMissionView(snap, activityEvents, artifactsQ.data ?? []) : null,
    [activityEvents, artifactsQ.data, snap],
  );
  const delivery = missionView?.delivery ?? null;
  const hasUnfinishedWork = snap?.backlog.some((item) =>
    ['pending', 'running', 'in_progress', 'claimed'].includes(item.status),
  ) ?? false;
  const operatorTaskComplete = missionIsComplete(missionView)
    && !hasUnfinishedWork
    && !snap?.continuous?.enabled;
  const completionId = loadedSid && operatorTaskComplete && missionView?.mission.id
    ? `completion:${loadedSid}:${missionView.mission.id}`
    : '';
  deliveryRef.current = delivery;
  completionContextRef.current = {
    sid: loadedSid || '',
    completionId,
    view: missionView,
    artifacts: artifactsQ.data ?? [],
  };
  const focusDeliveryPath = useCallback((path: string) => {
    const target = path.trim();
    if (!target) {
      setWorkspaceView('mission');
      return;
    }
    setRightPanelOpen(true);
    setMobileView('preview');
    setPreviewPathRequest((current) => ({ path: target, token: current.token + 1 }));
  }, [setMobileView, setRightPanelOpen, setWorkspaceView]);
  const openDelivery = useCallback((receipt: DeliveryReceipt) => {
    focusDeliveryPath(receipt.primary_target?.path ?? '');
  }, [focusDeliveryPath]);
  useEffect(() => {
    if (!snap) return;
    const id = delivery?.delivery_id || null;
    const previous = observedDeliveryRef.current;
    if (previous === undefined) {
      observedDeliveryRef.current = id;
      if (delivery) openDelivery(delivery);
      return;
    }
    if (previous === id) return;
    observedDeliveryRef.current = id;
    setDismissedDeliveryId('');
    if (delivery) openDelivery(delivery);
  }, [delivery, openDelivery, snap]);
  useEffect(() => {
    if (!loadedSid) return;
    const previous = observedCompletionRef.current;
    if (!previous || previous.sid !== loadedSid) {
      // Opening an already-completed project must not replay an old OS toast.
      observedCompletionRef.current = { sid: loadedSid, id: completionId || null };
      return;
    }
    if (!completionId) {
      observedCompletionRef.current = { sid: loadedSid, id: null };
      return;
    }
    if (previous.id === completionId) return;

    // Let the completion-triggered artifact refresh land so the native toast's
    // single action can open the best delivered file immediately.
    const timer = window.setTimeout(() => {
      const current = completionContextRef.current;
      if (current.sid !== loadedSid || current.completionId !== completionId || !current.view) return;
      const receipt = current.view.delivery;
      const artifact = selectCompletionArtifact(current.artifacts);
      const payload = completionNotificationPayload({
        completionId,
        title: receipt?.title || current.view.mission.title || t('mission.taskCompleted'),
        summary: receipt?.summary || current.view.mission.summary,
        path: receipt?.primary_target?.path || artifact?.path,
      });
      if (payload) void notifyDesktopCompletion(payload);
      observedCompletionRef.current = { sid: loadedSid, id: completionId };
    }, 500);
    return () => window.clearTimeout(timer);
  }, [completionId, loadedSid, t]);
  useEffect(() => subscribeDesktopDelivery((payload) => {
    const current = deliveryRef.current;
    if (current && current.delivery_id === payload.deliveryId) {
      openDelivery(current);
    } else if (payload.path) {
      focusDeliveryPath(payload.path);
    } else {
      setMobileView('activity');
      setWorkspaceView('mission');
    }
  }), [focusDeliveryPath, openDelivery, setMobileView, setWorkspaceView]);
  // Keep a ref so the /clear handler can read the current length without being
  // listed as a reactive dependency of commandHandlers.
  const activityEventsRef = useRef(activityEvents);
  activityEventsRef.current = activityEvents;
  // Reset event view state (filter, query, clear mark) when the active project changes.
  useEffect(() => {
    setEventFilter('all');
    setEventQuery('');
    setLocalConversationEvents([]);
    dispatchEventView({ kind: 'reset' });
  }, [loadedSid]);
  const actions = useProjectActions(activeSid, snap?.daemon_commands?.revision);
  const resumeSession = useCallback(async (sid: string) => {
    setResumingSid(sid);
    try {
      await api.startDaemon(sid);
      await projectsQ.refetch();
    } catch (error) {
      notify('error', errorText(error));
    } finally {
      setResumingSid(null);
    }
  }, [notify, projectsQ]);
  const {
    daemonBusy,
    manageDeleteProject,
    manageStopDaemon,
    manageRenameProject,
    manageStartDaemon,
    requestDispose,
    requestManageSession,
    requestStartDaemon,
    requestStopDaemon,
    requestStopIteration,
    toggleContinuous,
  } = useProjectDaemonActions({
    actions,
    activeSid,
    clearProjectSelection,
    continuous,
    currentSnapshotSid: snap?.session.id,
    notify,
    refetchProjects: projectsQ.refetch,
    selectProject,
    setDaemonManageOpen,
  });
  const renameCurrentProject = useCallback(async (name: string) => {
    if (!activeSid) return;
    const result = await actions.updateProject.mutateAsync({ sid: activeSid, name });
    notify('success', `Renamed to "${result.name}".`);
  }, [actions.updateProject, activeSid, notify]);
  const commandHandlers = useMemo<WebCommandHandlers>(() => buildWebCommandHandlers({
    activeSid,
    activityEventsRef,
    notify,
    onClearEvents: (offset) => dispatchEventView({ kind: 'clear', offset }),
    onDispose: requestDispose,
    onOpenConfig: () => setOverlay('config'),
    onOpenDoctor: () => setOverlay('doctor'),
    onOpenHelp: () => setOverlay('help'),
    onOpenIdentity: () => setOverlay('identity'),
    onOpenInspector: () => setOverlay('inspector'),
    onOpenNewDaemon: () => setNewDaemonOpen(true),
    onOpenOperations: () => setOverlay('operations'),
    onOpenSidebar: () => setSidebarOpen(true),
    onReconnectEvents: () => dispatchEventView({ kind: 'reconnect' }),
    onRenameProject: renameCurrentProject,
    onRewriteDraft: rewriteDraft,
    onSelectProject: selectProject,
    onSetArtifactPath: setArtifactPath,
    onSetEventFilter: setEventFilter,
    onSetEventQuery: setEventQuery,
    onSetTaskItemId: setTaskItemId,
    onSetWorkspaceView: setWorkspaceView,
    onShowArtifacts: () => setRightPanelOpen(true),
    onStopIteration: requestStopIteration,
    onStopWaiting: stopWaiting,
    refetchSnapshot: snapQ.refetch,
  }), [
    activeSid,
    notify,
    renameCurrentProject,
    requestDispose,
    requestStopIteration,
    selectProject,
    snapQ.refetch,
    stopWaiting,
    setWorkspaceView,
  ]);
  useGlobalKeyboardShortcuts({
    focusComposer: () => setComposerFocus((value) => value + 1),
    openHelp: () => setOverlay('help'),
    toggleKiosk: () => setKiosk((value) => !value),
    togglePalette: () => setOverlay((current) => current === 'palette' ? 'none' : 'palette'),
    toggleReasoning: () => setShowReasoning((value) => !value),
    toggleSidebarCollapse: () => setLeftPanelOpen((value) => !value),
  });

  const sendMessage = async (text: string, attachments: File[] = []): Promise<boolean> => {
    const requestSid = activeSid;
    if (!requestSid || messageSubmitLockRef.current || messageRequestRef.current) return false;

    messageSubmitLockRef.current = true;
    let requestId: number;
    let controller: AbortController;
    try {
      if (!attachments.length) {
        const command = await dispatchWebCommand(text, commandHandlers);
        if (command.kind === 'handled') return true;
        if (command.kind === 'error') {
          notify('error', command.message);
          return false;
        }
      }

      requestId = ++messageEpochRef.current;
      controller = new AbortController();
      messageRequestRef.current = { id: requestId, sid: requestSid, controller };
    } finally {
      messageSubmitLockRef.current = false;
    }

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
    const resetCurrentRequest = () => {
      if (messageRequestRef.current?.id === requestId) {
        messageRequestRef.current = null;
        setChatPending(false);
        setManagerSteps([]);
      }
    };

    setChatPending(true);
    setManagerSteps([]);
    let attachmentRefs: Array<{ attachment_id: string }> = [];
    if (attachments.length) {
      try {
        const uploaded = await api.uploadAttachments(
          requestSid,
          attachments,
          controller.signal,
        );
        if (!isCurrent()) return false;
        attachmentRefs = uploaded.attachments.map((attachment) => ({
          attachment_id: attachment.attachment_id,
        }));
      } catch (error) {
        if (isCurrent()) {
          notify('error', t('chat.attachmentUploadFailed', { error: errorText(error) }));
          resetCurrentRequest();
        }
        return false;
      }
    }
    setLocalConversationEvents((current) => [
      ...current,
      optimisticOperatorEvent(requestSid, requestId, text),
    ]);

    const showManagerText = (
      reply: unknown,
      messageId = '',
      fragmentMode: 'append' | 'snapshot' | 'auto' = 'auto',
    ) => {
      if (!isCurrent() || typeof reply !== 'string' || !reply.trim()) return;
      setLocalConversationEvents((current) => mergeOptimisticManagerDelta(
        current,
        requestSid,
        requestId,
        reply,
        messageId,
        Date.now(),
        fragmentMode,
      ));
    };

    const dispatchTask = (result: Record<string, unknown>) => {
      if (!isCurrent()) return;
      const daemon = result.daemon && typeof result.daemon === 'object'
        ? result.daemon as Record<string, unknown>
        : null;
      // Use result.reply (persisted by backend) as a non-durable accessibility
      // notice — the conversation event is already in the transcript refetch.
      const reply = typeof result.reply === 'string' ? result.reply : null;
      if (daemon?.admission_required) {
        notify(
          'error',
          reply || `Task queued, but all daemon slots are busy: ${String(daemon.error || 'operator action required')}`,
        );
      } else if (daemon && Number(daemon.rc ?? 0) !== 0) {
        notify('error', reply || `Task queued, but executor did not start: ${String(daemon.error || 'unknown error')}`);
      } else if (reply) {
        notify('success', reply);
      }
      snapQ.refetch?.();
    };

    const finishMessage = (result: Record<string, unknown>) => {
      if (!isCurrent()) return;
      finishManagerMessage(result, {
        dispatchTask,
        notifyError: (error) => notify('error', error),
        refetchTranscript: () => {
          void transcriptQ.refetch();
        },
      });
    };

    // Dispatch the streaming work fire-and-forget so the draft clears immediately.
    // Errors that surface during the stream are surfaced via notify().
    void (async () => {
      let gotDelta = false;
      let streamErr: Error | null = null;
      // Append-only record of the real steps in this turn (see phaseTrail.ts).
      // Kept in a local rather than state because React batches setManagerSteps
      // and a reply block can land in the same tick as the phase before it.
      let trail: PhaseStep[] = [];
      try {
        try {
          await api.messageStream(requestSid, text, {
            onPhase: (label, role, meta) => {
              if (!isCurrent() || meta.heartbeat) return;
              trail = appendPhaseStep(trail, {
                label,
                role,
                kind: meta.kind,
                detail: meta.detail,
                heartbeat: meta.heartbeat,
                quietS: meta.quietS,
              });
              setManagerSteps(trail);
            },
            onDelta: (block, messageId, fragmentMode) => {
              if (!isCurrent()) return;
              gotDelta = true;
              trail = closePhaseTrail(trail);
              setManagerSteps(trail);
              showManagerText(
                block,
                messageId,
                fragmentMode === 'append' || fragmentMode === 'snapshot'
                  ? fragmentMode
                  : 'auto',
              );
            },
            onDone: (result) => {
              if (!isCurrent()) return;
              showManagerText(result.reply, '', 'snapshot');
              finishMessage(result);
            },
            onError: (err) => {
              if (isCurrent()) streamErr = err;
            },
          }, {
            signal: controller.signal,
            attachments: attachmentRefs,
            routeOverride,
          });
        } catch (error) {
          if (isCurrent()) streamErr = error as Error;
        }

        if (!isCurrent()) return;

        if (streamErr) {
          // Retrying the POST automatically can execute a task twice when the
          // server accepted the first request but the SSE connection broke.
          // Keep any partial reply visible and let the operator choose retry.
          notify('error', managerStreamFailureMessage(streamErr, gotDelta));
        }
      } finally {
        resetCurrentRequest();
      }
    })();

    return true; // draft clears immediately on dispatch, not when stream finishes
  };

  // Stable ref so commandPaletteRows closures always call the latest sendMessage
  // without re-creating all 34 command items on every render.
  const sendMessageRef = useRef(sendMessage);
  sendMessageRef.current = sendMessage;

  const paletteItems: PaletteItem[] = useMemo(() => {
    const commandRows = commandPaletteRows(
      COMMANDS,
      (name) => { void sendMessageRef.current(name); },
      (text) => { setComposerDraft(text); setComposerFocus((x) => x + 1); },
      locale,
    );
    const nav: PaletteItem[] = [
      ...(kiosk ? [] : [{ id: 'new', label: t('palette.newDaemon'), hint: '+', group: t('palette.view'), run: () => setNewDaemonOpen(true) }]),
      { id: 'transcript', label: t('palette.openTranscript'), hint: '/transcript', group: t('palette.view'), run: () => setOverlay('transcript') },
      { id: 'inspector', label: t('palette.openProject'), hint: t('palette.projectHint'), group: t('palette.view'), run: () => setOverlay('inspector') },
      { id: 'operations', label: t('palette.openOperations'), hint: t('palette.operationsHint'), group: t('palette.view'), run: () => setOverlay('operations') },
      { id: 'help', label: t('help.title'), hint: '?', group: t('palette.view'), run: () => setOverlay('help') },
      {
        id: 'reasoning',
        label: showReasoning ? t('palette.hideReasoning') : t('palette.showReasoning'),
        hint: '⌘T',
        group: t('palette.view'),
        run: () => setShowReasoning((v) => !v),
      },
      {
        id: 'kiosk',
        label: kiosk ? t('palette.exitKiosk') : t('palette.enterKiosk'),
        hint: '⌘.',
        group: t('palette.view'),
        run: () => setKiosk((v) => !v),
      },
    ];
    const acts: PaletteItem[] = kiosk
      ? []
      : [
          { id: 'message', label: t('palette.messageArgus'), hint: '/', group: t('palette.action'), run: () => setComposerFocus((x) => x + 1) },
          ...(chatPending
            ? [{ id: 'cancel-message', label: t('palette.stopWaiting'), hint: 'Esc', group: t('palette.action'), run: stopWaiting }]
            : []),
          ...(continuous
            ? [
                {
                  id: 'continuous',
                  label: continuous.enabled ? t('palette.stopContinuous') : t('palette.startContinuous'),
                  group: t('palette.action'),
                  run: toggleContinuous,
                },
              ]
            : []),
          ...(snap?.daemon.control_available === false
            ? []
            : [
                snap?.daemon.alive
                  ? { id: 'stop', label: t('palette.stopDaemon'), group: t('palette.action'), run: requestStopDaemon }
                  : { id: 'start', label: t('palette.startDaemon'), group: t('palette.action'), run: requestStartDaemon },
              ]),
        ];
    const proj: PaletteItem[] = projects.map((p) => ({
      id: `p-${p.id}`,
      label: p.label || p.id,
      hint: p.daemon_alive ? `● ${t('common.live')}` : '○',
      keywords: `${p.id} ${p.display_name ?? ''} ${p.objective} ${p.daemon_alive ? 'live running' : 'stopped idle'}`,
      group: t('palette.project'),
      run: () => selectProject(p.id),
    }));
    return [...nav, ...acts, ...commandRows, ...proj];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, snap?.daemon.alive, kiosk, showReasoning, continuous?.enabled, chatPending, stopWaiting, locale, t]);

  return (
    <div ref={shellRef} className="workbench-shell ambient-canvas flex h-screen h-[100dvh] w-screen max-w-full overflow-hidden text-ink">
      <ConnectionProblemBanner
        error={connectionError}
        onRetry={() => {
          void projectsQ.refetch();
          void projectCostsQ.refetch();
        }}
      />
      {delivery && delivery.delivery_id !== dismissedDeliveryId ? (
        <DeliveryNotice
          delivery={delivery}
          onOpen={openDelivery}
          onDismiss={setDismissedDeliveryId}
        />
      ) : null}
      {!kiosk && sidebarOpen ? (
        <button
          type="button"
          aria-label={t('common.closeSessions')}
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
          onResume={(sid) => void resumeSession(sid)}
          resumingId={resumingSid}
          activeBackend={configuredBackend(activeConfigQ.data)}
          activeModel={configuredModel(activeConfigQ.data)}
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
          label={t('common.resizeSessions')}
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
                busy={daemonBusy}
                snapshotStale={snapQ.isError}
                readOnly={kiosk}
                missionView={missionView}
              />
              <div className="flex h-10 shrink-0 items-center gap-1 border-b border-line/60 px-3">
                <div className="workspace-tabs" data-active={workspaceView}>
                  <span className="workspace-tab-indicator" aria-hidden="true" />
                  <button type="button" onClick={() => setWorkspaceView('mission')} className="workspace-tab" data-selected={workspaceView === 'mission'}>{t('mobile.mission')}</button>
                  <button type="button" onClick={() => setWorkspaceView('activity')} className="workspace-tab" data-selected={workspaceView === 'activity'}>{t('mobile.activity')}</button>
                  <button type="button" onClick={() => setWorkspaceView('workbench')} className="workspace-tab" data-selected={workspaceView === 'workbench'}>{t('mobile.workbench')}</button>
                </div>
                {workspaceView === 'mission' ? <span className="ml-auto hidden max-w-72 truncate text-[10px] text-ink-faint sm:block">{missionView?.active_role ? t('mission.roleActive', { role: missionView.active_role }) : t('mission.overview')}</span> : <span className="ml-auto" />}
                {!kiosk ? <button type="button" onClick={() => setOverlay('operations')} className="rounded border border-line/60 px-2 py-1 text-[10px] text-ink-faint hover:border-blue/50 hover:text-blue">{t('mission.operations')}</button> : null}
              </div>
              <div className={`${workspaceView === 'workbench' ? 'hidden' : 'flex'} min-h-0 flex-1 flex-col`}>
                <GuardianBanner alert={guardianAlert} />
                {standardWorkspaceView === 'mission' && missionView ? (
                  <MissionControl
                    view={missionView}
                    sid={snap.session.id}
                    snapshot={snap}
                    gitDiff={gitDiffQ.data}
                    artifacts={artifactsQ.data}
                    onOpenArtifact={focusDeliveryPath}
                    onOpenDelivery={openDelivery}
                  />
                ) : (
                  <EventStream
                    events={activityEvents}
                    connected={connected}
                    showReasoning={showReasoning}
                    onToggleReasoning={() => setShowReasoning((value) => !value)}
                    embedded
                    filter={eventFilter}
                    query={eventQuery}
                    skipFirst={eventView.skipFirst}
                    artifacts={artifactsQ.data}
                    onOpenArtifact={focusDeliveryPath}
                    onOpenDelivery={openDelivery}
                  />
                )}
                {!kiosk ? (
                  <div className="composer-dock shrink-0 px-4 pt-3">
                    <div className="mx-auto w-full max-w-full lg:max-w-[61.8vw]">
                    <PendingBanner
                      questions={snap.pending_questions ?? []}
                      backlog={snap.backlog}
                      onAnswer={() => setPendingReplyOpen(true)}
                    />
                    <ChatBox
                      key={activeSid || 'no-session'}
                      value={composerDraft}
                      onChange={setComposerDraft}
                      onSend={sendMessage}
                      onCancel={stopWaiting}
                      disabled={!activeSid}
                      pending={chatPending}
                      focusSignal={composerFocus}
                      embedded
                      steps={managerSteps}
                      onRewrite={rewriteDraft}
                      rewriting={rewriting}
                      slashSelection={slashSelection}
                      onSlashSelectionChange={setSlashSelection}
                      routeOverride={routeOverride}
                      onRouteOverrideChange={setRouteOverride}
                    />
                    </div>
                  </div>
                ) : null}
              </div>
              {workbenchOpened && activeSid ? (
                <div className={`${workspaceView === 'workbench' ? 'flex' : 'hidden'} min-h-0 flex-1`}>
                  <Suspense fallback={<div className="flex min-h-0 flex-1 items-center justify-center text-xs text-ink-faint">{t('common.loading')}</div>}>
                    <ResearchWorkbenchPanel sid={activeSid} active={workspaceView === 'workbench'} />
                  </Suspense>
                </div>
              ) : null}
            </section>
            {rightPanelOpen ? (
              <SplitHandle
                label={t('common.resizePreview')}
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
                className={`min-h-0 flex-1 mobile-scroll-region ${rightPanelOpen ? 'lg:flex' : 'lg:hidden'}`}
                embedded
                onCollapse={() => setRightPanelOpen(false)}
                missionView={missionView}
                activityEvents={activityEvents}
                requestedPath={previewPathRequest.path}
                requestedPathToken={previewPathRequest.token}
              />
              {!rightPanelOpen ? (
                <div className="hidden h-12 items-center justify-center border-b border-line/50 text-ink-faint lg:flex">
                  <button type="button" onClick={() => setRightPanelOpen(true)} aria-label={t('common.expandPreview')} title={t('common.expandPreview')} className="flex h-8 w-8 items-center justify-center rounded-md border border-line/50 bg-bg/40 hover:border-blue/50 hover:text-ink">
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
          controlAvailable={snap.daemon.control_available !== false}
          busy={daemonBusy}
          onClose={() => setDaemonManageOpen(false)}
          onRename={manageRenameProject}
          onStart={manageStartDaemon}
          onStop={manageStopDaemon}
          onDelete={manageDeleteProject}
        />
      ) : null}
      <ActionNotice notice={notice} onClose={dismissNotice} />
      {snap && !kiosk ? (
        <MobileTabBar
          active={mobileView === 'preview' ? 'preview' : workspaceView}
          sidebarOpen={sidebarOpen}
          onSelect={(tab) => {
            if (tab === 'preview') {
              setMobileView('preview');
              return;
            }
            setMobileView('activity');
            setWorkspaceView(tab);
          }}
          onOpenSessions={() => setSidebarOpen(true)}
        />
      ) : null}
    </div>
  );
}
