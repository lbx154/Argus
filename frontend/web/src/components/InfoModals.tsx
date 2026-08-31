import { useDoctor, useConfig, useIdentity, useTranscript } from '../hooks';
import { Modal, ModalHeader } from './Modal';
import { Spinner, EmptyHint } from './primitives';
import { effortColor } from '../lib/theme';
import { ago } from '../lib/format';
import { useEffect, useState, type FormEvent } from 'react';
import { api } from '../api';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { faCheck, faChevronDown, faFloppyDisk } from '@fortawesome/free-solid-svg-icons';
import {
  conciseConfigKnobs,
  connectionTopology,
  type DisplayConfigKnob,
} from '../lib/configSurface';
import { roleLabel } from '../lib/enumLabels';
import { useI18n } from '../i18n';
import {
  BACKEND_OPTIONS,
  backendLabel,
  backendOption,
  configuredBackend,
  type BackendOption,
} from '../lib/backend';

const BUDGET_FIELDS = [
  { alias: 'global_daily_cap', env: 'ARGUS_SKILL_GLOBAL_DAILY_CAP_USD', label: 'settings.budget.global', unit: 'settings.unit.usd', step: '0.1' },
  { alias: 'codex_daily_requests', env: 'ARGUS_SKILL_CODEX_DAILY_CALL_CAP', label: 'settings.budget.codex', unit: 'settings.unit.calls', step: '1' },
  { alias: 'copilot_daily_requests', env: 'ARGUS_SKILL_COPILOT_DAILY_CALL_CAP', label: 'settings.budget.copilot', unit: 'settings.unit.calls', step: '1' },
  { alias: 'copilot_daily_premium', env: 'ARGUS_SKILL_COPILOT_DAILY_PREMIUM_CAP', label: 'settings.budget.premium', unit: 'settings.unit.requests', step: '1' },
] as const;

const KNOB_TEXT: Record<string, { label: string; doc: string }> = {
  ARGUS_SKILL_MAX_ACTIVE_DAEMONS: { label: 'settings.knob.activeDaemons', doc: 'settings.knob.activeDaemonsDoc' },
  ARGUS_SKILL_UNPRICED_COST_POLICY: { label: 'settings.knob.unpricedCalls', doc: 'settings.knob.unpricedCallsDoc' },
  ARGUS_SKILL_SAFE_MODE: { label: 'settings.knob.safeMode', doc: 'settings.knob.safeModeDoc' },
  ARGUS_SKILL_ENABLE_TELEGRAM: { label: 'settings.knob.telegram', doc: 'settings.knob.telegramDoc' },
  ARGUS_SKILL_SHOW_REASONING: { label: 'settings.knob.showReasoning', doc: 'settings.knob.showReasoningDoc' },
};

const GROUP_TEXT: Record<string, string> = {
  Limits: 'settings.group.limits',
  Safety: 'settings.group.safety',
  Interface: 'settings.group.interface',
};

const ROLE_DESCRIPTION_TEXT: Record<string, string> = {
  manager: 'settings.role.managerDoc',
  planner: 'settings.role.plannerDoc',
  engineer: 'settings.role.engineerDoc',
  reviewer: 'settings.role.reviewerDoc',
  curator: 'settings.role.curatorDoc',
};

type Translate = (key: string, variables?: Record<string, string | number>) => string;

function configSourceLabel(source: string, t: Translate): string {
  const value = source.trim();
  if (value === 'not applicable for this model') return t('settings.source.notApplicable');
  if (value.startsWith('capability vault')) return t('settings.source.vaultDefault');
  if (value.startsWith('default')) return t('settings.source.default');
  if (value.startsWith('persisted:') || value === 'persisted') return t('settings.source.saved');
  if (value.startsWith('ARGUS_SKILL_') || value === 'env') return t('settings.source.environment');
  if (value.startsWith('global:')) return t('settings.source.hostConfig');
  return t('settings.source.other');
}

function configValueLabel(knob: DisplayConfigKnob, t: Translate): string {
  const value = knob.value.trim().toLowerCase();
  if (knob.name === 'ARGUS_SKILL_UNPRICED_COST_POLICY') {
    if (value === 'block') return t('settings.value.block');
    if (value === 'allow') return t('settings.value.allow');
  }
  if (['ARGUS_SKILL_SAFE_MODE', 'ARGUS_SKILL_ENABLE_TELEGRAM', 'ARGUS_SKILL_SHOW_REASONING'].includes(knob.name)) {
    return t(['1', 'true', 'on', 'yes'].includes(value) ? 'settings.value.enabled' : 'settings.value.disabled');
  }
  return knob.value;
}

