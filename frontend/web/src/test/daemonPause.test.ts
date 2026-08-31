import { describe, expect, it } from 'vitest';
import { MANUAL_STOP_FORCE } from '../useProjectDaemonActions';

describe('visible daemon stop semantics', () => {
  it('uses the explicit verified force-stop path instead of waiting for a boundary', () => {
    expect(MANUAL_STOP_FORCE).toBe(true);
  });
});
