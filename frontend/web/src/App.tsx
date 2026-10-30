import { useQuery } from '@tanstack/react-query';
import { ApiError } from '../../core/src/http';
import type { MapSendOptions } from './map/submission';
import { lazy, Suspense, useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import { useProjects, useSnapshot, useProjectActions, useArtifacts, useWorkspaceTree } from './hooks';
import { useConversationHistory } from './useConversationHistory';
import { api, holdsToken, isConnectionError, newRequestId, signOut, type AttachmentUploadOptions, type UploadedAttachment, type EventMsg, type MessageRouteOverride, type SkillLibraryItem, type SkillScope, PairingRequiredError } from './api';
import { LearningLibrary, type LearningScope, type LearningSelection } from './components/LearningLibrary';
import { LearningStatus } from './components/LearningStatus';
import { ComposerRuntime } from './components/ComposerRuntime';
import { TopBar } from './components/TopBar';
import { ReplyChoiceContext } from './components/ReplyOptions';
import { WorkspaceHeader, WorkspaceShell } from './components/WorkspaceShell';
import { EventStream, latestConversationDelivery, SessionIdContext, WorkspaceRootsContext } from './components/EventStream';
import { ChatBox } from './components/ChatBox';
import { promptHistory, type PromptSources } from './lib/promptCompletion';
import { CommandPalette, type PaletteItem } from './components/CommandPalette';
import { KeybindingHelp } from './components/KeybindingHelp';
import { ConfigModal } from './components/InfoModals';
import { rankProjects } from '../../core/src/projects';
import { ArtifactModal } from './components/ArtifactModal';
import {
  ResearchCanvas,
  FILES_PATH,
} from './components/ResearchCanvas';
import { ActionNotice, type NoticeAction, type NoticeTone, type UiNotice } from './components/ActionNotice';
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
import { useQueryClient } from '@tanstack/react-query';
import { EDITIONS_CAPABILITY, ROUTE_CAPABILITY, SETTINGS_CAPABILITY, useCapability } from './lib/capabilities';
import {
  mergeManagerReply,
  optimisticOperatorEvent,
} from './lib/conversationEvents';
import { errorText, shortcut } from './lib/format';
import { useCreateDaemonSession } from './useCreateDaemonSession';
import { useGlobalKeyboardShortcuts } from './useGlobalKeyboardShortcuts';
import { usePendingReplySession } from './usePendingReplySession';
import { questionRole } from '../../core/src/decisions';
import { useProjectSelection, type ProjectHistoryMode } from './useProjectSelection';
import { useSessionComposer, type DraftSnapshot } from './useSessionComposer';
import { ArtifactEditor, HistorySearch, ProjectTrash } from './components/ExperiencePanels';
import type { SearchResult } from './api';
import { useDesktopDeliveryNavigation } from './useDesktopDeliveryNavigation';
import { useWorkbenchLayout } from './useWorkbenchLayout';
import { PREVIEW_DEFAULT_WIDTH, PREVIEW_MAX_WIDTH } from './lib/previewLayout';
import { translate, useI18n } from './i18n';
import { ConnectionProblemBanner } from './components/ConnectionProblemBanner';
import { addComposerFiles, MESSAGE_TEXT_MAX_CHARS } from './lib/attachments';
import './components/customerExperience.css';
import { useDeliveryCenter } from './useDeliveryCenter';
import { deliveryFiles, defaultDeliverySelection } from './components/deliveryPresentation';
import { projectAttention } from './lib/attention';
import { useAttentionSignals } from './useAttentionSignals';
import { DraftUploadContext, DraftUploads, type DraftUploadView } from './lib/draftUploads';
import type { DeliveryReceipt } from '../../core/src/types';
import {
  installDesktopExternalLinkBridge,
  subscribeDesktopNewChat,
} from './lib/desktopBridge';

type Overlay = 'none' | 'palette' | 'help' | 'config' | 'inspector' | 'learning';
interface ActiveMessageRequest {
  id: number;
  sid: string;
  controller: AbortController;
  recovered?: boolean;
  uploading?: boolean;

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

export default function App() {
  const { locale, t } = useI18n();
  // The pairing error carries a fixed English message; show the localized copy instead.
  const landingErrorText = (error: unknown): string =>
    error instanceof PairingRequiredError ? t(holdsToken() ? 'connection.invalidLink' : 'connection.pairingDetail') : errorText(error);
  const queryClient = useQueryClient();
  const projectsQ = useProjects();
  const projects = useMemo(() => rankProjects(projectsQ.data?.projects ?? []), [projectsQ.data?.projects]);
  const localCwd = projectsQ.data?.local_cwd ?? '';
  const connectionError = [projectsQ.error]
    .find((error) => isConnectionError(error));

  const [overlay, setOverlay] = useState<Overlay>('none');
  const openSettings = useCapability(SETTINGS_CAPABILITY) ? () => setOverlay('config') : undefined;
  const [learningSelection, setLearningSelection] = useState<LearningSelection | null>(null);
  const [learningScope, setLearningScope] = useState<LearningScope>('feed');
  const openLearningLibrary = useCallback((scope: LearningScope) => {
    setLearningSelection(null);
    setLearningScope(scope);
    setOverlay('learning');
  }, []);
  const openSkillLibrary = useCallback((item?: SkillLibraryItem, scope: SkillScope | 'recent' = 'recent') => {
    setLearningSelection(item ?? null);
    setLearningScope(item || scope !== 'recent' ? 'global' : 'feed');
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
    rightPanelVisible,
    rightWidth,
    setLeftWidth,
    setMapFull,
    setMobileView,
    setRightPanelOpen,
    setRightWidth,
    setSidebarOpen,
    setWorkspaceView,
    shellRef,
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
  const focusComposer = useCallback(() => {
    setWorkspaceView('activity'); setMobileView('activity');
    setComposerFocus(value => value + 1);
  }, [setWorkspaceView, setMobileView]);
  const [completionSelection, setCompletionSelection] = useState(0);
  // The project whose composer caret is in an @ word, so its workspace listing is wanted.
  const [treeSid, setTreeSid] = useState<string | null>(null);
  const [routeOverride, setRouteOverride] = useState<MessageRouteOverride>('auto');
  // Without the choice a message goes as Auto: the server decides whether to answer or hand it on.
  const setRoute = useCapability(ROUTE_CAPABILITY) ? setRouteOverride : undefined;
  const editions = useCapability(EDITIONS_CAPABILITY);
  const [chatPending, setChatPending] = useState(false);
  const [waitingForUploads, setWaitingForUploads] = useState(false);
  const [localConversationEvents, setLocalConversationEvents] = useState<EventMsg[]>([]);
  const localConversationSid = useRef<string | null>(null);
  const [artifactPath, setArtifactPath] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ path: string; version?: number } | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [trashOpen, setTrashOpen] = useState(false);
  const [searchTarget, setSearchTarget] = useState<SearchResult | null>(null);
  const homeRequest = useRef<{ text: string; files: File[] } | null>(null);
  const [initialSend, setInitialSend] = useState<{ sid: string; text: string; files: File[] } | null>(null);
  const [previewPathRequest, setPreviewPathRequest] = useState({ path: '', token: 0 });
  const [taskItemId, setTaskItemId] = useState<string | null>(null);
  const [daemonManageOpen, setDaemonManageOpen] = useState(false);
  const [manageTargetSid, setManageTargetSid] = useState<string | null>(null);
  const messageRequestRef = useRef<ActiveMessageRequest | null>(null);
  const messageEpochRef = useRef(0);
  const cancelDeliveryNavigationRef = useRef<() => void>(() => undefined);
  const [notice, setNotice] = useState<UiNotice | null>(null);
  const dismissNotice = useCallback(() => setNotice(null), []);
  const dismissUnconfirmedNotice = useCallback(() => setNotice(current =>
    current?.message === translate('error.message_failed', {}, 'en')
      || current?.message === translate('error.message_failed', {}, 'zh-CN') ? null : current), []);
  const notify = useCallback((tone: NoticeTone, message: string, details?: unknown, action?: NoticeAction) => {
    const id = ++noticeSequence;
    setNotice({ id, tone, message, details, action });
    return id;
  }, []);

  const cancelActiveMessage = useCallback(() => {
    const cancelled = Boolean(messageRequestRef.current);
    messageEpochRef.current += 1;
    messageRequestRef.current?.controller.abort();
    messageRequestRef.current = null;
    setChatPending(false);
    setWaitingForUploads(false);

    return cancelled;
  }, []);

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
    projectsReady: projectsQ.isFetchedAfterMount,
    queryClient,
    setArtifactPath,
    setSidebarOpen,
    setTaskItemId,
  });
  const {
    draft: { text: composerDraft, requestId: composerRequestId, attachments: composerAttachments, missingAttachments = [], uploaded: composerUploaded = [] },
    storageError: draftStorageError, seed: seedDraft, dismissMissingAttachments,
    rewriting, undoRewrite, setText: setComposerDraft, setAttachments: setComposerAttachments,
    capture: captureDraft, consume: consumeDraft,
    beginRewrite, finishRewrite, drop: dropSessionDraft,
    rememberUploaded, removeUploaded,
  } = useSessionComposer(selectedSid);
  useEffect(dismissUnconfirmedNotice, [composerRequestId, selectedSid, dismissUnconfirmedNotice]);
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
  const attachmentLimits = useQuery({ queryKey: ['attachment-limits', activeSid], queryFn: ({ signal }) => api.attachmentLimits(activeSid!, signal), enabled: !!activeSid });
  const draftUploadView = useMemo<DraftUploadView>(() => ({
    status: file => draftUploads.status(file),
    retry: file => draftUploads.retry(file),
    limits: attachmentLimits.data,
    ready: composerUploaded.filter(item => !item.file).map(item => item.attachment),
    removeReady: (attachmentId) => {
      removeUploaded(attachmentId);
      if (selectedSid) void api.discardAttachment(selectedSid, attachmentId).catch(() => undefined);
    },
    busy: draftUploads.busy(composerAttachments),
    // uploadsVersion: the store changed, so statuses and the busy count did too.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [composerAttachments, composerUploaded, draftUploads, removeUploaded, selectedSid, uploadsVersion, attachmentLimits.data]);
  const selectProject = useCallback((id: string, mode?: ProjectHistoryMode) => {
    cancelDeliveryNavigationRef.current();
    setComposerFocus(0);
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
   * edits it before anything is dispatched, and can put the original back until
   * they edit it. A failed rewrite leaves the original untouched.
   */
  const rewriteDraft = useCallback((draft: string) => {
    const body = (draft || '').trim();
    const sid = sidRef.current;
    if (!body || !sid) return;
    const operation = beginRewrite(sid);
    if (!operation) return;
    const failed = (reason: unknown, code?: string) => {
      if (!finishRewrite(operation) || sidRef.current !== sid) return;
      notify('error', errorText(reason, locale, code || 'rewrite_failed'), reason);
    };
    void api.rewritePrompt(sid, body).then(
      (result) => {
        if (result.error || !result.rewritten.trim()) return failed(result.error, result.error_code);
        if (finishRewrite(operation, result.rewritten) && sidRef.current === sid) setComposerFocus((x) => x + 1);
      },
      (error) => failed(error),
    );
  }, [beginRewrite, finishRewrite, notify, sidRef, locale]);

  const { createDaemon, creatingDaemon, createError, clearCreateError } = useCreateDaemonSession({
    localCwd,
    onFocusComposer: () => setComposerFocus((value) => value + 1),
    queryClient,
    refetchProjects: projectsQ.refetch,
    selectProject,
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
    setComposerFocus(value => value + 1);
  }, [clearProjectSelection, setWorkspaceView, setMobileView]);
  useEffect(() => { if (activeSid) setStarting(false); }, [activeSid]);
  const startNewSessionRef = useRef(startNewSession);
  startNewSessionRef.current = startNewSession;
  useEffect(() => subscribeDesktopNewChat(() => startNewSessionRef.current()), []);

  const snapQ = useSnapshot(activeSid);
  const manageSnapQ = useSnapshot(projects.some(project => project.id === manageTargetSid) ? manageTargetSid : null);
  const snap = snapQ.data;
  const showProject = Boolean(snap || selectedSid && projects.length > 0 && !projectsQ.isLoading);
  const loadedSid = snap?.session.id === activeSid ? activeSid : null;
  useEffect(() => {
    setEditor(null);
  }, [activeSid]);
  const artifactsQ = useArtifacts(loadedSid, true);
  const { query: transcriptQ, events: activityEvents, connected, status: historyStatus, pagination: conversationPagination } = useConversationHistory(
    activeSid, true, localConversationEvents, localConversationSid.current,
  );
  const workspaceTreeQ = useWorkspaceTree(loadedSid, treeSid === loadedSid);
  const promptHistoryList = useMemo(() => promptHistory(activityEvents), [activityEvents]);
  const promptSources = useMemo<PromptSources | undefined>(() => loadedSid ? {
    sid: loadedSid, tasks: snap?.backlog ?? [], history: promptHistoryList, files: workspaceTreeQ.data?.entries,
    wantFiles: (want) => setTreeSid(want ? loadedSid : null),
  } : undefined, [loadedSid, snap?.backlog, promptHistoryList, workspaceTreeQ.data]);
  // Content kept from an earlier visit shows at once; until its first refresh
  // lands it is marked as updating rather than presented as live.
  const refreshingKept = [snapQ, transcriptQ].some(
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
    }
  }, [activeManagerRequestId, loadedSid]);
  const {
    answerPendingReply,
    pendingReply,
    pendingReplyBusy,
  } = usePendingReplySession({
    activeSid: loadedSid,
    backlog: snap?.backlog,
    notify,
    pendingQuestions: snap?.pending_questions,
    refetchSnapshot: snapQ.refetch,
  });
  const delivery = useMemo(
    () => latestConversationDelivery(activityEvents),
    [activityEvents],
  );
  const attention = useMemo(() => projectAttention({
    snapshot: loadedSid ? snap : undefined,
    delivery,
    zh: locale === 'zh-CN',
  }), [loadedSid, snap, delivery, locale]);
  const projectBusy = useMemo(() => {
    if (!snap?.daemon.alive) return false;
    const state = currentWorkStatus(snap).state;
    return state === 'running' || state === 'waiting';
  }, [snap]);
  useAttentionSignals({
    projectName: projects.find(project => project.id === activeSid)?.display_name?.trim()
      || (loadedSid === activeSid ? snap?.session.display_name?.trim() || snap?.session.objective.split('\n')[0]?.trim() : '') || '',
    attention,
    zh: locale === 'zh-CN',
    running: projectBusy,
  });
  const openPreview = useCallback(() => {
    expandPreview();
  }, [expandPreview]);
  const focusDeliveryPath = useCallback((path: string, userInitiated = true) => {
    const target = path.trim();
    if (!target) {
      if (userInitiated) setWorkspaceView('map');
      return;
    }
    if (workspaceView === 'map' || !artifactsQ.data?.some(file => file.path === target)) {
      setArtifactPath(target);
      return;
    }
    setRightPanelOpen(true);
    setMobileView('preview');
    setPreviewPathRequest((current) => ({ path: target, token: current.token + 1 }));
  }, [artifactsQ.data, setMobileView, setRightPanelOpen, setWorkspaceView, workspaceView]);
  const deliveryCenter = useDeliveryCenter(loadedSid);
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
    for (const receipt of [delivery]) {
      if (receipt) receipts.set(receipt.delivery_id, receipt);
    }
    return [...receipts.values()].filter((receipt) => deliveryFiles(receipt).length).sort((a, b) => b.delivered_at - a.delivered_at);
  }, [activityEvents, delivery]);
  const deliveryCount = deliveryHistory.length;
  cancelDeliveryNavigationRef.current = useDesktopDeliveryNavigation({
    selectedSid, loadedSid, sidRef, snapshotError: snapQ.error, queryClient,
    receipts: transcriptQ.isPending ? [] : deliveryHistory,
    // Notification navigation uses the same selection path but is not a new
    // manual intent. Ordinary sidebar/palette selections cancel it.
    selectProject: (id) => { selectProjectBase(id); setSidebarOpen(false); },
    openDelivery,
    focusPath: (path) => {
      // A verified file can precede its listing refresh. The side preview only
      // selects listed files; use the existing direct modal rather than its
      // default-file fallback when the target has not reached that index yet.
      if (artifactsQ.data?.some(file => file.path === path)) focusDeliveryPath(path);
      else setArtifactPath(path);
    },
    noPath: () => { setMobileView('activity'); setWorkspaceView('map'); },
    notify,
  });
  // Reset the local conversation when the active project changes.
  useEffect(() => {
    setLocalConversationEvents([]);
  }, [loadedSid]);
  const actions = useProjectActions(activeSid);
  const manageActions = useProjectActions(manageTargetSid);
  const daemonBusy = actions.startDaemon.isPending
    || actions.stopDaemon.isPending
    || manageActions.updateProject.isPending
    || manageActions.deleteProject.isPending;

  const actionFeedback = useCallback((success: string) => ({
    onSuccess: () => notify('success', success),
    onError: (error: Error) => notify('error', errorText(error), error),
  }), [notify]);

  const requestStartDaemon = useCallback(() =>
    actions.startDaemon.mutate(undefined, actionFeedback(t('daemon.startRequested'))),
  [actionFeedback, actions.startDaemon, t]);

  /** Interrupt the current work; the conversation records what waits for Resume. */
  const requestStopDaemon = useCallback(() =>
    actions.stopDaemon.mutate(undefined, {
      onError: (error: Error) => notify('error', errorText(error), error),
    }),
  [notify, actions.stopDaemon, t]);

  const manageRenameProject = useCallback(async (name: string): Promise<boolean> => {
    if (!manageTargetSid) return false;
    try {
      await manageActions.updateProject.mutateAsync({ sid: manageTargetSid, name });
      notify('success', t('project.renamed'));
      return true;
    } catch (error) {
      notify('error', errorText(error), error);
      return false;
    }
  }, [manageActions.updateProject, manageTargetSid, notify, t]);

  const manageDeleteProject = useCallback(async (): Promise<boolean> => {
    if (!manageTargetSid) return false;
    try {
      const deletedSid = manageTargetSid;
      const deleted = await manageActions.deleteProject.mutateAsync();
      setDaemonManageOpen(false);
      setSidebarOpen(false);
      setManageTargetSid(null);
      const refreshed = await projectsQ.refetch();
      if (deletedSid === activeSid) {
        const next = rankProjects(refreshed.data?.projects ?? [])[0];
        if (next) selectProject(next.id, 'replace');
        else {
          setComposerFocus(0);
          clearProjectSelection('replace');
        }
      }
      notify('success', t('project.trashed'), undefined, {
        label: t('project.undoDelete'),
        onClick: async () => {
          try {
            const restored = await api.restoreTrash(deleted.trash_path.split(/[\\/]/).at(-1)!);
            await projectsQ.refetch();
            selectProject(restored.sid);
            notify('success', t('project.restored'));
          } catch (error) {
            notify('error', errorText(error), error);
          }
        },
      });
      return true;
    } catch (error) {
      notify('error', errorText(error), error);
      return false;
    }
  }, [activeSid, clearProjectSelection, manageActions.deleteProject, manageTargetSid, notify, projectsQ.refetch, selectProject, setDaemonManageOpen, setManageTargetSid, setSidebarOpen, t]);

  const requestManageSession = useCallback((projectId: string) => {
    setManageTargetSid(projectId);
    setDaemonManageOpen(true);
  }, [setDaemonManageOpen, setManageTargetSid]);

  // The one stop: a send still waiting on its uploads is withdrawn, as nothing of it reached the
  // server; what is under way there stops, a message cut off waiting for Resume.
  const stopWork = () => {
    const request = messageRequestRef.current;
    if (request?.uploading) {
      cancelActiveMessage();
      notify('info', locale === 'zh-CN' ? '已取消发送。附件会继续上传，文字和附件都还在输入框里。' : 'Sending cancelled. Uploads continue; your text and files stay in the composer.');
    }
    if (snap?.daemon.alive || (request && !request.uploading)) requestStopDaemon();
  };
  useGlobalKeyboardShortcuts({
    focusComposer: () => setComposerFocus((value) => value + 1),
    openHelp: () => setOverlay('help'),
    togglePalette: () => setOverlay((current) => current === 'palette' ? 'none' : 'palette'),
    toggleSidebarCollapse: toggleSidebar,
  });

  const prepareMessageAttachments = async (requestSid: string, files: File[], options?: AttachmentUploadOptions, signal: AbortSignal = new AbortController().signal): Promise<UploadedAttachment[]> => {
    if (!files.length && !options?.ready?.length) return [];
    const known = options?.completed ?? new Map<File, never>();
    if (files.some(file => !known.has(file))) setWaitingForUploads(true);
    try {
      const completed = await draftUploads.settle(requestSid, files, known, signal);
      signal?.throwIfAborted();
      for (const [file, attachment] of completed) if (!known.has(file)) options?.onUploaded?.(file, attachment);
      return (await api.uploadAttachments(requestSid, files, signal, { ready: options?.ready, completed })).attachments;
    } finally { setWaitingForUploads(false); }
  };

  const sendMessage = async (text: string, attachments: File[] = [], uploadOptions?: AttachmentUploadOptions, draft?: DraftSnapshot): Promise<boolean> => {
    const requestSid = activeSid;
    if (rewriting || !requestSid || requestSid !== sidRef.current || messageRequestRef.current || draft && !draft.requestId) return false;
    const requestId = ++messageEpochRef.current;
    const serverRequestId = draft?.requestId || newRequestId();
    const controller = new AbortController();
    messageRequestRef.current = { id: requestId, sid: requestSid, controller };
    const isCurrent = () => messageRequestRef.current?.id === requestId
      && sidRef.current === requestSid && !controller.signal.aborted;
    const reset = () => {
      if (messageRequestRef.current?.id !== requestId) return;
      messageRequestRef.current = null;
      setChatPending(false);
      setWaitingForUploads(false);
    };
    setChatPending(true);
    let attachmentRefs: UploadedAttachment[] = [];
    if (attachments.length || uploadOptions?.ready?.length) {
      if (messageRequestRef.current) messageRequestRef.current.uploading = true;
      try {
        attachmentRefs = await prepareMessageAttachments(requestSid, attachments, uploadOptions, controller.signal);
        if (!isCurrent()) return false;
        if (messageRequestRef.current) messageRequestRef.current.uploading = false;
      } catch (error) {
        if (isCurrent()) { notify('error', t('chat.attachmentUploadFailed', { error: errorText(error) })); reset(); }
        return false;
      }
    }
    // The draft and its request identity remain until the server confirms receipt.
    // A user retry sends that same identity; the server alone prevents duplicate work.
    void (async () => {
      let accepted = false;
      let error: Error | null = null;
      let named = Boolean(snap?.session.objective);
      const accept = () => {
        if (accepted) return;
        accepted = true;
        if (!isCurrent()) return;
        const same = localConversationSid.current === requestSid;
        localConversationSid.current = requestSid;
        setLocalConversationEvents(current => [
          ...(same ? current.filter(event => event.request_id !== serverRequestId) : []),
          optimisticOperatorEvent(requestSid, serverRequestId, text, Date.now(), attachmentRefs),
        ]);
        if (draft) { consumeDraft(draft, attachments); draftUploads.forget(attachments); }
        dismissUnconfirmedNotice();
        if (!named) {
          named = true;
          void projectsQ.refetch();
          void queryClient.invalidateQueries({ queryKey: ['snapshot', requestSid] });
        }
      };
      try {
        try {
          await api.messageStream(requestSid, text, {
            onAccepted: accept,
            onDelta: reply => {
              accept();
              if (isCurrent()) setLocalConversationEvents(current => mergeManagerReply(current, serverRequestId, reply));
            },
            onDone: result => {
              accept();
              if (!isCurrent() || result.kind === 'received') return;
              setRouteOverride('auto');
              void queryClient.invalidateQueries({ queryKey: ['artifacts', requestSid] });
              if (result.kind === 'task') {
                if (result.reply && !draft) notify('success', result.reply);
                void snapQ.refetch();
              }
            },
            onError: reason => { error = reason; },
          }, { signal: controller.signal, attachments: attachmentRefs, routeOverride, requestId: serverRequestId });
        } catch (reason) { error = reason as Error; }
        if (isCurrent() && error) {
          if (!accepted) {
            notify('error', error instanceof ApiError && error.status ? errorText(error) : t('error.message_failed'), error);
          }
        }
      } finally { reset(); }
    })();
    return true;
  };

  const sendMessageRef = useRef(sendMessage);
  sendMessageRef.current = sendMessage;

  const workspaceRoots = useMemo(() => [snap?.session.workdir].filter((root): root is string => Boolean(root)), [snap?.session.workdir]);
  const replyChoice = useCallback((choice: string) => { void sendMessageRef.current(choice); }, []);
  const sendComposerMessage = async (_text: string, _files: File[] = [], options?: MapSendOptions): Promise<boolean> => {
    if (rewriting) return false;
    const draft = captureDraft(sidRef.current);
    if (!draft || !draft.requestId || draft.sid !== activeSid) return false;
    const text = draft.text.trim();
    const files = draft.attachments;
    if (!text) return false;
    if (draft.missingAttachments?.length) {
      notify('error', locale === 'zh-CN' ? '刷新前的附件尚未上传。请重新选择，或在对话中移除提醒后再发送。' : 'Files selected before reloading were not uploaded. Select them again or discard their reminder in the conversation before sending.');
      return false;
    }
    const uploadOptions: AttachmentUploadOptions = {
      ready: draft.uploaded?.filter(item => !item.file).map(item => item.attachment),
      completed: new Map(draft.uploaded?.flatMap(item => item.file ? [[item.file, item.attachment] as const] : [])),
      onUploaded: (file, attachment) => rememberUploaded(draft, file, attachment),
    };
    if (draft.sid !== sidRef.current) return false;
    if (pendingReply) {
      const accepted = await answerPendingReply(text, () => prepareMessageAttachments(draft.sid, files, uploadOptions));
      if (accepted) {
        consumeDraft(draft, files);
        draftUploads.forget(files);
      }
      return accepted;
    }
    if (options?.whileRunning) {
      if (files.length || draft.uploaded?.length) {
        notify('error', t('chat.attachWhileRunning'));
        return false;
      }
      // The running reply keeps its own request; this message is answered as the next turn.
      try {
        await api.queueFollowup(draft.sid, text, routeOverride, draft.requestId);
      } catch (error) {
        if (draft.sid === sidRef.current) notify('error', error instanceof ApiError && error.status ? errorText(error) : t('error.message_failed'), error);
        return false;
      }
      if (draft.sid === sidRef.current) dismissUnconfirmedNotice();
      consumeDraft(draft, files);
      return true;
    }
    return sendMessage(text, files, uploadOptions, draft);
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
    else if (searchTarget.kind === 'conversation') return; // The conversation consumes it after history is ready.
    else if (searchTarget.task_id) {
      setWorkspaceView('map'); setMobileView('activity');
      return; // The map consumes the search result once its card is ready.
    }
    setSearchTarget(null);
  }, [searchTarget, loadedSid, focusDeliveryPath, setWorkspaceView, setMobileView]);

  const paletteItems: PaletteItem[] = useMemo(() => {
    const nav: PaletteItem[] = [
      ...(kiosk ? [] : [{ id: 'new', label: t('palette.newDaemon'), hint: '+', group: t('palette.view'), run: startNewSession }]),
      { id: 'history-search', label: locale === 'zh-CN' ? '搜索历史与成果' : 'Search history and results', group: t('palette.view'), run: () => setSearchOpen(true) },
      { id: 'inspector', label: t('palette.openProject'), hint: t('palette.projectHint'), group: t('palette.view'), run: () => setOverlay('inspector') },
      { id: 'operations', label: t('palette.openOperations'), hint: t('palette.operationsHint'), group: t('palette.view'), run: openRuntimeControls },
      { id: 'help', label: t('help.title'), hint: '?', group: t('palette.view'), run: () => setOverlay('help') },
    ];
    const acts: PaletteItem[] = kiosk
      ? []
      : [
          { id: 'message', label: t('palette.messageArgus'), hint: '/', group: t('palette.action'), run: () => setComposerFocus((x) => x + 1) },
          ...(snap?.daemon.resumable ? [{ id: 'start', label: t('palette.startDaemon'), group: t('palette.action'), run: requestStartDaemon }] : []),
        ];
    const proj: PaletteItem[] = projects.map((p) => ({
      id: `p-${p.id}`,
      label: p.display_name?.trim() || t('sidebar.unnamedSession'),
      hint: p.daemon_alive ? `● ${t('common.live')}` : '○',
      keywords: `${p.id} ${p.display_name ?? ''} ${p.daemon_alive ? 'live running' : 'stopped idle'}`,
      group: t('palette.project'),
      run: () => selectProject(p.id),
    }));
    return [...nav, ...acts, ...proj];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projects, snap?.daemon.resumable, kiosk, setComposerDraft, locale, t]);

  return (
    <DraftUploadContext.Provider value={draftUploadView}>
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
        }}
      />
      {deliveryCenter.selection && <ArtifactModal
        key={`${deliveryCenter.selection.sid}:${deliveryCenter.selection.receipt.delivery_id}`}
        sid={deliveryCenter.selection.sid} path={deliveryCenter.selection.path}
        delivery={deliveryCenter.selection.receipt} deliveries={deliveryHistory}
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
          separator={sidebarShown ? (
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
          onOpenTrash={() => { setTrashOpen(true); setSidebarOpen(false); }}
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
          onOpenLearning={openLearningLibrary}
          onOpenResults={() => {
            const selection = defaultDeliverySelection(deliveryHistory);
            if (selection) openDelivery(selection.receipt, selection.path);
            else {
              setPreviewPathRequest(current => ({ path: FILES_PATH, token: current.token + 1 }));
              openPreview();
            }
            setSidebarOpen(false);
          }}
          resultsCount={deliveryCount}
          onNew={() => { startNewSession(); setSidebarOpen(false); }}
          onSearch={() => setSearchOpen(true)}
          // The trial portal owns sign-in; a token kept here is this browser's own.
          onSignOut={import.meta.env.VITE_ARGUS_HOSTED_TRIAL === '1' || !holdsToken() ? undefined : signOut}
          loading={projectsQ.isLoading}
          creating={creatingDaemon}
          error={projectsQ.isError ? errorText(projectsQ.error) : undefined}
          onRetry={() => void projectsQ.refetch()}
          mobileOpen={sidebarOpen}
          onCloseMobile={() => setSidebarOpen(false)}
          collapsed={!sidebarShown}
          onToggleCollapse={toggleSidebar}
          themeMode={themeMode}
          onCycleTheme={cycleTheme}
        />
      ) : null}

      {/* Both conversations (activity and the map's) show this project's files. */}
      <SessionIdContext.Provider value={snap?.session.id ?? null}>
      <WorkspaceRootsContext.Provider value={workspaceRoots}>
      <main className="flex min-w-0 flex-1 flex-col overflow-x-hidden">
        <ActionNotice notice={daemonManageOpen ? null : notice} onClose={dismissNotice} />
        <div className="flex min-h-0 flex-1">
        {showProject ? (
          <>
            <section className={`${!snap || mobileView === 'activity' ? 'flex' : 'hidden'} ${workspaceView === 'map' && !kiosk ? 'mobile-scroll-region' : ''} glass-panel glass-panel--main h-full min-w-0 flex-1 flex-col min-[1200px]:flex`}>
              {snap ? <TopBar
                snap={snap}
                streamOk={connected}
                onStart={requestStartDaemon} onFocusComposer={focusComposer}
                onStop={stopWork}
                pending={messagePending}
                showRunControl={workspaceView !== 'map'}
                showStatus={workspaceView !== 'map'}
                onManage={() => activeSid && requestManageSession(activeSid)}
                busy={daemonBusy}
                snapshotStale={snapQ.isError}
                readOnly={kiosk}
              /> : <WorkspaceHeader className="project-header">
                <div className="topbar-title min-w-0 truncate font-serif text-[19px] font-medium leading-6 text-ink">
                  {projects.find(project => project.id === activeSid)?.display_name || t('sidebar.unnamedSession')}
                </div>
              </WorkspaceHeader>}
              <nav aria-label={t('mobile.views')} className="hidden h-11 shrink-0 items-center gap-3 border-b border-line/60 px-5 lg:flex">
                <div className="workspace-tabs">
                  <button type="button" onClick={() => setWorkspaceView('activity')} className="workspace-tab" aria-current={workspaceView === 'activity' ? 'page' : undefined}>{t('mobile.activity')}</button>
                  <button type="button" onClick={() => setWorkspaceView('map')} className="workspace-tab" aria-current={workspaceView === 'map' ? 'page' : undefined}>{t('mobile.map')}</button>
                  <button type="button" onClick={() => setWorkspaceView('workbench')} className="workspace-tab" aria-current={workspaceView === 'workbench' ? 'page' : undefined}>{t('mobile.workbench')}</button>
                </div>
                <button type="button" className="ml-auto flex shrink-0 items-center gap-1.5 whitespace-nowrap px-2 py-1 text-sm text-ink-dim hover:text-ink"
                  onClick={() => setOverlay('palette')} title={t('topbar.commandsHint', { keys: shortcut('K') })}>
                  {t('topbar.commands')}
                  <kbd className="rounded border border-line px-1 font-mono text-[11px] leading-4 text-ink-faint">{shortcut('K')}</kbd>
                </button>
                <button type="button" className="shrink-0 whitespace-nowrap px-2 py-1 text-sm text-ink-dim hover:text-ink" aria-expanded={rightPanelVisible}
                  onClick={() => {
                    if (rightPanelVisible) { setRightPanelOpen(false); setMobileView('activity'); }
                    else { setPreviewPathRequest(current => ({ path: FILES_PATH, token: current.token + 1 })); openPreview(); }
                  }}>
                  {locale === 'zh-CN' ? '成果与文件' : 'Results & files'}
                </button>
              </nav>
              {snap ? <>
              {!kiosk && workspaceView === 'workbench' && <LearningStatus sid={activeSid} onOpenSkills={() => openSkillLibrary()} />}
              {workspaceView === 'map' && <Suspense fallback={<div className="m-auto text-sm text-ink-faint">{t('common.loading')}</div>}><MapPanel requestedTaskId={searchTarget?.sid === loadedSid ? searchTarget.task_id : undefined} onTaskLocated={() => setSearchTarget(null)} key={snap.session.id} immersive={mapImmersive} onImmersiveChange={setMapFull} snapshot={snap} pending={messagePending} readOnly={kiosk}
                onStopWork={stopWork} onResumeTeam={requestStartDaemon} onFocusComposer={focusComposer} teamBusy={daemonBusy}
                conversationEvents={activityEvents} conversationPagination={conversationPagination} connected={connected} artifacts={artifactsQ.data ?? []}
                deliveryCount={deliveryCount} onOpenDelivery={() => {
                  const selection = defaultDeliverySelection(deliveryHistory);
                  if (selection) openDelivery(selection.receipt, selection.path);
                }}
                onOpenReceipt={openDelivery} onOpenArtifact={setArtifactPath}
                deliveries={deliveryHistory}
              /></Suspense>}
              <div className={`${workspaceView === 'activity' ? 'flex' : 'hidden'} workspace-view-surface min-h-0 flex-1 flex-col`}>
                {/* The mobile activity minimum includes its header/status and 120px of conversation. */}
                  <div className={`flex flex-1 flex-col ${compactViewport ? 'min-h-0' : 'min-h-[209px] lg:min-h-0'}`}>
                  <ReplyChoiceContext.Provider value={kiosk ? null : replyChoice}>
                  <EventStream
                    key={loadedSid}
                    snapshot={snap}
                    events={activityEvents}
                    connected={connected && !snapQ.isError}
                    showReasoning={false}
                    historyStatus={historyStatus}
                    historyRefreshing={transcriptQ.isRefetching}
                    {...conversationPagination}
                    refreshingKept={refreshingKept}
                    onRetryHistory={() => void transcriptQ.refetch()}
                    embedded
                    artifacts={artifactsQ.data}
                    onOpenArtifact={focusDeliveryPath}
                    onOpenDelivery={openDelivery}
                    onCompose={kiosk ? undefined : composeContext}
                    onConversationAction={kiosk ? undefined : text => {
                      if (composerDraft && !window.confirm(locale === 'zh-CN' ? '替换输入框中尚未发送的草稿？' : 'Replace the unsent composer draft?')) return;
                      setComposerDraft(text); setComposerFocus(value => value + 1);
                    }}
                    requestedMessageTs={searchTarget?.sid === loadedSid && searchTarget.kind === 'conversation' ? Number(searchTarget.ts) : undefined}
                    onMessageLocated={() => setSearchTarget(null)}
                    rememberPosition
                  />
                  </ReplyChoiceContext.Provider>
                  </div>
                {!kiosk ? (
                  <div className="composer-dock shrink-0 px-4 pt-3">
                    <div className="mx-auto w-full max-w-full lg:max-w-[61.8vw]">
                    <ChatBox
                      key={activeSid || 'no-session'}
                      value={composerDraft}
                      attachments={composerAttachments}
                      onAttachmentsChange={changeComposerAttachments}
                      onChange={setComposerDraft}
                      onSend={sendComposerMessage}
                      disabled={!loadedSid || pendingReplyBusy}
                      pending={pendingReply ? pendingReplyBusy : messagePending}
                      answeringRole={pendingReply ? questionRole(pendingReply) : undefined}
                      focusSignal={composerFocus}
                      embedded
                      onRewrite={rewriteDraft}
                      onUndoRewrite={undoRewrite}
                      rewriting={rewriting}
                      completionSelection={completionSelection}
                      onCompletionSelectionChange={setCompletionSelection}
                      routeOverride={routeOverride}
                      onRouteOverrideChange={setRoute}
                      completion={promptSources}
                      pendingNote={waitingForUploads ? (locale === 'zh-CN' ? '附件还在上传，传完后自动发送' : 'Waiting for uploads; sends automatically when they finish') : undefined}
                      footer={activeSid ? <ComposerRuntime sid={snap.session.id} roles={snap.roles} running={snap.daemon.alive || Boolean(snap.manager_requests?.length)} lastCall={snap.last_call} onOpenSettings={openSettings} /> : undefined}
                    />
                    {(missingAttachments.length > 0 || draftStorageError) && <div className="draft-status" role="status">
                      {draftStorageError ? <span>{locale === 'zh-CN' ? '浏览器未能保存草稿，请在离开前复制内容。' : 'This browser could not save the draft. Copy it before leaving.'}</span> : null}
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
                      snapshot={loadedSid ? snap : undefined} failed={snapQ.isError}
                      refresh={() => void snapQ.refetch()} />
                  </Suspense>
                </div>
              ) : null}
              </> : <EventStream events={[]} connected={false} showReasoning={false} embedded showHeader={false}
                historyStatus={snapQ.isError ? 'error' : 'loading'} onRetryHistory={() => void snapQ.refetch()} />}
            </section>
            {snap && rightPanelVisible && workspaceView !== 'map' ? (
              <div className="hidden min-[1200px]:contents"><SplitHandle
                label={t('common.resizePreview')}
                value={rightWidth}
                min={320}
                max={PREVIEW_MAX_WIDTH}
                onPointerDown={(event) => resizeSidebar('right', event)}
                onReset={() => setRightWidth(PREVIEW_DEFAULT_WIDTH)}
                onNudge={(delta) => setRightWidth((value) => Math.max(320, Math.min(PREVIEW_MAX_WIDTH, value - delta)))}
              /></div>
            ) : null}

            {snap && rightPanelVisible && <aside
              data-resizable-panel="right"
              className={`${mobileView === 'preview' ? 'flex' : 'hidden'} relative min-w-0 flex-1 flex-col overflow-hidden border-l border-line/60 bg-panel min-[1200px]:flex min-[1200px]:flex-none min-[1200px]:w-[var(--preview-width)]`}>
              <div className="min-[1200px]:hidden">
                <TopBar
                  snap={snap}
                  streamOk={connected}
                  onStart={requestStartDaemon} onFocusComposer={focusComposer}
                  onStop={stopWork}
                  pending={messagePending}
                  onManage={() => activeSid && requestManageSession(activeSid)}
                  busy={daemonBusy}
                  snapshotStale={snapQ.isError}
                  readOnly={kiosk}
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
                requestedPath={previewPathRequest.path}
                requestedPathToken={previewPathRequest.token}
                filesFirst
                latestDelivery={deliveryHistory[0] ?? null}
                deliveriesLoaded={transcriptQ.isFetchedAfterMount && Boolean(snap)}
                onOpenDelivery={(receipt) => openDelivery(receipt)}
                loading={artifactsQ.isLoading}
                onRevise={kiosk ? undefined : reviseArtifact}
                onEdit={onEditArtifact}
                onRetry={() => void artifactsQ.refetch()}
              />
            </aside>}
          </>
        ) : (
          <Landing
            loading={projectsQ.isLoading}
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
            focusSignal={composerFocus}
            onFocusComposer={() => setComposerFocus(value => value + 1)}
            onNew={startNewSession}
            onSubmit={async (text, files, workdir) => {
              if (homeRequest.current) return false;
              homeRequest.current = { text, files };
              setWorkspaceView('activity'); setMobileView('activity');
              const created = await createDaemon('', workdir, text);
              if (!created) homeRequest.current = null;
              return created;
            }}
            onChoose={() => setSidebarOpen(true)}
            canCreate={!kiosk}
            creating={creatingDaemon}
            createError={createError}
            onWorkdirChange={clearCreateError}
            workdirs={[...new Set(projects.map(project => project.workdir).filter((path): path is string => Boolean(path)))]}
          />
        )}
        </div>
      </main>
      </WorkspaceRootsContext.Provider>
      </SessionIdContext.Provider>

      {/* global overlays */}
      <ProjectTrash open={trashOpen} onClose={() => setTrashOpen(false)} onRestored={async (sid) => {
        await projectsQ.refetch({ throwOnError: true });
        selectProject(sid);
        setSidebarOpen(false);
        notify('success', t('project.restored'));
      }} />
      <HistorySearch open={searchOpen} onClose={() => setSearchOpen(false)} onChoose={result => {
        setSearchTarget(result); selectProject(result.sid);
        setWorkspaceView('activity'); setMobileView('activity'); setSidebarOpen(false);
      }} />
      {activeSid && editor && <ArtifactEditor key={`${activeSid}:${editor.path}:${editor.version ?? ''}`} sid={activeSid}
        path={editor.path} initialVersion={editor.version} onClose={() => setEditor(null)}
        onRequest={text => { if (composeContext(text)) setEditor(null); }} />}
      <Modal open={overlay === 'learning'} onClose={() => setOverlay('none')} label={locale === 'zh-CN' ? '技能库' : 'Skill library'} width="max-w-6xl">
        {overlay === 'learning' && <LearningLibrary sid={activeSid} initialSelection={learningSelection} initialScope={learningScope}
          onUse={kiosk ? undefined : useMaterial}
          projectNames={Object.fromEntries(projects.flatMap(project => project.display_name ? [[project.id, project.display_name] as const] : []))} />}
      </Modal>
      <CommandPalette open={overlay === 'palette'} onClose={() => setOverlay('none')} items={paletteItems} />
      <KeybindingHelp open={overlay === 'help'} onClose={() => setOverlay('none')} />
      {activeSid && (
        <ConfigModal
          sid={activeSid}
          themeMode={themeMode}
          onCycleTheme={cycleTheme}
          open={overlay === 'config'}
          onClose={() => setOverlay('none')}
        />
      )}
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
        onSelectPath={setArtifactPath}
        onClose={() => setArtifactPath(null)} />
      <TaskDetailModal sid={activeSid} itemId={taskItemId} onClose={() => setTaskItemId(null)} />
      {manageTargetSid ? (
        <DaemonManageModal
          open={daemonManageOpen}
          feedback={daemonManageOpen ? <ActionNotice notice={notice} onClose={dismissNotice} /> : null}
          sid={manageTargetSid}
          name={manageSnapQ.data?.session.display_name
            || projects.find((project) => project.id === manageTargetSid)?.display_name
            || projects.find((project) => project.id === manageTargetSid)?.label
            || ''}
          alive={manageSnapQ.data?.daemon.alive
            ?? Boolean(projects.find((project) => project.id === manageTargetSid)?.daemon_alive)}
          snapshot={manageSnapQ.data}
          busy={daemonBusy}
          onClose={() => {
            setDaemonManageOpen(false);
            setManageTargetSid(null);
          }}
          onRename={manageRenameProject}
          onDelete={async () => {
            const deletedSid = manageTargetSid;
            const deleted = await manageDeleteProject();
            if (deleted && deletedSid) dropSessionDraft(deletedSid);
            return deleted;
          }}
        />
      ) : null}
      {showProject && !kiosk ? (
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
    </DraftUploadContext.Provider>
  );
}
