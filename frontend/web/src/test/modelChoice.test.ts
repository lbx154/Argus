import { describe, expect, it } from 'vitest';
import { choiceWrites, formatLatency, formatRate, formatUsd } from '../lib/modelChoice';

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
  it("writes the project's model and effort, each only if it changes", () => {
    expect(choiceWrites({ model: 'b', effort: '' }, { model: 'a', effort: 'high' }))
      .toEqual([{ name: 'ARGUS_SKILL_MODEL', value: 'b' }]);
    expect(choiceWrites({ model: 'a', effort: 'medium' }, { model: 'a', effort: 'high' }))
      .toEqual([{ name: 'ARGUS_SKILL_REASONING_EFFORT', value: 'medium' }]);
    expect(choiceWrites({ model: 'a', effort: 'high' }, { model: 'a', effort: 'high' })).toEqual([]);
  });
});
