import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { api, type ModelChoice, type ModelChoices, type ModelProbeResult } from '../api';
import { useConfig, useModelChoices } from '../hooks';
import { useI18n } from '../i18n';
import { agentRoleName } from '../lib/agentRoles';
import { backendLabel } from '../lib/backend';
import {
  CHOOSER_ROLES,
  EFFORTS,
  choiceWrites,
  formatLatency,
  formatRate,
  formatUsd,
  modelTiers,
  type ChooserRole,
  type Effort,
  type ModelTier,
} from '../lib/modelChoice';
import { requestFailureText } from '../lib/requestFailure';
import { Modal, ModalHeader } from './Modal';
import { EmptyHint, Spinner } from './primitives';
import './modelPicker.css';

type Translate = (key: string, variables?: Record<string, string | number>) => string;
type Probe = 'running' | ModelProbeResult | { ok: false; error: string };

const unset = (model: string | undefined) => !model || ['auto', 'inherit', 'default'].includes(model.toLowerCase());
const roleRecord = (value: (role: ChooserRole) => string) =>
  Object.fromEntries(CHOOSER_ROLES.map(role => [role, value(role)])) as Record<ChooserRole, string>;

function priceText(choice: ModelChoice, choices: ModelChoices, t: Translate): string {
  if (choices.billing === 'premium_request' && choices.premium_request_usd != null) {
    return t('models.pricePerRequest', { price: formatUsd(choices.premium_request_usd) });
  }
  if (!choice.price) return t('models.priceUnknown');
  return t('models.price', { input: formatRate(choice.price.input), output: formatRate(choice.price.output) });
}

function ModelCard({ choice, choices, tier, selected, current, roles, probe, onSelect, onProbe, t, locale }: {
  choice: ModelChoice;
  choices: ModelChoices;
  tier?: ModelTier;
  selected: boolean;
  current: boolean;
  roles: ChooserRole[];
  probe?: Probe;
  onSelect: () => void;
  onProbe: () => void;
  t: Translate;
  locale: 'en' | 'zh-CN';
}) {
  const availability = choice.availability;
  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    onSelect();
  };
  const probeDone = probe && probe !== 'running' ? probe : undefined;
  return (
    <div
      role="radio"
      aria-checked={selected}
      tabIndex={0}
      data-model-card={choice.model}
      className={`model-card${selected ? ' is-selected' : ''}`}
      onClick={onSelect}
      onKeyDown={keyDown}
    >
      <div className="model-card-head">
        <span className="model-card-name">{choice.model}</span>
        {tier && <span className={`model-tier model-tier--${tier}`} title={t('models.tierHint')} data-tier={tier}>{t(`models.tier.${tier}`)}</span>}
        {selected && <span className="model-card-check" aria-hidden="true">
          <svg viewBox="0 0 16 16"><path d="m3.5 8.5 3 3 6-7" /></svg>
        </span>}
      </div>
      <div className="model-card-meta">
        {backendLabel(choices.backend, t)}
        {current && <> · {t('models.current')}</>}
        {choice.default && <> · {t('models.default')}</>}
        {choice.invalid && <> · <span className="text-err">{t('models.notListed')}</span></>}
      </div>
      <dl className="model-card-facts">
        <div><dt>{t('models.priceLabel')}</dt><dd data-price>{priceText(choice, choices, t)}</dd></div>
        {choice.task_estimate_usd != null && (
          <div><dt>{t('models.taskLabel')}</dt><dd data-task-estimate>{t('models.taskEstimate', { price: formatUsd(choice.task_estimate_usd) })}</dd></div>
        )}
        <div><dt>{t('models.speedLabel')}</dt><dd data-speed>{choice.latency_ms != null
          ? t('models.speed', { time: formatLatency(choice.latency_ms, locale), count: choice.latency_samples })
          : t('models.speedUnknown')}</dd></div>
      </dl>
      <div className="model-card-foot">
        <span className={`model-status model-status--${availability.state}`} data-availability={availability.state} title={availability.reason || undefined}>
          <span className="model-status-dot" aria-hidden="true" />
          {availability.state === 'ok' ? t('models.available')
            : availability.state === 'failed' ? t('models.failed', { reason: availability.reason })
              : t('models.unknown')}
        </span>
        <button
          type="button"
          className="model-probe"
          data-probe={choice.model}
          disabled={probe === 'running'}
          title={t('models.testHint')}
          onClick={(event) => { event.stopPropagation(); onProbe(); }}
          onKeyDown={(event) => event.stopPropagation()}
        >
          {probe === 'running' ? t('models.testing') : t('models.test')}
        </button>
      </div>
      {probeDone && (
        <div className={`model-probe-result ${probeDone.ok ? 'text-ok' : 'text-err'}`} role="status" data-probe-result>
          {probeDone.ok && 'duration_ms' in probeDone
            ? t('models.testOk', { time: formatLatency(probeDone.duration_ms, locale) })
            : t('models.testFailed', { reason: probeDone.error || '—' })}
        </div>
      )}
      {roles.length > 0 && (
        <div className="model-card-roles">{roles.map(role => <span key={role}>{agentRoleName(role, t)}</span>)}</div>
      )}
    </div>
  );
}

