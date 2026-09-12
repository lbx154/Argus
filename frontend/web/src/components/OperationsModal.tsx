import { isImeComposing } from '../lib/ime';
import { useEffect, useState } from 'react';
import { api, type MetricsSnapshot, type ResourceStatus, type Snapshot, type SourceUpdateStatus, type TrashEntry } from '../api';
import { Modal, ModalHeader } from './Modal';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import {
  faArrowRotateRight,
  faChartLine,
  faCheck,
  faCloudArrowDown,
  faDiagramProject,
  faGear,
  faListCheck,
  faMagnifyingGlass,
  faNoteSticky,
  faPaperPlane,
  faPlay,
  faRotateLeft,
  faTrashArrowUp,
} from '@fortawesome/free-solid-svg-icons';
import { useI18n } from '../i18n';
import { requestFailureText, routeRefused } from '../lib/requestFailure';
import { metricsFigures, outputSummary } from '../lib/rawSummary';
import { ResourceStatusView } from './ResourceStatus';
import { RawDisclosure } from './primitives';

type QuickAction = 'task' | 'nudge' | 'note' | 'plan';
type OperationTab = 'work' | 'runtime' | 'system' | 'recovery';
type Capability = 'sourceUpdate' | 'metrics' | 'trash' | 'resources';

