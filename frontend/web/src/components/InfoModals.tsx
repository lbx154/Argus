import { useDoctor, useConfig, useIdentity, useTranscript } from '../hooks';
import { Modal, ModalHeader } from './Modal';
import { Spinner, EmptyHint } from './primitives';
import { effortColor } from '../lib/theme';
import { ago } from '../lib/format';
import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../api';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCheck, faFloppyDisk } from '@fortawesome/free-solid-svg-icons';
import {
  compactConfigSource,
  conciseConfigKnobs,
  connectionTopology,
  type DisplayConfigKnob,
} from '../lib/configSurface';

const BUDGET_FIELDS = [
  { alias: 'per_mission_cap', env: 'ARGUS_SKILL_PER_MISSION_CAP_USD', label: 'Per mission', unit: 'USD' },
  { alias: 'daily_cap', env: 'ARGUS_SKILL_DAILY_CAP_USD', label: 'Project daily', unit: 'USD' },
  { alias: 'global_daily_cap', env: 'ARGUS_SKILL_GLOBAL_DAILY_CAP_USD', label: 'Global daily', unit: 'USD' },
  { alias: 'codex_daily_requests', env: 'ARGUS_SKILL_CODEX_DAILY_CALL_CAP', label: 'Codex calls / day', unit: 'calls' },
  { alias: 'copilot_daily_requests', env: 'ARGUS_SKILL_COPILOT_DAILY_CALL_CAP', label: 'Copilot calls / day', unit: 'calls' },
  { alias: 'copilot_daily_premium', env: 'ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP', label: 'Copilot premium / day', unit: 'requests' },
] as const;

/** Doctor: health checks with the single recommended fix pinned + gold, plus the
 *  daemon.log tail. Read-only diagnostics. */
