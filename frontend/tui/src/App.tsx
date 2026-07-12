import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Static, useApp, useInput, useStdout } from 'ink';
import type { WebSocket } from 'ws';
import {
  ApiClient,
  taskDispatchMessage,
  type DaemonStartResult,
  type ArtifactInfo,
  type EventMsg,
  type ProjectRow,
  type Snapshot,
} from './api.js';
import {
  backspace,
  deleteWordBefore,
  EMPTY,
  end,
  fromString,
  home,
  insert,
  killToEnd,
  killToStart,
  left,
  right,
  type Edit,
} from './input/editor.js';
import { EMPTY_HISTORY, newer, older, remember, type History } from './input/history.js';
import { applyCompletion, didYouMean, isSlash, parseCommand, parseEventViewArgs, parseResumeTarget, slashCompletions } from './input/slash.js';
import { Header } from './components/Header.js';
import { EventLog } from './components/EventLog.js';
import { PromptBox } from './components/PromptBox.js';
import { SlashMenu } from './components/SlashMenu.js';
import { Footer } from './components/Footer.js';
import { ThinkingLine } from './components/ThinkingLine.js';
import { GuardianBanner } from './components/GuardianBanner.js';
import { NewDaemonForm } from './components/NewDaemonForm.js';
import { PanelView, type PanelState } from './components/panels.js';
import { activeGuardianAlert } from './guardian.js';
import { useTerminalSize } from './useTerminalSize.js';
import { filterProjects, rankProjects } from '../../core/src/projects.js';
import { moveSelection } from './input/selection.js';
import { visibleBacklogItems } from '../../core/src/backlog.js';
import {
  overlayActiveRole,
  overlayRoleActivities,
  reduceOperatorEvent,
} from '../../core/src/activity.js';
import {
  daemonDraftValues,
  daemonFormInput,
  newDaemonDraft,
  type NewDaemonDraft,
} from './newDaemonForm.js';
import { MissionCockpit } from './components/MissionCockpit.js';
import { consumePasteChunk } from './input/paste.js';
import { transcriptEvents } from './transcript.js';
import {
  DaemonReplacementPicker,
  type DaemonReplacementState,
} from './components/DaemonReplacementPicker.js';
import { projectMissionView } from '../../core/src/missionView.js';

const MAX_EVENTS = 400;
const STREAM_RENDER_INTERVAL_MS = 50;

interface ActiveManagerRequest {
  id: number;
  project: string;
  controller: AbortController;
}

export interface AppProps {
  host: string;
  port: number;
  token?: string;
  project: string;
  initialNotice?: string;
  initialAdmission?: DaemonStartResult;
  initialResumeContinuous?: boolean;
}

function replacementState(
  start: Partial<DaemonStartResult> | undefined,
  targetProject: string,
  resumeContinuous: boolean,
): DaemonReplacementState | null {
  if (!start?.admission_required || !start.running_daemons?.length) return null;
  return {
    targetProject,
    running: start.running_daemons,
    limit: start.limit ?? start.running_daemons.length,
    activeCount: start.active_count ?? start.running_daemons.length,
    selection: 0,
    resumeContinuous,
    busy: false,
    error: '',
  };
}

