import type { DispatchObserver, MapSendOptions } from './map/submission';
import { lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { artifactRefreshEventKey, canvasRevisionFromEvents, snapshotRefreshEventKey, useProjects, useProjectCosts, useSnapshot, useEventStream, useProjectActions, useArtifacts, useWorkspaceTree } from './hooks';
import { refreshCanvas } from './map/canvas';
import { useConversationHistory } from './useConversationHistory';
import { api, holdsToken, isConnectionError, newRequestId, signOut, type AttachmentUploadOptions, type EventMsg, type MessageRouteOverride, type SkillLibraryItem, type SkillScope, type WikiLibraryItem, PairingRequiredError } from './api';
import { LearningLibrary, type LearningScope, type LearningSelection } from './components/LearningLibrary';
import { LearningStatus } from './components/LearningStatus';
import { ComposerRuntime } from './components/ComposerRuntime';
import { TopBar } from './components/TopBar';
import { ReplyChoiceContext } from './components/ReplyOptions';
import { WorkspaceShell } from './components/WorkspaceShell';
import { EventStream, latestConversationDelivery, SessionIdContext, WorkspaceRootsContext } from './components/EventStream';
import { ChatBox } from './components/ChatBox';
import { ContinuousSwitch } from './components/ContinuousSwitch';
import { promptHistory, type PromptSources } from './lib/promptCompletion';
import { appendPhaseStep, closePhaseTrail, type PhaseStep, trailToTurnSteps, turnStepsFrom } from '../../core/src/phaseTrail';
import { CommandPalette, commandPaletteRows, type PaletteItem } from './components/CommandPalette';
import { KeybindingHelp } from './components/KeybindingHelp';
import { DoctorModal, ConfigModal, IdentityModal, TranscriptModal } from './components/InfoModals';
import { PendingBanner } from './components/PendingBanner';
import { LaunchHoldCard, LaunchSettingsBar } from './components/LaunchPanel';
import { DEFAULT_LAUNCH_SETTINGS, isFreshProject, launchForMessage, readAskMode, writeAskMode, type AskMode } from './lib/launch';
import { PendingReplyDialog } from './components/PendingReplyDialog';
import { GuardianBanner } from './components/GuardianBanner';
import { NoOperatorBanner } from './components/NoOperatorBanner';
import { hasHumanProjectLabel, rankProjects } from '../../core/src/projects';
import { ArtifactModal } from './components/ArtifactModal';
import { ProgressQuestionsProvider } from './research-brief/ProgressQuestions';
import {
  missionIsComplete,
  ResearchCanvas,
  selectCompletionArtifact,
  FILES_PATH,
} from './components/ResearchCanvas';
import { ActionNotice, type NoticeTone, type UiNotice } from './components/ActionNotice';
import { DaemonManageModal } from './components/DaemonManageModal';
import { Sidebar } from './components/Sidebar';
import { ProjectInspectorModal } from './components/ProjectInspectorModal';
import { TaskDetailModal } from './components/TaskDetailModal';
import { currentWorkStatus } from './lib/workStatus';
import { SplitHandle } from './components/SplitHandle';
import { Modal } from './components/Modal';
import { Landing } from './components/Landing';
import { MobileTabBar } from './components/MobileTabBar';
import { useVisualViewport } from './useVisualViewport';
import { activeGuardianAlert } from './lib/guardian';
import { projectMissionView } from '../../core/src/missionView';
import { useQueryClient } from '@tanstack/react-query';
import { dispatchWebCommand, type WebCommandHandlers } from './lib/webCommands';
import { buildWebCommandHandlers } from './lib/commandHandlers';
import { finishManagerMessage } from './lib/messageResult';
import { COMMANDS, parseCommand } from '../../core/src/commands';
import { COMMANDS_CAPABILITY, EDITIONS_CAPABILITY, LAUNCH_CAPABILITY, POLL_CAPABILITY, ROUTE_CAPABILITY, SETTINGS_CAPABILITY, useCapability } from './lib/capabilities';
import { type EventViewFilter } from '../../core/src/events';
import { eventViewReducer, initialEventViewState } from './lib/eventView';
import {
  mergeOptimisticManagerDelta,
  mergeOptimisticManagerSteps,
  optimisticOperatorEvent,
  settleOptimisticManagerTurn,
} from './lib/conversationEvents';
import { mergeProjectCosts } from './lib/projectCosts';
import { errorText, managerStreamFailureMessage } from './lib/format';
import { useCreateDaemonSession } from './useCreateDaemonSession';
import { keepsGoing, useProjectDaemonActions } from './useProjectDaemonActions';
import { useGlobalKeyboardShortcuts } from './useGlobalKeyboardShortcuts';
import { usePendingReplySession } from './usePendingReplySession';
import { useProjectSelection, type ProjectHistoryMode } from './useProjectSelection';
import { useSessionComposer, type DraftSnapshot } from './useSessionComposer';
import { ArtifactEditor, ConversationEditor, HistorySearch, type ConversationRevision } from './components/ExperiencePanels';
import type { SearchResult } from './api';
import { useDesktopDeliveryNavigation } from './useDesktopDeliveryNavigation';
import { useWorkbenchLayout } from './useWorkbenchLayout';
import { PREVIEW_DEFAULT_WIDTH, PREVIEW_MAX_WIDTH } from './lib/previewLayout';
import { useI18n } from './i18n';
import { ConnectionProblemBanner } from './components/ConnectionProblemBanner';
import { addComposerFiles, MESSAGE_TEXT_MAX_CHARS } from './lib/attachments';
import './components/customerExperience.css';
import { useDeliveryCenter } from './useDeliveryCenter';
import { deliveryFiles, defaultDeliverySelection, selectActiveDelivery, hasPendingDeliveryDependents } from './components/deliveryPresentation';
import { isStagePackage } from './lib/deliveryKind';
import { projectAttention } from './lib/attention';
import { progressHealth } from './lib/progressHealth';
import { ProgressHealthBanner } from './components/ProgressHealthBanner';
import { useAttentionSignals } from './useAttentionSignals';
import { DraftUploadContext, DraftUploads, type DraftUploadView } from './lib/draftUploads';
import type { ArtifactInfo, DeliveryReceipt, LaunchSettings, MissionView } from '../../core/src/types';
import {
  completionNotificationPayload,
  installDesktopExternalLinkBridge,
  notifyDesktopCompletion,
  subscribeDesktopNewChat,
} from './lib/desktopBridge';

type Overlay = 'none' | 'palette' | 'help' | 'doctor' | 'config' | 'identity' | 'transcript' | 'inspector' | 'learning';
interface ActiveMessageRequest {
  id: number;
  serverRequestId: string;
  sid: string;
  controller: AbortController;
  recovered?: boolean;
  uploading?: boolean;
}
interface CompletionContext {
  sid: string;
  completionId: string;
  view: MissionView | null;
  artifacts: ArtifactInfo[];
}
let noticeSequence = 0;

const ResearchWorkbenchPanel = lazy(async () => {
  const module = await import('./research-workbench/ResearchWorkbenchPanel');
  return { default: module.ResearchWorkbenchPanel };
});
const MapPanel = lazy(async () => {
  const module = await import('./map/MapPanel');
  return { default: module.MapPanel };
});

/** Trailing-edge throttle for hot props. Streamed events arrive many times a
 * second while agents work; the memoized map only needs a beat-level view. */
function useThrottledValue<T>(value: T, ms: number): T {
  const [current, setCurrent] = useState(value);
  const latest = useRef(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const stamp = useRef(0);
  latest.current = value;
  useEffect(() => {
    if (value === current) return;
    const due = stamp.current + ms - Date.now();
    if (due <= 0) {
      stamp.current = Date.now();
      setCurrent(value);
      return;
    }
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      stamp.current = Date.now();
      setCurrent(latest.current);
    }, due);
  }, [value, current, ms]);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  return current;
}


