import { describe, expect, it } from 'vitest';
import { choiceWrites, formatLatency, formatRate, modelChoiceLabel } from '../lib/modelChoice';

describe('model figures', () => {
  it('keeps Automatic visible with the backend default, with no invented model when none is reported', () => {
    expect(modelChoiceLabel('', 'gpt-6.1-sol', 'zh-CN')).toBe('自动（gpt-6.1-sol）');
    expect(modelChoiceLabel('auto', 'copilot-default', 'en')).toBe('Automatic (copilot-default)');
    expect(modelChoiceLabel('inherit', undefined, 'zh-CN')).toBe('自动');
    expect(modelChoiceLabel('chosen-model', 'other-model', 'zh-CN')).toBe('chosen-model');
  });
  it('formats rates and call times', () => {
    expect(formatRate(2)).toBe('$2');
    expect(formatRate(1.25)).toBe('$1.25');
    expect(formatRate(0.2)).toBe('$0.2');
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
    // '' is the backend's default.
    expect(choiceWrites({ model: '', effort: '' }, { model: 'a', effort: 'high' }))
      .toEqual([{ name: 'ARGUS_SKILL_MODEL', value: '' }]);
  });
});