export function App({
  host,
  port,
  token,
  project: initialProject,
  initialNotice = '',
  initialAdmission,
  initialResumeContinuous = false,
}: AppProps) {
  const { exit } = useApp();
  const { stdout } = useStdout();
  const terminal = useTerminalSize();
  const [project, setProject] = useState(initialProject);
  const projectRef = useRef(project);
  projectRef.current = project;
  const api = useMemo(
    () => new ApiClient({ host, port, project, token }),
    [host, port, project, token],
  );
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<EventMsg[]>([]);
  const [connected, setConnected] = useState(false);
  const [snapshotError, setSnapshotError] = useState('');
  const [streamError, setStreamError] = useState('');
  const [edit, setEdit] = useState<Edit>(EMPTY);
  const [history, setHistory] = useState<History>(EMPTY_HISTORY);
  const [menuSel, setMenuSel] = useState(0);
  const [notice, setNotice] = useState(initialNotice);
  const [panel, setPanel] = useState<PanelState | null>(null);
  const [daemonDraft, setDaemonDraft] = useState<NewDaemonDraft | null>(null);
  const [replacement, setReplacement] = useState<DaemonReplacementState | null>(
    () => replacementState(
      initialAdmission,
      initialProject,
      initialResumeContinuous,
    ),
  );
  const [pendingExit, setPendingExit] = useState(false);
  // A Manager turn in flight → drive the live "thinking" indicator (spinner +
  // elapsed + phase) so the terminal never looks frozen while Argus works.
  const [pending, setPending] = useState(false);
  const [phase, setPhase] = useState('');
  const [startedAt, setStartedAt] = useState(0);
  const [tick, setTick] = useState(0);
  const wsRef = useRef<WebSocket | null>(null);
  const aliveRef = useRef(true);
  const creatingProjectRef = useRef(false);
  const dismissedAdmissionRef = useRef(0);
  const managerRequestRef = useRef<ActiveManagerRequest | null>(null);
  const managerEpochRef = useRef(0);
  const pasteActiveRef = useRef(false);

  useEffect(() => {
    if (!stdout.isTTY) return;
    stdout.write('\u001b[?2004h');
    return () => {
      stdout.write('\u001b[?2004l');
    };
  }, [stdout]);

  const cancelManagerTurn = () => {
    const cancelled = Boolean(managerRequestRef.current);
    managerEpochRef.current += 1;
    managerRequestRef.current?.controller.abort();
    managerRequestRef.current = null;
    setPending(false);
    setPhase('');
    setStartedAt(0);
    return cancelled;
  };

  const stopWaiting = () => {
    if (cancelManagerTurn()) {
      setNotice('stopped waiting · server-side work may still finish in the project timeline');
    } else {
      setNotice('no Manager reply is currently in flight');
    }
  };

  const changeProject = (id: string): boolean => {
    if (id === projectRef.current) return false;
    cancelManagerTurn();
    projectRef.current = id;
    setProject(id);
    setReplacement(null);
    dismissedAdmissionRef.current = 0;
    return true;
  };

  const captureAdmission = (
    start: DaemonStartResult | undefined,
    targetProject: string,
    resumeContinuous: boolean,
  ) => {
    const next = replacementState(start, targetProject, resumeContinuous);
    if (next) {
      dismissedAdmissionRef.current = 0;
      setReplacement(next);
    }
  };

  useEffect(() => {
    const admission = snap?.daemon_admission;
    if (
      replacement ||
      !admission ||
      admission.requested_at <= dismissedAdmissionRef.current
    ) return;
    setReplacement(
      replacementState(
        admission,
        admission.target_sid || project,
        admission.resume_continuous,
      ),
    );
  }, [project, replacement, snap?.daemon_admission]);

  const replaceRunningDaemon = async () => {
    if (!replacement || replacement.busy) return;
    const victim = replacement.running[replacement.selection];
    if (!victim) return;
    setReplacement((current) => current ? { ...current, busy: true, error: '' } : current);
    try {
      const targetApi = new ApiClient({
        host,
        port,
        project: replacement.targetProject,
        token,
      });
      const result = await targetApi.replaceDaemon(
        victim.id,
        replacement.resumeContinuous,
        snap?.daemon_commands?.revision,
      );
      if (result.rc !== 0) {
        const refreshed = replacementState(
          result,
          replacement.targetProject,
          replacement.resumeContinuous,
        );
        setReplacement(
          refreshed ?? {
            ...replacement,
            busy: false,
            error: result.error || 'could not replace the selected session',
          },
        );
        return;
      }
      dismissedAdmissionRef.current = Date.now() / 1000;
      setReplacement(null);
      setNotice(`parked ${victim.label || victim.id} · queued work started`);
    } catch (error) {
      setReplacement((current) => current ? {
        ...current,
        busy: false,
        error: (error as Error).message,
      } : current);
    }
  };

  useEffect(() => () => {
    managerEpochRef.current += 1;
    managerRequestRef.current?.controller.abort();
    managerRequestRef.current = null;
  }, []);

  // Switching project: reset the feed/snapshot so stale data never bleeds across.
  useEffect(() => {
    setEvents([]);
    setSnap(null);
    setConnected(false);
    setSnapshotError('');
    setStreamError('');
  }, [project]);

  useEffect(() => {
    aliveRef.current = true;
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let renderTimer: ReturnType<typeof setTimeout> | undefined;
    let pendingEvents: EventMsg[] = [];
    const flushEvents = () => {
      renderTimer = undefined;
      if (!active || pendingEvents.length === 0) return;
      const batch = pendingEvents;
      pendingEvents = [];
      setEvents((prev) =>
        batch.reduce(
          (current, event) => reduceOperatorEvent(current, event, MAX_EVENTS),
          prev,
        ),
      );
    };
    const queueEvent = (event: EventMsg) => {
      if (!active) return;
      pendingEvents.push(event);
      if (!renderTimer) renderTimer = setTimeout(flushEvents, STREAM_RENDER_INTERVAL_MS);
    };
    const connect = () => {
      if (!active || !aliveRef.current) return;
      wsRef.current = api.connectStream({
        replay: 60,
        onOpen: () => {
          if (!active) return;
          setConnected(true);
          setStreamError('');
        },
        onEvent: queueEvent,
        onClose: () => {
          if (!active) return;
          flushEvents();
          setConnected(false);
          if (aliveRef.current) retry = setTimeout(connect, 1000);
        },
        onError: (error) => {
          if (active) setStreamError(error.message || 'event stream unavailable');
        },
      });
    };
    connect();
    return () => {
      active = false;
      if (retry) clearTimeout(retry);
      if (renderTimer) clearTimeout(renderTimer);
      pendingEvents = [];
      wsRef.current?.close();
    };
  }, [api]);

  useEffect(() => {
    let active = true;
    api.getTranscript(MAX_EVENTS).then(
      (turns) => {
        if (!active) return;
        const persisted = transcriptEvents(turns);
        setEvents((live) =>
          [...persisted, ...live]
            .sort((left, right) => Number(left.ts ?? 0) - Number(right.ts ?? 0))
            .reduce(
              (current, event) => reduceOperatorEvent(current, event, MAX_EVENTS),
              [] as EventMsg[],
            ),
        );
      },
      () => {
        // Event streaming remains usable when an old project has no transcript.
      },
    );
    return () => {
      active = false;
    };
  }, [api]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.snapshot();
        if (alive) {
          setSnap(s);
          setSnapshotError('');
        }
      } catch (error) {
        if (alive) setSnapshotError((error as Error).message || 'snapshot refresh failed');
      }
    };
    tick();
    const id = setInterval(tick, 5_000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [api]);

  const quit = () => {
    cancelManagerTurn();
    aliveRef.current = false;
    wsRef.current?.close();
    exit();
  };

  // While a Manager turn is pending, tick a lightweight clock so the thinking
  // spinner animates and the elapsed counter climbs (cleared the instant it
  // settles). ~120ms = a calm, non-jittery cadence.
  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => setTick((t) => t + 1), 120);
    return () => clearInterval(id);
  }, [pending]);

  // /resume + /attach — switch the active project (reconnects the stream).
  const switchProject = async (arg: string) => {
    const target = parseResumeTarget(arg);
    if (target.kind === 'list') {
      openPanel('daemons');
      return;
    }
    const a = target.query;
    try {
      const projects = await api.listProjects();
      const match =
        projects.find((p) => p.id === a) ||
        projects.find((p) => p.id.startsWith(a)) ||
        projects.find((p) => (p.label || '').toLowerCase().includes(a.toLowerCase())) ||
        filterProjects(projects, a)[0];
      if (!match) {
        setNotice(`no project matching "${a}" — /daemons to list`);
        return;
      }
      if (match.id === project) {
        setNotice(`already on ${match.id}`);
        return;
      }
      changeProject(match.id);
      setNotice(`switched to ${match.label || match.id}`);
    } catch (e) {
      setNotice(`error: ${(e as Error).message}`);
    }
  };

  const activateProject = (match: ProjectRow) => {
    setPanel(null);
    if (match.id === project) {
      setNotice(`already on ${match.label || match.id}`);
      return;
    }
    changeProject(match.id);
    setNotice(`switched to ${match.label || match.id}`);
  };

  const openNewDaemon = (objective = '') => {
    setPanel(null);
    setPendingExit(false);
    setNotice('');
    setDaemonDraft(newDaemonDraft(objective));
  };

  const submitNewDaemon = async () => {
    if (!daemonDraft || daemonDraft.busy) return;
    if (creatingProjectRef.current) {
      setDaemonDraft((current) => current ? { ...current, error: 'a daemon is already being created' } : current);
      return;
    }
    const { objective, name } = daemonDraftValues(daemonDraft);
    creatingProjectRef.current = true;
    setDaemonDraft((current) => current ? { ...current, busy: true, error: '' } : current);
    try {
      const created = await api.createDaemon(objective, name);
      setPanel(null);
      setDaemonDraft(null);
      changeProject(created.sid);
      captureAdmission(created.start, created.sid, Boolean(objective));
      setNotice(
        created.start?.admission_required
          ? `created ${created.sid} · choose running work to park`
          : created.spawned
          ? `created ${created.sid} · campaign started`
          : `created ${created.sid} · message Argus when ready`,
      );
    } catch (error) {
      setDaemonDraft((current) => current ? {
        ...current,
        busy: false,
        error: (error as Error).message || 'daemon creation failed',
      } : current);
    } finally {
      creatingProjectRef.current = false;
    }
  };

  // ── open a read/inspect panel (fetches its data async) ──
  const openPanel = (kind: PanelState['kind'], opts: Partial<PanelState> = {}) => {
    const needsFetch = !['help', 'backlog', 'events'].includes(kind);
    setPanel({ kind, page: 0, ...opts, loading: needsFetch });
    if (!needsFetch) return;
    const fetchers: Record<string, () => Promise<unknown>> = {
      status: () => api.getStatus(),
      doctor: () => api.getDoctor(),
      journal: () => api.getJournal(20),
      config: () => api.getConfig(),
      identity: () => api.getIdentity(),
      daemons: () => api.listProjects(),
      artifacts: () => api.getArtifacts(),
      artifact: () => api.getArtifact(String(opts.path ?? '')),
      task: () => api.getBacklogItem(String(opts.itemId ?? '')),
    };
    const f = fetchers[kind];
    if (!f) return;
    f().then(
      (data) => setPanel((p) => {
        if (!p || p.kind !== kind) return p;
        let selection = p.selection ?? 0;
        if (kind === 'daemons') {
          const ranked = filterProjects(rankProjects(data as ProjectRow[]), String(opts.query ?? ''));
          const current = ranked.findIndex((row) => row.id === project);
          selection = current >= 0 ? current : 0;
        }
        return { ...p, loading: false, data, selection };
      }),
      (e) => setPanel((p) => (p && p.kind === kind ? { ...p, loading: false, error: (e as Error).message } : p)),
    );
  };

  const dispatchSlash = (line: string) => {
    const p = parseCommand(line);
    if (!p) return;
    if (!p.cmd) {
      const s = didYouMean(p.name);
      setNotice(s ? `unknown ${p.name} — did you mean ${s}?` : `unknown command ${p.name} — /help`);
      return;
    }
    const ok = (m: string) => () => setNotice(m);
    const err = (e: unknown) => setNotice(`error: ${(e as Error).message}`);
    const need = (usage: string) => setNotice(`usage: ${usage}`);
    const showOutput = (text: string) => setEvents((events) => [
      ...events,
      { type: 'ui.argus', text, message_id: `local-${Date.now()}`, ts: Date.now() / 1000 } as EventMsg,
    ]);
    switch (p.cmd.name) {
      case '/help':
        openPanel('help');
        break;
      case '/status':
        openPanel('status');
        break;
      case '/roles':
        openPanel('config');
        break;
      case '/doctor':
        openPanel('doctor');
        break;
      case '/identity':
        if (!p.rest) openPanel('identity');
        else if (p.rest.toLowerCase().startsWith('set ')) {
          const body = p.rest.slice(4).trim();
          if (body) void api.setIdentity(body).then(ok('identity updated'), err);
          else need('/identity set <text>');
        } else need('/identity [set <text>]');
        break;
      case '/journal':
        openPanel('journal');
        break;
      case '/backlog':
        openPanel('backlog', { all: p.rest.trim() === 'all', selection: 0 });
        break;
      case '/daemons':
        openPanel('daemons', { query: p.rest });
        break;
      case '/artifacts':
        openPanel('artifacts');
        break;
      case '/artifact':
        if (p.rest) openPanel('artifact', { path: p.rest });
        else need('/artifact <path>');
        break;
      case '/events': {
        const view = parseEventViewArgs(p.rest);
        openPanel('events', { ...view });
        break;
      }
      case '/find':
        if (p.rest) openPanel('events', { filter: 'all', query: p.rest });
        else need('/find <text>');
        break;
      case '/item':
        if (p.rest) openPanel('task', { itemId: p.rest });
        else need('/item <id>');
        break;
      case '/resume':
      case '/attach':
        void switchProject(p.rest);
        break;
      case '/clear':
        setEvents([]);
        setNotice('feed cleared');
        break;
      case '/run':
        setPanel(null);
        setNotice('already following the live daemon feed');
        break;
      case '/reconnect':
        setNotice('reconnecting…');
        wsRef.current?.close();
        break;
      case '/cancel':
        stopWaiting();
        break;
      case '/quit':
        quit();
        break;
      case '/task':
        if (p.rest) void api.postTask(p.rest).then((it) => setNotice(`queued ${it.id}`), err);
        else need('/task <text>');
        break;
      case '/plan':
        if (!p.rest) need('/plan <objective>');
        else void api.previewPlan(p.rest).then((plan) => {
          if (plan.error) {
            showOutput(`Planner could not draft a plan: ${plan.error}`);
            return;
          }
          const lines = ['Planner preview (nothing queued):'];
          plan.steps.forEach((step, index) => {
            lines.push(`${index + 1}. ${step.title}${step.detail ? ` — ${step.detail}` : ''}`);
          });
          if (plan.notes.length) lines.push(`Notes: ${plan.notes.join('; ')}`);
          lines.push('Use /task <objective> to queue it.');
          showOutput(lines.join('\n'));
        }, err);
        break;
      case '/nudge':
        if (p.rest) void api.postNudge(p.rest).then(ok('nudge sent'), err);
        else need('/nudge <text>');
        break;
      case '/note':
        if (p.rest) void api.postNote(p.rest).then(ok('note added'), err);
        else need('/note <text>');
        break;
      case '/done':
        if (p.rest) void api.disposeBacklog(p.rest, 'done').then(ok(`done ${p.rest}`), err);
        else need('/done <id>');
        break;
      case '/skip':
        if (p.rest) void api.disposeBacklog(p.rest, 'skip').then(ok(`skipped ${p.rest}`), err);
        else need('/skip <id>');
        break;
      case '/stop':
        if (p.rest) void api.stopBacklog(p.rest).then(ok(`stopped ${p.rest}`), err);
        else need('/stop <id>');
        break;
      case '/new':
        openNewDaemon(p.rest);
        break;
      case '/backend':
        if (!p.rest) openPanel('config');
        else void api.setConfig('backend', p.rest).then(
          () => setNotice(`backend set to ${p.rest}`),
          err,
        );
        break;
      case '/config': {
        if (!p.rest) {
          openPanel('config');
          break;
        }
        const pairs = p.rest.split(/\s+/).filter(Boolean);
        const invalid = pairs.find((pair) => {
          const at = pair.indexOf('=');
          return at <= 0 || at === pair.length - 1;
        });
        if (invalid) {
          setNotice(`expected key=value, got ${invalid}`);
          break;
        }
        const updates = pairs.map((pair) => {
          const at = pair.indexOf('=');
          return api.setConfig(pair.slice(0, at), pair.slice(at + 1));
        });
        void Promise.all(updates).then(
          () => setNotice(`updated ${updates.length} setting(s)`),
          err,
        );
        break;
      }
      case '/reset':
        void api.resetManager().then(ok('Manager context reset'), err);
        break;
      case '/skills':
        void api.skills(p.rest || 'ls').then(showOutput, err);
        break;
      default:
        setNotice(`${p.cmd.name} not yet wired`);
    }
  };

  const submitFreeText = async (text: string) => {
    // Natural language goes to the Manager front-door — it decides whether to
    // reply (chat) or dispatch a mission. No task/nudge modes to think about.
    // We STREAM the turn (SSE): the operator's line lands immediately, a live
    // "thinking" indicator shows Argus is working, phases update it in real
    // time, and the reply grows block-by-block — instead of a frozen screen
    // until the whole turn ends. Reply blocks share a message_id so EventLog
    // coalesces them into ONE growing row (kept live, out of <Static>).
    if (managerRequestRef.current) {
      setNotice('Argus is still working · wait or switch daemons to cancel');
      return;
    }
    const requestProject = projectRef.current;
    const requestId = ++managerEpochRef.current;
    const controller = new AbortController();
    managerRequestRef.current = { id: requestId, project: requestProject, controller };
    const isCurrent = () => {
      const request = managerRequestRef.current;
      return Boolean(
        request
        && request.id === requestId
        && request.project === requestProject
        && projectRef.current === requestProject
        && !controller.signal.aborted
      );
    };

    const replyId = `argus-${Date.now()}`;
    setEvents((e) => [...e, { type: 'ui.operator', text, ts: Date.now() / 1000 } as EventMsg]);
    setPhase('');
    setStartedAt(Date.now());
    setTick(0);
    setPending(true);
    setNotice('');

    const say = (t: string) => {
      if (!isCurrent()) return;
      setEvents((events) => isCurrent()
        ? [
            ...events,
            { type: 'ui.argus', text: t, message_id: replyId, ts: Date.now() / 1000 } as EventMsg,
          ]
        : events);
    };

    let gotDelta = false;
    let streamErr: Error | null = null;
    try {
      try {
        await api.messageStream(text, {
          onPhase: (label) => {
            if (isCurrent()) setPhase(label);
          },
          onDelta: (block) => {
            if (!isCurrent()) return;
            gotDelta = true;
            say(block); // same message_id → EventLog merges into the growing reply
          },
          onDone: (result) => {
            if (!isCurrent()) return;
            if (result.kind === 'task') {
              captureAdmission(
                result.daemon,
                requestProject,
                Boolean(result.continuous),
              );
              say(taskDispatchMessage(result));
            }
            else if (!gotDelta) say(result.reply || '(no reply)');
          },
          onError: (err) => {
            if (isCurrent()) streamErr = err;
          },
        }, controller.signal);
      } catch (error) {
        if (isCurrent()) streamErr = error as Error; // network failure / stream couldn't open
      }

      if (!isCurrent()) return;

      // Fallback to the blocking endpoint only if streaming produced nothing.
      if (streamErr && !gotDelta) {
        try {
          const result = await api.message(text, controller.signal);
          if (!isCurrent()) return;
          if (result.kind === 'chat' && result.reply) say(result.reply);
          else if (result.kind === 'task') {
            captureAdmission(
              result.daemon,
              requestProject,
              Boolean(result.continuous),
            );
            say(taskDispatchMessage(result));
          }
          else say(result.reply || '(no response)');
        } catch (error) {
          if (isCurrent()) say(`(couldn’t reach Argus: ${(error as Error).message})`);
        }
      }
    } finally {
      if (managerRequestRef.current?.id === requestId) {
        managerRequestRef.current = null;
        setPending(false);
      }
    }
  };

  const submit = () => {
    const text = edit.value.trim();
    if (!text) return;
    if (!isSlash(text) && managerRequestRef.current) {
      setNotice('Argus is still working · wait or switch daemons to cancel');
      return;
    }
    setEdit(EMPTY);
    setMenuSel(0);
    setHistory((h) => remember(h, text));
    if (isSlash(text)) dispatchSlash(text);
    else void submitFreeText(text);
  };

  useInput((input, key) => {
    const paste = consumePasteChunk(input, pasteActiveRef.current);
    if (paste.handled) {
      pasteActiveRef.current = paste.active;
      if (paste.text && !panel) {
        if (replacement) return;
        if (daemonDraft) {
          const result = daemonFormInput(daemonDraft, paste.text, {});
          setDaemonDraft(result.draft);
        } else {
          setEdit((current) => insert(current, paste.text));
          setHistory((current) => current.pos === 0 ? current : { ...current, pos: 0 });
        }
        if (paste.pasted && paste.text.length > 20) {
          setNotice(`pasted ${Array.from(paste.text).length} chars · Enter to send`);
        }
      }
      return;
    }
    if (replacement) {
      if (key.escape) {
        dismissedAdmissionRef.current = Date.now() / 1000;
        setReplacement(null);
        setNotice('new work remains queued');
      } else if (!replacement.busy && (key.downArrow || input === 'j')) {
        setReplacement((current) => current ? {
          ...current,
          selection: moveSelection(current.selection, current.running.length, 1),
        } : current);
      } else if (!replacement.busy && (key.upArrow || input === 'k')) {
        setReplacement((current) => current ? {
          ...current,
          selection: moveSelection(current.selection, current.running.length, -1),
        } : current);
      } else if (!replacement.busy && key.return) {
        void replaceRunningDaemon();
      }
      return;
    }
    if (daemonDraft) {
      if (key.ctrl && input === 'd') {
        quit();
        return;
      }
      if (key.ctrl && input === 'c') {
        if (!daemonDraft.busy) setDaemonDraft(null);
        return;
      }
      const result = daemonFormInput(daemonDraft, input, key);
      if (result.intent === 'submit') void submitNewDaemon();
      else if (result.intent === 'cancel') setDaemonDraft(null);
      else if (result.draft !== daemonDraft) setDaemonDraft(result.draft);
      return;
    }
    if (key.ctrl && input === 'c') {
      if (pendingExit) {
        quit();
        return;
      }
      setPendingExit(true);
      setNotice('Ctrl-C again to exit · Ctrl-D also quits · the daemon keeps running');
      return;
    }
    if (key.ctrl && input === 'd') {
      quit();
      return;
    }
    if (key.ctrl && input === 'o') {
      setPanel((current) => current?.kind === 'operations' ? null : { kind: 'operations' });
      return;
    }
    if (pendingExit) setPendingExit(false); // any other key disarms the double-Ctrl-C
    if (panel) {
      const selectable = panel.kind === 'daemons' || panel.kind === 'artifacts' || panel.kind === 'backlog';
      const daemonRows = panel.kind === 'daemons'
        ? filterProjects(rankProjects((panel.data as ProjectRow[]) ?? []), panel.query ?? '')
        : [];
      const backlogRows = panel.kind === 'backlog'
        ? (panel.all ? snap?.backlog ?? [] : visibleBacklogItems(snap?.backlog ?? [], false))
        : [];
      if (key.escape || input === 'q') {
        setPanel(null);
      } else if (panel.kind === 'daemons' && input === 'n') {
        setPanel(null);
        openNewDaemon();
      } else if (panel.kind === 'daemons' && input === '/') {
        setPanel(null);
        setEdit(fromString('/daemons '));
        setMenuSel(0);
      } else if (selectable && (key.downArrow || input === 'j')) {
        const count = panel.kind === 'daemons'
          ? daemonRows.length
          : panel.kind === 'backlog'
          ? backlogRows.length
          : Array.isArray(panel.data)
          ? panel.data.length
          : 0;
        setPanel((current) => current ? { ...current, selection: moveSelection(current.selection ?? 0, count, 1) } : current);
      } else if (selectable && (key.upArrow || input === 'k')) {
        const count = panel.kind === 'daemons'
          ? daemonRows.length
          : panel.kind === 'backlog'
          ? backlogRows.length
          : Array.isArray(panel.data)
          ? panel.data.length
          : 0;
        setPanel((current) => current ? { ...current, selection: moveSelection(current.selection ?? 0, count, -1) } : current);
      } else if (selectable && key.return) {
        if (panel.kind === 'daemons') {
          const selected = daemonRows[panel.selection ?? 0];
          if (selected) activateProject(selected);
        } else if (panel.kind === 'artifacts') {
          const rows = (panel.data as ArtifactInfo[]) ?? [];
          const selected = rows[panel.selection ?? 0];
          if (selected?.exists) openPanel('artifact', { path: selected.path });
          else if (selected) {
            setPanel(null);
            setNotice(`artifact is declared but missing: ${selected.path}`);
          }
        } else {
          const selected = backlogRows[panel.selection ?? 0];
          if (selected) openPanel('task', { itemId: selected.id });
        }
      } else if (key.return) {
        setPanel(null);
      } else if (key.downArrow || input === 'j') {
        setPanel((current) => current ? { ...current, page: (current.page ?? 0) + 1 } : current);
      } else if (key.upArrow || input === 'k') {
        setPanel((current) => current ? { ...current, page: Math.max(0, (current.page ?? 0) - 1) } : current);
      }
      return;
    }

    const comps = slashCompletions(edit.value);
    const menuOpen = comps.length > 0;

    if (key.escape && managerRequestRef.current && !menuOpen) {
      stopWaiting();
      return;
    }
    if (key.escape) {
      if (menuOpen) setEdit(EMPTY);
      return;
    }
    if (menuOpen) {
      if (key.upArrow) {
        setMenuSel((s) => (s - 1 + comps.length) % comps.length);
        return;
      }
      if (key.downArrow) {
        setMenuSel((s) => (s + 1) % comps.length);
        return;
      }
      const chosen = comps[Math.min(menuSel, comps.length - 1)];
      if (key.tab) {
        setEdit(fromString(applyCompletion(chosen)));
        setMenuSel(0);
        return;
      }
      if (key.return) {
        const typed = edit.value.trim();
        const isFull =
          typed.toLowerCase() === chosen.name.toLowerCase() ||
          (chosen.aliases ?? []).some((a) => a.toLowerCase() === typed.toLowerCase());
        if (!isFull && chosen.arg) {
          // partial token + the command takes an arg → complete and wait for it
          setEdit(fromString(applyCompletion(chosen)));
          setMenuSel(0);
        } else {
          // the name is fully typed (run it as-is) or takes no arg (complete + run)
          const run = isFull ? typed : chosen.name;
          setEdit(EMPTY);
          setMenuSel(0);
          setHistory((h) => remember(h, run));
          dispatchSlash(run);
        }
        return;
      }
    }

    if (key.return) {
      submit();
      return;
    }
    if (key.leftArrow) {
      setEdit(left);
      return;
    }
    if (key.rightArrow) {
      setEdit(right);
      return;
    }
    if (key.upArrow) {
      const r = older(history, edit.value);
      setHistory(r.h);
      setEdit(fromString(r.value));
      return;
    }
    if (key.downArrow) {
      const r = newer(history);
      setHistory(r.h);
      setEdit(fromString(r.value));
      return;
    }
    if (key.ctrl && input === 'a') {
      setEdit(home);
      return;
    }
    if (key.ctrl && input === 'e') {
      setEdit(end);
      return;
    }
    if (key.ctrl && input === 'b') {
      setEdit(left);
      return;
    }
    if (key.ctrl && input === 'f') {
      setEdit(right);
      return;
    }
    if (key.ctrl && input === 'w') {
      setEdit(deleteWordBefore);
      return;
    }
    if (key.ctrl && input === 'u') {
      setEdit(killToStart);
      return;
    }
    if (key.ctrl && input === 'k') {
      setEdit(killToEnd);
      return;
    }
    if (key.backspace || key.delete) {
      setEdit(backspace);
      setHistory((hh) => (hh.pos === 0 ? hh : { ...hh, pos: 0 }));
      return;
    }
    if (input === '?' && edit.value === '') {
      openPanel('help');
      return;
    }
    if (input && !key.ctrl && !key.meta) {
      setEdit((e) => insert(e, input));
      setHistory((hh) => (hh.pos === 0 ? hh : { ...hh, pos: 0 }));
    }
  });

  const comps = slashCompletions(edit.value);
  const backgroundExcludedRoles = pending ? ['manager'] : [];
  const eventRoles = overlayRoleActivities(snap?.roles ?? [], events);
  const managerPhase = (phase || 'handling your message')
    .replace(/^Manager\s*·\s*/i, '')
    .replace(/[.…]+$/u, '');
  const displayRoles = pending
    ? overlayActiveRole(
        eventRoles,
        'manager',
        managerPhase,
        Math.max(0, (Date.now() - startedAt) / 1000),
      )
    : eventRoles;
  const missionView = snap
    ? projectMissionView({ ...snap, roles: displayRoles }, events)
    : null;
  const partialDetail = snap?.partial
    ? (snap.diagnostics ?? []).map((item) => `${item.section}: ${item.message}`).join(' · ')
    : '';
  const sloDetail = snap?.observability?.slo.status === 'degraded'
    ? snap.observability.slo.violations.join(' · ')
    : '';
  const healthNotice = snapshotError
    ? `snapshot refresh failed · ${snapshotError}`
    : snap?.partial
    ? `snapshot partial · ${partialDetail || 'backend reported incomplete state'}`
    : sloDetail
    ? `SLO degraded · ${sloDetail}`
    : streamError && !connected
    ? `event stream reconnecting · ${streamError}`
    : '';

  return (
    <Box flexDirection="column" paddingX={1}>
      <Static items={['argus-header']}>
        {() => <Header width={terminal.columns} />}
      </Static>
      <GuardianBanner alert={activeGuardianAlert(events)} />
      {replacement ? (
        <DaemonReplacementPicker state={replacement} width={terminal.columns} />
      ) : daemonDraft ? (
        <NewDaemonForm draft={daemonDraft} />
      ) : panel ? (
        <PanelView
          panel={panel}
          snap={snap}
          events={events}
          viewportRows={terminal.rows}
          viewportColumns={terminal.columns}
          activeProject={project}
        />
      ) : (
        <>
          {missionView ? (
            <MissionCockpit
              view={missionView}
              width={terminal.columns}
              spentUsd={snap?.spend_usd}
              spendStatus={snap?.spend_status}
              dailyCapUsd={snap?.daemon.daily_cap_usd}
              globalDailyCapUsd={snap?.daemon.global_daily_cap_usd}
              requestUsage={snap?.request_usage}
            />
          ) : null}
          <EventLog events={events} width={terminal.columns} mode="conversation" />
          {pending && (
            <ThinkingLine
              tick={tick}
              phase={phase}
              elapsedS={Math.max(0, Math.floor((Date.now() - startedAt) / 1000))}
            />
          )}
          <PromptBox edit={edit} width={terminal.columns} />
          <SlashMenu items={comps} selected={Math.min(menuSel, comps.length - 1)} />
          <Footer
            notice={notice}
            health={healthNotice}
            width={terminal.columns}
          />
        </>
      )}
    </Box>
  );
}