function effortLabel(effort: string, t: Translate): string {
  const key = ({ low: 'low', medium: 'medium', high: 'high', xhigh: 'xhigh' } as Record<string, string>)[effort.toLowerCase()];
  return key ? t(`settings.effort.${key}`) : effort;
}

function LoadFailure({ message, retrying, onRetry, t }: {
  message: string;
  retrying: boolean;
  onRetry: () => void;
  t: Translate;
}) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 px-4 py-8 text-center">
      <p className="text-sm text-err">{message}</p>
      <button type="button" onClick={onRetry} disabled={retrying} className="rounded-md border border-err/40 px-3 py-1.5 text-xs font-medium text-err hover:bg-err/10 disabled:opacity-40">
        {retrying ? t('common.loading') : t('common.retry')}
      </button>
    </div>
  );
}

/** Doctor: health checks with the single recommended fix pinned + gold, plus the
 *  daemon.log tail. Read-only diagnostics. */
export function DoctorModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const { data, isLoading, isError, isFetching, refetch } = useDoctor(sid, open);
  const hasData = Boolean(data && (data.recommended || data.checks.length || data.log_tail.trim()));
  return (
    <Modal open={open} onClose={onClose} label={t('doctor.title')} width="max-w-3xl">
      <ModalHeader title={t('doctor.title')} sub={t('doctor.subtitle')} />
      <div className="p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && isError && <LoadFailure message={t('doctor.loadError')} retrying={isFetching} onRetry={() => void refetch()} t={t} />}
        {!isLoading && !isError && !hasData && <EmptyHint>{t('doctor.empty')}</EmptyHint>}
        {!isLoading && !isError && data?.recommended && (
          <div className="mb-4 rounded-lg border border-gold/40 bg-gold/5 p-3">
            <div className="text-[10px] font-semibold uppercase tracking-wide text-gold">{t('doctor.recommended')}</div>
            <div className="mt-1 text-sm text-ink">{data.recommended.name}</div>
            <div className="mt-0.5 text-xs text-ink-dim">{data.recommended.detail}</div>
            {data.recommended.fix && (
              <pre className="mt-2 whitespace-pre-wrap break-words rounded bg-bg p-2 font-mono text-xs text-blue-sky">{data.recommended.fix}</pre>
            )}
          </div>
        )}
        {!isLoading && !isError && <div className="space-y-1.5">
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
        </div>}
        {!isLoading && !isError && data?.log_tail && (
          <div className="mt-4">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t('doctor.daemonLog')}</div>
            <pre className="max-h-48 overflow-x-hidden overflow-y-auto whitespace-pre-wrap break-words rounded-lg bg-bg p-3 font-mono text-xs leading-relaxed text-ink-dim scroll-thin">{data.log_tail}</pre>
          </div>
        )}
      </div>
    </Modal>
  );
}

