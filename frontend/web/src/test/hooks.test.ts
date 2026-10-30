import { createElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ACTIVE_DAEMON_POLL_MS,
  PROJECT_POLL_MS,
  SNAPSHOT_POLL_MS,
  projectPollInterval,
  snapshotPollInterval,
  useThrottledValue,
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


describe('local log display throttle', () => {
  let view: ReactTestRenderer | undefined;
  let displayed: string;
  function Probe({ value }: { value: string }) {
    displayed = useThrottledValue(value, 250);
    return null;
  }
  afterEach(() => { act(() => view?.unmount()); view = undefined; vi.useRealTimers(); });

  it('displays the latest log at the trailing edge and cancels pending display work on unmount', () => {
    vi.useFakeTimers();
    act(() => { view = create(createElement(Probe, { value: 'initial' })); });
    expect(displayed).toBe('initial');
    act(() => view!.update(createElement(Probe, { value: 'first' })));
    expect(displayed).toBe('first');
    act(() => { vi.advanceTimersByTime(50); view!.update(createElement(Probe, { value: 'second' })); });
    act(() => { vi.advanceTimersByTime(50); view!.update(createElement(Probe, { value: 'latest' })); });
    expect(displayed).toBe('first');
    act(() => { vi.advanceTimersByTime(149); });
    expect(displayed).toBe('first');
    act(() => { vi.advanceTimersByTime(1); });
    expect(displayed).toBe('latest');
    act(() => view!.update(createElement(Probe, { value: 'pending' })));
    expect(vi.getTimerCount()).toBe(1);
    act(() => view!.unmount()); view = undefined;
    expect(vi.getTimerCount()).toBe(0);
  });

  it('starts a new project with its own logs even when the previous project has a pending update', () => {
    vi.useFakeTimers();
    act(() => { view = create(createElement(Probe, { key: 'a', value: 'project A' })); });
    act(() => view!.update(createElement(Probe, { key: 'a', value: 'A first' })));
    act(() => view!.update(createElement(Probe, { key: 'a', value: 'A pending' })));
    act(() => view!.update(createElement(Probe, { key: 'b', value: 'project B' })));
    expect(displayed).toBe('project B');
    act(() => { vi.advanceTimersByTime(250); });
    expect(displayed).toBe('project B');
  });
});
