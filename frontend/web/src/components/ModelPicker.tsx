import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useState, type KeyboardEvent } from 'react';
import { api, type ModelChoice, type ModelChoices, type ModelProbeResult } from '../api';
import { useConfig, useModelChoices } from '../hooks';
import { useI18n } from '../i18n';
import { backendLabel } from '../lib/backend';
import {
  EFFORTS,
  choiceWrites,
  formatLatency,
  formatRate,
  formatUsd,
  modelTiers,
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

function priceText(choice: ModelChoice, choices: ModelChoices, t: Translate): string {
  if (choices.billing === 'premium_request' && choices.premium_request_usd != null) {
    return t('models.pricePerRequest', { price: formatUsd(choices.premium_request_usd) });
  }
  if (!choice.price) return t('models.priceUnknown');
  return t('models.price', { input: formatRate(choice.price.input), output: formatRate(choice.price.output) });
}

function ModelCard({ choice, choices, backend, tier, selected, current, probe, onSelect, onProbe, t, locale }: {
  choice: ModelChoice;
  choices: ModelChoices;
  backend: string;
  tier?: ModelTier;
  selected: boolean;
  current: boolean;
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
        {backend}
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
  const [model, setModel] = useState('');
  const [effort, setEffort] = useState<Effort | ''>('');
  const [probes, setProbes] = useState<Record<string, Probe>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);

  // A project has one model and effort, which every role runs on.
  const settings = config.data?.roles[0];
  const current = { model: unset(settings?.model) ? '' : settings!.model, effort: settings?.reasoning_effort ?? '' };

  useEffect(() => {
    if (!open || !config.data) return;
    setModel(current.model);
    setEffort((EFFORTS as string[]).includes(current.effort) ? current.effort as Effort : '');
    // Only when the sheet opens or the saved settings change underneath it.
  }, [open, config.data]);
  useEffect(() => {
    if (!open) { setMessage(null); setProbes({}); }
  }, [open]);

  const choices = choicesQuery.data;
  const options = choices?.options ?? [];
  const tiers = useMemo(() => modelTiers(options), [options]);
  const writes = choiceWrites({ model, effort }, current);

  const select = (chosen: string) => {
    setMessage(null);
    setModel(chosen);
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
      for (const write of writes) await api.setConfig(sid, write.name, write.value);
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
  const summary = [model || t('models.none'), effort ? effortName(effort) : ''].filter(Boolean).join(' · ');
  const trial = config.data?.trial_mode === true;
  const loading = config.isLoading || choicesQuery.isLoading;
  const failed = config.isError || choicesQuery.isError;

  return (
    <Modal open={open} onClose={onClose} label={t('models.title')} width="max-w-4xl" align="bottom">
      <ModalHeader title={t('models.title')} sub={t('models.subtitle')} />
      <div className="model-picker">
        {loading && <div className="flex justify-center py-10"><Spinner /></div>}
        {!loading && failed && <p role="alert" className="px-1 py-8 text-center text-sm text-err">{t('models.loadError')}</p>}
        {!loading && !failed && trial && <EmptyHint>{t('models.trial')}</EmptyHint>}
        {!loading && !failed && !trial && choices && <>
          <div className="model-picker-controls">
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
                  backend={backendLabel(choices.backend, config.data)}
                  tier={tiers[choice.model]}
                  selected={model === choice.model}
                  current={current.model === choice.model}
                  probe={probes[choice.model]}
                  onSelect={() => select(choice.model)}
                  onProbe={() => void probe(choice.model)}
                  t={t}
                  locale={locale}
                />
              ))}
            </div>}
          {/* A model the list does not have, as the backend names it. */}
          <label className="model-picker-by-name">
            <span>{t('models.byName')}</span>
            <input value={options.some(choice => choice.model === model) ? '' : model} spellCheck={false} data-model-name
              placeholder={t('models.byNamePlaceholder')} onChange={event => select(event.target.value.trim())} />
          </label>
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
              {busy ? t('models.saving') : t('models.use')}
            </button>
          </div>
        </>}
      </div>
    </Modal>
  );
}
