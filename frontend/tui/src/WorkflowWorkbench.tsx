import React, { useEffect, useState } from 'react';
import { Box, Text, useApp, useInput } from 'ink';
import stringWidth from 'string-width';
import type { ApiClient } from './api.js';
import type { WorkflowCatalog, WorkflowCreate, WorkflowSnapshot } from '../../core/src/applicationWorkflows.js';
import { workflowActorLabel } from '../../core/src/applicationWorkflows.js';
import { EMPTY, backspace, fromString, insert, left, right, killToStart, type Edit } from './input/editor.js';
import { PromptBox } from './components/PromptBox.js';
import { Wordmark } from './components/Wordmark.js';
import { useTerminalSize } from './useTerminalSize.js';
import { theme } from './theme.js';

type DraftField = 'project' | 'domain' | 'profile' | 'mode' | 'objective';
const HELP = '/new  /open <absolute directory>  /use <id>  /run  /resume <reason>  /recover  /stop  /spec  /conversation  /file <path>  /status  /exit';

function wrappedLines(text: string, columns: number): string[] {
  const rows = [''];
  let width = 0;
  for (const character of text.replace(/\t/g, '    ')) {
    if (character === '\n') { rows.push(''); width = 0; continue; }
    const size = stringWidth(character);
    if (width + size > columns) { rows.push(''); width = 0; }
    rows[rows.length - 1] += character;
    width += size;
  }
  return rows;
}

