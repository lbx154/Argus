import { translate } from '../i18n';
import { agentRoleName, isAgentRole } from '../lib/agentRoles';

export type WorkbenchText = (zh: string, en: string) => string;

const ROLE_LABELS: Record<string, readonly [string, string]> = {
  system: ['Argus', 'Argus'],
  operator: ['你', 'You'],
  stopped: ['已停止', 'Stopped'],
  idle: ['等待中', 'Waiting'],
};

function label(
  value: string | null | undefined,
  labels: Record<string, readonly [string, string]>,
  fallback: readonly [string, string],
  text: WorkbenchText,
): string {
  const pair = labels[String(value ?? '').toLowerCase()] ?? fallback;
  return text(pair[0], pair[1]);
}

export function roleLabel(value: string | null | undefined, text: WorkbenchText): string {
  const role = String(value ?? '').toLowerCase();
  if (isAgentRole(role)) return agentRoleName(role, key => text(translate(key, {}, 'zh-CN'), translate(key, {}, 'en')));
  return label(value, ROLE_LABELS, ['Argus', 'Argus'], text);
}
