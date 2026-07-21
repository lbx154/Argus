import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import type { ApiClient, EventMsg, Snapshot } from './api.js';
import { didYouMean, parseCommand, parseEventViewArgs } from './input/slash.js';
import type { PanelState } from './components/panels.js';

interface SlashDispatchDeps {
  api: ApiClient;
  openPanel: (kind: PanelState['kind'], opts?: Partial<PanelState>) => void;
  setPanel: Dispatch<SetStateAction<PanelState | null>>;
  setEvents: Dispatch<SetStateAction<EventMsg[]>>;
  setNotice: Dispatch<SetStateAction<string>>;
  setSnap: Dispatch<SetStateAction<Snapshot | null>>;
  projectRef: MutableRefObject<string>;
  closeStream: () => void;
  stopWaiting: () => void;
  quit: () => void;
  switchProject: (arg: string) => Promise<void>;
  openNewDaemon: (objective?: string) => void;
}

export function dispatchSlashCommand(line: string, deps: SlashDispatchDeps): void {
  const parsed = parseCommand(line);
  if (!parsed) return;
  if (!parsed.cmd) {
    const suggestion = didYouMean(parsed.name);
    deps.setNotice(
      suggestion
        ? `unknown ${parsed.name} — did you mean ${suggestion}?`
        : `unknown command ${parsed.name} — /help`,
    );
    return;
  }
  const ok = (message: string) => () => deps.setNotice(message);
  const err = (error: unknown) => deps.setNotice(`error: ${(error as Error).message}`);
  const need = (usage: string) => deps.setNotice(`usage: ${usage}`);
  const showOutput = (text: string) => deps.setEvents((events) => [
    ...events,
    { type: 'ui.argus', text, message_id: `local-${Date.now()}`, ts: Date.now() / 1000 } as EventMsg,
  ]);

  switch (parsed.cmd.name) {
    case '/help':
      deps.openPanel('help');
      break;
    case '/status':
      deps.openPanel('status');
      break;
    case '/roles':
      deps.openPanel('config');
      break;
    case '/doctor':
      deps.openPanel('doctor');
      break;
    case '/identity':
      if (!parsed.rest) deps.openPanel('identity');
      else if (parsed.rest.toLowerCase().startsWith('set ')) {
        const body = parsed.rest.slice(4).trim();
        if (body) void deps.api.setIdentity(body).then(ok('identity updated'), err);
        else need('/identity set <text>');
      } else need('/identity [set <text>]');
      break;
    case '/journal':
      deps.openPanel('journal');
      break;
    case '/backlog':
      deps.openPanel('backlog', { all: parsed.rest.trim() === 'all', selection: 0 });
      break;
    case '/daemons':
      deps.openPanel('daemons', { query: parsed.rest });
      break;
    case '/artifacts':
      deps.openPanel('artifacts');
      break;
    case '/artifact':
      if (parsed.rest) deps.openPanel('artifact', { path: parsed.rest });
      else need('/artifact <path>');
      break;
    case '/events':
      deps.openPanel('events', { ...parseEventViewArgs(parsed.rest) });
      break;
    case '/find':
      if (parsed.rest) deps.openPanel('events', { filter: 'all', query: parsed.rest });
      else need('/find <text>');
      break;
    case '/item':
      if (parsed.rest) deps.openPanel('task', { itemId: parsed.rest });
      else need('/item <id>');
      break;
    case '/resume':
    case '/attach':
      void deps.switchProject(parsed.rest);
      break;
    case '/rename':
      if (!parsed.rest) {
        need('/rename <name>');
        break;
      }
      void deps.api.renameProject(parsed.rest).then((result) => {
        deps.setSnap((current) => (
          current && current.session.id === result.sid
            ? {
                ...current,
                session: { ...current.session, display_name: result.name },
              }
            : current
        ));
        if (deps.projectRef.current === result.sid) {
          deps.setNotice(`renamed conversation to ${result.name}`);
        }
      }, err);
      break;
    case '/clear':
      deps.setEvents([]);
      deps.setNotice('feed cleared');
      break;
    case '/run':
      deps.setPanel(null);
      deps.setNotice('already following the live daemon feed');
      break;
    case '/reconnect':
      deps.setNotice('reconnecting…');
      deps.closeStream();
      break;
    case '/cancel':
      deps.stopWaiting();
      break;
    case '/abort':
      void deps.api.abortMission('operator used /abort').then(
        (result) => deps.setNotice(result.message),
        err,
      );
      break;
    case '/quit':
      deps.quit();
      break;
    case '/task':
      if (parsed.rest) void deps.api.postTask(parsed.rest).then((item) => deps.setNotice(`queued ${item.id}`), err);
      else need('/task <text>');
      break;
    case '/plan':
      if (!parsed.rest) need('/plan <objective>');
      else void deps.api.previewPlan(parsed.rest).then((plan) => {
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
      if (parsed.rest) void deps.api.postNudge(parsed.rest).then(ok('nudge sent'), err);
      else need('/nudge <text>');
      break;
    case '/note':
      if (parsed.rest) void deps.api.postNote(parsed.rest).then(ok('note added'), err);
      else need('/note <text>');
      break;
    case '/done':
      if (parsed.rest) void deps.api.disposeBacklog(parsed.rest, 'done').then(ok(`done ${parsed.rest}`), err);
      else need('/done <id>');
      break;
    case '/skip':
      if (parsed.rest) void deps.api.disposeBacklog(parsed.rest, 'skip').then(ok(`skipped ${parsed.rest}`), err);
      else need('/skip <id>');
      break;
    case '/stop':
      if (parsed.rest) void deps.api.stopBacklog(parsed.rest).then(ok(`stopped ${parsed.rest}`), err);
      else need('/stop <id>');
      break;
    case '/new':
      deps.openNewDaemon(parsed.rest);
      break;
    case '/backend':
      if (!parsed.rest) deps.openPanel('config');
      else void deps.api.setConfig('backend', parsed.rest).then(
        () => deps.setNotice(`backend set to ${parsed.rest}`),
        err,
      );
      break;
    case '/config': {
      if (!parsed.rest) {
        deps.openPanel('config');
        break;
      }
      const pairs = parsed.rest.split(/\s+/).filter(Boolean);
      const invalid = pairs.find((pair) => {
        const at = pair.indexOf('=');
        return at <= 0 || at === pair.length - 1;
      });
      if (invalid) {
        deps.setNotice(`expected key=value, got ${invalid}`);
        break;
      }
      const updates = pairs.map((pair) => {
        const at = pair.indexOf('=');
        return deps.api.setConfig(pair.slice(0, at), pair.slice(at + 1));
      });
      void Promise.all(updates).then(
        () => deps.setNotice(`updated ${updates.length} setting(s)`),
        err,
      );
      break;
    }
    case '/reset':
      void deps.api.resetManager().then(ok('Manager context reset'), err);
      break;
    case '/skills':
      void deps.api.skills(parsed.rest || 'ls').then(showOutput, err);
      break;
    default:
      deps.setNotice(`${parsed.cmd.name} not yet wired`);
  }
}