export function WorkflowWorkbench({ api, initialProject = '', initialObjective = '' }: {
  api: ApiClient; initialProject?: string; initialObjective?: string;
}) {
  const { exit } = useApp();
  const terminal = useTerminalSize();
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null);
  const [selected, setSelected] = useState(initialProject);
  const [work, setWork] = useState<WorkflowSnapshot | null>(null);
  const [edit, setEdit] = useState<Edit>(EMPTY);
  const [error, setError] = useState('');
  const [loadError, setLoadError] = useState('');
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(HELP);
  const [page, setPage] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [field, setField] = useState<DraftField | null>(null);
  const [draft, setDraft] = useState<WorkflowCreate>({
    project: '', domain: '', profile: '', mode: 'design', objective: initialObjective,
  });

  useEffect(() => {
    let cancelled = false;
    void api.workflows.catalog().then(value => { if (!cancelled) setCatalog(value); })
      .catch(failure => { if (!cancelled) setLoadError(String(failure)); });
    return () => { cancelled = true; };
  }, [api]);
  useEffect(() => {
    let cancelled = false;
    let polling = false;
    setWork(null);
    if (!selected) return;
    const refresh = async () => {
      if (polling) return;
      polling = true;
      try {
        const value = await api.workflows.snapshot(selected);
        if (!cancelled) { setWork(value); setLoadError(''); }
      } catch (failure) {
        if (!cancelled) setLoadError(String(failure));
      } finally { polling = false; }
    };
    void refresh();
    const timer = setInterval(() => void refresh(), 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [api, selected]);

  async function submit(raw: string) {
    if (busy) return;
    const input = raw.trim();
    setError('');
    setBusy(true);
    try {
      if (field) {
        if (!input) throw new Error(`Provide ${field}; Escape cancels creation.`);
        const next = { ...draft };
        if (field === 'mode') {
          if (input !== 'design' && input !== 'verify') throw new Error('Choose design or verify.');
          next.mode = input;
        } else next[field] = input;
        if (field === 'domain' && !catalog?.domains[input]) throw new Error('Choose one of the listed domains.');
        if (field === 'profile' && !catalog?.domains[next.domain]?.includes(input)) throw new Error('Choose a listed profile.');
        setDraft(next);
        const following: Record<DraftField, DraftField | null> = {
          project: 'domain', domain: 'profile', profile: 'mode', mode: 'objective', objective: null,
        };
        const nextField = following[field];
        if (nextField) {
          setField(nextField);
          const value = nextField === 'domain' ? (catalog?.domains.digital_circuit ? 'digital_circuit' : '')
            : nextField === 'profile' ? catalog?.domains[next.domain][0] || ''
              : nextField === 'mode' ? 'design' : next.objective;
          setEdit(fromString(value));
          return;
        }
        const created = await api.workflows.create(next);
        setSelected(created.id);
        setCatalog(await api.workflows.catalog());
        setField(null);
        setNotice('Created. /run starts engineering; plain messages ask Coordinator.');
        setEdit(EMPTY);
        return;
      }
      setEdit(EMPTY);
      const separator = input.indexOf(' ');
      const command = separator < 0 ? input : input.slice(0, separator);
      const argument = separator < 0 ? '' : input.slice(separator + 1).trim();
      if (command === '/exit') { exit(); return; }
      if (command === '/help' || !input) { setNotice(HELP); return; }
      if (command === '/new') {
        setField('project');
        setEdit(fromString(argument || process.cwd()));
        setPage(null);
        return;
      }
      if (command === '/open') {
        const opened = await api.workflows.open(argument);
        setSelected(opened.id);
        setCatalog(await api.workflows.catalog());
        return;
      }
      if (command === '/use') {
        const opened = await api.workflows.snapshot(argument);
        setSelected(opened.id);
        return;
      }
      if (!selected) throw new Error('Create /new or /open an existing work order first.');
      if (command === '/file') {
        const document = await api.workflows.document(selected, argument);
        setPage(`${document.path}${document.truncated ? ' (first 128 KiB)' : ''}\n\n${document.text}`);
        setOffset(0);
        return;
      }
      if (command === '/spec') {
        const current = await api.workflows.snapshot(selected);
        setPage(current.plan ? `${current.plan.roadmap.join('\n')}\n\n${current.plan.spec}` : 'No specification proposed yet.');
        setOffset(0);
        return;
      }
      if (command === '/conversation') {
        const current = await api.workflows.snapshot(selected);
        setPage(current.messages.map(turn => `You: ${turn.user}\nCoordinator: ${turn.reply}`).join('\n\n')
          || 'No conversation yet.');
        setOffset(0);
        return;
      }
      if (command === '/status') {
        setWork(await api.workflows.snapshot(selected));
        setPage(null);
        return;
      }
      const action = command === '/run' ? 'run' : command === '/resume' ? 'resume'
        : command === '/recover' ? 'recover' : command === '/stop' ? 'stop' : 'chat';
      if (input.startsWith('/') && action === 'chat') throw new Error(`Unknown command. ${HELP}`);
      const current = await api.workflows.action(selected, {
        action, allow_model: ['run', 'resume', 'chat'].includes(action),
        reason: action === 'resume' ? argument : '', message: action === 'chat' ? raw : '',
      });
      setWork(current);
      setNotice(action === 'chat' ? 'Coordinator is reading current state; engineering is not restarted.'
        : `${action} request recorded; live state follows below.`);
    } catch (failure) {
      setError(String(failure));
    } finally { setBusy(false); }
  }

  useInput((input, key) => {
    if (key.ctrl && (input === 'c' || input === 'd' && !edit.value)) { exit(); return; }
    if (key.escape) { setPage(null); setField(null); setEdit(EMPTY); return; }
    if (page && key.pageDown) {
      setOffset(value => Math.min(value + 10, wrappedLines(page, terminal.columns - 2).length - 1));
      return;
    }
    if (page && key.pageUp) { setOffset(value => Math.max(0, value - 10)); return; }
    if (busy) return;
    if (key.return) { void submit(edit.value); return; }
    if (key.backspace || key.delete) setEdit(backspace);
    else if (key.leftArrow) setEdit(left);
    else if (key.rightArrow) setEdit(right);
    else if (key.ctrl && input === 'u') setEdit(killToStart);
    else if (!key.ctrl && !key.meta) setEdit(value => insert(value, input));
  });

  const lines = Math.max(4, terminal.rows - 12);
  const summary = work ? [
    `${work.objective}`,
    `${work.status}${work.independently_accepted ? ' - independently accepted' : ''} | ${work.project}`,
    catalog?.roles.map(role => `${role}${role === 'designer' && work.mode === 'verify' ? ' (not selected)' : ''}`).join(' / ') || '',
    work.feedback,
    ...work.calls.slice(-5).map(call =>
      `${workflowActorLabel(call.role)} ${call.operation}: ${call.status}${call.decision ? `; review ${call.decision.status}` : ''}${call.duration_s === null ? '' : ` (${call.duration_s.toFixed(2)}s wall)`}`),
    ...work.jobs.filter(job => job.detail).slice(-1).map(job => `${job.status}: ${job.detail}`),
    ...work.messages.slice(-1).flatMap(turn => [`You: ${turn.user}`, `Coordinator: ${turn.reply}`]),
  ].filter(Boolean).join('\n') : 'Create /new or /open an existing work order. Creation itself makes no model calls.';
  const choices = field === 'domain' ? Object.keys(catalog?.domains ?? {}).join(', ')
    : field === 'profile' ? (catalog?.domains[draft.domain] ?? []).join(', ')
      : field === 'mode' ? 'design, verify' : '';
  return <Box flexDirection="column" paddingX={1} width={terminal.columns}>
    <Wordmark />
    <Text dimColor>{catalog?.projects.map(project => `${project.id}: ${project.project}`).join(' | ') || 'No registered work orders'}</Text>
    <Box height={lines} flexDirection="column" overflow="hidden">
      <Text>{page ? wrappedLines(page, terminal.columns - 2).slice(offset, offset + lines).join('\n') : summary}</Text>
    </Box>
    <Text dimColor>{page ? 'PageUp/PageDown scroll; Escape closes. /file reads a relative project or evidence path.' : notice}</Text>
    {field && <Text color={theme.accent}>New work order - {field}{choices ? ` (${choices})` : ''}</Text>}
    {(error || loadError) && <Text color={theme.error}>{error || loadError}</Text>}
    {busy && <Text dimColor>Sending request...</Text>}
    <PromptBox edit={edit} width={terminal.columns} />
    <Text dimColor>Enter asks Coordinator; /run authorizes engineering model calls. /help lists controls. Ctrl-C detaches this UI.</Text>
  </Box>;
}
