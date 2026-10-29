import { useState } from 'react';
import type { Role, Snapshot } from '../api';
import { useConfig } from '../hooks';
import { useI18n } from '../i18n';
import { backendLabel } from '../lib/backend';
import { agentRoleName, isAgentRole } from '../lib/agentRoles';
import { canOpenDesktopSettings, openDesktopTrialSettings } from '../lib/desktopBridge';
import { ModelPicker } from './ModelPicker';

type LastCall = NonNullable<Snapshot['last_call']>;

// A call older than this, with nothing running, no longer describes the
// project: the next call will follow the current settings instead.
const LAST_CALL_CURRENT_S = 6 * 3600;

const unset = (value: string | null | undefined) => !value || ['auto', 'inherit', 'default'].includes(value.toLowerCase());

/**
 * What the work runs on, as one quiet chip under the message box: the newest
 * real call's model and effort, a dot that lights while the team works, and,
 * when settings now ask for something else, what the next call will use.
 * Clicking opens the model chooser, which links on to the full settings.
 */
export function ComposerRuntime({ sid, roles, running, lastCall, onOpenSettings }: {
  sid: string;
  roles: Role[];
  running: boolean;
  lastCall?: LastCall | null;
  onOpenSettings?: () => void;
}) {
  const { locale, t } = useI18n();
  const { data } = useConfig(sid, true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const zh = locale === 'zh-CN';
  const role = (running ? roles.find((item) => item.active) : undefined)
    ?? roles.find((item) => item.role === 'manager');
  const trial = data?.trial_mode === true;
  const recent = !!lastCall && (running || Date.now() / 1000 - (lastCall.ts || 0) < LAST_CALL_CURRENT_S);
  const actual = !trial && recent && lastCall?.backend ? lastCall : null;
  const backend = actual ? actual.backend : role?.backend;
  const model = trial ? 'GPT-5.5' : actual ? (actual.model || 'auto') : role?.model;
  const effort = trial ? 'high' : actual ? actual.effort : role?.effort;
  // The configured role that made the last call, read from settings so an
  // Apply shows up here immediately rather than after the next call.
  // The backend names the role whose settings govern the call; without it
  // there is nothing honest to compare against, so no "next call" hint.
  const configured = actual?.role
    ? data?.roles.find((item) => item.role === actual.role)
    : undefined;
  const nextBackend = configured && configured.backend && configured.backend !== actual?.backend ? configured.backend : '';
  const nextModel = configured && !unset(configured.model) && configured.model !== actual?.model ? configured.model : '';
  const next = nextBackend || nextModel
    ? [backendLabel(nextBackend || configured!.backend, data), nextModel || configured!.model || 'auto'].join(' · ')
    : '';
  const effortName = effort ? ({ low: zh ? '低' : 'low', medium: zh ? '中' : 'medium', high: zh ? '高' : 'high' } as Record<string, string>)[effort] ?? effort : '';
  const shown = trial ? 'GPT-5.5' : model && !unset(model) ? model : (zh ? '自动' : 'Automatic');
  // Everything else about the run is a hover away: the backend, and the role at work.
  const detail = [
    trial ? (zh ? '试用' : 'Trial') : backend ? backendLabel(backend, data) : '',
    !trial && !actual && running && role?.active ? (isAgentRole(role.role) ? agentRoleName(role.role, t) : role.role) : '',
  ].filter(Boolean).join(' · ');
  const title = [detail, actual
    ? (zh ? '最近一次调用实际用的模型；点击选择模型' : 'The model the latest call actually used; click to choose a model')
    : (zh ? '当前设置的模型；点击选择模型' : 'The configured model; click to choose a model')].filter(Boolean).join('\n');
  const chip = <>
    <span className="composer-model-dot" data-running={running || undefined} aria-hidden="true" />
    <span className="composer-model-name">{shown}</span>
    {effortName ? <span className="composer-model-meta">{effortName}</span> : null}
    {next ? <span className="composer-model-next" data-runtime-next>{zh ? `下次：${next}` : `next call: ${next}`}</span> : null}
  </>;
  return <div className="composer-runtime" aria-label={zh ? '当前后端与模型' : 'Current backend and model'}>
    {onOpenSettings && !trial
      ? <button type="button" className="composer-model" onClick={() => setPickerOpen(true)} title={title} data-runtime-open-models>{chip}</button>
      : <span className="composer-model" title={title}>{chip}</span>}
    {trial && canOpenDesktopSettings() ? <button type="button" className="composer-model" onClick={openDesktopTrialSettings}>
      {zh ? '更换 Key' : 'Change Key'}
    </button> : null}
    {onOpenSettings && !trial && <ModelPicker sid={sid} open={pickerOpen} onClose={() => setPickerOpen(false)} onOpenSettings={onOpenSettings} />}
  </div>;
}
