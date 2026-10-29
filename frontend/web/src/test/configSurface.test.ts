import { describe, expect, it } from 'vitest';
import type { ConfigKnob } from '../api';
import { noOperatorMode } from '../lib/configSurface';

const knob = (name: string, group = 'internal'): ConfigKnob => ({
  name,
  group,
  value: 'value',
  source: 'default',
  default: 'default',
  doc: 'raw description',
});

describe('noOperatorMode', () => {
  const operator = (value: string): ConfigKnob => ({ ...knob('ARGUS_SKILL_OPERATOR_AVAILABLE', 'mission'), value });
  it('is on only when the operator switch is off', () => {
    expect(noOperatorMode([operator('0')])).toBe(true);
    expect(noOperatorMode([operator('false')])).toBe(true);
    expect(noOperatorMode([operator('true')])).toBe(false);
    expect(noOperatorMode([operator('1')])).toBe(false);
    expect(noOperatorMode([])).toBe(false);
    expect(noOperatorMode(undefined)).toBe(false);
  });
});
