import type { Role } from '../api';
import { useConfig } from '../hooks';
import { useI18n } from '../i18n';
import { backendLabel } from '../lib/backend';
import { canOpenDesktopSettings, openDesktopTrialSettings } from '../lib/desktopBridge';

/** Use the live session's resolved role, including per-role backend overrides. */
export function ComposerRuntime({ sid, roles, running }: { sid: string; roles: Role[]; running: boolean }) {
  const { locale, t } = useI18n();
  const { data } = useConfig(sid, true);
  const zh = locale === 'zh-CN';
  const role = (running ? roles.find((item) => item.active) : undefined)
    ?? roles.find((item) => item.role === 'manager');
  const trial = data?.trial_mode === true;
  const model = trial ? 'GPT-5.5' : role?.model;
  const effort = trial ? 'high' : role?.effort;
  return <div className="composer-runtime" aria-label={zh ? '当前后端与模型' : 'Current backend and model'}>
    <span className="composer-runtime-model">
      {trial ? (zh ? '试用' : 'Trial') : role ? backendLabel(role.backend, t) : (zh ? '模型信息待确认' : 'Model information unavailable')}
      {model ? ` · ${model}` : ''}{effort ? ` · ${effort}` : ''}
      {!trial && running && role?.active ? ` · ${role.role}` : ''}
    </span>
    {trial && canOpenDesktopSettings() ? <button type="button" onClick={openDesktopTrialSettings}>
      {zh ? '更换 Key' : 'Change Key'}
    </button> : null}
  </div>;
}