// The hosted trial portal is built with this flag and never offers a source
// update, so the modal does not ask for one there.
const HOSTED_TRIAL = import.meta.env.VITE_ARGUS_HOSTED_TRIAL === '1';

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
  const { t } = useI18n();
  const [action, setAction] = useState<QuickAction>('task');
  const [text, setText] = useState('');
  const [workdir, setWorkdir] = useState(snap.session.workdir ?? snap.session.cwd ?? '');
  const [skillsArgs, setSkillsArgs] = useState('ls');
  // What a command came back with: the sentence on the page, the record in a fold.
  const [output, setOutput] = useState<{ text: string; raw?: string } | null>(null);
  const [failure, setFailure] = useState<{ text: string; technical: string } | null>(null);
  const [unavailable, setUnavailable] = useState<ReadonlySet<Capability>>(() => new Set());
  const [skillsOutput, setSkillsOutput] = useState('');
  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);
  const [sourceUpdate, setSourceUpdate] = useState<SourceUpdateStatus | null>(null);
  const [resources, setResources] = useState<ResourceStatus | null>(null);
  const [resourceError, setResourceError] = useState('');
  const [trash, setTrash] = useState<TrashEntry[]>([]);
  const [trashTotal, setTrashTotal] = useState(0);
  const [trashQuery, setTrashQuery] = useState('');
  const [busy, setBusy] = useState('');
  const [tab, setTab] = useState<OperationTab>('work');
  const markUnavailable = (capability: Capability) => setUnavailable((current) => {
    if (current.has(capability)) return current;
    return new Set([...current, capability]);
  });
  // One plain sentence on the page; the request line and status code wait in a fold.
  const reportFailure = (error: unknown) => setFailure(requestFailureText(error, t));
  const sourceUpdateOffered = !HOSTED_TRIAL && !unavailable.has('sourceUpdate');

  useEffect(() => {
    if (!open) return;
    setWorkdir(snap.session.workdir ?? snap.session.cwd ?? '');
    void api.metrics().then(setMetrics, (error) => {
      if (routeRefused(error)) markUnavailable('metrics');
      else reportFailure(error);
    });
    void api.trash().then((nextTrash) => {
      setTrash(nextTrash.entries);
      setTrashTotal(nextTrash.total);
    }, (error) => {
      if (routeRefused(error)) markUnavailable('trash');
      else reportFailure(error);
    });
  }, [open, snap.session.cwd, snap.session.workdir]);

  useEffect(() => {
    if (!open || !sourceUpdateOffered) return;
    let cancelled = false;
    const fail = (error: unknown) => {
      if (cancelled) return;
      if (routeRefused(error)) markUnavailable('sourceUpdate');
      else reportFailure(error);
    };
    const refresh = async () => {
      try {
        const next = await api.sourceUpdateStatus();
        if (!cancelled) setSourceUpdate(next);
      } catch (error) {
        fail(error);
      }
    };
    void api.sourceUpdateStatus().then(async (initial) => {
      if (cancelled) return;
      setSourceUpdate(initial);
      if (!initial.running) {
        const checking = await api.checkSourceUpdate();
        if (!cancelled) setSourceUpdate(checking);
      }
    }).catch(fail);
    const timer = window.setInterval(() => void refresh(), 1_500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, sourceUpdateOffered]);

  useEffect(() => {
    if (!open || tab !== 'system') return;
    setResources(null);
    setResourceError('');
    void api.resources().then(
      setResources,
      (error) => {
        if (routeRefused(error)) markUnavailable('resources');
        else setResourceError(t('operations.requestFailed'));
      },
    );
  }, [open, tab]);

  const run = async (key: string, operation: () => Promise<unknown>, success: string | null) => {
    if (busy) return;
    setBusy(key);
    setOutput(null);
    setFailure(null);
    try {
      const result = await operation();
      if (success !== null) {
        setOutput(success ? { text: success } : { text: t('operations.done'), raw: JSON.stringify(result, null, 2) });
      }
      onChanged();
    } catch (error) {
      reportFailure(error);
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
        setOutput({
          text: [
            ...plan.steps.map((step, index) => `${index + 1}. ${step.title}${step.detail ? ` — ${step.detail}` : ''}`),
            ...plan.notes.map((note) => `Note: ${note}`),
            ...(plan.error ? [`Error: ${plan.error}`] : []),
          ].join('\n'),
        });
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
      setTrashTotal((total) => Math.max(0, total - 1));
      await onRestored(result.sid);
      return result;
    }, `Restored ${entry.label}.`);
  };

  const incompatible = snap.daemon.alive && snap.daemon.protocol_compatible === false;
  const externalDaemon = snap.daemon.alive && snap.daemon.control_available === false;
  const replacements = snap.daemon_admission?.running_daemons ?? [];
  const actionIcon = action === 'task' ? faListCheck : action === 'nudge' ? faPaperPlane : action === 'note' ? faNoteSticky : faDiagramProject;
  const actionLabel = t(`operations.action.${action}`);
  const searchTrash = async () => {
    await run('trash-search', async () => {
      const result = await api.trash(trashQuery);
      setTrash(result.entries);
      setTrashTotal(result.total);
      return result;
    }, null);
  };

  return (
    <Modal open={open} onClose={() => !busy && onClose()} label={t('operations.title')} width="max-w-5xl">
      <ModalHeader title={t('operations.title')} sub={snap.session.display_name || sid} />
      <div className="flex gap-1 overflow-x-auto border-b border-line bg-panel px-4 py-2 scroll-thin">
        {([
          ['work', t('operations.work'), faListCheck],
          ['runtime', t('operations.runtime'), faGear],
          ['system', t('operations.system'), faChartLine],
          ['recovery', t('operations.recovery'), faTrashArrowUp],
        ] as const).map(([value, label, icon]) => (
          <button key={value} type="button" onClick={() => { setTab(value); setOutput(null); setFailure(null); }} aria-current={tab === value ? 'page' : undefined} className={`flex h-8 shrink-0 items-center justify-center gap-2 rounded-md px-3 text-xs font-medium ${tab === value ? 'bg-blue/10 text-blue' : 'text-ink-faint hover:bg-bg hover:text-ink'}`}><FontAwesomeIcon icon={icon} /><span>{label}</span></button>
        ))}
      </div>
      <div className="grid max-h-[76vh] gap-3 overflow-y-auto bg-bg p-3 scroll-thin lg:grid-cols-2">
        {tab === 'work' ? <section className="rounded-lg border border-line bg-panel p-4 lg:col-span-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">{t('operations.workInput')}</h3>
          <p className="mt-1 text-xs text-ink-faint">{t('operations.workHint')}</p>
          <div className="mt-3 grid grid-cols-2 gap-1 sm:grid-cols-4">
            {([
              ['task', faListCheck],
              ['nudge', faPaperPlane],
              ['note', faNoteSticky],
              ['plan', faDiagramProject],
            ] as const).map(([value, icon]) => (
              <button key={value} type="button" onClick={() => setAction(value)} aria-pressed={action === value} className={`flex h-9 items-center justify-center gap-2 rounded px-2 text-xs font-medium ${action === value ? 'bg-blue/10 text-blue' : 'bg-bg text-ink-dim hover:text-ink'}`}><FontAwesomeIcon icon={icon} /><span>{t(`operations.action.${value}`)}</span></button>
            ))}
          </div>
          <textarea value={text} onChange={(event) => setText(event.target.value)} rows={5} placeholder={action === 'plan' ? t('operations.planPlaceholder') : t('operations.actionPlaceholder', { action })} className="mt-3 w-full resize-y rounded border border-line bg-bg p-3 text-sm text-ink outline-none focus:border-blue" />
          <button type="button" onClick={() => void runQuickAction()} disabled={!!busy || !text.trim()} className="mt-2 flex h-9 items-center justify-center gap-2 rounded border border-blue/35 bg-blue/8 px-3 text-xs font-medium text-blue hover:border-blue-deep hover:bg-blue-deep hover:text-white disabled:opacity-40">{busy === 'quick' ? '…' : <><FontAwesomeIcon icon={actionIcon} /><span>{action === 'plan' ? t('operations.previewPlan') : t('operations.submitAction', { action: actionLabel })}</span></>}</button>
        </section> : null}

        {tab === 'runtime' ? <section className="rounded-lg border border-line bg-panel p-4 lg:col-span-2">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">{t('operations.runtime')}</h3>
          <p className="mt-1 text-xs text-ink-faint">{t('operations.runtimeHint')}</p>
          <label className="mt-3 block text-[10px] uppercase tracking-wide text-ink-faint">{t('operations.workdir')}</label>
          <div className="mt-1 flex gap-2">
            <input value={workdir} onChange={(event) => setWorkdir(event.target.value)} className="h-9 min-w-0 flex-1 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" />
            <button type="button" onClick={() => void run('cwd', () => api.setWorkdir(sid, workdir), t('operations.workdirUpdated'))} disabled={!!busy || !workdir.trim()} title={t('operations.applyWorkdir')} aria-label={t('operations.applyWorkdir')} className="flex h-9 w-9 items-center justify-center rounded border border-blue/50 text-xs text-blue disabled:opacity-40"><FontAwesomeIcon icon={faCheck} /></button>
          </div>
          <div className="mt-4 flex flex-wrap gap-2">
            <button type="button" onClick={() => void run('reset', () => api.resetManager(sid), 'Manager context reset.')} disabled={!!busy} title={t('operations.resetManager')} aria-label={t('operations.resetManager')} className="flex h-9 w-9 items-center justify-center rounded border border-line text-xs text-ink-dim disabled:opacity-40"><FontAwesomeIcon icon={faRotateLeft} /></button>
            <button type="button" onClick={() => void run('upgrade', () => requireCommandSuccess(api.upgradeDaemon(sid, snap.daemon_commands?.revision)), 'Current-release daemon started after safely draining active work.')} disabled={!!busy || externalDaemon} title={externalDaemon ? 'Externally supervised daemon cannot be restarted from this Web host' : incompatible ? 'Upgrade incompatible daemon' : 'Restart on current release'} aria-label={externalDaemon ? 'Externally supervised daemon' : incompatible ? 'Upgrade incompatible daemon' : 'Restart on current release'} className={`flex h-9 w-9 items-center justify-center rounded border text-xs disabled:opacity-40 ${incompatible ? 'border-err/60 bg-err/10 text-err' : 'border-line text-ink-dim'}`}><FontAwesomeIcon icon={faArrowRotateRight} /></button>
          </div>
          {sourceUpdateOffered ? <div className="mt-4 rounded-lg border border-line bg-bg p-3">
            <div className="flex flex-wrap items-start gap-3">
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-ink">{t('operations.sourceUpdate')}</span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${sourceUpdate?.state === 'failed' ? 'bg-err/10 text-err' : sourceUpdate?.update_available ? 'bg-warn/10 text-warn' : sourceUpdate?.update_available === false ? 'bg-ok/10 text-ok' : 'bg-line text-ink-dim'}`}>
                    {sourceUpdate?.running ? t('operations.updateRunning') : sourceUpdate?.update_available ? t('operations.updateAvailable') : sourceUpdate?.update_available === false ? t('operations.updateCurrent') : t('operations.updateChecking')}
                  </span>
                </div>
                <p className="mt-1 text-xs text-ink-faint">{sourceUpdate?.error || sourceUpdate?.message || t('operations.updateChecking')}</p>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-ink-dim">
                  <span>{t('operations.currentRevision')}: {sourceUpdate?.current_revision?.slice(0, 12) || '—'}</span>
                  <span>{t('operations.latestRevision')}: {sourceUpdate?.upstream_revision?.slice(0, 12) || '—'}</span>
                  {sourceUpdate?.phase && sourceUpdate.phase !== 'complete' && sourceUpdate.phase !== 'idle' ? <span>{t('operations.updatePhase')}: {sourceUpdate.phase}</span> : null}
                </div>
                {sourceUpdate?.running ? <div className="mt-2 h-1 overflow-hidden rounded bg-line"><div className="h-full w-1/2 animate-pulse rounded bg-blue" /></div> : null}
              </div>
              <button
                type="button"
                onClick={() => void run('source-update', async () => {
                  const next = await api.applySourceUpdate();
                  setSourceUpdate(next);
                  return next;
                }, null)}
                disabled={!!busy || Boolean(sourceUpdate?.running) || sourceUpdate?.can_update === false}
                title={sourceUpdate?.can_update === false ? (sourceUpdate.error || t('operations.updateUnavailable')) : t('operations.pullLatest')}
                aria-label={t('operations.pullLatest')}
                className="flex h-9 items-center gap-2 rounded border border-blue/50 px-3 text-xs font-medium text-blue disabled:opacity-40"
              >
                <FontAwesomeIcon icon={faCloudArrowDown} />
                <span>{sourceUpdate?.running ? t('operations.updateRunning') : t('operations.pullLatest')}</span>
              </button>
            </div>
            {sourceUpdate?.restart_required ? <p className="mt-2 text-xs text-warn">{t('operations.updateRestart')}</p> : null}
          </div> : null}
          {snap.daemon.protocol_error ? <p className="mt-2 text-xs text-err">{snap.daemon.protocol_error}</p> : null}
          {replacements.length ? (
            <div className="mt-4">
              <div className="text-[10px] uppercase tracking-wide text-ink-faint">{t('operations.replaceSlot')}</div>
              <div className="mt-2 space-y-1">
                {replacements.map((row) => (
                  <button key={row.id} type="button" disabled={!!busy} onClick={() => void run(`replace:${row.id}`, () => requireCommandSuccess(api.replaceDaemon(sid, row.id, Boolean(snap.continuous?.enabled), snap.daemon_commands?.revision)), `Parked ${row.label || row.id} and started this session.`)} title={`Replace ${row.label || row.id}`} aria-label={`Replace ${row.label || row.id}`} className="flex w-full items-center justify-between rounded border border-line bg-bg px-2 py-1.5 text-left text-xs text-ink-dim disabled:opacity-40"><span className="truncate">{row.label || row.id}</span><FontAwesomeIcon icon={faArrowRotateRight} className="ml-2 text-warn" /></button>
                ))}
              </div>
            </div>
          ) : null}
        </section> : null}

        {tab === 'system' ? <section className="rounded-lg border border-line bg-panel p-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">{t('operations.skills')}</h3>
          <div className="mt-3 flex gap-2">
            <input value={skillsArgs} onChange={(event) => setSkillsArgs(event.target.value)} className="h-9 min-w-0 flex-1 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" placeholder="ls, stats, show NAME…" />
            <button type="button" disabled={!!busy} onClick={() => void run('skills', async () => { const result = await api.skills(sid, skillsArgs); setSkillsOutput(result); return result; }, null)} title={t('operations.runSkill')} aria-label={t('operations.runSkill')} className="flex h-9 w-9 items-center justify-center rounded border border-blue/50 text-xs text-blue disabled:opacity-40"><FontAwesomeIcon icon={faPlay} /></button>
          </div>
          {skillsOutput ? (() => {
            const glance = outputSummary(skillsOutput);
            return (
              <div className="mt-3 text-xs text-ink-dim">
                <p className="break-words">
                  {glance.first}
                  {glance.lines > 1 ? <span className="text-ink-faint"> · {t('operations.outputLines', { count: glance.lines })}</span> : null}
                </p>
                <RawDisclosure>
                  <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg p-3 font-mono text-xs text-ink-dim scroll-thin">{skillsOutput}</pre>
                </RawDisclosure>
              </div>
            );
          })() : null}
        </section> : null}

        {tab === 'system' && !unavailable.has('metrics') ? <section className="rounded-lg border border-line bg-panel p-4">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-dim">{t('operations.metrics')}</h3>
          <div className="mt-3 flex items-center gap-3">
            <span className={`rounded px-2 py-1 text-xs font-semibold ${metrics?.slo?.status === 'healthy' ? 'bg-ok/10 text-ok' : 'bg-warn/10 text-warn'}`}>
              {!metrics ? t('operations.slo.loading') : t(metrics.slo?.status === 'healthy' ? 'operations.slo.healthy' : 'operations.slo.degraded')}
            </span>
            <span className="text-xs text-ink-faint">{t('operations.validationFailures', { count: metrics?.event_validation_failures ?? '—' })}</span>
          </div>
          {metrics ? (
            <p className="mt-3 text-xs text-ink-dim">
              {metricsFigures(metrics).map((figure, index) => (
                <span key={figure.key}>
                  {index > 0 ? <span className="text-ink-faint"> · </span> : null}
                  <span className="text-ink-faint">{t(`operations.metric.${figure.key}`)} </span>
                  <span className="font-mono tabular-nums text-ink">{figure.value}</span>
                </span>
              ))}
            </p>
          ) : null}
          {metrics ? (
            <RawDisclosure>
              <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-bg p-3 font-mono text-[10px] text-ink-dim scroll-thin">{JSON.stringify({ web: metrics.web, provider: metrics.provider, cost_control: metrics.cost_control }, null, 2)}</pre>
            </RawDisclosure>
          ) : null}
        </section> : null}

        {tab === 'system' && !unavailable.has('resources') ? <ResourceStatusView status={resources} error={resourceError} /> : null}

        {tab === 'recovery' && unavailable.has('trash') ? <p className="text-sm text-ink-faint lg:col-span-2">{t('operations.unavailableHere')}</p> : null}
        {tab === 'recovery' && !unavailable.has('trash') ? <section className="rounded-lg border border-line bg-panel p-4 lg:col-span-2">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="mr-auto text-xs font-semibold uppercase tracking-wide text-ink-dim">{t('operations.trash')} · {trashTotal}</h3>
            <input value={trashQuery} onChange={(event) => setTrashQuery(event.target.value)} onKeyDown={(event) => { if (!isImeComposing(event) && event.key === 'Enter') void searchTrash(); }} placeholder={t('operations.searchTrash')} className="h-8 min-w-52 rounded border border-line bg-bg px-2 text-xs text-ink outline-none focus:border-blue" />
            <button type="button" disabled={!!busy} onClick={() => void searchTrash()} title={t('operations.searchTrash')} aria-label={t('operations.searchTrash')} className="flex h-8 w-8 items-center justify-center rounded border border-blue/50 text-xs text-blue disabled:opacity-40"><FontAwesomeIcon icon={faMagnifyingGlass} /></button>
          </div>
          {!trash.length ? <p className="mt-3 text-xs text-ink-faint">{t('operations.trashEmpty')}</p> : (
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {trash.map((entry) => (
                <div key={entry.trash_id} className="flex items-center gap-3 rounded border border-line bg-bg p-2">
                  <div className="min-w-0 flex-1"><div className="truncate text-xs text-ink">{entry.label}</div><div className="truncate font-mono text-[10px] text-ink-faint">{entry.trash_path}</div></div>
                  <button type="button" disabled={!!busy} onClick={() => void restore(entry)} title={`Restore ${entry.label}`} aria-label={`Restore ${entry.label}`} className="flex h-8 w-8 items-center justify-center rounded border border-blue/50 text-xs text-blue disabled:opacity-40"><FontAwesomeIcon icon={faTrashArrowUp} /></button>
                </div>
              ))}
            </div>
          )}
          {trashTotal > trash.length ? <p className="mt-2 text-[10px] text-ink-faint">Showing the newest {trash.length} matches. Narrow the search to find older sessions.</p> : null}
        </section> : null}

        {failure ? (
          <div className="rounded-lg border border-line bg-panel p-3 text-sm text-ink-dim lg:col-span-2">
            <p>{failure.text}</p>
            {failure.technical ? (
              <RawDisclosure label={t('operations.technicalDetails')}>
                <pre className="mt-1 whitespace-pre-wrap font-mono">{failure.technical}</pre>
              </RawDisclosure>
            ) : null}
          </div>
        ) : null}
        {output ? (
          <div className="rounded-lg border border-line bg-panel p-3 text-sm text-ink-dim lg:col-span-2">
            <p className="whitespace-pre-wrap break-words">{output.text}</p>
            {output.raw ? (
              <RawDisclosure>
                <pre className="mt-1 whitespace-pre-wrap font-mono text-xs">{output.raw}</pre>
              </RawDisclosure>
            ) : null}
          </div>
        ) : null}
      </div>
    </Modal>
  );
}
