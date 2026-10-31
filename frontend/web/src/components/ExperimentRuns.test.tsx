import { describe, expect, it } from 'vitest';
import { progressOf } from './ExperimentRuns';

describe('progressOf', () => {
  it('reads tqdm and step counters from the latest line that has one', () => {
    expect(progressOf(['loading', ' 30%|███       | 120/400 [00:41<01:36,  2.90it/s]'])).toEqual({ done: 120, total: 400 });
    expect(progressOf(['epoch 3/10', 'step: 250/1000 loss 0.31', 'saving'])).toEqual({ done: 250, total: 1000 });
    expect(progressOf(['Epoch [2/5] lr 1e-4'])).toEqual({ done: 2, total: 5 });
  });
  it('takes no date, ratio or impossible count for progress', () => {
    expect(progressOf(['run 2026/10/31 started', 'accuracy 81/100 correct', 'step 12/0', 'step 9/8'])).toBeNull();
  });
});
