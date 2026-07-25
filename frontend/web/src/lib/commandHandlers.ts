import { type MutableRefObject } from 'react';
import { parseEventViewArgs } from '../../../core/src/commands';
import { type EventViewFilter } from '../../../core/src/events';
import { api } from '../api';
import { type NoticeTone } from '../components/ActionNotice';
import { type WebCommandHandlers } from './webCommands';

interface BuildWebCommandHandlersOptions {
  activeSid: string | null;
  activityEventsRef: MutableRefObject<{ length: number }>;
  notify: (tone: NoticeTone, message: string) => void;
  onClearEvents: (offset: number) => void;
  onDispose: (id: string, op: 'done' | 'skip' | 'rm') => void;
  onOpenConfig: () => void;
  onOpenDoctor: () => void;
  onOpenHelp: () => void;
  onOpenIdentity: () => void;
  onOpenInspector: () => void;
  onOpenNewDaemon: () => void;
  onOpenOperations: () => void;
  onOpenSidebar: () => void;
  onReconnectEvents: () => void;
  onRenameProject: (name: string) => Promise<void>;
  onRewriteDraft: (draft: string) => void;
  onSelectProject: (id: string) => void;
  onSetArtifactPath: (path: string) => void;
  onSetEventFilter: (filter: EventViewFilter) => void;
  onSetEventQuery: (query: string) => void;
  onSetTaskItemId: (id: string) => void;
  onSetWorkspaceView: (view: 'mission' | 'activity') => void;
  onShowArtifacts: () => void;
  onStopIteration: (id: string) => void;
  onStopWaiting: () => void;
  refetchSnapshot: () => Promise<unknown>;
}

export function buildWebCommandHandlers({
  activeSid,
  activityEventsRef,
  notify,
  onClearEvents,
  onDispose,
  onOpenConfig,
  onOpenDoctor,
  onOpenHelp,
  onOpenIdentity,
  onOpenInspector,
  onOpenNewDaemon,
  onOpenOperations,
  onOpenSidebar,
  onReconnectEvents,
  onRenameProject,
  onRewriteDraft,
  onSelectProject,
  onSetArtifactPath,
  onSetEventFilter,
  onSetEventQuery,
  onSetTaskItemId,
  onSetWorkspaceView,
  onShowArtifacts,
  onStopIteration,
  onStopWaiting,
  refetchSnapshot,
}: BuildWebCommandHandlersOptions): WebCommandHandlers {
  return {
    status: async () => onOpenInspector(),
    roles: async () => onOpenOperations(),
    journal: async () => onOpenInspector(),
    backlog: async () => onSetWorkspaceView('mission'),
    item: async (rest) => { if (rest) onSetTaskItemId(rest); },
    artifacts: async () => onShowArtifacts(),
    artifact: async (rest) => { if (rest) onSetArtifactPath(rest); },
    events: async (rest) => {
      onSetWorkspaceView('activity');
      const { filter, query } = parseEventViewArgs(rest);
      onSetEventFilter(filter);
      onSetEventQuery(query);
    },
    find: async (rest) => {
      onSetWorkspaceView('activity');
      onSetEventFilter('all');
      onSetEventQuery(rest);
    },
    run: async () => onSetWorkspaceView('activity'),
    clear: async () => {
      onSetWorkspaceView('activity');
      onSetEventFilter('all');
      onSetEventQuery('');
      onClearEvents(activityEventsRef.current.length);
    },
    cancel: async () => onStopWaiting(),
    task: async (rest) => {
      if (!activeSid) return;
      await api.addTask(activeSid, rest);
      void refetchSnapshot();
      notify('success', 'Task queued.');
    },
    rewrite: async (rest) => {
      const draft = rest.trim();
      if (!draft) {
        notify('info', 'Type your prompt in the composer and press Rewrite, or use /rewrite <text>.');
        return;
      }
      onRewriteDraft(draft);
    },
    plan: async (rest) => {
      if (!activeSid) return;
      const result = await api.previewPlan(activeSid, rest);
      if (result.error) notify('error', result.error);
      else notify('info', result.steps.map((step) => step.title).join('\n') || 'Plan preview ready.');
    },
    nudge: async (rest) => {
      if (!activeSid) return;
      await api.nudge(activeSid, rest);
      notify('success', 'Guidance injected.');
    },
    abort: async (rest) => {
      if (!activeSid) return;
      await api.abortMission(activeSid, rest || 'operator abort');
      notify('info', 'Abort requested.');
    },
    note: async (rest) => {
      if (!activeSid) return;
      await api.note(activeSid, rest);
      notify('success', 'Note appended to timeline.');
    },
    done: async (rest) => { if (rest) onDispose(rest, 'done'); },
    skip: async (rest) => { if (rest) onDispose(rest, 'rm'); },
    stop: async (rest) => { if (rest) onStopIteration(rest); },
    new: async () => onOpenNewDaemon(),
    daemons: async () => onOpenSidebar(),
    resume: async (rest) => {
      if (rest && rest !== 'list') onSelectProject(rest);
      else onOpenSidebar();
    },
    attach: async (rest) => { if (rest) onSelectProject(rest); },
    rename: async (rest) => {
      if (!activeSid || !rest) return;
      await onRenameProject(rest);
    },
    doctor: async () => onOpenDoctor(),
    backend: async (rest) => {
      if (!activeSid || !rest) {
        onOpenConfig();
        return;
      }
      await api.setConfig(activeSid, 'runner_backend', rest);
      notify('success', `Backend set to ${rest}.`);
    },
    config: async (rest) => {
      if (!activeSid || !rest) {
        onOpenConfig();
        return;
      }
      const eqIdx = rest.indexOf('=');
      if (eqIdx > 0) {
        await api.setConfig(activeSid, rest.slice(0, eqIdx).trim(), rest.slice(eqIdx + 1).trim());
        notify('success', 'Config updated.');
      } else {
        onOpenConfig();
      }
    },
    identity: async () => onOpenIdentity(),
    reset: async () => {
      if (!activeSid) return;
      await api.resetManager(activeSid);
      notify('success', 'Manager context reset.');
    },
    skills: async (rest) => {
      if (!activeSid) return;
      const text = await api.skills(activeSid, rest || 'ls');
      notify('info', text.slice(0, 400));
    },
    reconnect: async () => onReconnectEvents(),
    help: async () => onOpenHelp(),
    quit: async () => notify('info', 'Background work continues; close this browser tab when ready.'),
  };
}
