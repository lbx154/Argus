import { describe, expect, it } from 'vitest';
import { choiceWrites, formatLatency, formatRate, formatUsd, modelTiers } from '../lib/modelChoice';

const priced = (model: string, output: number | null) => ({
  model,
  price: output == null ? null : { input: output / 5, cached_input: output / 50, output },
});

describe('model price bands', () => {
  it('ranks by the price table: the dearest is best, a quarter or less is economy, unpriced has no band', () => {
    expect(modelTiers([
      priced('luna', 1.2), priced('mini', 2), priced('sol-6.1', 10), priced('gpt-5.5', 10),
      priced('sol-5.6', 30), priced('astra', null),
    ])).toEqual({ luna: 'economy', mini: 'economy', 'sol-6.1': 'balanced', 'gpt-5.5': 'balanced', 'sol-5.6': 'best' });
  });

  it('has nothing to compare with a single price', () => {
    expect(modelTiers([priced('a', 10), priced('b', 10)])).toEqual({ a: 'balanced', b: 'balanced' });
    expect(modelTiers([priced('a', null)])).toEqual({});
  });
});

describe('model figures', () => {
  it('formats rates, small amounts and call times', () => {
    expect(formatRate(2)).toBe('$2');
    expect(formatRate(1.25)).toBe('$1.25');
    expect(formatRate(0.2)).toBe('$0.2');
    expect(formatUsd(0.0104375)).toBe('$0.010');
    expect(formatUsd(0.0004)).toBe('<$0.001');
    expect(formatUsd(1.234)).toBe('$1.23');
    expect(formatLatency(20_400, 'en')).toBe('20 s');
    expect(formatLatency(20_400, 'zh-CN')).toBe('20 秒');
    expect(formatLatency(95_000, 'en')).toBe('1.6 min');
    expect(formatLatency(300, 'zh-CN')).toBe('不到 1 秒');
  });
});

describe('applying a choice', () => {
  const roles = (model: string) => ({ manager: model, planner: model, engineer: model, reviewer: model });

  it('one model for every role sets the shared model and releases role pins', () => {
    expect(choiceWrites({ mode: 'all', shared: 'b', perRole: roles('a'), current: roles('a'), effort: '', currentEffort: roles('high') }))
      .toEqual([{ name: 'ARGUS_SKILL_MODEL', value: 'b', applyToRoles: true }]);
    expect(choiceWrites({ mode: 'all', shared: 'a', perRole: roles('a'), current: roles('a'), effort: '', currentEffort: roles('high') }))
      .toEqual([]);
  });

  it('per role writes only the roles that changed, and an effort only where it differs', () => {
    const writes = choiceWrites({
      mode: 'roles', shared: '', perRole: { ...roles('a'), engineer: 'b' }, current: roles('a'),
      effort: 'medium', currentEffort: { ...roles('high'), reviewer: 'medium' },
    });
    expect(writes).toEqual([
      { name: 'ARGUS_SKILL_ENGINEER_MODEL', value: 'b' },
      { name: 'ARGUS_SKILL_MANAGER_REASONING_EFFORT', value: 'medium' },
      { name: 'ARGUS_SKILL_PLANNER_REASONING_EFFORT', value: 'medium' },
      { name: 'ARGUS_SKILL_ENGINEER_REASONING_EFFORT', value: 'medium' },
    ]);
  });
});
