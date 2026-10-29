import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import { api, type ModelProbeResult } from '../api';
import { useConfig, useModelChoices } from '../hooks';
import { useI18n } from '../i18n';
import { backendLabel } from '../lib/backend';
import { EFFORTS, automaticModel, modelChoiceLabel, choiceWrites, formatLatency, formatRate, type Effort } from '../lib/modelChoice';
import { requestFailureText } from '../lib/requestFailure';
import { Modal, ModalHeader } from './Modal';
import { Spinner } from './primitives';
import { ErrorMessage } from './ErrorMessage';
import { rawErrorText } from '../lib/format';
import './modelPicker.css';

type Probe = 'running' | ModelProbeResult | { ok: false; error: string };

/** One row of the list: a radio with the model's name, what is on record of it, and an action. */
function ModelRow({ name, model, meta, selected, current, onSelect, action, children }: {
  name: string;
  model: string;
  meta?: string;
  selected: boolean;
  current: boolean;
  onSelect: () => void;
  action?: ReactNode;
  children?: ReactNode;
}) {
  const { t } = useI18n();
  const keyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget || (event.key !== 'Enter' && event.key !== ' ')) return;
    event.preventDefault();
    onSelect();
  };
  return (
    <div role="radio" aria-checked={selected} tabIndex={0} data-model-row={model}
      className={`model-row${selected ? ' is-selected' : ''}`} onClick={onSelect} onKeyDown={keyDown}>
      <span className="model-row-dot" aria-hidden="true" />
      <div className="model-row-text">
        <span className={`model-row-name${model ? '' : ' is-auto'}`}>{name}</span>
        {current && <span className="model-row-tag">{t('models.current')}</span>}
        {meta && <span className="model-row-meta" data-meta>{meta}</span>}
        {children}
      </div>
      {action}
    </div>
  );
}

/**
 * The models the project's backend offers, as one compact list headed by
 * Automatic, the backend's own default, each with the price the price table
 * has for it; the one chosen is applied through the regular settings writes.
 * Opening it calls no model: "Test" is the one control that does.
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
  const [failure, setFailure] = useState('');

  // A project has one model and effort, which every role runs on.
  const settings = config.data?.roles[0];
  const current = { model: automaticModel(settings?.model) ? '' : settings!.model, effort: settings?.reasoning_effort ?? '' };

  useEffect(() => {
    if (!open || !config.data) return;
    setModel(current.model);
    setEffort((EFFORTS as string[]).includes(current.effort) ? current.effort as Effort : '');
    // Only when the sheet opens or the saved settings change underneath it.
  }, [open, config.data]);
  useEffect(() => {
    if (!open) { setFailure(''); setProbes({}); }
  }, [open]);

  const choices = choicesQuery.data;
  const options = choices?.options ?? [];
  const writes = choiceWrites({ model, effort }, current);

  const select = (chosen: string) => {
    setFailure('');
    setModel(chosen);
  };
  const probe = async (model: string) => {
    setProbes(previous => ({ ...previous, [model]: 'running' }));
    try {
      const result = await api.probeModel(sid, model);
      setProbes(previous => ({ ...previous, [model]: result }));
    } catch (error) {
      setProbes(previous => ({ ...previous, [model]: { ok: false, error: rawErrorText(error) } }));
    }
  };
  const apply = async () => {
    if (busy || !writes.length) return;
    setBusy(true);
    setFailure('');
    try {
      for (const write of writes) await api.setConfig(sid, write.name, write.value);
      // Chosen, the sheet closes; the chip under the message box names the model now.
      onClose();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['config', sid] }),
        queryClient.invalidateQueries({ queryKey: ['model-choices', sid] }),
      ]);
    } catch (error) {
      setFailure(requestFailureText(error, t).text);
    } finally {
      setBusy(false);
    }
  };

  const effortName = (value: Effort) => t(`models.effort.${value}`);
  const defaultModel = options.find(choice => choice.default)?.model;
  const automaticLabel = modelChoiceLabel('', defaultModel, locale);
  const summary = [modelChoiceLabel(model, defaultModel, locale), effort ? effortName(effort) : ''].filter(Boolean).join(' · ');
  const loading = config.isLoading || choicesQuery.isLoading;
  const failed = config.isError || choicesQuery.isError;

  return (
    <Modal open={open} onClose={onClose} label={t('models.title')} width="max-w-2xl" align="bottom">
      <ModalHeader title={t('models.title')} sub={t('models.subtitle')} />
      <div className="model-picker">
        {loading && <div className="flex justify-center py-10"><Spinner /></div>}
        {!loading && failed && <p role="alert" className="px-1 py-8 text-center text-sm text-err">{t('models.loadError')}</p>}
        {!loading && !failed && choices && <>
          <div className="model-picker-effort">
            <span>{t('models.effort')}</span>
            <div className="route-seg" role="radiogroup" aria-label={t('models.effort')}>
              <button type="button" role="radio" aria-checked={effort === ''} onClick={() => setEffort('')}>{t('models.effortKeep')}</button>
              {EFFORTS.map(value => (
                <button key={value} type="button" role="radio" aria-checked={effort === value} data-effort={value} onClick={() => setEffort(value)}>{effortName(value)}</button>
              ))}
            </div>
          </div>
          <div className="model-list" role="radiogroup" aria-label={t('models.title')}>
            <ModelRow name={automaticLabel} model="" selected={model === ''} current={current.model === ''} onSelect={() => select('')}
              meta={t('models.autoHint', { backend: backendLabel(choices.backend, config.data) })} />
            {options.map(choice => {
              const result = probes[choice.model];
              return (
                <ModelRow key={choice.model} name={choice.model} model={choice.model} selected={model === choice.model}
                  current={current.model === choice.model} onSelect={() => select(choice.model)}
                  meta={choice.price ? t('models.price', { input: formatRate(choice.price.input), output: formatRate(choice.price.output) }) : undefined}
                  action={<button type="button" className="model-probe" data-probe={choice.model} disabled={result === 'running'} title={t('models.testHint')}
                    onClick={(event) => { event.stopPropagation(); void probe(choice.model); }} onKeyDown={(event) => event.stopPropagation()}>
                    {result === 'running' ? t('models.testing') : t('models.test')}
                  </button>}>
                  {result && result !== 'running' && (
                    <div className={`model-probe-result ${result.ok ? 'text-ok' : 'text-err'}`} role="status" data-probe-result onClick={event => event.stopPropagation()}>
                      {result.ok && 'duration_ms' in result
                        ? t('models.testOk', { time: formatLatency(result.duration_ms, locale) })
                        : <ErrorMessage error={result.error} code="model_probe_failed" />}
                    </div>
                  )}
                </ModelRow>
              );
            })}
          </div>
          {/* A model the list does not have, as the backend names it. */}
          <label className="model-picker-by-name">
            <span>{t('models.byName')}</span>
            <input value={options.some(choice => choice.model === model) ? '' : model} spellCheck={false} data-model-name
              placeholder={t('models.byNamePlaceholder')} onChange={event => select(event.target.value.trim())} />
          </label>
          {onOpenSettings && <button type="button" className="model-picker-link" onClick={() => { onClose(); onOpenSettings(); }}>{t('models.moreSettings')}</button>}
          <div className="model-picker-bar" data-confirm-bar>
            <div className="model-picker-bar-text">
              <span className="model-picker-bar-label">{t('models.selected')}</span>
              <span className="model-picker-bar-choice" data-choice-summary>{summary}</span>
              {failure
                ? <span role="alert" className="text-err" data-apply-message>{failure}</span>
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
