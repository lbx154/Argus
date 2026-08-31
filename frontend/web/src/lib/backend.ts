import type { ConfigSnapshot } from '../api';

export const BACKEND_OPTIONS = [
  { value: 'copilot', label: 'settings.backendLabel.copilot' },
  { value: 'openai', label: 'settings.backendLabel.openai' },
  { value: 'anthropic', label: 'settings.backendLabel.anthropic' },
] as const;

export type BackendOption = (typeof BACKEND_OPTIONS)[number]['value'];

const BACKEND_ALIASES: Record<string, BackendOption> = {
  copilot: 'copilot',
  openai: 'openai',
  anthropic: 'anthropic',
  codex: 'openai',
  claude: 'anthropic',
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
