import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useProjects, useSnapshot, useJournal, useEventStream, useProjectActions, useArtifacts } from './hooks';
import { api } from './api';
import { computeSpend } from './lib/cost';
import { mergeFragment } from './lib/eventRender';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { EventStream } from './components/EventStream';
import { RolesPanel } from './components/RolesPanel';
import { BacklogPanel } from './components/BacklogPanel';
import { JournalPanel } from './components/JournalPanel';
import { ChatBox, type ChatTurn } from './components/ChatBox';
import { CommandPalette, type PaletteItem } from './components/CommandPalette';
import { KeybindingHelp } from './components/KeybindingHelp';
import { DoctorModal, ConfigModal, IdentityModal, TranscriptModal } from './components/InfoModals';
import { PendingBanner } from './components/PendingBanner';
import { GuardianBanner } from './components/GuardianBanner';
import { activeGuardianAlert } from './lib/guardian';
import { Wordmark } from './components/Wordmark';
import { TAGLINE, WELCOME } from './lib/soul';
import { rankProjects, resolveProjectSelection } from '../../core/src/projects';
import { deriveMissionView } from '../../core/src/mission';
import { ResultSummary } from './components/ResultSummary';
import { ArtifactModal } from './components/ArtifactModal';
import { LiveViewPanel } from './components/LiveViewPanel';
import { ActionNotice, type NoticeTone, type UiNotice } from './components/ActionNotice';
import { TaskDetailModal } from './components/TaskDetailModal';
import { NewDaemonModal } from './components/NewDaemonModal';

type Overlay = 'none' | 'palette' | 'help' | 'doctor' | 'config' | 'identity' | 'transcript';
type CompactPane = 'feed' | 'tasks' | 'journal' | 'team';
type ProjectHistoryMode = 'push' | 'replace';
interface ActiveMessageRequest {
  id: number;
  sid: string;
  controller: AbortController;
}
let noticeSequence = 0;

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
      <Wordmark size={32} tag={TAGLINE} />
      <p className={`max-w-md text-sm leading-relaxed ${error ? 'text-err' : 'text-ink-faint'}`}>
        {error
          ? error
          : loading
          ? 'Connecting to the Argus service…'
          : hasProjects
          ? 'Select a session from the sidebar, or create a new one.'
          : 'No sessions yet. Create one to begin.'}
      </p>
      {!loading && (
        <div className="flex flex-wrap justify-center gap-2">
          {error ? (
            <button type="button" onClick={onRetry} className="rounded border border-err/50 px-3 py-1.5 text-xs text-err hover:bg-err/10">
              Retry
            </button>
          ) : null}
          {hasProjects ? (
            <button type="button" onClick={onChoose} className="rounded border border-line px-3 py-1.5 text-xs text-ink-dim hover:bg-panel">
              Select session
            </button>
          ) : canCreate ? (
            <button type="button" onClick={onNew} className="rounded border border-blue-deep bg-blue-deep px-3 py-1.5 text-xs text-ink hover:bg-blue-deep/80">
              New session
            </button>
          ) : null}
        </div>
      )}
    </div>
  );
}