export function DoctorModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading } = useDoctor(sid, open);
  return (
    <Modal open={open} onClose={onClose} label="Doctor" width="max-w-3xl">
      <ModalHeader title="Doctor" sub="daemon health checks + recommended root-cause fix" />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {data?.recommended && (
          <div className="mb-4 rounded-lg border border-gold/40 bg-gold/5 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-gold">recommended fix</div>
            <div className="mt-1 text-sm text-ink">{data.recommended.name}</div>
            <div className="mt-0.5 text-xs text-ink-dim">{data.recommended.detail}</div>
            {data.recommended.fix && (
              <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-bg p-2 font-mono text-xs text-blue-sky">{data.recommended.fix}</pre>
            )}
          </div>
        )}
        <div className="space-y-1.5">
          {(data?.checks ?? []).map((c, i) => (
            <div key={i} className="flex items-start gap-2 rounded-md border border-line/60 px-3 py-2">
              <span className={c.ok ? 'text-ok' : 'text-err'}>{c.ok ? '✓' : '✗'}</span>
              <div className="min-w-0">
                <div className="text-xs font-medium text-ink">{c.name}</div>
                {c.detail && <div className="mt-0.5 text-[11px] text-ink-dim">{c.detail}</div>}
                {!c.ok && c.fix && (
                  <pre className="mt-1 whitespace-pre-wrap break-words rounded bg-bg p-1.5 font-mono text-xs text-ink-dim">{c.fix}</pre>
                )}
              </div>
            </div>
          ))}
        </div>
        {data?.log_tail && (
          <div className="mt-4">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">daemon.log</div>
            <pre className="max-h-48 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-bg p-3 font-mono text-xs leading-relaxed text-ink-dim scroll-thin">{data.log_tail}</pre>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function ConfigModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading, refetch } = useConfig(sid, open);
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [budgetResult, setBudgetResult] = useState('');
  const [budgets, setBudgets] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!open || !data) return;
    const byName = new Map(data.operator_knobs.map((knob) => [knob.name, knob.value]));
    setBudgets(Object.fromEntries(
      BUDGET_FIELDS.map((field) => [field.alias, byName.get(field.env) ?? '']),
    ));
  }, [data, open]);
  const saveBudgets = async () => {
    if (budgetBusy) return;
    setBudgetBusy(true);
    setBudgetResult('');
    try {
      const values = Object.fromEntries(BUDGET_FIELDS.map((field) => {
        const value = String(budgets[field.alias] ?? '').trim();
        if (!value) throw new Error(`${field.label} is required`);
        return [field.alias, value];
      }));
      await api.setBudgets(sid, values);
      await refetch();
      setBudgetResult('Budget limits saved. Restart running daemons to reload their process caps.');
    } catch (error) {
      setBudgetResult(error instanceof Error ? error.message : String(error));
    } finally {
      setBudgetBusy(false);
    }
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !value.trim() || busy) return;
    setBusy(true);
    setResult('');
    try {
      await api.setConfig(sid, name.trim(), value.trim());
      await refetch();
      setResult('Applied. Restart affected daemons to reload process-scoped settings.');
    } catch (error) {
      setResult(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  const knobGroups = conciseConfigKnobs(data?.operator_knobs ?? []).reduce<Record<string, DisplayConfigKnob[]>>(
    (groups, knob) => {
      (groups[knob.group] ??= []).push(knob);
      return groups;
    },
    {},
  );
  const connection = connectionTopology(window.location.origin, sid);
  return (
    <Modal open={open} onClose={onClose} label="Settings" width="max-w-4xl">
      <ModalHeader title="Settings" sub="effective roles, budgets, and essential controls" />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        <section className="mb-4 rounded-lg border border-line bg-surface p-3">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">Connection</div>
          <div className="mt-2 grid gap-2 text-[10px] sm:grid-cols-[100px_minmax(0,1fr)]">
            <span className="text-ink-faint">Web + REST API</span>
            <code className="min-w-0 break-all text-ink-dim">{connection.webApi}</code>
            <span className="text-ink-faint">Event stream</span>
            <code className="min-w-0 break-all text-ink-dim">{connection.eventStream}</code>
            <span className="text-ink-faint">Task daemon</span>
            <span className="text-ink-dim">{connection.daemon}</span>
          </div>
        </section>
        <section className="mb-4 rounded-lg border border-gold/40 bg-gold/5 p-3">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-gold">Budget and quota limits</div>
              <p className="mt-0.5 text-[10px] text-ink-faint">Set 0 for an uncapped provider-call limit where supported.</p>
            </div>
            <button type="button" onClick={() => void saveBudgets()} disabled={budgetBusy || isLoading} title="Save budget limits" aria-label="Save budget limits" className="flex h-9 w-9 items-center justify-center rounded bg-gold text-xs font-semibold text-bg disabled:opacity-40">{budgetBusy ? '…' : <FontAwesomeIcon icon={faFloppyDisk} />}</button>
          </div>
          <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {BUDGET_FIELDS.map((field) => (
              <label key={field.alias} className="rounded border border-line/70 bg-bg/60 p-2">
                <span className="block text-[10px] text-ink-faint">{field.label}</span>
                <div className="mt-1 flex items-center gap-2">
                  <input type="number" min="0" step={field.unit === 'USD' ? '0.1' : '1'} value={budgets[field.alias] ?? ''} onChange={(event) => setBudgets((current) => ({ ...current, [field.alias]: event.target.value }))} className="h-8 min-w-0 flex-1 bg-transparent font-mono text-sm text-ink outline-none" />
                  <span className="text-[9px] text-ink-faint">{field.unit}</span>
                </div>
              </label>
            ))}
          </div>
          {budgetResult ? <div className="mt-2 text-xs text-ink-dim">{budgetResult}</div> : null}
        </section>
        <form onSubmit={(event) => void submit(event)} className="mb-4 rounded-lg border border-blue/30 bg-blue/5 p-3">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-blue">Advanced setting</div>
          <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="name or alias, e.g. manager_model" className="h-9 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" />
            <input value={value} onChange={(event) => setValue(event.target.value)} placeholder="value" className="h-9 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" />
            <button disabled={busy || !name.trim() || !value.trim()} title="Apply advanced setting" aria-label="Apply advanced setting" className="flex h-9 w-9 items-center justify-center rounded bg-blue-deep text-xs font-medium text-white disabled:opacity-40">{busy ? '…' : <FontAwesomeIcon icon={faCheck} />}</button>
          </div>
          {result ? <div className="mt-2 text-xs text-ink-dim">{result}</div> : null}
        </form>
        <div className="grid gap-2 sm:grid-cols-2">
          {(data?.roles ?? []).map((r) => (
            <div key={r.role} className="rounded-lg border border-line bg-surface p-3">
              <div className="flex items-center justify-between gap-2">
                <div className="text-xs font-semibold capitalize text-ink">{r.role}</div>
                <span className="text-[10px] text-ink-faint">{r.backend_label}</span>
              </div>
              <div className="mt-2 truncate font-mono text-[11px] text-ink-dim" title={r.model}>{r.model}</div>
              <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-faint">
                <span className="truncate" title={r.model_source}>{compactConfigSource(r.model_source)}</span>
                {r.reasoning_effort && (
                  <span className="ml-auto shrink-0" style={{ color: effortColor(r.reasoning_effort) }}>
                    {r.reasoning_effort}
                  </span>
                )}
              </div>
              {r.description ? <p className="mt-2 text-[10px] leading-relaxed text-ink-faint">{r.description}</p> : null}
            </div>
          ))}
        </div>
        {Object.entries(knobGroups).map(([group, knobs]) => (
          <section key={group} className="mt-5">
            <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{group}</div>
            <div className="overflow-hidden rounded-lg border border-line">
              {knobs.map((knob, index) => (
                <div key={knob.name} className={`grid gap-1 px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto] ${index ? 'border-t border-line/60' : ''}`}>
                  <div className="min-w-0">
                    <div className="truncate text-xs font-medium text-ink-dim" title={knob.name}>{knob.label}</div>
                    <div className="mt-0.5 text-[10px] leading-relaxed text-ink-faint">{knob.doc}</div>
                  </div>
                  <div className="text-left sm:text-right">
                    <div className="font-mono text-[11px] text-ink">{knob.value}</div>
                    <div className="mt-0.5 text-[9px] text-ink-faint">{compactConfigSource(knob.source)}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))}
        <p className="mt-4 text-[10px] text-ink-faint">
          Role overrides are summarized above. Use Advanced setting for a specific
          override; the full registry remains available via <code>argus-skill --config-help</code>.
        </p>
      </div>
    </Modal>
  );
}

/** Identity: the operator identity text on a wordmarked panel. */
export function IdentityModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading, refetch } = useIdentity(sid, open);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');
  useEffect(() => {
    if (open && data != null) setDraft(data);
  }, [data, open]);
  const save = async () => {
    if (busy) return;
    setBusy(true);
    setResult('');
    try {
      await api.setIdentity(sid, draft);
      await refetch();
      setResult('Identity saved.');
    } catch (error) {
      setResult(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} label="Identity" width="max-w-2xl">
      <ModalHeader title="Identity" sub="who argus is working for on this project" />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-5">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading ? (
          <>
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={12} className="w-full resize-y rounded-lg border border-line bg-bg p-3 font-sans text-sm leading-relaxed text-ink outline-none focus:border-blue" placeholder="Describe who Argus is working for and durable preferences…" />
            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-ink-faint">{result}</span>
              <button type="button" onClick={() => void save()} disabled={busy || draft === (data ?? '')} title="Save identity" aria-label="Save identity" className="flex h-9 w-9 items-center justify-center rounded bg-blue-deep text-xs font-medium text-white disabled:opacity-40">{busy ? '…' : <FontAwesomeIcon icon={faFloppyDisk} />}</button>
            </div>
          </>
        ) : null}
      </div>
    </Modal>
  );
}

/** Transcript: recent operator↔argus turns for replay/resume. Reply via the
 *  composer (nudge/note) — this pane is read-only history. */
export function TranscriptModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { data, isLoading } = useTranscript(sid, open);
  const turns = data ?? [];
  return (
    <Modal open={open} onClose={onClose} label="Transcript" width="max-w-2xl">
      <ModalHeader title="Transcript" sub="recent operator ↔ argus turns · reply from the composer" />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && turns.length === 0 && <EmptyHint>no conversation turns yet</EmptyHint>}
        {turns.map((t, i) => {
          const me = t.role === 'operator';
          return (
            <div key={i} className="grid grid-cols-[72px_minmax(0,1fr)] border-b border-line/50 py-2.5 last:border-b-0">
              <div>
                <div className={`font-mono text-[10px] font-semibold uppercase tracking-wide ${me ? 'text-ink-faint' : 'text-blue-sky'}`}>
                  {me ? 'operator' : 'argus'}
                </div>
                <div className="mt-0.5 text-[9px] text-ink-faint">{ago(t.ts)}</div>
              </div>
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink-dim">{t.text}</div>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
