import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, useApp, useInput, useStdout } from 'ink';
import {
  ApiClient,
  type DaemonStartResult,
  type ArtifactInfo,
  type ProjectRow,
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
import { applyCompletion, isSlash, parseResumeTarget, slashCompletions } from './input/slash.js';
import { Header } from './components/Header.js';
import { EventLog } from './components/EventLog.js';
import { PromptBox } from './components/PromptBox.js';
import { SlashMenu, slashMenuVisibleRows } from './components/SlashMenu.js';
import { Footer } from './components/Footer.js';
import { ThinkingLine } from './components/ThinkingLine.js';
import { GuardianBanner } from './components/GuardianBanner.js';
import { NewDaemonForm } from './components/NewDaemonForm.js';
import { PanelView } from './components/panels.js';
import { activeGuardianAlert } from './guardian.js';
import { useTerminalSize } from './useTerminalSize.js';
import { filterProjects, rankProjects } from '../../core/src/projects.js';
import { moveSelection } from './input/selection.js';
import { visibleBacklogItems } from '../../core/src/backlog.js';
import {
  overlayActiveRole,
  overlayRoleActivities,
} from '../../core/src/activity.js';
import {
  daemonDraftValues,
  daemonFormInput,
  newDaemonDraft,
  type NewDaemonDraft,
} from './newDaemonForm.js';
import { MissionCockpit } from './components/MissionCockpit.js';
import { consumePasteChunk } from './input/paste.js';
import {
  DaemonReplacementPicker,
  type DaemonReplacementState,
} from './components/DaemonReplacementPicker.js';
import { projectMissionView } from '../../core/src/missionView.js';
import { useProjectFeed } from './appProjectFeed.js';
import { useManagerSession } from './appManagerSession.js';
import { usePanelState } from './appPanelState.js';
import { dispatchSlashCommand } from './appSlashDispatch.js';

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
  const {
    snap,
    setSnap,
    events,
    setEvents,
    connected,
    snapshotError,
    streamError,
    closeStream,
    shutdown: shutdownFeed,
  } = useProjectFeed(api, project);
  const [edit, setEdit] = useState<Edit>(EMPTY);
  const [history, setHistory] = useState<History>(EMPTY_HISTORY);
  const [menuSel, setMenuSel] = useState(0);
  const [notice, setNotice] = useState(initialNotice);
  const { panel, setPanel, openPanel } = usePanelState(api, project);
  const [daemonDraft, setDaemonDraft] = useState<NewDaemonDraft | null>(null);
  const [replacement, setReplacement] = useState<DaemonReplacementState | null>(
    () => replacementState(
      initialAdmission,
      initialProject,
      initialResumeContinuous,
    ),
  );
  const [pendingExit, setPendingExit] = useState(false);
  const [showReasoning, setShowReasoning] = useState(() => {
    const configured = String(process.env.ARGUS_SKILL_SHOW_REASONING ?? '').toLowerCase();
    return !['0', 'false', 'no', 'off'].includes(configured);
  });

  useEffect(() => {
    setMenuSel(0);
  }, [edit.value]);
  const creatingProjectRef = useRef(false);
  const dismissedAdmissionRef = useRef(0);
  const pasteActiveRef = useRef(false);

  useEffect(() => {
    if (!stdout.isTTY) return;
    stdout.write('\u001b[?2004h');
    return () => {
      stdout.write('\u001b[?2004l');
    };
  }, [stdout]);

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

  const {
    pending,
    phase,
    phaseHeartbeat,
    phaseQuietS,
    startedAt,
    tick,
    managerRequestRef,
    cancelManagerTurn,
    stopWaiting,
    submitFreeText,
  } = useManagerSession({
    api,
    projectRef,
    setEvents,
    setNotice,
    captureAdmission,
  });

  const changeProject = (id: string): boolean => {
    if (id === projectRef.current) return false;
    cancelManagerTurn();
    projectRef.current = id;
    setProject(id);
    setReplacement(null);
    dismissedAdmissionRef.current = 0;
    return true;
  };

  const quit = () => {
    cancelManagerTurn();
    shutdownFeed();
    exit();
  };

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

  const dispatchSlash = (line: string) => {
    dispatchSlashCommand(line, {
      api,
      openPanel,
      setPanel,
      setEvents,
      setNotice,
      setSnap,
      projectRef,
      closeStream,
      stopWaiting,
      quit,
      switchProject,
      openNewDaemon,
    });
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
    if (key.ctrl && input === 't') {
      setShowReasoning((current) => {
        setNotice(`reasoning ${current ? 'hidden' : 'shown'}`);
        return !current;
      });
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
  const slashMenuOpen = comps.length > 0 && !replacement && !daemonDraft && !panel;
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
      <Header width={terminal.columns} />
      {!slashMenuOpen ? <GuardianBanner alert={activeGuardianAlert(events)} /> : null}
      {replacement ? (
        <DaemonReplacementPicker state={replacement} width={terminal.columns} />
      ) : daemonDraft ? (
        <NewDaemonForm draft={daemonDraft} />
      ) : (
        <>
          {missionView && !slashMenuOpen && !panel ? (
            <MissionCockpit
              view={missionView}
              width={terminal.columns}
              height={terminal.rows}
              spentUsd={snap?.global_spend_usd}
              spendStatus={snap?.global_spend_status}
              globalDailyCapUsd={snap?.daemon.global_daily_cap_usd}
              requestUsage={snap?.request_usage}
            />
          ) : null}
          {/* Ink 5 retains a root pointer to the latest Static node after unmount;
              keep EventLog mounted and collapse it while overlays are open. */}
          <EventLog
            events={events}
            width={terminal.columns}
            mode="conversation"
            liveMessageId={managerRequestRef.current?.messageId}
            collapsed={slashMenuOpen || Boolean(panel)}
            showIdle={!missionView}
            showReasoning={showReasoning}
          />
          {panel ? (
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
              {pending && !slashMenuOpen && (
                <ThinkingLine
                  tick={tick}
                  phase={phase}
                  heartbeat={phaseHeartbeat}
                  quietS={phaseQuietS}
                  elapsedS={Math.max(0, Math.floor((Date.now() - startedAt) / 1000))}
                />
              )}
              <Box flexDirection="column" flexShrink={0}>
                <SlashMenu
                  items={comps}
                  selected={Math.min(menuSel, comps.length - 1)}
                  maxVisible={slashMenuVisibleRows(terminal.rows)}
                />
                <PromptBox
                  edit={edit}
                  width={terminal.columns}
                  rowsBelow={slashMenuOpen ? 0 : 1}
                />
              </Box>
              {!slashMenuOpen ? (
                <Footer
                  notice={notice}
                  health={healthNotice}
                  width={terminal.columns}
                />
              ) : null}
            </>
          )}
        </>
      )}
    </Box>
  );
}
