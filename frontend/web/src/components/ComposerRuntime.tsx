import { useState } from 'react';
import type { Role, Snapshot } from '../api';
import { useConfig } from '../hooks';
import { useI18n } from '../i18n';
import { backendLabel } from '../lib/backend';
import { agentRoleName, isAgentRole } from '../lib/agentRoles';
import { ModelPicker } from './ModelPicker';

type LastCall = NonNullable<Snapshot['last_call']>;

// A call older than this, with nothing running, no longer describes the
// project: the next call will follow the current settings instead.
const LAST_CALL_CURRENT_S = 6 * 3600;

const unset = (value: string | null | undefined) => !value || ['auto', 'inherit', 'default'].includes(value.toLowerCase());

/**
 * What the work runs on, as one quiet chip under the message box: the model and
 * effort the settings choose, or, set to Automatic, what the newest real call
 * used; a dot that lights while the team works; and, while a turn begun on an
 * earlier choice is still running, the model that turn keeps. Clicking opens the
 * model chooser, which links on to the full settings.
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
  const recent = !!lastCall && (running || Date.now() / 1000 - (lastCall.ts || 0) < LAST_CALL_CURRENT_S);
  const actual = recent && lastCall?.backend ? lastCall : null;
  // The settings of the role at work, read so that a model just chosen shows here at once.
  const configured = data?.roles.find((item) => item.role === (actual?.role ?? role?.role));
  const model = !unset(configured?.model) ? configured!.model : actual ? (actual.model || 'auto') : role?.model;
  const backend = configured?.backend || actual?.backend || role?.backend;
  const effort = configured?.reasoning_effort || actual?.effort || role?.effort;
  const earlier = running && actual && !unset(actual.model) && actual.model !== model ? actual.model : '';
  const effortName = effort ? ({ low: zh ? '低' : 'low', medium: zh ? '中' : 'medium', high: zh ? '高' : 'high' } as Record<string, string>)[effort] ?? effort : '';
  const shown = model && !unset(model) ? model : (zh ? '自动' : 'Automatic');
  // Everything else about the run is a hover away: the backend, and the role at work.
  const detail = [
    backend ? backendLabel(backend, data) : '',
    !actual && running && role?.active ? (isAgentRole(role.role) ? agentRoleName(role.role, t) : role.role) : '',
  ].filter(Boolean).join(' · ');
  const title = [detail, zh ? '工作用的模型；点击选择模型' : 'The model the work runs on; click to choose a model'].filter(Boolean).join('\n');
  const chip = <>
    <span className="composer-model-dot" data-running={running || undefined} aria-hidden="true" />
    <span className="composer-model-name">{shown}</span>
    {effortName ? <span className="composer-model-meta">{effortName}</span> : null}
    {earlier ? <span className="composer-model-next" data-runtime-earlier>{zh ? `这一轮仍用 ${earlier}` : `this turn: ${earlier}`}</span> : null}
  </>;
  return <div className="composer-runtime" aria-label={zh ? '当前后端与模型' : 'Current backend and model'}>
    {onOpenSettings
      ? <button type="button" className="composer-model" onClick={() => setPickerOpen(true)} title={title} data-runtime-open-models>{chip}</button>
      : <span className="composer-model" title={title}>{chip}</span>}
    {onOpenSettings && <ModelPicker sid={sid} open={pickerOpen} onClose={() => setPickerOpen(false)} onOpenSettings={onOpenSettings} />}
  </div>;
}