export function ConfigModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
  const { data, isLoading, isError, isFetching, refetch } = useConfig(sid, open);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [quickModelValue, setQuickModelValue] = useState('');
  const [quickConfigBusy, setQuickConfigBusy] = useState(false);
  const [quickConfigMsg, setQuickConfigMsg] = useState('');
  const [name, setName] = useState('');
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState('');
  const [budgetBusy, setBudgetBusy] = useState(false);
  const [budgetResult, setBudgetResult] = useState('');
  const [budgets, setBudgets] = useState<Record<string, string>>({});
  useEffect(() => {
    if (!open) setAdvancedOpen(false);
  }, [open]);
  useEffect(() => {
    if (!open || !data) return;
    setQuickModelValue(data.operator_knobs.find((knob) => knob.name === 'ARGUS_SKILL_MODEL')?.value ?? '');
    const byName = new Map(data.operator_knobs.map((knob) => [knob.name, knob.value]));
    setBudgets(Object.fromEntries(
      BUDGET_FIELDS.map((field) => [field.alias, byName.get(field.env) ?? '']),
    ));
  }, [data, open]);
  const currentBackend = configuredBackend(data);
  const setBackend = async (backend: BackendOption) => {
    if (quickConfigBusy) return;
    setQuickConfigBusy(true);
    setQuickConfigMsg('');
    try {
      await api.setConfig(sid, 'ARGUS_SKILL_RUNNER_BACKEND', backend);
      await refetch();
      setQuickConfigMsg(t('settings.backendSwitched', { backend: backendLabel(backend, t) }));
    } catch (error) {
      setQuickConfigMsg(error instanceof Error ? error.message : String(error));
    } finally {
      setQuickConfigBusy(false);
    }
  };
  const applyModel = async () => {
    if (quickConfigBusy) return;
    setQuickConfigBusy(true);
    setQuickConfigMsg('');
    try {
      await api.setConfig(sid, 'ARGUS_SKILL_MODEL', quickModelValue.trim() || 'auto');
      await refetch();
      setQuickConfigMsg(t('settings.applied'));
    } catch (error) {
      setQuickConfigMsg(error instanceof Error ? error.message : String(error));
    } finally {
      setQuickConfigBusy(false);
    }
  };
  const saveBudgets = async () => {
    if (budgetBusy) return;
    setBudgetBusy(true);
    setBudgetResult('');
    try {
      const values = Object.fromEntries(BUDGET_FIELDS.map((field) => {
        const value = String(budgets[field.alias] ?? '').trim();
        if (!value) throw new Error(t('settings.required', { field: t(field.label) }));
        return [field.alias, value];
      }));
      await api.setBudgets(sid, values);
      await refetch();
      setBudgetResult(t('settings.budgetSaved'));
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
      setResult(t('settings.applied'));
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
  const hasData = Boolean(data && (data.roles.length || data.operator_knobs.length));
  return (
    <Modal open={open} onClose={onClose} label={t('common.settings')} width="max-w-4xl">
      <ModalHeader title={t('common.settings')} sub={t('settings.subtitle')} />
      <div className="p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && isError && <LoadFailure message={t('settings.loadError')} retrying={isFetching} onRetry={() => void refetch()} t={t} />}
        {!isLoading && !isError && !hasData && <EmptyHint>{t('settings.empty')}</EmptyHint>}
        {!isLoading && !isError && hasData && data && (
          <div className="space-y-4">
            <section className="rounded-lg border border-line glass-card p-3">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t('settings.quickConfig')}</div>
              <label className="flex flex-wrap items-center gap-2">
                <span className="w-12 shrink-0 text-[10px] text-ink-faint">{t('settings.backend')}</span>
                <select
                  value={backendOption(currentBackend)}
                  disabled={quickConfigBusy}
                  onChange={(event) => void setBackend(event.target.value as BackendOption)}
                  className="h-8 min-w-44 rounded border border-line bg-bg px-2 text-xs text-ink outline-none focus:border-blue disabled:opacity-40"
                >
                  {BACKEND_OPTIONS.map((backend) => (
                    <option key={backend.value} value={backend.value}>{t(backend.label)}</option>
                  ))}
                </select>
              </label>
              <div className="mt-2 flex items-center gap-2">
                <span className="w-12 shrink-0 text-[10px] text-ink-faint">{t('settings.model')}</span>
                <input
                  value={quickModelValue}
                  onChange={(event) => setQuickModelValue(event.target.value)}
                  placeholder={t('settings.modelPlaceholder')}
                  className="h-8 min-w-0 flex-1 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue"
                />
                <button type="button" onClick={() => void applyModel()} disabled={quickConfigBusy} className="h-8 shrink-0 rounded border border-line/70 px-2.5 text-xs font-medium text-ink-dim hover:border-blue/50 disabled:opacity-40">
                  {t('settings.applyModel')}
                </button>
              </div>
              {quickConfigMsg && <div className="mt-1.5 text-[10px] text-ink-dim">{quickConfigMsg}</div>}
            </section>

            <section className="rounded-lg border border-gold/40 bg-gold/5 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <div className="text-[10px] font-semibold uppercase tracking-wide text-gold">{t('settings.budgetTitle')}</div>
                  <p className="mt-0.5 text-[10px] text-ink-faint">{t('settings.budgetHint')}</p>
                </div>
                <button type="button" onClick={() => void saveBudgets()} disabled={budgetBusy} title={t('settings.saveBudgets')} aria-label={t('settings.saveBudgets')} className="flex h-9 w-9 items-center justify-center rounded bg-gold text-xs font-semibold text-bg disabled:opacity-40">{budgetBusy ? '…' : <FontAwesomeIcon icon={faFloppyDisk} />}</button>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {BUDGET_FIELDS.map((field) => (
                  <label key={field.alias} className="rounded border border-line/70 bg-bg/60 p-2">
                    <span className="block text-[10px] text-ink-faint">{t(field.label)}</span>
                    <div className="mt-1 flex items-center gap-2">
                      <input type="number" min="0" step={field.step} value={budgets[field.alias] ?? ''} onChange={(event) => setBudgets((current) => ({ ...current, [field.alias]: event.target.value }))} className="h-8 min-w-0 flex-1 bg-transparent font-mono text-sm text-ink outline-none" />
                      <span className="text-[9px] text-ink-faint">{t(field.unit)}</span>
                    </div>
                  </label>
                ))}
              </div>
              {budgetResult ? <div className="mt-2 text-xs text-ink-dim">{budgetResult}</div> : null}
            </section>

            <section className="overflow-hidden rounded-lg border border-line bg-surface/50">
              <button
                type="button"
                aria-expanded={advancedOpen}
                aria-controls="config-advanced-settings"
                onClick={() => setAdvancedOpen((current) => !current)}
                className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left hover:bg-bg/30"
              >
                <span>
                  <span className="block text-xs font-semibold text-ink">{t('settings.advanced')}</span>
                  <span className="mt-0.5 block text-[10px] text-ink-faint">{t('settings.advancedHint')}</span>
                </span>
                <FontAwesomeIcon icon={faChevronDown} className={`text-xs text-ink-faint transition-transform ${advancedOpen ? 'rotate-180' : ''}`} />
              </button>

              {advancedOpen && (
                <div id="config-advanced-settings" className="space-y-4 border-t border-line/70 p-3">
                  <section className="rounded-lg border border-line bg-surface p-3">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-faint">{t('settings.connection')}</div>
                    <div className="mt-2 grid gap-2 text-[10px] sm:grid-cols-[100px_minmax(0,1fr)]">
                      <span className="text-ink-faint">{t('settings.webApi')}</span>
                      <code className="min-w-0 break-all text-ink-dim">{connection.webApi}</code>
                      <span className="text-ink-faint">{t('settings.eventStream')}</span>
                      <code className="min-w-0 break-all text-ink-dim">{connection.eventStream}</code>
                      <span className="text-ink-faint">{t('settings.taskDaemon')}</span>
                      <span className="text-ink-dim">{t('settings.taskDaemonValue')}</span>
                    </div>
                  </section>

                  <form onSubmit={(event) => void submit(event)} className="rounded-lg border border-blue/30 bg-blue/5 p-3">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-blue">{t('settings.overrideTitle')}</div>
                    <p className="mt-0.5 text-[10px] text-ink-faint">{t('settings.overrideHint')}</p>
                    <div className="mt-2 grid gap-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                      <input value={name} onChange={(event) => setName(event.target.value)} placeholder={t('settings.namePlaceholder')} className="h-9 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" />
                      <input value={value} onChange={(event) => setValue(event.target.value)} placeholder={t('settings.valuePlaceholder')} className="h-9 rounded border border-line bg-bg px-2 font-mono text-xs text-ink outline-none focus:border-blue" />
                      <button disabled={busy || !name.trim() || !value.trim()} title={t('settings.applyAdvanced')} aria-label={t('settings.applyAdvanced')} className="flex h-9 w-9 items-center justify-center rounded bg-blue-deep text-xs font-medium text-white disabled:opacity-40">{busy ? '…' : <FontAwesomeIcon icon={faCheck} />}</button>
                    </div>
                    {result ? <div className="mt-2 text-xs text-ink-dim">{result}</div> : null}
                  </form>

                  {data.roles.length > 0 && (
                    <section>
                      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{t('settings.rolesTitle')}</div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {data.roles.map((role) => (
                          <div key={role.role} className="rounded-lg border border-line bg-surface p-3">
                            <div className="flex items-center justify-between gap-2">
                              <div className="text-xs font-semibold text-ink">{role.role === 'curator' ? t('settings.role.curator') : roleLabel(role.role, t)}</div>
                              <span className="text-[10px] text-ink-faint">{role.backend_label}</span>
                            </div>
                            <div className="mt-2 truncate font-mono text-[11px] text-ink-dim" title={role.model}>{role.model}</div>
                            <div className="mt-1 flex items-center gap-2 text-[10px] text-ink-faint">
                              <span className="truncate">{configSourceLabel(role.model_source, t)}</span>
                              {role.reasoning_effort && (
                                <span className="ml-auto shrink-0" style={{ color: effortColor(role.reasoning_effort) }}>
                                  {effortLabel(role.reasoning_effort, t)}
                                </span>
                              )}
                            </div>
                            {ROLE_DESCRIPTION_TEXT[role.role] && <p className="mt-2 text-[10px] leading-relaxed text-ink-faint">{t(ROLE_DESCRIPTION_TEXT[role.role])}</p>}
                          </div>
                        ))}
                      </div>
                    </section>
                  )}

                  {Object.keys(knobGroups).length > 0 && (
                    <section>
                      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{t('settings.rawConfig')}</div>
                      {Object.entries(knobGroups).map(([group, knobs]) => (
                        <div key={group} className="mt-3 first:mt-0">
                          <div className="mb-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-ink-faint">{t(GROUP_TEXT[group])}</div>
                          <div className="overflow-hidden rounded-lg border border-line">
                            {knobs.map((knob, index) => {
                              const copy = KNOB_TEXT[knob.name];
                              const displayValue = configValueLabel(knob, t);
                              return (
                                <div key={knob.name} className={`grid gap-1 px-3 py-2.5 sm:grid-cols-[minmax(0,1fr)_auto] ${index ? 'border-t border-line/60' : ''}`}>
                                  <div className="min-w-0">
                                    <div className="text-xs font-medium text-ink-dim">{t(copy.label)}</div>
                                    <code className="mt-0.5 block break-all text-[9px] text-ink-faint">{knob.name}</code>
                                    <div className="mt-1 text-[10px] leading-relaxed text-ink-faint">{t(copy.doc)}</div>
                                  </div>
                                  <div className="text-left sm:text-right">
                                    <div className="text-[11px] text-ink">
                                      {displayValue}
                                      {displayValue !== knob.value && <code className="ml-1 text-[9px] text-ink-faint">({knob.value})</code>}
                                    </div>
                                    <div className="mt-0.5 text-[9px] text-ink-faint">{configSourceLabel(knob.source, t)}</div>
                                  </div>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      ))}
                    </section>
                  )}

                  <p className="text-[10px] text-ink-faint">
                    {t('settings.footer')} <code>argus-skill --config-help</code>.
                  </p>
                </div>
              )}
            </section>
          </div>
        )}
      </div>
    </Modal>
  );
}

/** Identity: the operator identity text on a wordmarked panel. */
export function IdentityModal({ sid, open, onClose }: { sid: string; open: boolean; onClose: () => void }) {
  const { t } = useI18n();
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
      setResult(t('identity.saved'));
    } catch (error) {
      setResult(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal open={open} onClose={onClose} label={t('identity.title')} width="max-w-2xl">
      <ModalHeader title={t('identity.title')} sub={t('identity.subtitle')} />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-5">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading ? (
          <>
            <textarea value={draft} onChange={(event) => setDraft(event.target.value)} rows={12} className="w-full resize-y rounded-lg border border-line bg-bg p-3 font-sans text-sm leading-relaxed text-ink outline-none focus:border-blue" placeholder={t('identity.placeholder')} />
            <div className="mt-3 flex items-center justify-between">
              <span className="text-xs text-ink-faint">{result}</span>
              <button type="button" onClick={() => void save()} disabled={busy || draft === (data ?? '')} title={t('identity.save')} aria-label={t('identity.save')} className="flex h-9 w-9 items-center justify-center rounded bg-blue-deep text-xs font-medium text-white disabled:opacity-40">{busy ? '…' : <FontAwesomeIcon icon={faFloppyDisk} />}</button>
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
  const { t } = useI18n();
  const { data, isLoading } = useTranscript(sid, open);
  const turns = data ?? [];
  return (
    <Modal open={open} onClose={onClose} label={t('transcript.title')} width="max-w-2xl">
      <ModalHeader title={t('transcript.title')} sub={t('transcript.subtitle')} />
      <div className="max-h-[64vh] overflow-y-auto scroll-thin p-4">
        {isLoading && <div className="flex justify-center py-8"><Spinner /></div>}
        {!isLoading && turns.length === 0 && <EmptyHint>{t('transcript.empty')}</EmptyHint>}
        {turns.map((turn, i) => {
          const me = turn.role === 'operator';
          return (
            <div key={i} className="grid grid-cols-[72px_minmax(0,1fr)] border-b border-line/50 py-2.5 last:border-b-0">
              <div>
                <div className={`font-mono text-[10px] font-semibold uppercase tracking-wide ${me ? 'text-ink-faint' : 'text-blue-sky'}`}>
                  {me ? t('transcript.operator') : 'argus'}
                </div>
                <div className="mt-0.5 text-[9px] text-ink-faint">{ago(turn.ts)}</div>
              </div>
              <div className="whitespace-pre-wrap text-sm leading-relaxed text-ink-dim">{turn.text}</div>
            </div>
          );
        })}
      </div>
    </Modal>
  );
}
