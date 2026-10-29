import { describe, expect, it } from 'vitest';
import {
  ACTIVE_DAEMON_POLL_MS,
  PROJECT_POLL_MS,
  SNAPSHOT_POLL_MS,
  projectPollInterval,
  snapshotPollInterval,
} from '../hooks';

describe('project state polling capability', () => {
  it('keeps background polling below interaction-jank cadence', () => {
    expect(PROJECT_POLL_MS).toBeGreaterThanOrEqual(15_000);
    expect(SNAPSHOT_POLL_MS).toBeGreaterThanOrEqual(8_000);
    expect(ACTIVE_DAEMON_POLL_MS).toBeLessThanOrEqual(2_000);
  });
  it('polls live daemons quickly enough to show their automatic exit', () => {
    expect(projectPollInterval({ projects: [], local_cwd: '' })).toBe(PROJECT_POLL_MS);
    expect(projectPollInterval({
      projects: [{ workdir: '/work', daemon_alive: true } as never],
      local_cwd: '',
    })).toBe(ACTIVE_DAEMON_POLL_MS);
    expect(snapshotPollInterval()).toBe(SNAPSHOT_POLL_MS);
    expect(snapshotPollInterval({ daemon: { alive: true } } as never)).toBe(
      ACTIVE_DAEMON_POLL_MS,
    );
  });
});
