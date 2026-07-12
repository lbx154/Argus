import { useEffect, useState } from 'react';
import { api, type MetricsSnapshot, type Snapshot, type TrashEntry } from '../api';
import { Modal, ModalHeader } from './Modal';

type QuickAction = 'task' | 'nudge' | 'note' | 'plan';

const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error || 'Unknown error');

async function requireCommandSuccess<T>(operation: Promise<T>): Promise<T> {
  const result = await operation;
  const row = result && typeof result === 'object'
    ? result as Record<string, unknown>
    : {};
  const status = String(row.command_status ?? '');
  if (
    Number(row.rc ?? 0) !== 0
    || status === 'failed'
    || status === 'rejected'
  ) {
    throw new Error(String(row.error || `daemon command ${status || 'failed'}`));
  }
  return result;
}

export function OperationsModal({
  open,
  sid,
  snap,
  onClose,
  onChanged,
  onRestored,
}: {
  open: boolean;
  sid: string;
  snap: Snapshot;
  onClose: () => void;
  onChanged: () => void;
  onRestored: (sid: string) => void | Promise<void>;
}) {
  const [action, setAction] = useState<QuickAction>('task');
  const [text, setText] = useState('');
  const [launchCwd, setLaunchCwd] = useState(snap.session.launch_cwd ?? '');
  const [skillsArgs, setSkillsArgs] = useState('ls');
  const [output, setOutput] = useState('');
  const [skillsOutput, setSkillsOutput] = useState('');
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [busy, setBusy] = useState('');

  useEffect(() => {
    if (!open) return;
    setLaunchCwd(snap.session.launch_cwd ?? '');
    void Promise.all([api.metrics(), api.trash()]).then(
      ([nextMetrics, nextTrash]) => {
        setMetrics(nextMetrics);
        setTrash(nextTrash);
      },
      (error) => setOutput(errorText(error)),
    );
  }, [open, snap.session.launch_cwd]);

  const run = async (key: string, operation: () => Promise<unknown>, success: string | null) => {
    if (busy) return;
    setBusy(key);
    setOutput('');
    try {
      const result = await operation();
      if (success !== null) setOutput(success || JSON.stringify(result, null, 2));
      onChanged();
    } catch (error) {
      setOutput(errorText(error));
    } finally {
      setBusy('');
    }
  };

  const runQuickAction = async () => {
    const body = text.trim();
    if (!body) return;
    if (action === 'plan') {
      await run('quick', async () => {
        const plan = await api.previewPlan(sid, body);
        setOutput([
          ...plan.steps.map((step, index) => `${index + 1}. ${step.title}${step.detail ? ` — ${step.detail}` : ''}`),
          ...plan.notes.map((note) => `Note: ${note}`),
          ...(plan.error ? [`Error: ${plan.error}`] : []),
        ].join('\n'));
        return plan;
      }, null);
      return;
    }
    const operation = action === 'task'
      ? () => api.addTask(sid, body)
      : action === 'nudge'
      ? () => api.nudge(sid, body)
      : () => api.note(sid, body);
    await run('quick', operation, `${action} submitted.`);
    setText('');
  };

  const restore = async (entry: TrashEntry) => {
    await run(`restore:${entry.trash_id}`, async () => {
      const result = await api.restoreTrash(entry.trash_id);
      setTrash((rows) => rows.filter((row) => row.trash_id !== entry.trash_id));
      await onRestored(result.sid);
      return result;
    }, `Restored ${entry.label}.`);
  };

  const incompatible = snap.daemon.alive && snap.daemon.protocol_compatible === false;
  const replacements = snap.daemon_admission?.running_daemons ?? [];

  return (
    <Modal open={open} onClose={() => !busy && onClose()} label="Operations" width="max-w-5xl">
      <ModalHeader title="Operations" sub={`${snap.session.display_name || sid} · complete backend control surface`} />
      <div className="grid max-h-[76vh] gap-3 overflow-y-auto bg-bg p-3 scroll-thin lg:grid-cols-2">
        <section className="rounded-lg border border-line bg-panel p-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">Work input</h3>
          <div className="mt-3 flex gap-1">
            {(['task', 'nudge', 'note', 'plan'] as const).map((value) => (
              <button key={value} type="button" onClick={() => setAction(value)} className={`rounded px-2.5 py-1.5 text-xs capitalize ${action === value ? 'bg-blue-deep text-white' : 'bg-bg text-ink-dim'}`}>{value}</button>
            ))}
          </div>
          <textarea value={text} onChange={(event) => setText(event.target.value)} rows={5} placeholder={action === 'plan' ? 'Objective to preview; preview never queues work' : `${action} text`} className="mt-3 w-full resize-y rounded border border-line bg-bg p-3 text-sm text-ink outline-none focus:border-blue" />
          <button type="button" onClick={() => void runQuickAction()} disabled={!!busy || !text.trim()} className="mt-2 rounded bg-blue-deep px-3 py-2 text-xs font-medium text-white disabled:opacity-40">{busy === 'quick' ? 'Running…' : action === 'plan' ? 'Preview plan' : `Submit ${action}`}</button>
        </section>

        <section className="rounded-lg border border-line bg-panel p-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">Runtime</h3>
          <label className="mt-3 block text-[10px] uppercase tracking-wide text-ink-faint">Launch cwd</label>
          <div className="mt-1 flex gap-2">
            <input value={launchCwd} onChange={(event) => setLaunchCwd(event.target.value)} className="h-9 min-w-0 flex-1 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" />
            <button type="button" onClick={() => void run('cwd', () => api.setLaunchCwd(sid, launchCwd), 'Launch directory updated.')} disabled={!!busy || !launchCwd.trim()} className="rounded border border-blue/50 px-3 text-xs text-blue disabled:opacity-40">Apply</button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={() => void run('reset', () => api.resetManager(sid), 'Manager context reset.')} disabled={!!busy} className="rounded border border-line px-3 py-2 text-xs text-ink-dim disabled:opacity-40">Reset Manager</button>
            <button type="button" onClick={() => void run('upgrade', () => requireCommandSuccess(api.upgradeDaemon(sid, snap.daemon_commands?.revision)), 'Current-release handoff started; active work will finish before takeover.')} disabled={!!busy} className={`rounded border px-3 py-2 text-xs disabled:opacity-40 ${incompatible ? 'border-err/60 bg-err/10 text-err' : 'border-line text-ink-dim'}`}>{incompatible ? 'Upgrade incompatible daemon' : 'Restart current release'}</button>
          </div>
          {snap.daemon.protocol_error ? <p className="mt-2 text-xs text-err">{snap.daemon.protocol_error}</p> : null}
          {replacements.length ? (
            <div className="mt-4">
              <div className="text-[10px] uppercase tracking-wide text-ink-faint">Replace a running daemon slot</div>
              <div className="mt-2 space-y-1">
                {replacements.map((row) => (
                  <button key={row.id} type="button" disabled={!!busy} onClick={() => void run(`replace:${row.id}`, () => requireCommandSuccess(api.replaceDaemon(sid, row.id, Boolean(snap.continuous?.enabled), snap.daemon_commands?.revision)), `Parked ${row.label || row.id} and started this session.`)} className="flex w-full items-center justify-between rounded border border-line bg-bg px-2 py-1.5 text-left text-xs text-ink-dim disabled:opacity-40"><span className="truncate">{row.label || row.id}</span><span className="ml-2 text-warn">Replace</span></button>
                ))}
              </div>
            </div>
          ) : null}
        </section>

        <section className="rounded-lg border border-line bg-panel p-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">Skills</h3>
          <div className="mt-3 flex gap-2">
            <input value={skillsArgs} onChange={(event) => setSkillsArgs(event.target.value)} className="h-9 min-w-0 flex-1 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" placeholder="ls, stats, show NAME…" />
            <button type="button" disabled={!!busy} onClick={() => void run('skills', async () => { const result = await api.skills(sid, skillsArgs); setSkillsOutput(result); return result; }, null)} className="rounded border border-blue/50 px-3 text-xs text-blue disabled:opacity-40">Run</button>
          </div>
          {skillsOutput ? <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg p-3 font-mono text-xs text-ink-dim scroll-thin">{skillsOutput}</pre> : null}
        </section>

        <section className="rounded-lg border border-line bg-panel p-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">System metrics</h3>
          <div className="mt-3 flex items-center gap-3">
            <span className={`rounded px-2 py-1 text-xs font-semibold ${metrics?.slo?.status === 'healthy' ? 'bg-ok/10 text-ok' : 'bg-warn/10 text-warn'}`}>{metrics?.slo?.status ?? 'loading'}</span>
            <span className="text-xs text-ink-faint">event validation failures: {metrics?.event_validation_failures ?? '—'}</span>
          </div>
          {metrics ? <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg p-3 font-mono text-[10px] text-ink-dim scroll-thin">{JSON.stringify({ web: metrics.web, provider: metrics.provider, cost_control: metrics.cost_control }, null, 2)}</pre> : null}
        </section>

        <section className="rounded-lg border border-line bg-panel p-4 lg:col-span-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">Recoverable trash</h3>
          {!trash.length ? <p className="mt-3 text-xs text-ink-faint">Trash is empty.</p> : (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {trash.map((entry) => (
                <div key={entry.trash_id} className="flex items-center gap-3 rounded border border-line bg-bg p-2">
                  <div className="min-w-0 flex-1"><div className="truncate text-xs text-ink">{entry.label}</div><div className="truncate font-mono text-[10px] text-ink-faint">{entry.trash_path}</div></div>
                  <button type="button" disabled={!!busy} onClick={() => void restore(entry)} className="rounded border border-blue/50 px-2 py-1 text-xs text-blue disabled:opacity-40">Restore</button>
                </div>
              ))}
            </div>
          )}
        </section>

        {output ? <pre className="rounded-lg border border-line bg-panel p-3 font-mono text-xs whitespace-pre-wrap text-ink-dim lg:col-span-2">{output}</pre> : null}
      </div>
    </Modal>
  );
}