/**
 * Compare the models the active backend can be given — price band, price,
 * a per-task estimate from this project's own usage, typical speed and
 * recent availability — and apply one through the regular settings writes.
 * Opening it reads recorded data only; "Test" is the one control that calls a model.
 */
export function ModelPicker({ sid, open, onClose, onOpenSettings }: {
  sid: string;
  open: boolean;
  onClose: () => void;
  onOpenSettings?: () => void;
}) {
  const { t, locale } = useI18n();
  const queryClient = useQueryClient();
  const config = useConfig(sid, open);
  const choicesQuery = useModelChoices(sid, open);
  const [mode, setMode] = useState<'all' | 'roles'>('all');
  const [shared, setShared] = useState('');
  const [perRole, setPerRole] = useState<Record<ChooserRole, string>>(() => roleRecord(() => ''));
  const [activeRole, setActiveRole] = useState<ChooserRole>('engineer');
  const [effort, setEffort] = useState<Effort | ''>('');
  const [probes, setProbes] = useState<Record<string, Probe>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  const roles = config.data?.roles ?? [];
  const current = useMemo(() => roleRecord(role => {
    const model = roles.find(row => row.role === role)?.model ?? '';
    return unset(model) ? '' : model;
  }), [roles]);
  const currentEffort = useMemo(() => roleRecord(role => roles.find(row => row.role === role)?.reasoning_effort ?? ''), [roles]);

  useEffect(() => {
    if (!open || !config.data) return;
    const models = CHOOSER_ROLES.map(role => current[role]);
    const same = models.every(model => model === models[0]);
    setMode(same ? 'all' : 'roles');
    setShared(same ? models[0] : current.engineer);
    setPerRole(current);
    const efforts = CHOOSER_ROLES.map(role => currentEffort[role]);
    setEffort(efforts.every(value => value === efforts[0]) && (EFFORTS as string[]).includes(efforts[0]) ? efforts[0] as Effort : '');
    // Only when the sheet opens or the saved settings change underneath it.
  }, [open, config.data]);
  useEffect(() => {
    if (!open) { setMessage(null); setProbes({}); }
  }, [open]);

  const choices = choicesQuery.data;
  const options = choices?.options ?? [];
  const tiers = useMemo(() => modelTiers(options), [options]);
  const writes = choiceWrites({ mode, shared, perRole, current, effort, currentEffort });
  const selectedModel = mode === 'all' ? shared : perRole[activeRole];

  const select = (model: string) => {
    setMessage(null);
    if (mode === 'all') setShared(model);
    else setPerRole(previous => ({ ...previous, [activeRole]: model }));
  };
  const probe = async (model: string) => {
    setProbes(previous => ({ ...previous, [model]: 'running' }));
    try {
      const result = await api.probeModel(sid, model);
      setProbes(previous => ({ ...previous, [model]: result }));
    } catch (error) {
      setProbes(previous => ({ ...previous, [model]: { ok: false, error: requestFailureText(error, t).text } }));
    }
    await queryClient.invalidateQueries({ queryKey: ['model-choices', sid] });
  };
  const apply = async () => {
    if (busy || !writes.length) return;
    setBusy(true);
    setMessage(null);
    try {
      for (const write of writes) await api.setConfig(sid, write.name, write.value, write.applyToRoles ?? false);
      setMessage({ text: t('models.saved'), error: false });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['config', sid] }),
        queryClient.invalidateQueries({ queryKey: ['model-choices', sid] }),
        queryClient.invalidateQueries({ queryKey: ['map-copy'] }),
      ]);
    } catch (error) {
      setMessage({ text: requestFailureText(error, t).text, error: true });
    } finally {
      setBusy(false);
    }
  };

  const effortName = (value: Effort) => t(`models.effort.${value}`);
  const summary = mode === 'all'
    ? [t('models.allRoles'), shared || t('models.none'), effort ? effortName(effort) : ''].filter(Boolean).join(' · ')
    : [...CHOOSER_ROLES.reduce((groups, role) => {
      const model = perRole[role] || t('models.none');
      groups.set(model, [...(groups.get(model) ?? []), agentRoleName(role, t)]);
      return groups;
    }, new Map<string, string[]>())]
      .map(([model, names]) => `${names.join(locale === 'zh-CN' ? '、' : ', ')}${locale === 'zh-CN' ? '：' : ': '}${model}`)
      .join(' · ') + (effort ? ` · ${effortName(effort)}` : '');
  const loading = config.isLoading || choicesQuery.isLoading;
  const failed = config.isError || choicesQuery.isError;

  return (
    <Modal open={open} onClose={onClose} label={t('models.title')} width="max-w-4xl" align="bottom">
      <ModalHeader title={t('models.title')} sub={t('models.subtitle')} />
      <div className="model-picker">
        {loading && <div className="flex justify-center py-10"><Spinner /></div>}
        {!loading && failed && <p role="alert" className="px-1 py-8 text-center text-sm text-err">{t('models.loadError')}</p>}
        {!loading && !failed && choices && <>
          <div className="model-picker-controls">
            <div className="route-seg" role="radiogroup" aria-label={t('models.modeLabel')}>
              <button type="button" role="radio" aria-checked={mode === 'all'} onClick={() => { setMode('all'); setMessage(null); }}>{t('models.modeAll')}</button>
              <button type="button" role="radio" aria-checked={mode === 'roles'} onClick={() => { setMode('roles'); setMessage(null); }}>{t('models.modeRoles')}</button>
            </div>
            <div className="model-picker-effort">
              <span>{t('models.effort')}</span>
              <div className="route-seg" role="radiogroup" aria-label={t('models.effort')}>
                <button type="button" role="radio" aria-checked={effort === ''} onClick={() => setEffort('')}>{t('models.effortKeep')}</button>
                {EFFORTS.map(value => (
                  <button key={value} type="button" role="radio" aria-checked={effort === value} data-effort={value} onClick={() => setEffort(value)}>{effortName(value)}</button>
                ))}
              </div>
            </div>
          </div>
          {mode === 'roles' && (
            <div className="model-picker-roles" role="tablist" aria-label={t('models.modeRoles')}>
              {CHOOSER_ROLES.map(role => (
                <button key={role} type="button" role="tab" aria-selected={activeRole === role} data-role-tab={role} onClick={() => setActiveRole(role)}>
                  <span>{agentRoleName(role, t)}</span>
                  <span className="model-picker-role-model">{perRole[role] || t('models.none')}</span>
                </button>
              ))}
            </div>
          )}
          {choices.task_sample > 0 && choices.billing === 'token' && (
            <p className="model-picker-note">{t('models.taskEstimateBasis', { count: choices.task_sample })}</p>
          )}
          {options.length === 0
            ? <EmptyHint>{t('models.empty')}</EmptyHint>
            : <div className="model-card-grid" role="radiogroup" aria-label={t('models.title')}>
              {options.map(choice => (
                <ModelCard
                  key={choice.model}
                  choice={choice}
                  choices={choices}
                  tier={tiers[choice.model]}
                  selected={selectedModel === choice.model}
                  current={CHOOSER_ROLES.some(role => current[role] === choice.model)}
                  roles={mode === 'roles' ? CHOOSER_ROLES.filter(role => perRole[role] === choice.model) : []}
                  probe={probes[choice.model]}
                  onSelect={() => select(choice.model)}
                  onProbe={() => void probe(choice.model)}
                  t={t}
                  locale={locale}
                />
              ))}
            </div>}
          <p className="model-picker-note">
            {t('models.persistNote')}
            {onOpenSettings && <> <button type="button" className="model-picker-link" onClick={() => { onClose(); onOpenSettings(); }}>{t('models.moreSettings')}</button></>}
          </p>
          <div className="model-picker-bar" data-confirm-bar>
            <div className="model-picker-bar-text">
              <span className="model-picker-bar-label">{t('models.selected')}</span>
              <span className="model-picker-bar-choice" data-choice-summary>{summary}</span>
              {message
                ? <span role={message.error ? 'alert' : 'status'} className={message.error ? 'text-err' : 'text-ink-dim'} data-apply-message>{message.text}</span>
                : writes.length === 0 && <span className="text-ink-faint">{t('models.nothingChanged')}</span>}
            </div>
            <button type="button" className="model-picker-use" data-use-model disabled={busy || writes.length === 0} onClick={() => void apply()}>
              {busy ? t('models.saving') : mode === 'all' ? t('models.use') : t('models.useRoles')}
            </button>
          </div>
        </>}
      </div>
    </Modal>
  );
}

/**
 * The visible way into the chooser beside the composer: names the model the
 * roles are set to (or how many differ) and opens the sheet.
 */
export function ModelPickerButton({ sid, onOpenSettings }: { sid: string; onOpenSettings?: () => void }) {
  const { t } = useI18n();
  const { data } = useConfig(sid, true);
  const [open, setOpen] = useState(false);
  if (!data) return null;
  const models = [...new Set(data.roles
    .filter(row => (CHOOSER_ROLES as string[]).includes(row.role))
    .map(row => (unset(row.model) ? t('models.none') : row.model)))];
  const label = models.length > 1 ? t('models.mixed', { count: models.length }) : models[0] ?? t('models.none');
  return <>
    <button type="button" className="composer-model-chip" data-open-model-picker title={t('models.title')} onClick={() => setOpen(true)}>
      {t('models.chip', { model: label })}
    </button>
    <ModelPicker sid={sid} open={open} onClose={() => setOpen(false)} onOpenSettings={onOpenSettings} />
  </>;
}