export default function App() {
  const { locale, t } = useI18n();
  // The pairing error carries a fixed English message; show the localized copy instead.
  const landingErrorText = (error: unknown): string =>
    error instanceof PairingRequiredError ? t('connection.pairingDetail') : errorText(error);
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
  const commands = useCapability(COMMANDS_CAPABILITY);
  const openSettings = useCapability(SETTINGS_CAPABILITY) ? () => setOverlay('config') : undefined;
  const [learningSelection, setLearningSelection] = useState<LearningSelection | null>(null);
  const [learningScope, setLearningScope] = useState<LearningScope>('feed');
  const openLearningLibrary = useCallback((scope: LearningScope) => {
    setLearningSelection(null);
    setLearningScope(scope);
    setOverlay('learning');
  }, []);
  const openSkillLibrary = useCallback((item?: SkillLibraryItem, scope: SkillScope | 'recent' = 'recent') => {
    setLearningSelection(item ? { kind: 'skill', item } : null);
    setLearningScope(item || scope !== 'recent' ? 'global' : 'feed');
    setOverlay('learning');
  }, []);
  const openWiki = useCallback((page?: WikiLibraryItem) => {
    setLearningSelection(page ? { kind: 'knowledge', item: page } : null);
    setLearningScope(page ? 'global' : 'feed');
    setOverlay('learning');
  }, []);
  const {
    cycleTheme,
    kiosk,
    leftWidth,
    mapImmersive,
    mobileView,
    openPreview: expandPreview,
    resizeSidebar,
    rightPanelOpen,
    rightWidth,
    setKiosk,
    setLeftWidth,
    setMapFull,
    setMobileView,
    setRightPanelOpen,
    setRightWidth,
    setShowReasoning,
    setSidebarOpen,
    setWorkspaceView,
    shellRef,
    showReasoning,
    sidebarOpen,
    sidebarShown,
    themeMode,
    toggleSidebar,
    workspaceView,
  } = useWorkbenchLayout();
  const [workbenchOpened, setWorkbenchOpened] = useState(workspaceView === 'workbench');
  const [runtimeRequest, setRuntimeRequest] = useState(0);
  const openRuntimeControls = useCallback(() => {
    setOverlay('none');
    setMobileView('activity');
    setWorkspaceView('workbench');
    setRuntimeRequest(value => value + 1);
  }, [setMobileView, setWorkspaceView]);
  useEffect(() => {
    if (workspaceView === 'workbench') {
      setWorkbenchOpened(true);
    }
  }, [workspaceView]);
  // Share the visible-height decision with the reading card and composer.
  const compactViewport = useVisualViewport();
  const [composerFocus, setComposerFocus] = useState(0);
  const [slashSelection, setSlashSelection] = useState(0);
  // The project whose composer caret is in an @ word, so its workspace listing is wanted.
  const [treeSid, setTreeSid] = useState<string | null>(null);
  const [routeOverride, setRouteOverride] = useState<MessageRouteOverride>('auto');
  // Without the choice a message goes as Auto: the server decides whether to answer or hand it on.
  const setRoute = useCapability(ROUTE_CAPABILITY) ? setRouteOverride : undefined;
  const [launchSettings, setLaunchSettings] = useState<LaunchSettings>(DEFAULT_LAUNCH_SETTINGS);
  const launches = useCapability(LAUNCH_CAPABILITY);
  const editions = useCapability(EDITIONS_CAPABILITY);
  const [chatPending, setChatPending] = useState(false);
  const [waitingForUploads, setWaitingForUploads] = useState(false);
  const [localConversationEvents, setLocalConversationEvents] = useState<EventMsg[]>([]);
  const localConversationSid = useRef<string | null>(null);
  const [managerSteps, setManagerSteps] = useState<PhaseStep[]>([]);
  const [artifactPath, setArtifactPath] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ path: string; version?: number } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [conversationEditor, setConversationEditor] = useState<ConversationRevision | null>(null);
  const [revisionContext, setRevisionContext] = useState<ConversationRevision | null>(null);
  const [searchTarget, setSearchTarget] = useState<SearchResult | null>(null);
  const [highlightedTs, setHighlightedTs] = useState<number | undefined>();
  const homeRequest = useRef<{ text: string; files: File[] } | null>(null);
  const [initialSend, setInitialSend] = useState<{ sid: string; text: string; files: File[] } | null>(null);
  const [previewPathRequest, setPreviewPathRequest] = useState({ path: '', token: 0 });
  const [taskItemId, setTaskItemId] = useState<string | null>(null);
  const [daemonManageOpen, setDaemonManageOpen] = useState(false);
  const [manageTargetSid, setManageTargetSid] = useState<string | null>(null);
  const [resumingSid, setResumingSid] = useState<string | null>(null);
  const messageSubmitLockRef = useRef(false);
  const messageRequestRef = useRef<ActiveMessageRequest | null>(null);
  const messageEpochRef = useRef(0);
  const observedCompletionRef = useRef<{ sid: string; id: string | null }>();
  const completionContextRef = useRef<CompletionContext>({
    sid: '',
    completionId: '',
    view: null,
    artifacts: [],
  });
  const cancelDeliveryNavigationRef = useRef<() => void>(() => undefined);
  const [notice, setNotice] = useState<UiNotice | null>(null);
  const [eventView, dispatchEventView] = useReducer(eventViewReducer, initialEventViewState);
  const [eventFilter, setEventFilter] = useState<EventViewFilter>('all');
  const [eventQuery, setEventQuery] = useState('');
  const dismissNotice = useCallback(() => setNotice(null), []);
  const notify = useCallback((tone: NoticeTone, message: string) => {
    setNotice({ id: ++noticeSequence, tone, message });
  }, []);

  const cancelActiveMessage = useCallback(() => {
    const cancelled = Boolean(messageRequestRef.current);
    messageEpochRef.current += 1;
    messageRequestRef.current?.controller.abort();
    messageRequestRef.current = null;
    setChatPending(false);
    setWaitingForUploads(false);
    setManagerSteps([]);
    return cancelled;
  }, []);

  const stopWaiting = useCallback(() => {
    const request = messageRequestRef.current;
    if (!request || !cancelActiveMessage()) return;
    if (request.uploading) {
      notify('info', locale === 'zh-CN' ? '已取消发送。附件会继续上传，文字和附件都还在输入框里。' : 'Sending cancelled. Uploads continue; your text and files stay in the composer.');
      return;
    }
    const epoch = messageEpochRef.current;
    notify('info', t('chat.stoppingReply'));
    // This POST has its own lifetime: aborting the SSE connection must not
    // abort the cancellation that tells the server to release the Manager.
    void api.cancelMessage(request.sid, request.serverRequestId).then((receipt) => {
      if (messageEpochRef.current !== epoch) return;
      notify('info', t(receipt.requested ? 'chat.stopReplyRequested' : 'chat.replyAlreadyFinished'));
    }).catch((error: unknown) => {
      if (messageEpochRef.current === epoch) notify('error', t('chat.stopReplyFailed', { error: errorText(error) }));
    });
  }, [cancelActiveMessage, notify, t, locale]);
  const {
    activeSid,
    clearProjectSelection: clearSelectedProject,
    prefetchProject,
    selectProject: selectProjectBase,
    sid: selectedSid,
    sidRef,
  } = useProjectSelection({
    cancelActiveMessage,
    notify,
    formatProjectMissing: (requested, fallback) => fallback
      ? t('project.missingSwitched', { requested: requested ?? '', fallback })
      : t('project.missingCreate', { requested: requested ?? '' }),
    projects,
    projectsError: projectsQ.isError,
    projectsReady: projectsQ.isSuccess,
    queryClient,
    setArtifactPath,
    setSidebarOpen,
    setTaskItemId,
  });
  const {
    draft: { text: composerDraft, attachments: composerAttachments, missingAttachments = [], uploaded: composerUploaded = [] },
    storageError: draftStorageError, seed: seedDraft, dismissMissingAttachments,
    rewriting, setText: setComposerDraft, setAttachments: setComposerAttachments,
    capture: captureDraft, consume: consumeDraft, restore: restoreDraft,
    beginRewrite, finishRewrite, drop: dropSessionDraft,
    rememberUploaded, removeUploaded,
  } = useSessionComposer(selectedSid);
  // Chosen files upload at once, one after another; sending waits only for
  // what is still on its way. Removing a file cancels or deletes it.
  const draftUploadCallbacks = useRef({ rememberUploaded, captureDraft });
  draftUploadCallbacks.current = { rememberUploaded, captureDraft };
  const draftUploads = useMemo(() => new DraftUploads(
    (sid, files, signal, options) => api.uploadAttachments(sid, files, signal, options),
    (sid, attachmentId) => api.discardAttachment(sid, attachmentId),
    (sid, file, attachment) => {
      const draft = draftUploadCallbacks.current.captureDraft(sid);
      if (draft) draftUploadCallbacks.current.rememberUploaded(draft, file, attachment);
    },
  ), []);
  const [uploadsVersion, redrawUploads] = useReducer((value: number) => value + 1, 0);
  useEffect(() => draftUploads.subscribe(redrawUploads), [draftUploads]);
  const completedUploads = useMemo(() => new Map(composerUploaded
    .flatMap(item => item.file ? [[item.file, item.attachment] as const] : [])), [composerUploaded]);
  const changeComposerAttachments = useCallback((next: File[]) => {
    if (!selectedSid) return;
    const removed = composerAttachments.filter(file => !next.includes(file));
    setComposerAttachments(next);
    for (const file of removed) {
      const arrived = completedUploads.get(file);
      if (!draftUploads.status(file) && arrived) void api.discardAttachment(selectedSid, arrived.attachment_id).catch(() => undefined);
      draftUploads.cancel(file);
    }
    draftUploads.ensure(selectedSid, next, completedUploads);
  }, [completedUploads, composerAttachments, draftUploads, selectedSid, setComposerAttachments]);
  const draftUploadView = useMemo<DraftUploadView>(() => ({
    status: file => draftUploads.status(file),
    retry: file => draftUploads.retry(file),
    ready: composerUploaded.filter(item => !item.file).map(item => item.attachment),
    removeReady: (attachmentId) => {
      removeUploaded(attachmentId);
      if (selectedSid) void api.discardAttachment(selectedSid, attachmentId).catch(() => undefined);
    },
    busy: draftUploads.busy(composerAttachments),
    // uploadsVersion: the store changed, so statuses and the busy count did too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [composerAttachments, composerUploaded, draftUploads, removeUploaded, selectedSid, uploadsVersion]);
  const selectProject = useCallback((id: string, mode?: ProjectHistoryMode) => {
    cancelDeliveryNavigationRef.current();
    selectProjectBase(id, mode);
  }, [selectProjectBase]);
  const clearProjectSelection = useCallback((mode?: ProjectHistoryMode) => {
    cancelDeliveryNavigationRef.current();
    clearSelectedProject(mode);
  }, [clearSelectedProject]);
  // A message category belongs to this conversation, never another project
  // or a preference left in storage by an older browser tab.
  useEffect(() => setRouteOverride('auto'), [activeSid]);
  useEffect(() => {
    setPreviewPathRequest(current => current.path ? { path: '', token: current.token + 1 } : current);
  }, [selectedSid]);

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
    if (!body || !sid) return;
    const operation = beginRewrite(sid);
    if (!operation) return;
    const zh = locale === 'zh-CN';
    /** The draft stays as it was; the notice says why, as the server or the network put it. */
    const failed = (reason: string) => {
      if (!finishRewrite(operation) || sidRef.current !== sid) return;
      notify('error', `${zh ? '这次没改写成功，你的输入没有动。' : "Couldn't rewrite it this time; your text is unchanged."}${reason ? `${zh ? '原因：' : ' Reason: '}${reason}` : ''}`);
    };
    void api.rewritePrompt(sid, body).then(
      (result) => {
        if (result.error || !result.rewritten.trim()) return failed(result.error);
        if (!finishRewrite(operation, result.rewritten) || sidRef.current !== sid) return;
        setComposerFocus((x) => x + 1);
        const open = result.questions.length
          ? `${zh ? ' 还想跟你确认：' : ' One thing to check: '}${result.questions.join(' · ')}`
          : '';
        notify('success', `${zh ? '改写好了，看一下再发送。' : 'Rewritten. Have a look, then send.'}${open}`);
      },
      (error) => failed(errorText(error)),
    );
  }, [beginRewrite, finishRewrite, notify, sidRef, locale]);


  const { createDaemon, creatingDaemon } = useCreateDaemonSession({
    localCwd,
    notify,
    onFocusComposer: () => setComposerFocus((value) => value + 1),
    queryClient,
    refetchProjects: projectsQ.refetch,
    selectProject,
    translate: t,
    onCreated: (sid, draft) => {
      if (draft) seedDraft(sid, draft, homeRequest.current?.files ?? []);
      if (homeRequest.current) {
        setInitialSend({ sid, ...homeRequest.current });
        homeRequest.current = null;
      }
    },
  });
  // "+ New" opens the start page, whose first message makes the project: none is made empty.
  const [starting, setStarting] = useState(false);
  const startNewSession = useCallback(() => {
    setWorkspaceView('activity');
    setMobileView('activity');
    setStarting(true);
    clearProjectSelection('push');
  }, [clearProjectSelection, setWorkspaceView, setMobileView]);
  useEffect(() => { if (activeSid) setStarting(false); }, [activeSid]);
  const startNewSessionRef = useRef(startNewSession);
  startNewSessionRef.current = startNewSession;
  useEffect(() => subscribeDesktopNewChat(() => startNewSessionRef.current()), []);


  const activeProjectRow = projects.find((project) => project.id === activeSid);
  const activeProjectName = activeProjectRow
    ? hasHumanProjectLabel(activeProjectRow) ? (activeProjectRow.label || activeProjectRow.display_name || '').trim()
      : activeProjectRow.objective.trim() || t('sidebar.unnamedSession')
    : '';
  const snapQ = useSnapshot(activeSid);
  const manageSnapQ = useSnapshot(manageTargetSid);
  const snap = snapQ.data;
  const loadedSid = snap?.session.id === activeSid ? activeSid : null;
  // Launch settings apply to a project's first task only, and never carry over.
  const freshProject = Boolean(loadedSid) && isFreshProject(snap);
  const [askMode, setAskModeState] = useState<AskMode>(() => readAskMode(activeSid));
  useEffect(() => { setLaunchSettings(DEFAULT_LAUNCH_SETTINGS); setAskModeState(readAskMode(activeSid)); }, [activeSid]);
  const setAskMode = useCallback((mode: AskMode) => { setAskModeState(mode); writeAskMode(activeSid, mode); }, [activeSid]);
  useEffect(() => {
    setEditor(null); setConversationEditor(null); setRevisionContext(null); setHighlightedTs(undefined);
  }, [activeSid]);
  const startHeldLaunch = useCallback(async () => {
    if (!loadedSid) return;
    try {
      const result = await api.confirmLaunch(loadedSid);
      const daemon = result.daemon;
      if (daemon?.admission_required) notify('error', locale === 'zh-CN' ? '已确认。现在同时在做的事已满，空出来就开始。' : 'Confirmed. Everything that can run at once is busy; this starts as soon as a slot frees up.');
      else if (daemon && Number(daemon.rc ?? 0) !== 0) notify('error', locale === 'zh-CN' ? '已确认，但没能开始工作；可以在“启动详情”里看原因，或稍后再试。' : "Confirmed, but the work couldn't start; see the startup details for why, or try again later.");
    } catch (error) {
      notify('error', errorText(error));
    } finally {
      void snapQ.refetch();
    }
  }, [loadedSid, locale, notify, snapQ]);
  const rewriteHeldLaunch = useCallback(async () => {
    if (!loadedSid) return;
    try {
      const result = await api.discardLaunch(loadedSid);
      if (result.request) setComposerDraft(result.request);
      setComposerFocus((value) => value + 1);
    } catch (error) {
      notify('error', errorText(error));
    } finally {
      void snapQ.refetch();
    }
  }, [loadedSid, notify, snapQ, setComposerDraft]);
  const continuous = snap?.continuous;
  const artifactsQ = useArtifacts(loadedSid, true);
  const { events, connected, restored: eventsRestored, seeded: eventsSeeded } = useEventStream(loadedSid, eventView.reconnectKey);
  const artifactRefreshKey = useMemo(() => artifactRefreshEventKey(events), [events]);
  // A server that pushes state sends the snapshot with each change itself.
  const polled = useCapability(POLL_CAPABILITY);
  const snapshotRefreshKey = useMemo(() => polled ? snapshotRefreshEventKey(events) : '', [events, polled]);
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
  // A canvas write in another window arrives as an event: refetch the canvas
  // now instead of on its next poll.
  const canvasRevision = useMemo(() => canvasRevisionFromEvents(events), [events]);
  useEffect(() => {
    if (loadedSid && canvasRevision) refreshCanvas(queryClient, loadedSid, canvasRevision);
  }, [canvasRevision, loadedSid, queryClient]);
  const guardianAlert = useMemo(() => activeGuardianAlert(events, locale), [events, locale]);
  const { query: transcriptQ, events: activityEvents, status: historyStatus } = useConversationHistory(
    loadedSid, true, events, localConversationEvents, localConversationSid.current,
  );
  const workspaceTreeQ = useWorkspaceTree(loadedSid, treeSid === loadedSid);
  const promptHistoryList = useMemo(() => promptHistory(activityEvents), [activityEvents]);
  const promptSources = useMemo<PromptSources | undefined>(() => loadedSid ? {
    sid: loadedSid, tasks: snap?.backlog ?? [], history: promptHistoryList, files: workspaceTreeQ.data?.entries,
    wantFiles: (want) => setTreeSid(want ? loadedSid : null),
  } : undefined, [loadedSid, snap?.backlog, promptHistoryList, workspaceTreeQ.data]);
  // Content kept from an earlier visit shows at once; until its first refresh
  // lands it is marked as updating rather than presented as live.
  const refreshingKept = eventsRestored || [snapQ, transcriptQ].some(
    (query) => query.data !== undefined && query.isFetching && !query.isFetchedAfterMount,
  );
  const activeManagerRequestId = loadedSid
    ? snap?.manager_requests?.filter(request => request.status === 'running').at(-1)?.request_id || ''
    : '';
  const messagePending = chatPending || Boolean(activeManagerRequestId);
  useEffect(() => {
    if (!loadedSid) return;
    const current = messageRequestRef.current;
    if (activeManagerRequestId) {
      if (!current) {
        messageRequestRef.current = {
          id: ++messageEpochRef.current,
          serverRequestId: activeManagerRequestId,
          sid: loadedSid,
          controller: new AbortController(),
          recovered: true,
        };
        setChatPending(true);
      }
      return;
    }
    if (current?.recovered && current.sid === loadedSid) {
      messageRequestRef.current = null;
      setChatPending(false);
      setManagerSteps([]);
      void transcriptQ.refetch();
    }
  }, [activeManagerRequestId, loadedSid, transcriptQ.refetch]);
  // The map view follows the stream at a beat, not per token.
  const mapInput = useMemo(() => ({ sid: loadedSid, events, conversation: activityEvents }), [loadedSid, events, activityEvents]);
  const mapHistory = useThrottledValue(mapInput, 250);
  // A pending throttle belongs to its original project, even when the next
  // project's snapshot is already cached and renders synchronously.
  const mapEvents = mapHistory.sid === loadedSid ? mapHistory.events : [];
  const mapConversationEvents = mapHistory.sid === loadedSid ? mapHistory.conversation : [];
  const missionView = useMemo(
    () => snap ? projectMissionView(snap, activityEvents, artifactsQ.data ?? []) : null,
    [activityEvents, artifactsQ.data, snap],
  );
  const {
    answerPendingReply,
    pendingReply,
    pendingReplyBusy,
    pendingReplyError,
    pendingReplyOpen,
    setPendingReplyOpen,
  } = usePendingReplySession({
    activeSid,
    currentTaskId: missionView?.mission.id,
    // The map surfaces decisions through its banner and node highlight; the
    // modal would cover the very trajectory the operator came to inspect.
    autoOpen: workspaceView !== 'map',
    backlog: snap?.backlog,
    notify,
    pendingQuestions: snap?.pending_questions,
    refetchSnapshot: snapQ.refetch,
  });
  const reviewActivity = snap?.daemon.alive && missionView?.routing.vertical === 'research'
    ? missionView.active_role.startsWith('reviewer') ? 'reviewing'
      : missionView.active_role.startsWith('engineer') ? 'revising' : undefined
    : undefined;
  const conversationDelivery = useMemo(
    () => latestConversationDelivery(activityEvents),
    [activityEvents],
  );
  const delivery = selectActiveDelivery(conversationDelivery, missionView?.delivery ?? null, activityEvents);
  const hasUnfinishedWork = snap?.backlog.some((item) =>
    ['pending', 'running', 'in_progress', 'claimed'].includes(item.status),
  ) ?? false;
  const operatorTaskComplete = missionIsComplete(missionView)
    && !hasUnfinishedWork
    && !snap?.continuous?.enabled;
  const completionId = loadedSid && operatorTaskComplete && missionView?.mission.id
    ? `completion:${loadedSid}:${missionView.mission.id}`
    : '';
  const [healthNow, setHealthNow] = useState(() => Date.now() / 1000);
  useEffect(() => { const timer = setInterval(() => setHealthNow(Date.now() / 1000), 60_000); return () => clearInterval(timer); }, []);
  const health = useMemo(() => (loadedSid && snap ? progressHealth(snap, healthNow) : null), [loadedSid, snap, healthNow]);
  const attention = useMemo(() => projectAttention({
    snapshot: loadedSid ? snap : undefined,
    view: missionView,
    events: activityEvents,
    complete: operatorTaskComplete,
    delivery,
    health,
    zh: locale === 'zh-CN',
  }), [loadedSid, snap, missionView, activityEvents, operatorTaskComplete, delivery, health, locale]);
  const projectBusy = useMemo(() => {
    if (!snap?.daemon.alive) return false;
    const state = currentWorkStatus(snap, missionView, events).state;
    return state === 'running' || state === 'waiting';
  }, [events, missionView, snap]);
  useAttentionSignals({
    projectName: snap?.session.display_name || '',
    attention,
    zh: locale === 'zh-CN',
    running: projectBusy,
  });
  completionContextRef.current = {
    sid: loadedSid || '',
    completionId,
    view: missionView,
    artifacts: artifactsQ.data ?? [],
  };
  const openPreview = useCallback(() => {
    expandPreview();
  }, [expandPreview]);
  const focusDeliveryPath = useCallback((path: string, userInitiated = true) => {
    const target = path.trim();
    if (!target) {
      if (userInitiated) setWorkspaceView('map');
      return;
    }
    if (workspaceView === 'map' || !artifactsQ.data?.some(file => file.path === target && file.exists)) {
      setArtifactPath(target);
      return;
    }
    setRightPanelOpen(true);
    setMobileView('preview');
    setPreviewPathRequest((current) => ({ path: target, token: current.token + 1 }));
  }, [artifactsQ.data, setMobileView, setRightPanelOpen, setWorkspaceView, workspaceView]);
  const deliveryCenter = useDeliveryCenter(
    loadedSid,
    Boolean(snap) && !transcriptQ.isPending,
    delivery,
    // Intermediate files stay quiet while downstream work runs; a stage package is
    // announced precisely because the rest of the goal is still running.
    !artifactPath && (isStagePackage(delivery) || !hasPendingDeliveryDependents(snap?.backlog ?? [], delivery?.item_id)),
  );
  const openDelivery = deliveryCenter.open;
  const composeContext = (text: string) => {
    if (!loadedSid || loadedSid !== sidRef.current || kiosk) return false;
    const next = [composerDraft, text].filter(Boolean).join('\n\n');
    if (next.length > MESSAGE_TEXT_MAX_CHARS) { notify('error', t('chat.messageTooLong')); return false; }
    setComposerDraft(next);
    setOverlay('none');
    setArtifactPath(null);
    deliveryCenter.close();
    setWorkspaceView('activity');
    setMobileView('activity');
    setComposerFocus(value => value + 1);
    return true;
  };
  const reviseArtifact = (path: string) => composeContext(locale === 'zh-CN'
    ? `请继续修改当前项目的成果文件：${path}\n修改要求：`
    : `Please revise this result in the current project: ${path}\nRequested changes:`);
  const editArtifact = (path: string) => {
    setEditor({ path }); setArtifactPath(null); deliveryCenter.close();
  };
  // Editing keeps versions, which only a server that has them offers.
  const onEditArtifact = kiosk || !editions ? undefined : editArtifact;
  const useMaterial = (title: string, content: string, scope: string) => {
    if (!loadedSid || loadedSid !== sidRef.current || kiosk) return;
    const name = `${title.replace(/[\\/:*?"<>|]/g, '-')}.md`;
    const file = new File([`# ${title}\n\n${scope}\n\n---\n\n${content}`], name, { type: 'text/markdown' });
    const { accepted, issues } = addComposerFiles(composerAttachments, [file]);
    if (issues.length || !accepted.length) {
      notify('error', locale === 'zh-CN' ? '资料未加入：请检查附件数量和大小限制，或先移除一个附件。' : 'Material was not added. Check attachment size/count limits or remove an attachment first.');
      return;
    }
    changeComposerAttachments([...composerAttachments, ...accepted]);
    setOverlay('none');
    setWorkspaceView('activity');
    setMobileView('activity');
    setComposerFocus(value => value + 1);
    notify('info', locale === 'zh-CN' ? '资料已加入草稿，发送后生效。' : 'Added to the draft; it takes effect when you send.');
  };
  const deliveryHistory = useMemo(() => {
    const receipts = new Map<string, DeliveryReceipt>();
    for (const event of activityEvents) {
      const receipt = event.delivery as DeliveryReceipt | undefined;
      if (receipt?.delivery_id && Array.isArray(receipt.targets)) receipts.set(receipt.delivery_id, receipt);
    }
    for (const receipt of [missionView?.delivery, delivery]) {
      if (receipt) receipts.set(receipt.delivery_id, receipt);
    }
    return [...receipts.values()].filter((receipt) => deliveryFiles(receipt).length).sort((a, b) => b.delivered_at - a.delivered_at);
  }, [activityEvents, missionView?.delivery, delivery]);
  useEffect(() => {
    if (loadedSid && delivery?.delivery_id) void queryClient.invalidateQueries({ queryKey: ['artifacts', loadedSid], exact: true });
  }, [loadedSid, delivery?.delivery_id, queryClient]);
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
        sessionId: loadedSid,
        title: receipt?.title || current.view.mission.title || t('mission.taskCompleted'),
        summary: receipt?.summary || current.view.mission.summary,
        path: receipt?.primary_target?.path || artifact?.path,
      });
      if (payload) void notifyDesktopCompletion(payload);
      observedCompletionRef.current = { sid: loadedSid, id: completionId };
    }, 500);
    return () => window.clearTimeout(timer);
  }, [completionId, loadedSid, t]);
  cancelDeliveryNavigationRef.current = useDesktopDeliveryNavigation({
    selectedSid, loadedSid, sidRef, snapshotError: snapQ.error, queryClient,
    receipts: transcriptQ.isPending ? [] : deliveryHistory,
    completionId,
    // Notification navigation uses the same selection path but is not a new
    // manual intent. Ordinary sidebar/palette/command selections cancel it.
    selectProject: (id) => { selectProjectBase(id); setSidebarOpen(false); },
    openDelivery,
    focusPath: (path) => {
      // A verified file can precede its listing refresh. The side preview only
      // selects listed files; use the existing direct modal rather than its
      // default-file fallback when the target has not reached that index yet.
      if (artifactsQ.data?.some(file => file.path === path && file.exists)) focusDeliveryPath(path);
      else setArtifactPath(path);
    },
    noPath: () => { setMobileView('activity'); setWorkspaceView('map'); },
    notify,
  });
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
  const manageActions = useProjectActions(
    manageTargetSid,
    manageSnapQ.data?.daemon_commands?.revision,
  );
  const resumeSession = useCallback(async (sid: string) => {
    setResumingSid(sid);
    try {
      await api.startDaemon(sid);
      await projectsQ.refetch();
      notify('success', t('sidebar.resumeSuccess'));
    } catch (error) {
      notify('error', t('sidebar.resumeFailed', { error: errorText(error) }));
    } finally {
      setResumingSid(null);
    }
  }, [notify, projectsQ, t]);
  const {
    daemonBusy,
    manageDeleteProject,
    manageStopDaemon,
    manageRenameProject,
    manageStartDaemon,
    requestManageSession,
    requestPauseDaemon,
    requestStartDaemon,
    requestStopDaemon,
    toggleContinuous,
  } = useProjectDaemonActions({
    actions,
    manageActions,
    manageTargetSid,
    setManageTargetSid,
    activeSid,
    clearProjectSelection,
    continuous,
    notify,
    refetchProjects: projectsQ.refetch,
    selectProject,
    setDaemonManageOpen,
    t,
  });
  const renameCurrentProject = useCallback(async (name: string) => {
    if (!activeSid) return;
    const result = await actions.updateProject.mutateAsync({ sid: activeSid, name });
    notify('success', t('project.renamedTo', { name: result.name }));
  }, [actions.updateProject, activeSid, notify]);
  const commandHandlers = useMemo<WebCommandHandlers>(() => buildWebCommandHandlers({
    activeSid,
    activityEventsRef,
    notify,
    onClearEvents: (offset) => dispatchEventView({ kind: 'clear', offset }),
    onOpenConfig: () => setOverlay('config'),
    onOpenDoctor: () => setOverlay('doctor'),
    onOpenHelp: () => setOverlay('help'),
    onOpenIdentity: () => setOverlay('identity'),
    onOpenInspector: () => setOverlay('inspector'),
    onOpenNewDaemon: startNewSession,
    onOpenOperations: openRuntimeControls,
    onOpenSidebar: () => setSidebarOpen(true),
    onOpenSkills: () => openSkillLibrary(undefined, 'global'),
    onReconnectEvents: () => dispatchEventView({ kind: 'reconnect' }),
    onRenameProject: renameCurrentProject,
    onRewriteDraft: rewriteDraft,
    onSelectProject: selectProject,
    onSetArtifactPath: setArtifactPath,
    onSetEventFilter: setEventFilter,
    onSetEventQuery: setEventQuery,
    onSetTaskItemId: setTaskItemId,
    onSetWorkspaceView: setWorkspaceView,
    onShowArtifacts: openPreview,
    onStopWaiting: stopWaiting,
    refetchSnapshot: snapQ.refetch,
  }), [
    activeSid,
    notify,
    openPreview,
    openSkillLibrary,
    openRuntimeControls,
    renameCurrentProject,
    rewriteDraft,
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
    toggleSidebarCollapse: toggleSidebar,
  });

  const sendMessage = async (text: string, attachments: File[] = [], observe?: DispatchObserver, uploadOptions?: AttachmentUploadOptions): Promise<boolean> => {
    const requestSid = activeSid;
    if (!requestSid || requestSid !== sidRef.current || messageSubmitLockRef.current || messageRequestRef.current) return false;
    const requestEpoch = messageEpochRef.current;
    const revision = revisionContext;

    messageSubmitLockRef.current = true;
    let requestId: number;
    let serverRequestId: string;
    let controller: AbortController;
    try {
      if (commands && !revision && !attachments.length && !uploadOptions?.ready?.length) {
        const command = await dispatchWebCommand(text, commandHandlers);
        if (command.kind === 'handled') { observe?.({ type: 'settled', outcome: 'message' }); return true; }
        if (command.kind === 'error') {
          notify('error', command.message);
          return false;
        }
      }

      // Command dispatch yields even for ordinary text. A switch during that
      // yield must not create a new request after the old wait was cancelled.
      if (sidRef.current !== requestSid || messageEpochRef.current !== requestEpoch) return false;
      requestId = ++messageEpochRef.current;
      serverRequestId = newRequestId();
      controller = new AbortController();
      messageRequestRef.current = { id: requestId, serverRequestId, sid: requestSid, controller };
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
        setWaitingForUploads(false);
        setManagerSteps([]);
      }
    };

    setChatPending(true);
    setManagerSteps([]);
    let attachmentRefs: Array<{ attachment_id: string }> = [];
    if (attachments.length || uploadOptions?.ready?.length) {
      if (messageRequestRef.current) messageRequestRef.current.uploading = true;
      try {
        // Files started uploading when chosen; wait for the ones still on
        // their way. Cancelling this wait leaves those uploads running.
        const known = uploadOptions?.completed ?? new Map<File, never>();
        if (attachments.some(file => !known.has(file))) setWaitingForUploads(true);
        const completed = await draftUploads.settle(requestSid, attachments, known, controller.signal);
        if (!isCurrent()) return false;
        for (const [file, attachment] of completed) if (!known.has(file)) uploadOptions?.onUploaded?.(file, attachment);
        setWaitingForUploads(false);
        const uploaded = await api.uploadAttachments(requestSid, attachments, controller.signal, { ready: uploadOptions?.ready, completed });
        if (!isCurrent()) return false;
        attachmentRefs = uploaded.attachments.map((attachment) => ({
          attachment_id: attachment.attachment_id,
        }));
        if (messageRequestRef.current) messageRequestRef.current.uploading = false;
        setWaitingForUploads(false);
      } catch (error) {
        if (isCurrent()) {
          notify('error', t('chat.attachmentUploadFailed', { error: errorText(error) }));
          resetCurrentRequest();
        }
        return false;
      }
    }
    const sameConversation = localConversationSid.current === requestSid;
    localConversationSid.current = requestSid;
    setLocalConversationEvents((current) => mergeOptimisticManagerSteps([
      ...(sameConversation ? current : []),
      optimisticOperatorEvent(requestSid, requestId, text),
    ], requestSid, requestId, []));

    const showManagerText = (
      reply: unknown,
      messageId = '',
      fragmentMode: 'append' | 'snapshot' | 'auto' = 'auto',
      inProgress = false,
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
        inProgress,
      ));
    };

    const dispatchTask = (result: Record<string, unknown>) => {
      if (!isCurrent()) return;
      const item = result.item as { id?: unknown } | undefined;
      if (typeof item?.id === 'string') observe?.({ type: 'task', taskId: item.id });
      const daemon = result.daemon && typeof result.daemon === 'object'
        ? result.daemon as Record<string, unknown>
        : null;
      // Use result.reply (persisted by backend) as a non-durable accessibility
      // notice — the conversation event is already in the transcript refetch.
      const reply = typeof result.reply === 'string' ? result.reply : null;
      if (daemon?.admission_required) {
        notify(
          'error',
          reply || (locale === 'zh-CN' ? '记下了。现在同时在做的事已满，空出来就开始。' : 'Got it. Everything that can run at once is busy; this starts as soon as a slot frees up.'),
        );
      } else if (daemon && Number(daemon.rc ?? 0) !== 0) {
        notify('error', reply || (locale === 'zh-CN' ? '记下了，但没能开始工作；可以在“启动详情”里看原因，或稍后再试。' : "Saved, but the work couldn't start; see the startup details for why, or try again later."));
      } else if (reply && !observe) {
        notify('success', reply);
      }
      snapQ.refetch?.();
    };

    const finishMessage = (result: Record<string, unknown>) => {
      if (!isCurrent()) return;
      void queryClient.invalidateQueries({ queryKey: ['artifacts', requestSid] });
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
      // A project's first message names it and starts its work: the list and the project are
      // read again once the server has it.
      let named = Boolean(projects.find(project => project.id === requestSid)?.objective);
      try {
        try {
          await api.messageStream(requestSid, text, {
            onPhase: (label, role, meta) => {
              if (!named) {
                named = true;
                void projectsQ.refetch();
                void queryClient.invalidateQueries({ queryKey: ['snapshot', requestSid] });
              }
              if (!isCurrent() || meta.heartbeat) return;
              trail = appendPhaseStep(trail, {
                label,
                role,
                kind: meta.kind,
                detail: meta.detail,
                heartbeat: meta.heartbeat,
                quietS: meta.quietS,
                tool: meta.tool,
                toolKind: meta.toolKind,
                callId: meta.callId,
                status: meta.status,
                output: meta.output,
              });
              setManagerSteps(trail);
              // Tool work shows up in the conversation as it happens, under
              // the reply that is still being written.
              const steps = trailToTurnSteps(trail);
              if (steps.length) {
                setLocalConversationEvents((current) => mergeOptimisticManagerSteps(
                  current, requestSid, requestId, steps, Date.now(), true,
                ));
              }
            },
            onDelta: (block, messageId, fragmentMode, inProgress) => {
              if (!isCurrent()) return;
              gotDelta = true;
              showManagerText(
                block,
                messageId,
                fragmentMode === 'append' || fragmentMode === 'snapshot'
                  ? fragmentMode
                  : 'auto',
                inProgress,
              );
            },
            onDone: (result) => {
              if (!isCurrent()) return;
              if (result.kind !== 'error') setRouteOverride('auto');
              trail = closePhaseTrail(trail);
              setManagerSteps(trail);
              // Prefer the journaled steps: they carry real end times and
              // outcomes. The local trail stands in when a turn had none.
              const journaled = turnStepsFrom(result.steps);
              const steps = journaled.length ? journaled : trailToTurnSteps(trail, Date.now() / 1000, true);
              setLocalConversationEvents((current) => mergeOptimisticManagerSteps(
                current, requestSid, requestId, steps, Date.now(), false, result.kind === 'error' ? 'failed' : 'completed',
              ));
              showManagerText(result.reply, '', 'snapshot');
              finishMessage(result);
              if (revision && result.kind !== 'error' && typeof result.reply === 'string' && result.reply.trim()) {
                void api.saveEdition(requestSid, 'conversation', {
                  source: revision.source, content: result.reply, note: text, original: revision.answer,
                  base_version: revision.baseVersion,
                }).then(() => queryClient.invalidateQueries({ queryKey: ['editions', requestSid, 'conversation', revision.source] }))
                  .catch(error => notify('error', `${locale === 'zh-CN' ? '回答已保留在对话中，但修订版本未保存：' : 'Answer kept in conversation, but version was not saved: '}${errorText(error)}`));
              }
              const item = result.item as { id?: unknown } | undefined;
              if (result.kind !== 'task' || typeof item?.id !== 'string')
                observe?.({ type: 'settled', outcome: result.kind === 'error' ? 'error' : 'message' });
            },
            onError: (err) => {
              if (isCurrent()) streamErr = err;
            },
          }, {
            signal: controller.signal,
            attachments: attachmentRefs,
            routeOverride,
            requestId: serverRequestId,
            launch: launches && !revision && routeOverride !== 'chat' ? launchForMessage(launchSettings, askMode, freshProject) : undefined,
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
          observe?.({ type: 'settled', outcome: 'error' });
        }
      } finally {
        // Whatever ended the turn, nothing in it is live any more.
        setLocalConversationEvents((current) => settleOptimisticManagerTurn(
          current, requestId, Date.now(), streamErr && !controller.signal.aborted ? 'failed' : 'stopped',
        ));
        if (controller.signal.aborted) observe?.({ type: 'settled', outcome: 'cancelled' });
        resetCurrentRequest();
      }
    })();

    if (revision) setRevisionContext(null);
    return true; // draft clears immediately on dispatch, not when stream finishes
  };

  // Stable ref so commandPaletteRows closures always call the latest sendMessage
  // without re-creating all 34 command items on every render.
  const sendMessageRef = useRef(sendMessage);
  sendMessageRef.current = sendMessage;

  const workspaceRoots = useMemo(() => [snap?.session.workdir, snap?.session.cwd].filter((root): root is string => Boolean(root)), [snap?.session.workdir, snap?.session.cwd]);
  const replyChoice = useCallback((choice: string) => { void sendMessageRef.current(choice); }, []);
  const sendComposerMessage = async (text: string, files: File[] = [], observe?: DispatchObserver, options?: MapSendOptions): Promise<boolean> => {
    const draft = captureDraft(sidRef.current);
    if (!draft) return false;
    if (draft.missingAttachments?.length) {
      notify('error', locale === 'zh-CN' ? '刷新前的附件尚未上传。请重新选择，或在对话中移除提醒后再发送。' : 'Files selected before reloading were not uploaded. Select them again or discard their reminder in the conversation before sending.');
      return false;
    }
    if (options?.whileRunning) {
      if (revisionContext) {
        notify('error', locale === 'zh-CN' ? '请等当前回复结束后再发送修订。' : 'Wait for the current reply before sending a revision.');
        return false;
      }
      if (files.length || draft.uploaded?.length) {
        notify('error', t('chat.attachWhileRunning'));
        return false;
      }
      // The running reply keeps its own request. Commands still run as
      // commands; a plain message is shown now and answered as the next turn
      // (Argus decides whether it steers a live mission or is answered in chat).
      try {
        if (commands && !files.length) {
          const command = await dispatchWebCommand(text, commandHandlers);
          if (command.kind === 'error') { notify('error', command.message); return false; }
          if (command.kind === 'handled') { consumeDraft(draft, files); return true; }
        }
        await api.queueFollowup(draft.sid, text, routeOverride);
      } catch (error) {
        notify('error', errorText(error));
        return false;
      }
      consumeDraft(draft, files);
      return true;
    }
    let cleared: DraftSnapshot | null = null;
    let failedBeforeAcceptance = false;
    // /rewrite owns its pending revision until its preview lands, not a send.
    const rewriteCommand = commands && !revisionContext && !files.length && !draft.uploaded?.length && parseCommand(text.trim())?.cmd?.id === 'rewrite';
    const accepted = await sendMessage(text, files, (result) => {
      if (result.type === 'settled' && result.outcome === 'error') {
        failedBeforeAcceptance = true;
        if (cleared) restoreDraft(cleared, draft);
      }
      if (sidRef.current === draft.sid) observe?.(result);
    }, {
      ready: draft.uploaded?.filter(item => !item.file).map(item => item.attachment),
      completed: new Map(draft.uploaded?.flatMap(item => item.file ? [[item.file, item.attachment] as const] : [])),
      onUploaded: (file, attachment) => rememberUploaded(draft, file, attachment),
    });
    if (accepted && !failedBeforeAcceptance && !rewriteCommand) {
      cleared = consumeDraft(draft, files);
      draftUploads.forget(files);
    }
    return accepted;
  };
  const composerSendRef = useRef(sendComposerMessage);
  composerSendRef.current = sendComposerMessage;
  useEffect(() => {
    if (!initialSend) return;
    if (activeSid && activeSid !== initialSend.sid) { setInitialSend(null); return; }
    if (loadedSid !== initialSend.sid) return;
    setInitialSend(null);
    void composerSendRef.current(initialSend.text, initialSend.files);
  }, [initialSend, loadedSid, activeSid]);
  useEffect(() => {
    if (!searchTarget || loadedSid !== searchTarget.sid) return;
    if (searchTarget.kind === 'artifact' && searchTarget.path) focusDeliveryPath(searchTarget.path);
    else if (searchTarget.kind === 'edition' && searchTarget.path) setEditor({ path: searchTarget.path, version: searchTarget.version });
    else if (searchTarget.kind === 'conversation') {
      localConversationSid.current = loadedSid;
      setLocalConversationEvents(current => [...current, {
        type: searchTarget.role === 'operator' ? 'ui.operator' : 'ui.argus',
        agent_layer: searchTarget.role === 'operator' ? 'operator' : 'manager',
        text: searchTarget.text, ts: Number(searchTarget.ts), message_id: searchTarget.message_id,
      }]);
      setHighlightedTs(Number(searchTarget.ts));
    }
    setSearchTarget(null);
  }, [searchTarget, loadedSid, focusDeliveryPath]);

  const paletteItems: PaletteItem[] = useMemo(() => {
    const commandRows = commandPaletteRows(
      commands ? COMMANDS : [],
      (name) => { void sendMessageRef.current(name); },
      (text) => { setComposerDraft(text); setComposerFocus((x) => x + 1); },
      locale,
    );
    const nav: PaletteItem[] = [
      ...(kiosk ? [] : [{ id: 'new', label: t('palette.newDaemon'), hint: '+', group: t('palette.view'), run: startNewSession }]),
      { id: 'transcript', label: t('palette.openTranscript'), hint: '/transcript', group: t('palette.view'), run: () => setOverlay('transcript') },
      { id: 'history-search', label: locale === 'zh-CN' ? '搜索历史与成果' : 'Search history and results', group: t('palette.view'), run: () => setSearchOpen(true) },
      { id: 'inspector', label: t('palette.openProject'), hint: t('palette.projectHint'), group: t('palette.view'), run: () => setOverlay('inspector') },
      { id: 'operations', label: t('palette.openOperations'), hint: t('palette.operationsHint'), group: t('palette.view'), run: openRuntimeControls },
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
          ...(messagePending
            ? [{ id: 'cancel-message', label: t('palette.stopWaiting'), hint: 'Esc', group: t('palette.action'), run: stopWaiting }]
            : []),
          ...(continuous
            ? [
                {
                  id: 'continuous',
                  label: keepsGoing(continuous) ? t('palette.stopContinuous') : t('palette.startContinuous'),
                  group: t('palette.action'),
                  run: toggleContinuous,
                },
              ]
            : []),
          ...(snap?.daemon.control_available === false
            ? []
            : [
                snap?.daemon.alive
                  ? { id: 'stop', label: t('palette.stopDaemon'), group: t('palette.action'), run: requestPauseDaemon }
                  : { id: 'start', label: t('palette.startDaemon'), group: t('palette.action'), run: requestStartDaemon },
              ]),
        ];
    const proj: PaletteItem[] = projects.map((p) => ({
      id: `p-${p.id}`,
      label: hasHumanProjectLabel(p) ? (p.label || p.display_name || '').trim() : p.objective.trim() || t('sidebar.unnamedSession'),
      hint: p.daemon_alive ? `● ${t('common.live')}` : '○',
      keywords: `${p.id} ${p.display_name ?? ''} ${p.objective} ${p.daemon_alive ? 'live running' : 'stopped idle'}`,
      group: t('palette.project'),
      run: () => selectProject(p.id),
    }));
    return [...nav, ...acts, ...commandRows, ...proj];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, snap?.daemon.alive, kiosk, showReasoning, continuous?.enabled, continuous?.open_ended, messagePending, stopWaiting, setComposerDraft, locale, t, commands]);

  return (
    <DraftUploadContext.Provider value={draftUploadView}>
    <ProgressQuestionsProvider sid={activeSid} readOnly={kiosk}>
    <WorkspaceShell
      ref={shellRef}
      style={{
        '--sidebar-width': `${leftWidth}px`,
        '--preview-width': `${rightWidth}px`,
      } as React.CSSProperties}
    >
      <ConnectionProblemBanner
        error={connectionError}
        onRetry={() => {
          void projectsQ.refetch();
          if (projectCostsQ.isEnabled) void projectCostsQ.refetch();
        }}
      />
      {deliveryCenter.fresh && !deliveryCenter.selection && <div className="delivery-toast" role="status" aria-live="polite">
        <span>{t(isStagePackage(deliveryCenter.fresh) ? 'delivery.freshStage' : 'delivery.freshTask')}{deliveryCenter.fresh.title}</span>
        <button type="button" onClick={() => openDelivery(deliveryCenter.fresh!)}>{locale === 'zh-CN' ? '查看' : 'Open'}</button>
        <button type="button" aria-label={locale === 'zh-CN' ? '关闭' : 'Dismiss'} onClick={deliveryCenter.dismissFresh}>×</button>
      </div>}
      {deliveryCenter.selection && <ArtifactModal
        key={`${deliveryCenter.selection.sid}:${deliveryCenter.selection.receipt.delivery_id}`}
        sid={deliveryCenter.selection.sid} path={deliveryCenter.selection.path}
        delivery={deliveryCenter.selection.receipt} deliveries={deliveryHistory}
        reviewActivity={deliveryCenter.selection.sid === loadedSid ? reviewActivity : undefined}
        onSelectDelivery={openDelivery} onSelectPath={deliveryCenter.selectPath} onClose={deliveryCenter.close}
        onRevise={kiosk ? undefined : reviseArtifact}
        onEdit={onEditArtifact}
      />}
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
          activeWork={loadedSid ? {
            sessionId: loadedSid,
            status: currentWorkStatus(snap, missionView, activityEvents),
            connected: connected && !snapQ.isError,
          } : undefined}
          localCwd={localCwd}
          onSelect={(id) => {
            selectProject(id);
            setSidebarOpen(false);
          }}
          onPrefetch={prefetchProject}
          onManage={requestManageSession}
          onResume={(sid) => void resumeSession(sid)}
          resumingId={resumingSid}
          onOpenPanel={(panel) => setOverlay(panel)}
          onOpenLearning={openLearningLibrary}
          onOpenResults={() => {
            const selection = defaultDeliverySelection(deliveryHistory, missionView?.routing.vertical || '');
            if (selection) { openDelivery(selection.receipt, selection.path); setSidebarOpen(false); }
          }}
          resultsCount={loadedSid ? deliveryHistory.length : 0}
          onNew={() => startNewSession()}
          onSearch={() => setSearchOpen(true)}
          // The trial portal owns sign-in; a token kept here is this browser's own.
          onSignOut={import.meta.env.VITE_ARGUS_HOSTED_TRIAL === '1' || !holdsToken() ? undefined : signOut}
          loading={projectsQ.isLoading}
          creating={creatingDaemon}
          error={projectsQ.isError ? errorText(projectsQ.error) : undefined}
          onRetry={() => void projectsQ.refetch()}
          mobileOpen={sidebarOpen}
          collapsed={!sidebarShown}
          onToggleCollapse={toggleSidebar}
          themeMode={themeMode}
          onCycleTheme={cycleTheme}
        />
      ) : null}
      {!kiosk && sidebarShown ? (
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

      {/* Both conversations (activity and the map's) show this project's files. */}
      <SessionIdContext.Provider value={snap?.session.id ?? null}>
      <WorkspaceRootsContext.Provider value={workspaceRoots}>
      <main className="flex min-w-0 flex-1 overflow-x-hidden">
        {snap ? (
          <>
            <section className={`${mobileView === 'activity' ? 'flex' : 'hidden'} ${workspaceView === 'map' && !kiosk ? 'mobile-scroll-region' : ''} glass-panel glass-panel--main h-full min-w-0 flex-1 flex-col lg:flex`}>
              <NoOperatorBanner sid={loadedSid} />
              {workspaceView !== 'map' && <TopBar
                events={activityEvents}
                snap={snap}
                streamOk={connected}
                onStart={requestStartDaemon}
                onStop={requestStopDaemon}
                onPause={requestPauseDaemon}
                onManage={() => activeSid && requestManageSession(activeSid)}
                onOpenPalette={() => setOverlay('palette')}
                busy={daemonBusy}
                snapshotStale={snapQ.isError}
                readOnly={kiosk}
                missionView={missionView}
              />}
              <nav aria-label={t('mobile.views')} className="hidden h-11 shrink-0 items-center gap-3 border-b border-line/60 px-5 lg:flex">
                <div className="workspace-tabs">
                  <button type="button" onClick={() => setWorkspaceView('activity')} className="workspace-tab" data-selected={workspaceView === 'activity'}>{t('mobile.activity')}</button>
                  <button type="button" onClick={() => setWorkspaceView('map')} className="workspace-tab" data-selected={workspaceView === 'map'}>{locale === 'zh-CN' ? '研究地图' : 'Research map'}</button>
                  <button type="button" onClick={() => setWorkspaceView('workbench')} className="workspace-tab" data-selected={workspaceView === 'workbench'}>{locale === 'zh-CN' ? '高级工作台' : 'Advanced workbench'}</button>
                </div>
                <button type="button" className="ml-auto flex items-center gap-1.5 px-2 py-1 text-sm text-ink-dim hover:text-ink"
                  onClick={() => setOverlay('palette')} title={t('topbar.commandsHint')}>
                  {t('topbar.commands')}
                  <kbd className="rounded border border-line px-1 font-mono text-[11px] leading-4 text-ink-faint">{/Mac|iPhone|iPad/.test(navigator.platform) ? '⌘K' : 'Ctrl K'}</kbd>
                </button>
                <button type="button" className="px-2 py-1 text-sm text-ink-dim hover:text-ink" aria-expanded={rightPanelOpen}
                  onClick={() => {
                    if (rightPanelOpen) { setRightPanelOpen(false); setMobileView('activity'); }
                    else { setPreviewPathRequest(current => ({ path: FILES_PATH, token: current.token + 1 })); openPreview(); }
                  }}>
                  {locale === 'zh-CN' ? '成果与文件' : 'Results & files'}{artifactsQ.data?.length ? ` · ${artifactsQ.data.filter(file => file.exists).length}` : ''}
                </button>
              </nav>
              {!kiosk && workspaceView === 'workbench' && <LearningStatus sid={activeSid} onOpenKnowledge={() => openWiki()} onOpenSkills={() => openSkillLibrary()} />}
              {workspaceView === 'map' && <Suspense fallback={<div className="m-auto text-sm text-ink-faint">{t('common.loading')}</div>}><MapPanel key={snap.session.id} immersive={mapImmersive} onImmersiveChange={setMapFull} snapshot={snap} events={mapEvents} managerSteps={managerSteps} draft={composerDraft} onDraftChange={setComposerDraft} onSend={sendComposerMessage} attachments={composerAttachments} onAttachmentsChange={changeComposerAttachments} pending={messagePending} onCancel={stopWaiting} focusSignal={composerFocus} readOnly={kiosk} onOpenSettings={openSettings}
                onPauseTeam={requestPauseDaemon} onResumeTeam={requestStartDaemon} teamBusy={daemonBusy}
                currentTaskId={missionView?.mission.id}
                routeOverride={routeOverride} onRouteOverrideChange={setRoute}
                conversationEvents={mapConversationEvents} connected={connected} artifacts={artifactsQ.data ?? []}
                deliveryCount={deliveryHistory.length} onOpenDelivery={() => {
                  const selection = defaultDeliverySelection(deliveryHistory, missionView?.routing.vertical || '');
                  if (selection) openDelivery(selection.receipt, selection.path);
                }}
                onOpenReceipt={openDelivery} onOpenArtifact={setArtifactPath} onAnswer={() => setPendingReplyOpen(true)}
                deliveries={deliveryHistory} onStartLaunch={snap.launch_hold ? startHeldLaunch : undefined}
              /></Suspense>}
              {workspaceView === 'map' && !kiosk && (missingAttachments.length > 0 || draftStorageError) && <div className="draft-status" role="status">
                <span className="text-warn">{draftStorageError
                  ? locale === 'zh-CN' ? '浏览器未能保存草稿，请在离开前复制内容。' : 'This browser could not save the draft. Copy it before leaving.'
                  : locale === 'zh-CN' ? '刷新前的附件尚未上传，请重新选择或移除提醒。' : 'Unuploaded files must be selected again or discarded.'}</span>
                <button type="button" onClick={() => { setWorkspaceView('activity'); setMobileView('activity'); }}>{locale === 'zh-CN' ? '前往对话处理' : 'Review in conversation'}</button>
              </div>}
              <div className={`${workspaceView === 'activity' ? 'flex' : 'hidden'} workspace-view-surface min-h-0 flex-1 flex-col`}>
                <GuardianBanner alert={guardianAlert} />
                <ProgressHealthBanner key={loadedSid || 'none'} sid={snap.session.id} health={health}
                  onPause={kiosk || !snap.daemon.alive ? undefined : requestPauseDaemon}
                  onAdjust={kiosk ? undefined : () => setComposerFocus((value) => value + 1)} />
                {snap.launch_hold ? <LaunchHoldCard
                  key={snap.launch_hold.created_at}
                  hold={snap.launch_hold}
                  backlog={snap.backlog}
                  readOnly={kiosk}
                  onStart={startHeldLaunch}
                  onRewrite={rewriteHeldLaunch}
                  onOpenArtifact={setArtifactPath}
                /> : null}
                {/* The mobile activity minimum includes its header/status and 120px of conversation. */}
                  <div className={`flex flex-1 flex-col ${compactViewport ? 'min-h-0' : 'min-h-[209px] lg:min-h-0'}`}>
                  <ReplyChoiceContext.Provider value={kiosk ? null : replyChoice}>
                  <EventStream
                    key={loadedSid}
                    snapshot={snap}
                    missionView={missionView}
                    events={activityEvents}
                    connected={connected && !snapQ.isError}
                    showReasoning={showReasoning}
                    historyStatus={historyStatus}
                    historyRefreshing={transcriptQ.isFetching}
                    refreshingKept={refreshingKept}
                    onPauseWork={snap.daemon.alive && !kiosk ? requestPauseDaemon : undefined}
                    onResumeWork={kiosk || daemonBusy ? undefined : requestStartDaemon}
                    onRetryHistory={() => void transcriptQ.refetch()}
                    embedded
                    filter={eventFilter}
                    query={eventQuery}
                    skipFirst={eventView.skipFirst}
                    artifacts={artifactsQ.data}
                    onOpenArtifact={focusDeliveryPath}
                    onOpenDelivery={openDelivery}
                    onCompose={kiosk ? undefined : composeContext}
                    onConversationAction={kiosk || messagePending || !editions ? undefined : setConversationEditor}
                    requestedMessageTs={highlightedTs}
                    rememberPosition
                  />
                  </ReplyChoiceContext.Provider>
                  </div>
                {!kiosk ? (
                  <div className="composer-dock shrink-0 px-4 pt-3">
                    <div className="mx-auto w-full max-w-full lg:max-w-[61.8vw]">
                    <PendingBanner
                      questions={snap.pending_questions ?? []}
                      backlog={snap.backlog}
                      currentTaskId={missionView?.mission.id}
                      onAnswer={() => setPendingReplyOpen(true)}
                    />
                    {revisionContext && <div className="experience-notice">
                      <span>{locale === 'zh-CN' ? '正在修订对话' : 'Revising chat'}</span>
                      <button type="button" onClick={() => setRevisionContext(null)}>{locale === 'zh-CN' ? '取消修订关联' : 'Detach revision'}</button>
                    </div>}
                    <ChatBox
                      key={activeSid || 'no-session'}
                      projectName={activeProjectName}
                      value={composerDraft}
                      attachments={composerAttachments}
                      onAttachmentsChange={changeComposerAttachments}
                      onChange={setComposerDraft}
                      onSend={sendComposerMessage}
                      onCancel={stopWaiting}
                      disabled={!activeSid}
                      pending={messagePending}
                      focusSignal={composerFocus}
                      embedded
                      onRewrite={rewriteDraft}
                      rewriting={rewriting}
                      slashSelection={slashSelection}
                      onSlashSelectionChange={setSlashSelection}
                      routeOverride={routeOverride}
                      onRouteOverrideChange={setRoute}
                      completion={promptSources}
                      settings={launches && loadedSid && routeOverride !== 'chat' ? <LaunchSettingsBar value={launchSettings} onChange={setLaunchSettings} ask={askMode} onAskChange={setAskMode}
                        started={!freshProject} research={missionView?.routing?.vertical ? missionView.routing.vertical === 'research' : null} disabled={messagePending} /> : undefined}
                      pendingNote={waitingForUploads ? (locale === 'zh-CN' ? '附件还在上传，传完后自动发送 · 按 Esc 取消发送' : 'Waiting for uploads; sends automatically when they finish · Esc to cancel') : undefined}
                      footer={activeSid ? <ComposerRuntime sid={snap.session.id} roles={snap.roles} running={snap.daemon.alive || Boolean(snap.manager_requests?.length)} lastCall={snap.last_call} onOpenSettings={openSettings} /> : undefined}
                      continuousControl={activeSid && continuous ? <ContinuousSwitch enabled={keepsGoing(continuous)} pending={actions.setContinuous.isPending} onToggle={toggleContinuous} /> : undefined}
                    />
                    {(missingAttachments.length > 0 || (draftStorageError && composerDraft)) && <div className="draft-status" role="status">
                      {draftStorageError && composerDraft ? <span>{locale === 'zh-CN' ? '浏览器未能保存草稿，请在离开前复制内容。' : 'This browser could not save the draft. Copy it before leaving.'}</span> : null}
                      {missingAttachments.length > 0 && <><span className="text-warn">{locale === 'zh-CN' ? '刷新前的附件尚未上传，请重新选择：' : 'Unuploaded files must be selected again: '}{missingAttachments.join('、')}</span><button type="button" onClick={dismissMissingAttachments}>{locale === 'zh-CN' ? '不再使用这些附件' : 'Discard missing attachments'}</button></>}
                    </div>}
                    </div>
                  </div>
                ) : null}
              </div>
              {workbenchOpened && activeSid ? (
                <div className={`${workspaceView === 'workbench' ? 'flex' : 'hidden'} workspace-view-surface min-h-0 flex-1`}>
                  <Suspense fallback={<div className="flex min-h-0 flex-1 items-center justify-center text-xs text-ink-faint">{t('common.loading')}</div>}>
                    <ResearchWorkbenchPanel sid={activeSid} active={workspaceView === 'workbench'} readOnly={kiosk} runtimeRequest={runtimeRequest}
                      snapshot={loadedSid ? snap : undefined} failed={snapQ.isError} events={events} connected={connected && !snapQ.isError}
                      refresh={() => void snapQ.refetch()}
                      controls={{
                        start: () => actions.startDaemon.mutateAsync().catch(() => null),
                        stop: drain => actions.stopDaemon.mutateAsync(drain).catch(() => null),
                        busy: actions.startDaemon.isPending || actions.stopDaemon.isPending,
                        error: [actions.startDaemon.error, actions.stopDaemon.error].filter(Boolean).map(errorText)[0] ?? '',
                      }}
                      onCompose={() => { setWorkspaceView('activity'); setMobileView('activity'); setComposerFocus(value => value + 1); }}
                      onRestored={async restoredSid => { await projectsQ.refetch(); selectProject(restoredSid); }} />
                  </Suspense>
                </div>
              ) : null}
            </section>
            {rightPanelOpen && workspaceView !== 'map' ? (
              <SplitHandle
                label={t('common.resizePreview')}
                value={rightWidth}
                min={320}
                max={PREVIEW_MAX_WIDTH}
                onPointerDown={(event) => resizeSidebar('right', event)}
                onReset={() => setRightWidth(PREVIEW_DEFAULT_WIDTH)}
                onNudge={(delta) => setRightWidth((value) => Math.max(320, Math.min(PREVIEW_MAX_WIDTH, value - delta)))}
              />
            ) : null}

            {((rightPanelOpen && workspaceView !== 'map') || mobileView === 'preview') && <aside
              data-resizable-panel="right"
              className={`${mobileView === 'preview' ? 'flex' : 'hidden'} relative min-w-0 flex-1 flex-col overflow-hidden border-l border-line/60 bg-panel lg:flex lg:flex-none lg:w-[var(--preview-width)]`}>
              <div className="lg:hidden">
                <TopBar
                  events={activityEvents}
                  snap={snap}
                  streamOk={connected}
                  onStart={requestStartDaemon}
                  onStop={requestStopDaemon}
                  onPause={requestPauseDaemon}
                  onManage={() => activeSid && requestManageSession(activeSid)}
                  onOpenPalette={() => setOverlay('palette')}
                  busy={daemonBusy}
                  snapshotStale={snapQ.isError}
                  readOnly={kiosk}
                  missionView={missionView}
                />
              </div>
              <ResearchCanvas
                snapshot={snap}
                connected={connected && !snapQ.isError}
                sid={loadedSid}
                artifacts={artifactsQ.data}
                error={artifactsQ.isError}
                onExpand={setArtifactPath}
                onOpenFile={openPreview}
                className="min-h-0 flex-1 mobile-scroll-region"
                embedded
                onCollapse={() => { setRightPanelOpen(false); setMobileView('activity'); }}
                missionView={missionView}
                activityEvents={activityEvents}
                requestedPath={previewPathRequest.path}
                requestedPathToken={previewPathRequest.token}
                filesFirst
                latestDelivery={deliveryHistory[0] ?? null}
                deliveriesLoaded={eventsSeeded && transcriptQ.isFetchedAfterMount && Boolean(snap)}
                onOpenDelivery={(receipt) => openDelivery(receipt)}
                loading={artifactsQ.isLoading}
                onRevise={kiosk ? undefined : reviseArtifact}
                onEdit={onEditArtifact}
                onRetry={() => void artifactsQ.refetch()}
                routeVisible
              />
            </aside>}
          </>
        ) : (
          <Landing
            loading={projectsQ.isLoading || Boolean(activeSid && snapQ.isLoading)}
            hasProjects={projects.length > 0 && !starting}
            error={
              projectsQ.isError && projects.length === 0
                ? landingErrorText(projectsQ.error)
                : snapQ.isError && !snap
                ? landingErrorText(snapQ.error)
                : undefined
            }
            onRetry={() => {
              void projectsQ.refetch();
              if (activeSid) void snapQ.refetch();
            }}
            onNew={startNewSession}
            onSubmit={async (text, files) => {
              if (homeRequest.current) return false;
              homeRequest.current = { text, files };
              setWorkspaceView('activity'); setMobileView('activity');
              const created = await createDaemon('', '', '', text);
              if (!created) homeRequest.current = null;
              return created;
            }}
            onChoose={() => setSidebarOpen(true)}
            canCreate={!kiosk}
            creating={creatingDaemon}
          />
        )}
      </main>
      </WorkspaceRootsContext.Provider>
      </SessionIdContext.Provider>

      {/* global overlays */}
      <HistorySearch open={searchOpen} onClose={() => setSearchOpen(false)} onChoose={result => {
        setSearchTarget(result); selectProject(result.sid);
        setWorkspaceView('activity'); setMobileView('activity'); setSidebarOpen(false);
      }} />
      {activeSid && editor && <ArtifactEditor key={`${activeSid}:${editor.path}:${editor.version ?? ''}`} sid={activeSid}
        path={editor.path} initialVersion={editor.version} onClose={() => setEditor(null)}
        onRequest={text => { if (composeContext(text)) setEditor(null); }} />}
      {activeSid && conversationEditor && <ConversationEditor key={`${activeSid}:${conversationEditor.source}`} sid={activeSid}
        target={conversationEditor} onClose={() => setConversationEditor(null)}
        onCompose={(text, target) => {
          if (composerDraft && !window.confirm(locale === 'zh-CN' ? '替换输入框中尚未发送的草稿？' : 'Replace the unsent composer draft?')) return;
          setComposerDraft(text); setRevisionContext(target);
          setConversationEditor(null); setWorkspaceView('activity'); setMobileView('activity'); setComposerFocus(value => value + 1);
        }} />}
      <Modal open={overlay === 'learning'} onClose={() => setOverlay('none')} label={locale === 'zh-CN' ? '知识与技能' : 'Knowledge & skills'} width="max-w-6xl">
        {overlay === 'learning' && <LearningLibrary sid={activeSid} initialSelection={learningSelection} initialScope={learningScope}
          onUse={kiosk ? undefined : useMaterial}
          projectNames={Object.fromEntries(projects.flatMap(project => project.display_name ? [[project.id, project.display_name] as const] : []))} />}
      </Modal>
      <CommandPalette open={overlay === 'palette'} onClose={() => setOverlay('none')} items={paletteItems} />
      <KeybindingHelp open={overlay === 'help'} onClose={() => setOverlay('none')} />
      {activeSid && <DoctorModal sid={activeSid} open={overlay === 'doctor'} onClose={() => setOverlay('none')} />}
      {activeSid && (
        <ConfigModal
          sid={activeSid}
          themeMode={themeMode}
          onCycleTheme={cycleTheme}
          open={overlay === 'config'}
          onClose={() => setOverlay('none')}
        />
      )}
      {activeSid && <IdentityModal sid={activeSid} open={overlay === 'identity'} onClose={() => setOverlay('none')} />}
      {activeSid && <TranscriptModal sid={activeSid} open={overlay === 'transcript'} onClose={() => setOverlay('none')} />}
      {activeSid && snap ? (
        <ProjectInspectorModal
          open={overlay === 'inspector'}
          snap={snap}
          onClose={() => setOverlay('none')}
          onInspect={setTaskItemId}
        />
      ) : null}
      <ArtifactModal sid={activeSid} path={artifactPath} artifacts={artifactsQ.data ?? []}
        onRevise={kiosk ? undefined : reviseArtifact}
        onEdit={onEditArtifact}
        reviewActivity={activeSid === loadedSid ? reviewActivity : undefined}
        onSelectPath={setArtifactPath}
        onClose={() => setArtifactPath(null)} />
      <TaskDetailModal sid={activeSid} itemId={taskItemId} onClose={() => setTaskItemId(null)} />
      <PendingReplyDialog
        reply={pendingReply}
        open={pendingReplyOpen}
        busy={pendingReplyBusy}
        error={pendingReplyError}
        onClose={() => setPendingReplyOpen(false)}
        onSubmit={answerPendingReply}
      />
      {manageTargetSid ? (
        <DaemonManageModal
          open={daemonManageOpen}
          sid={manageTargetSid}
          name={manageSnapQ.data?.session.display_name
            || projects.find((project) => project.id === manageTargetSid)?.display_name
            || projects.find((project) => project.id === manageTargetSid)?.label
            || ''}
          alive={manageSnapQ.data?.daemon.alive
            ?? Boolean(projects.find((project) => project.id === manageTargetSid)?.daemon_alive)}
          controlAvailable={manageSnapQ.data?.daemon.control_available !== false}
          snapshot={manageSnapQ.data}
          busy={daemonBusy}
          onClose={() => {
            setDaemonManageOpen(false);
            setManageTargetSid(null);
          }}
          onRename={manageRenameProject}
          onStart={manageStartDaemon}
          onStop={manageStopDaemon}
          onDelete={async () => {
            const deletedSid = manageTargetSid;
            const deleted = await manageDeleteProject();
            if (deleted && deletedSid) dropSessionDraft(deletedSid);
            return deleted;
          }}
        />
      ) : null}
      <ActionNotice notice={notice} onClose={dismissNotice} />
      {snap && !kiosk ? (
        <MobileTabBar
          active={mobileView === 'preview' ? 'preview' : workspaceView}
          sidebarOpen={sidebarOpen}
          onSelect={(tab) => {
            if (tab === 'preview') {
              setPreviewPathRequest(current => ({ path: FILES_PATH, token: current.token + 1 }));
              setMobileView('preview');
              return;
            }
            setMobileView('activity');
            setWorkspaceView(tab);
          }}
          onOpenSessions={() => setSidebarOpen(true)}
        />
      ) : null}
    </WorkspaceShell>
    </ProgressQuestionsProvider>
    </DraftUploadContext.Provider>
  );
}
