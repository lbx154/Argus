import type { Role, Snapshot } from '../api';
import { useConfig } from '../hooks';
import { useI18n } from '../i18n';
import { backendLabel } from '../lib/backend';
import { agentRoleName, isAgentRole } from '../lib/agentRoles';
import { canOpenDesktopSettings, openDesktopTrialSettings } from '../lib/desktopBridge';

type LastCall = NonNullable<Snapshot['last_call']>;

// A call older than this, with nothing running, no longer describes the
// project: the next call will follow the current settings instead.
const LAST_CALL_CURRENT_S = 6 * 3600;

const unset = (value: string | null | undefined) => !value || ['auto', 'inherit', 'default'].includes(value.toLowerCase());

/**
 * What the work is actually running on: the newest real call's backend, model
 * and effort. When settings now ask for something else (just applied, not yet
 * used by a call), say what the next call will use. Clicking opens settings.
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
    ? [backendLabel(nextBackend || configured!.backend, t), nextModel || configured!.model || 'auto'].join(' · ')
    : '';
  const label = <>
    {trial ? (zh ? '试用' : 'Trial') : backend ? backendLabel(backend, t) : (zh ? '模型信息待确认' : 'Model information unavailable')}
    {model ? ` · ${model}` : ''}{effort ? ` · ${effort}` : ''}
    {!trial && !actual && running && role?.active ? ` · ${isAgentRole(role.role) ? agentRoleName(role.role, t) : role.role}` : ''}
    {next ? <span className="composer-runtime-next" data-runtime-next>{zh ? ` → 下一次调用：${next}` : ` → next call: ${next}`}</span> : null}
  </>;
  const title = actual
    ? (zh ? '最近一次模型调用实际使用的后端、模型和推理强度；点击更改' : 'Backend, model and effort the latest model call actually used; click to change')
    : (zh ? '当前配置的后端与模型；点击更改' : 'Configured backend and model; click to change');
  return <div className="composer-runtime" aria-label={zh ? '当前后端与模型' : 'Current backend and model'}>
    {onOpenSettings && !trial
      ? <button type="button" className="composer-runtime-model" onClick={onOpenSettings} title={title} data-runtime-open-settings>{label}</button>
      : <span className="composer-runtime-model" title={trial ? undefined : title}>{label}</span>}
    {trial && canOpenDesktopSettings() ? <button type="button" onClick={openDesktopTrialSettings}>
      {zh ? '更换 Key' : 'Change Key'}
    </button> : null}
  </div>;
}
