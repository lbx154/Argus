import type { ConfigKnob } from '../api';

interface EssentialKnob {
  name: string;
  group: string;
  label: string;
  description: string;
}

export type DisplayConfigKnob = ConfigKnob & { label: string };

const ESSENTIAL_KNOBS: EssentialKnob[] = [
  {
    name: 'ARGUS_SKILL_CONTROL_PLANE_CALL_CAP_USD',
    group: 'Limits',
    label: 'Manager call cap',
    description: 'Maximum USD reserved for one Manager, router, or inline reply.',
  },
  {
    name: 'ARGUS_SKILL_MAX_ACTIVE_DAEMONS',
    group: 'Limits',
    label: 'Active daemon limit',
    description: 'Maximum background sessions running on this host.',
  },
  {
    name: 'ARGUS_SKILL_UNPRICED_COST_POLICY',
    group: 'Safety',
    label: 'Unpriced calls',
    description: 'Whether calls with unresolved pricing are blocked or allowed.',
  },
  {
    name: 'ARGUS_SKILL_SAFE_MODE',
    group: 'Safety',
    label: 'Safe mode',
    description: 'Enable extra-conservative runtime guardrails.',
  },
  {
    name: 'ARGUS_SKILL_ENABLE_TELEGRAM',
    group: 'Interface',
    label: 'Telegram',
    description: 'Enable the Telegram notification bridge.',
  },
  {
    name: 'ARGUS_SKILL_SHOW_REASONING',
    group: 'Interface',
    label: 'Show reasoning',
    description: 'Stream role reasoning into the cockpit activity view.',
  },
];

export function conciseConfigKnobs(knobs: ConfigKnob[]): DisplayConfigKnob[] {
  const byName = new Map(knobs.map((knob) => [knob.name, knob]));
  return ESSENTIAL_KNOBS.flatMap((item) => {
    const knob = byName.get(item.name);
    return knob
      ? [{
          ...knob,
          group: item.group,
          label: item.label,
          doc: item.description,
        }]
      : [];
  });
}

export function compactConfigSource(source: string): string {
  const value = source.trim();
  if (!value) return '';
  if (value === 'not applicable for this model') return 'n/a';
  if (value.startsWith('capability vault')) return 'vault / default';
  if (value.startsWith('default:')) return 'default';

  const persisted = value.startsWith('persisted:');
  const raw = persisted ? value.slice('persisted:'.length) : value;
  if (raw.startsWith('ARGUS_SKILL_')) {
    const label = raw
      .slice('ARGUS_SKILL_'.length)
      .toLowerCase()
      .replaceAll('_', ' ');
    return `${label}${persisted ? ' · persisted' : ' · env'}`;
  }
  return value;
}

