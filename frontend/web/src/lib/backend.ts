import type { ConfigSnapshot } from '../api';

export const BACKEND_OPTIONS = [
  { value: 'copilot', label: 'settings.backendLabel.copilot' },
  { value: 'codex', label: 'settings.backendLabel.codex' },
  { value: 'claude', label: 'settings.backendLabel.claude' },
  { value: 'cursor', label: 'settings.backendLabel.cursor' },
  { value: 'opencode', label: 'settings.backendLabel.opencode' },
  { value: 'pi', label: 'settings.backendLabel.pi' },
  { value: 'grok', label: 'settings.backendLabel.grok' },
  { value: 'qoder', label: 'settings.backendLabel.qoder' },
  { value: 'dsh', label: 'settings.backendLabel.dsh' },
] as const;

export type BackendOption = (typeof BACKEND_OPTIONS)[number]['value'];

const BACKEND_ALIASES: Record<string, BackendOption> = {
  copilot: 'copilot',
  codex: 'codex',
  claude: 'claude',
  cursor: 'cursor',
  opencode: 'opencode',
  pi: 'pi',
  grok: 'grok',
  qoder: 'qoder',
  dsh: 'dsh',
};

export function backendOption(value: string): BackendOption | '' {
  return BACKEND_ALIASES[value] ?? '';
}

export function backendLabel(
  value: string,
  t: (key: string) => string,
): string {
  const option = backendOption(value);
  return option ? t(`settings.backendLabel.${option}`) : value;
}

export function configuredBackend(config?: ConfigSnapshot): string {
  return config?.operator_knobs.find((knob) => knob.name === 'ARGUS_SKILL_RUNNER_BACKEND')?.value
    ?? config?.roles[0]?.backend
    ?? '';
}

export function configuredModel(config?: ConfigSnapshot): string {
  return config?.operator_knobs.find((knob) => knob.name === 'ARGUS_SKILL_MODEL')?.value
    ?? config?.roles[0]?.model
    ?? '';
}