export default function App() {
  const params = new URLSearchParams(window.location.search);
  const projectsQ = useProjects();
  const projects = useMemo(() => rankProjects(projectsQ.data ?? []), [projectsQ.data]);

  const [sid, setSid] = useState<string | null>(params.get('project'));
  const sidRef = useRef(sid);
  sidRef.current = sid;
  const [overlay, setOverlay] = useState<Overlay>('none');
  const [kiosk, setKiosk] = useState(params.get('kiosk') === '1');
  const [showReasoning, setShowReasoning] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [compactPane, setCompactPane] = useState<CompactPane>('feed');
  const [composerFocus, setComposerFocus] = useState(0);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [chatPending, setChatPending] = useState(false);
  const [artifactPath, setArtifactPath] = useState<string | null>(null);
  const [taskItemId, setTaskItemId] = useState<string | null>(null);
  const [newDaemonOpen, setNewDaemonOpen] = useState(false);
  const [creatingDaemon, setCreatingDaemon] = useState(false);
  const creatingDaemonRef = useRef(false);
  const messageRequestRef = useRef<ActiveMessageRequest | null>(null);
  const messageEpochRef = useRef(0);
  const [notice, setNotice] = useState<UiNotice | null>(null);
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
    return cancelled;
  }, []);

  const stopWaiting = useCallback(() => {
    if (!cancelActiveMessage()) return;
    setChatTurns((turns) => turns.map((turn) => turn.pending ? { ...turn, pending: false } : turn));
    notify('info', 'Stopped waiting for this reply. Server-side work may still finish in the project timeline.');
  }, [cancelActiveMessage, notify]);

  const activateProject = useCallback((id: string | null) => {
    if (id !== sidRef.current) {
      cancelActiveMessage();
      setChatTurns([]); // each daemon keeps its own conversation
      setArtifactPath(null);
      setTaskItemId(null);
    }
    sidRef.current = id;
    setSid(id);
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

  const createDaemon = async (name: string, objective: string): Promise<boolean> => {
    if (creatingDaemonRef.current) return false;
    creatingDaemonRef.current = true;
    setCreatingDaemon(true);
    try {
      const r = await api.createDaemon(objective, name);
      await projectsQ.refetch();
      selectProject(r.sid);
      setChatTurns([{ role: 'system', text: WELCOME }]);
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

  // Resolve the initial/deleted project only after the authoritative list is
  // available. Invalid IDs never reach snapshot/stream endpoints.
  useEffect(() => {
    if (!projectsQ.isSuccess) return;
    const selection = resolveProjectSelection(projects, sidRef.current);
    if (selection.id === sidRef.current) return;
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
  }, [activateProject, notify, projects, projectsQ.isSuccess]);

  // Project switches are real browser-history entries. Back/Forward restores
  // the corresponding cockpit without reloading the page.
  useEffect(() => {
    const onPopState = () => {
      const requested = new URLSearchParams(window.location.search).get('project');
      setSidebarOpen(false);
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
  const continuous = snap?.continuous;
  const mission = snap ? deriveMissionView(snap, continuous) : null;
  const journalQ = useJournal(activeSid, 5, !kiosk || mission?.state === 'complete');
  const artifactsQ = useArtifacts(activeSid, true);
  const { events, connected } = useEventStream(activeSid);
  const spend = useMemo(() => computeSpend(events), [events]);
  const actions = useProjectActions(activeSid);
  const daemonBusy = actions.startDaemon.isPending || actions.stopDaemon.isPending;
  const actionFeedback = (success: string) => ({
    onSuccess: () => notify('success', success),
    onError: (error: Error) => notify('error', errorText(error)),
  });
  const requestStartDaemon = () =>
    actions.startDaemon.mutate(undefined, actionFeedback('Daemon start requested.'));
  const requestStopDaemon = () =>
    actions.stopDaemon.mutate(true, actionFeedback('Daemon is draining and will stop safely.'));
  const requestDispose = (id: string, op: 'done' | 'skip' | 'rm') =>
    actions.disposeBacklog.mutate(
      { id, op },
      actionFeedback(op === 'done' ? `Marked ${id} done.` : `Skipped ${id}.`),
    );
  const requestStopIteration = (id: string) =>
    actions.stopBacklog.mutate(id, actionFeedback(`Stopped auto-iteration for ${id}.`));

  const toggleContinuous = () => {
    if (!continuous) return;
    const enabled = !continuous.enabled;
    actions.setContinuous.mutate(
      { enabled, objective: continuous.objective },
      actionFeedback(enabled ? 'Continuous campaign enabled.' : 'Continuous campaign stopped.'),
    );
  };

  // global keybindings — ⌘K palette · ⌘O reasoning · ⌘. kiosk · ? help · / composer
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

    setChatTurns((t) => [...t, { role: 'you', text }]);
    setChatPending(true);

    // Grow (or create) the trailing Argus bubble from the actual state — no
    // external accumulator, so out-of-order React batches can't drop a block.
    const applyReply = (next: (prev: string) => string, done: boolean) => {
      if (!isCurrent()) return;
      setChatTurns((t) => {
        if (!isCurrent()) return t;
        const copy = [...t];
        const last = copy[copy.length - 1];
        if (last && last.role === 'argus') copy[copy.length - 1] = { role: 'argus', text: next(last.text), pending: !done };
        else copy.push({ role: 'argus', text: next(''), pending: !done });
        return copy;
      });
    };

    const dispatchTask = (result: { item?: { title?: string; objective?: string } | null }) => {
      if (!isCurrent()) return;
      const title = result.item?.title || result.item?.objective || 'new mission';
      setChatTurns((turns) => isCurrent()
        ? [...turns, { role: 'system', text: `→ dispatched to the team: ${title}` }]
        : turns);
      snapQ.refetch?.();
    };

    let gotDelta = false;
    let streamErr: Error | null = null;
    try {
      try {
        await api.messageStream(requestSid, text, {
          onDelta: (block) => {
            if (!isCurrent()) return;
            gotDelta = true;
            applyReply((prev) => mergeFragment(prev, block), false); // same reply row grows
          },
          onDone: (result) => {
            if (!isCurrent()) return;
            if (result.kind === 'task') dispatchTask(result);
            else if (!gotDelta) applyReply(() => result.reply || 'no response', true);
            else applyReply((prev) => prev, true); // settle: stop the pending shimmer
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
          else applyReply(() => result.reply || 'no response', true);
        } catch (error) {
          if (!isCurrent()) return;
          setChatTurns((turns) => isCurrent()
            ? [...turns, { role: 'system', text: `message failed · ${errorText(error)}` }]
            : turns);
        }
      }
    } finally {
      if (messageRequestRef.current?.id === requestId) {
        messageRequestRef.current = null;
        setChatPending(false);
      }
    }
  };

  const paletteItems: PaletteItem[] = useMemo(() => {
    const nav: PaletteItem[] = [
      ...(kiosk ? [] : [{ id: 'new', label: 'New daemon', hint: '+', group: 'View', run: () => setNewDaemonOpen(true) }]),
      { id: 'doctor', label: 'Open Doctor', hint: '/doctor', group: 'View', run: () => setOverlay('doctor') },
      { id: 'config', label: 'Open Config', hint: '/config', group: 'View', run: () => setOverlay('config') },
      { id: 'identity', label: 'Open Identity', hint: '/identity', group: 'View', run: () => setOverlay('identity') },
      { id: 'transcript', label: 'Open Transcript', hint: '/transcript', group: 'View', run: () => setOverlay('transcript') },
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
    <div className="flex h-screen w-screen overflow-hidden bg-bg text-ink">
      {!kiosk && (
        <>
          {sidebarOpen && (
            <button
              aria-label="close project navigation"
                className="fixed inset-0 z-30 bg-black/60 md:hidden"
              onClick={() => setSidebarOpen(false)}
            />
          )}
          <Sidebar
            projects={projects}
            activeId={activeSid}
            onSelect={(id) => {
              selectProject(id);
              setSidebarOpen(false);
            }}
            onOpenPanel={(p) => {
              setOverlay(p);
              setSidebarOpen(false);
            }}
            onNew={() => {
              setNewDaemonOpen(true);
              setSidebarOpen(false);
            }}
            loading={projectsQ.isLoading}
            creating={creatingDaemon}
            error={projectsQ.isError ? errorText(projectsQ.error) : undefined}
            onRetry={() => void projectsQ.refetch()}
            mobileOpen={sidebarOpen}
          />
          <button
            aria-label="open project navigation"
            className="fixed left-3 top-3 z-20 rounded border border-line bg-panel px-2 py-1 text-ink-dim md:hidden"
            onClick={() => setSidebarOpen(true)}
          >
            ☰
          </button>
        </>
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        {snap ? (
          <>
            <TopBar
              snap={snap}
              spend={spend}
              streamOk={connected}
              onStart={requestStartDaemon}
              onStop={requestStopDaemon}
              busy={daemonBusy}
              busyLabel={actions.startDaemon.isPending ? 'starting…' : 'stopping…'}
              snapshotStale={snapQ.isError}
              readOnly={kiosk}
              continuous={continuous}
              onToggleContinuous={toggleContinuous}
              continuousBusy={actions.setContinuous.isPending}
            />
            <PendingBanner
              questions={snap.pending_questions ?? []}
              backlog={snap.backlog}
              onAnswer={() => setComposerFocus((x) => x + 1)}
            />
            <GuardianBanner alert={activeGuardianAlert(events)} />
            <div className="mx-3 mt-3 grid grid-cols-4 border border-line bg-surface p-1 xl:hidden">
              {(['feed', 'tasks', 'journal', 'team'] as CompactPane[]).map((pane) => (
                <button
                  key={pane}
                  onClick={() => setCompactPane(pane)}
                  className={`rounded-sm px-2 py-1.5 text-[11px] capitalize transition-colors ${
                    compactPane === pane ? 'bg-panel text-ink' : 'text-ink-faint'
                  }`}
                >
                  {pane}
                </button>
              ))}
            </div>
            <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 p-3 xl:grid-cols-[minmax(0,1fr)_360px]">
              <div className={`${compactPane === 'feed' ? 'flex' : 'hidden'} min-h-0 flex-col xl:flex`}>
                <LiveViewPanel
                  artifacts={artifactsQ.data}
                  error={artifactsQ.isError}
                  onOpenArtifact={setArtifactPath}
                  className="mb-3 xl:hidden"
                />
                {mission?.state === 'complete' && (
                  <ResultSummary
                    entries={journalQ.data ?? []}
                    artifacts={artifactsQ.data}
                    artifactError={artifactsQ.isError}
                    onOpenArtifact={setArtifactPath}
                  />
                )}
                <EventStream
                  events={events}
                  connected={connected}
                  showReasoning={showReasoning}
                  onToggleReasoning={() => setShowReasoning((v) => !v)}
                />
              </div>
              <div className={`${compactPane === 'tasks' ? 'flex' : 'hidden'} min-h-0 flex-col xl:hidden`}>
                <BacklogPanel
                  items={snap.backlog}
                  onDispose={requestDispose}
                  onStop={requestStopIteration}
                  onInspect={setTaskItemId}
                  busy={actions.disposeBacklog.isPending || actions.stopBacklog.isPending}
                  readOnly={kiosk}
                />
              </div>
              <div className={`${compactPane === 'journal' ? 'flex' : 'hidden'} min-h-0 flex-col xl:hidden`}>
                <JournalPanel entries={journalQ.data ?? []} />
              </div>
              <div className={`${compactPane === 'team' ? 'block' : 'hidden'} min-h-0 xl:hidden`}>
                <RolesPanel roles={snap.roles} />
              </div>
              <div className="hidden min-h-0 flex-col gap-3 xl:flex">
                <LiveViewPanel
                  artifacts={artifactsQ.data}
                  error={artifactsQ.isError}
                  onOpenArtifact={setArtifactPath}
                />
                <div className="shrink-0">
                  <RolesPanel roles={snap.roles} />
                </div>
                <BacklogPanel
                  items={snap.backlog}
                  onDispose={requestDispose}
                  onStop={requestStopIteration}
                  onInspect={setTaskItemId}
                  busy={actions.disposeBacklog.isPending || actions.stopBacklog.isPending}
                  readOnly={kiosk}
                />
                <JournalPanel entries={journalQ.data ?? []} />
              </div>
            </div>
            {!kiosk && (
              <div className="px-3 pb-3">
                <ChatBox
                  turns={chatTurns}
                  onSend={sendMessage}
                  onCancel={stopWaiting}
                  disabled={!activeSid}
                  pending={chatPending}
                  focusSignal={composerFocus}
                />
              </div>
            )}
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
      <ActionNotice notice={notice} onClose={dismissNotice} />
    </div>
  );
}
